"""Named-agent helpers for the OSS Hermes API extension."""

from __future__ import annotations

import asyncio
import base64
import codecs
import json
import os
import re
import secrets
import shutil
import sqlite3
import string
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

import yaml

from .nine_router import (
    NINE_ROUTER_DEFAULT_MODEL,
    NINE_ROUTER_PROVIDER,
    NineRouterAPIError,
    NineRouterManager,
    normalize_nine_router_config,
)


AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
GENERATED_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9]{5}$")
SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_TEXT_CHARS = 200_000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_CHAT_TIMEOUT_SECONDS = 3600
DEFAULT_CHAT_TIMEOUT_SECONDS = 900
GENERATED_AGENT_ID_LENGTH = 6
GENERATED_AGENT_ID_FIRST_ALPHABET = string.ascii_lowercase
GENERATED_AGENT_ID_ALPHABET = string.ascii_lowercase + string.digits
DEFAULT_ROOT_PROFILE = "/root/.hermes"
DEFAULT_PROFILES_ROOT = "/root/.hermes/profiles"
DEFAULT_LEGACY_AGENTS_ROOT = "/root/agents"
DEFAULT_AGENT_CONFIG_DIR = "/root/.config/sandbox-agent"
METADATA_FILE = "agent.json"
CREDENTIAL_FILES = (".env", "auth.json")
AGENT_CREDENTIAL_ENV_KEYS = ("NINE_ROUTER_API_KEY",)
SEED_FILES = ("config.yaml", "SOUL.md", "AGENTS.md", "mcp.json", *CREDENTIAL_FILES)
SEED_DIRS = ("memories", "cron", "plugins", "home")
PROFILE_STATE_DIRS = (
    "skills",
    "sessions",
    "logs",
    "memories",
    "cron",
    "plugins",
    "home",
)


class AgentAPIError(ValueError):
    """Expected API error for named-agent operations."""

    def __init__(self, message: str, *, code: str = "invalid_agent", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class AgentManager:
    """Filesystem-backed manager for per-user Hermes agents inside one VM."""

    def __init__(
        self,
        *,
        root_profile: str | Path | None = None,
        profiles_root: str | Path | None = None,
        legacy_agents_root: str | Path | None = None,
    ):
        self.root_profile = Path(
            root_profile
            or os.environ.get("HERMES_ROOT_PROFILE")
            or DEFAULT_ROOT_PROFILE
        )
        self.profiles_root = Path(
            profiles_root
            or os.environ.get("HERMES_PROFILES_ROOT")
            or self.root_profile / "profiles"
            or DEFAULT_PROFILES_ROOT
        )
        self.legacy_agents_root = Path(
            legacy_agents_root
            or os.environ.get("HERMES_LEGACY_AGENTS_ROOT")
            or os.environ.get("HERMES_AGENTS_ROOT")
            or DEFAULT_LEGACY_AGENTS_ROOT
        )
        self.nine_router = NineRouterManager()

    def list_agents(self) -> dict[str, Any]:
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        agents = []
        seen: set[str] = set()
        for path in sorted(self.profiles_root.iterdir(), key=lambda item: item.name):
            if not self._is_native_agent_profile(path):
                continue
            seen.add(path.name)
            agents.append(self.describe_agent(path.name, include_memory=False))
        if self.legacy_agents_root.is_dir():
            for path in sorted(self.legacy_agents_root.iterdir(), key=lambda item: item.name):
                if path.name in seen or not path.is_dir() or not (path / ".profile").is_dir():
                    continue
                agents.append(self.describe_agent(path.name, include_memory=False))
        return {
            "object": "hermes.agents",
            "root": str(self.profiles_root),
            "agents": agents,
        }

    def create_agent(self, body: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        name = self._agent_name(body.get("name") or self._new_agent_name())
        existed = self._existing_profile_dir(name) is not None
        if existed and not bool(body.get("idempotent", False)):
            raise AgentAPIError(
                f"Agent already exists: {name}", code="agent_exists", status=409
            )

        profile_dir = self._profile_dir(name)
        workspace_dir = self._workspace_dir(name)
        profile_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        for dirname in PROFILE_STATE_DIRS:
            (profile_dir / dirname).mkdir(parents=True, exist_ok=True)

        if not existed or bool(body.get("refresh_seed", False)):
            self._copy_seed_profile(profile_dir, copy_credentials=bool(body.get("copy_credentials", True)))
            self._copy_root_skills(profile_dir, overwrite=True)
        self._write_workspace_cwd(profile_dir, workspace_dir)
        self._ensure_workspace_agents(profile_dir, workspace_dir)
        self._initialize_state_db(profile_dir)

        metadata = self._read_metadata(profile_dir)
        now = time.time()
        metadata.setdefault("name", name)
        metadata.setdefault("profile_name", name)
        metadata.setdefault("created_at", now)
        metadata["updated_at"] = now
        for field in ("description", "title"):
            if field in body:
                value = body.get(field)
                metadata[field] = "" if value is None else str(value).strip()
        self._write_metadata(profile_dir, metadata)
        self._write_profile_manifest(profile_dir, metadata)

        if "soul" in body:
            self._write_text(profile_dir / "SOUL.md", body.get("soul"))
        if "memory" in body:
            self.write_memory(name, {"memory": body.get("memory")})
        if "instructions" in body:
            self._write_text(workspace_dir / "AGENTS.md", body.get("instructions"))
        if isinstance(body.get("config"), Mapping):
            self.update_config(name, body["config"])

        return self.describe_agent(name), 200 if existed else 201

    def describe_agent(self, raw_name: Any, *, include_memory: bool = True) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        workspace_dir = self._workspace_dir(name)
        metadata = self._read_metadata(profile_dir)
        config = self._read_config(profile_dir)
        payload = {
            "object": "hermes.agent",
            "name": name,
            "profile_name": name,
            "path": str(self._agent_dir(name)),
            "profile_path": str(profile_dir),
            "workspace_path": str(workspace_dir),
            "metadata": metadata,
            "config": self._effective_config(config, metadata),
            "soul": self._read_text(profile_dir / "SOUL.md"),
            "skills": self.list_skills(name)["skills"],
        }
        if include_memory:
            payload["memory"] = self.read_memory(name)
        return payload

    def delete_agent(self, raw_name: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if profile_dir == self._legacy_profile_dir(name):
            shutil.rmtree(self._legacy_agent_dir(name))
        else:
            shutil.rmtree(profile_dir)
        return {"object": "hermes.agent_delete", "agent": name, "deleted": True}

    def update_config(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        config_path = profile_dir / "config.yaml"
        config = self._read_config(profile_dir)

        allowed = {
            "provider",
            "model",
            "reasoning",
            "effort",
            "approval_mode",
            "system_prompt",
            "language",
            "stream_output",
            "config",
            "soul",
        }
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise AgentAPIError(
                f"Unsupported config fields: {', '.join(unknown)}",
                code="invalid_agent_config",
            )

        if "provider" in body:
            provider = self._nonempty_string(body["provider"], "provider").lower()
            if provider not in {"9router", "nine-router", "auto", NINE_ROUTER_PROVIDER}:
                raise AgentAPIError(
                    "provider must be nine-router",
                    code="unsupported_provider",
                )
        if "model" in body:
            model = self._nonempty_string(body["model"], "model")
            self._set_nested(config, ("model", "default"), model)
        if isinstance(body.get("config"), Mapping):
            self._deep_merge(config, dict(body["config"]))
        if "reasoning" in body or "effort" in body:
            effort = str(body.get("effort") or self._get_nested(config, ("agent", "reasoning_effort"), "medium")).strip().lower()
            if "reasoning" in body and not self._coerce_bool(body["reasoning"]):
                effort = "none"
            elif effort == "none":
                effort = "medium"
            if effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
                raise AgentAPIError(
                    "effort must be one of: minimal, low, medium, high, xhigh",
                    code="invalid_agent_config",
                )
            self._set_nested(config, ("agent", "reasoning_effort"), effort)
        if "approval_mode" in body:
            mode = self._approval_mode(body["approval_mode"])
            self._set_nested(config, ("approvals", "mode"), "manual" if mode == "on" else "off")
        if "system_prompt" in body:
            prompt = self._text_value(body["system_prompt"], field="system_prompt", max_chars=20_000)
            self._set_nested(config, ("agent", "system_prompt"), prompt)
        if "language" in body:
            language = self._text_value(body["language"], field="language", max_chars=128).strip()
            self._set_nested(config, ("agent", "language"), language)
        if "stream_output" in body:
            self._set_nested(config, ("agent", "stream_output"), self._coerce_bool(body["stream_output"]))
        self._set_nested(config, ("terminal", "backend"), self._get_nested(config, ("terminal", "backend"), "local"))
        self._set_nested(config, ("terminal", "cwd"), str(self._workspace_dir(name)))
        self._normalize_agent_skill_config(config)
        normalize_nine_router_config(
            config,
            str(body["model"]).strip() if "model" in body else None,
        )
        if "soul" in body:
            self._write_text(profile_dir / "SOUL.md", body["soul"])

        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(config, file, sort_keys=False, allow_unicode=False)
        return self.describe_agent(name)

    def list_skills(self, raw_name: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        disabled = self._disabled_skills(self._read_config(profile_dir))
        skills_root = profile_dir / "skills"
        skills = []
        seen: set[str] = set()
        self._append_skills_from_dir(skills, seen, skills_root, disabled)
        return {
            "object": "hermes.agent_skills",
            "agent": name,
            "skills": sorted(skills, key=lambda item: (item["category"], item["name"])),
        }

    async def install_skill(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if not isinstance(body, Mapping):
            raise AgentAPIError("request body must be an object", code="invalid_skill_request")
        target_profile = profile_dir
        enable = bool(body.get("enable", True))
        if "content" in body:
            skill_id = self._skill_id(body.get("skill_id") or body.get("name"))
            content = self._text_value(body["content"], field="content", max_chars=MAX_TEXT_CHARS)
            skill_dir = target_profile / "skills" / skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)
            self._write_text(skill_dir / "SKILL.md", content)
            self._set_skill_enabled(profile_dir, skill_id, enable)
            payload = self.list_skills(name)
            return payload

        source = self._nonempty_string(body.get("source"), "source")
        command = [self._hermes_binary(), "skills", "install", source, "--yes"]
        if body.get("name"):
            command.extend(["--name", self._skill_id(body["name"])])
        if body.get("category"):
            command.extend(["--category", self._safe_category(body["category"])])
        if bool(body.get("force", False)):
            command.append("--force")
        result = await self._run_hermes_command(
            target_profile,
            self._workspace_dir(name),
            command,
            timeout_seconds=int(body.get("timeout_seconds") or 180),
        )
        if result["exit_code"] != 0:
            raise AgentAPIError(
                "skill install failed",
                code="skill_install_failed",
                status=422,
            )
        if body.get("name"):
            self._set_skill_enabled(profile_dir, self._skill_id(body["name"]), enable)
        payload = self.list_skills(name)
        payload["command"] = result
        return payload

    def set_skill_enabled(
        self,
        raw_name: Any,
        raw_skill_id: Any,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if not isinstance(body, Mapping):
            raise AgentAPIError("request body must be an object", code="invalid_skill_request")
        skill_id = self._skill_id(raw_skill_id)
        if self._find_agent_skill(profile_dir, skill_id) is None:
            raise AgentAPIError(
                f"Skill not found: {skill_id}", code="skill_not_found", status=404
            )
        if "enabled" not in body:
            raise AgentAPIError("enabled is required", code="invalid_skill_request")
        self._set_skill_enabled(profile_dir, skill_id, self._coerce_bool(body["enabled"]))
        return self.list_skills(name)

    def remove_skill(self, raw_name: Any, raw_skill_id: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        skill_id = self._skill_id(raw_skill_id)
        skills_root = profile_dir / "skills"
        removed = False
        if skills_root.is_dir():
            for skill_file in skills_root.rglob("SKILL.md"):
                frontmatter = self._read_skill_frontmatter(skill_file)
                candidates = {skill_file.parent.name, str(frontmatter.get("name") or "").strip()}
                if skill_id in candidates:
                    shutil.rmtree(skill_file.parent)
                    removed = True
                    break
        if not removed:
            raise AgentAPIError(
                f"Skill not found: {skill_id}", code="skill_not_found", status=404
            )
        self._set_skill_enabled(profile_dir, skill_id, True)
        return self.list_skills(name)

    async def chat(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        prepared = self._prepare_chat_command(raw_name, body, ensure_conversation=False)
        if prepared["model"] == NINE_ROUTER_DEFAULT_MODEL:
            await self.nine_router.ensure_auto_combo()
        name = prepared["name"]
        profile_dir = prepared["profile_dir"]
        before = self._latest_session_ids(profile_dir)

        started = time.time()
        result = await self._run_profile_command(
            name,
            prepared["command"],
            engine=str(prepared.get("engine") or "hermes"),
            timeout_seconds=prepared["timeout_seconds"],
        )
        after = self._latest_session_ids(profile_dir)
        session_id = self._detect_changed_session(before, after)
        session = self._session(profile_dir, session_id) if session_id else None
        return {
            "object": "hermes.agent_chat",
            "agent": name,
            "conversation_id": session_id,
            "session": session,
            "response": result["stdout"].strip(),
            "stderr": result["stderr"].strip(),
            "exit_code": result["exit_code"],
            "duration_seconds": round(time.time() - started, 3),
            "provider": prepared.get("provider") or "",
            "engine": prepared.get("engine") or "hermes",
            "workspace_path": str(self._workspace_dir(name)),
            "profile_path": str(profile_dir),
        }

    def chat_stream(self, raw_name: Any, body: Mapping[str, Any]):
        prepared = self._prepare_chat_command(raw_name, body, ensure_conversation=True)
        return self._chat_stream_events(prepared)

    def _prepare_chat_command(
        self,
        raw_name: Any,
        body: Mapping[str, Any],
        *,
        ensure_conversation: bool,
    ) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        self._ensure_router_profile(profile_dir, body.get("model"))
        message = self._text_value(body.get("message"), field="message", max_chars=MAX_TEXT_CHARS)
        conversation_id = ""
        if body.get("conversation_id"):
            conversation_id = self._session_id(body["conversation_id"])
        if ensure_conversation and not conversation_id:
            conversation_id = uuid.uuid4().hex
        if ensure_conversation and self._session(profile_dir, conversation_id) is None:
            self._create_session(
                profile_dir,
                conversation_id,
                model=self._conversation_model(profile_dir, body),
                title=None,
            )

        provider = self._conversation_provider(profile_dir, body)
        model = self._conversation_model(profile_dir, body)
        engine = "hermes"
        command = [self._hermes_binary()]
        if conversation_id:
            command.extend(["--resume", conversation_id])
        if body.get("model"):
            command.extend(["--model", self._nonempty_string(body["model"], "model")])
        skills = body.get("skills")
        if skills is None:
            disabled = self._disabled_skills(self._read_config(profile_dir))
            skills = [
                item["skill_id"]
                for item in self.list_skills(name)["skills"]
                if item["skill_id"] not in disabled
            ] if disabled else []
        normalized_skills = self._normalize_skill_list(skills) if skills else []
        if normalized_skills:
            command.extend(["--skills", ",".join(normalized_skills)])
        if bool(body.get("yolo", False)):
            command.append("--yolo")
        command.extend(["-z", message])

        timeout_seconds = int(body.get("timeout_seconds") or DEFAULT_CHAT_TIMEOUT_SECONDS)
        timeout_seconds = max(1, min(timeout_seconds, MAX_CHAT_TIMEOUT_SECONDS))
        return {
            "name": name,
            "profile_dir": profile_dir,
            "workspace_dir": self._workspace_dir(name),
            "conversation_id": conversation_id,
            "provider": provider,
            "model": model,
            "engine": engine,
            "command": command,
            "timeout_seconds": timeout_seconds,
        }

    async def _chat_stream_events(self, prepared: Mapping[str, Any]):
        profile_dir = prepared["profile_dir"]
        workspace_dir = prepared["workspace_dir"]
        command = list(prepared["command"])
        timeout_seconds = int(prepared["timeout_seconds"])
        conversation_id = str(prepared.get("conversation_id") or "")
        model = str(prepared.get("model") or "")
        created = int(time.time())
        chat_id = "chatcmpl-" + (conversation_id or uuid.uuid4().hex)

        if model == NINE_ROUTER_DEFAULT_MODEL:
            try:
                await self.nine_router.ensure_auto_combo()
            except NineRouterAPIError as exc:
                yield self._chat_sse_error(str(exc))
                yield self._chat_sse_done(chat_id, created, model, conversation_id)
                return
        env = self._command_env(profile_dir, str(prepared.get("engine") or "hermes"))
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(workspace_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stderr_chunks: list[bytes] = []

        async def drain_stderr() -> None:
            while True:
                chunk = await proc.stderr.read(8192)
                if not chunk:
                    return
                stderr_chunks.append(chunk)

        stderr_task = asyncio.create_task(drain_stderr())
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        deadline = time.monotonic() + timeout_seconds
        sent_role = False
        timed_out = False

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                chunk = await asyncio.wait_for(proc.stdout.read(1024), timeout=remaining)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    yield self._chat_sse_chunk(
                        chat_id,
                        created,
                        model,
                        conversation_id,
                        text,
                        include_role=not sent_role,
                    )
                    sent_role = True
            tail = decoder.decode(b"", final=True)
            if tail:
                yield self._chat_sse_chunk(
                    chat_id,
                    created,
                    model,
                    conversation_id,
                    tail,
                    include_role=not sent_role,
                )
                sent_role = True
            remaining = max(0.1, deadline - time.monotonic())
            await asyncio.wait_for(proc.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            await proc.wait()
            yield self._chat_sse_error("agent command timed out")
        finally:
            await stderr_task

        if timed_out:
            yield self._chat_sse_done(chat_id, created, model, conversation_id)
            return
        if int(proc.returncode or 0) != 0:
            stderr = b"".join(stderr_chunks).decode("utf-8", "replace").strip()
            yield self._chat_sse_error(stderr or "agent command failed")
        elif not sent_role:
            yield self._chat_sse_chunk(
                chat_id,
                created,
                model,
                conversation_id,
                "",
                include_role=True,
            )
        yield self._chat_sse_done(chat_id, created, model, conversation_id)

    def _chat_sse_chunk(
        self,
        chat_id: str,
        created: int,
        model: str,
        conversation_id: str,
        content: str,
        *,
        include_role: bool,
    ) -> bytes:
        delta: dict[str, Any] = {}
        if include_role:
            delta["role"] = "assistant"
        if content:
            delta["content"] = content
        return self._sse_data(
            {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "conversation_id": conversation_id,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
        )

    def _chat_sse_done(self, chat_id: str, created: int, model: str, conversation_id: str) -> bytes:
        return self._sse_data(
            {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "conversation_id": conversation_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        ) + b"data: [DONE]\n\n"

    def _chat_sse_error(self, message: str) -> bytes:
        payload = json.dumps({"error": {"message": message}}, separators=(",", ":"))
        return f"event: error\ndata: {payload}\n\n".encode("utf-8")

    def _sse_data(self, payload: Mapping[str, Any]) -> bytes:
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        return f"data: {raw}\n\n".encode("utf-8")

    def list_conversations(self, raw_name: Any, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        body = body or {}
        limit = max(1, min(int(body.get("limit") or 50), 200))
        return {
            "object": "hermes.agent_conversations",
            "agent": name,
            "conversations": self._sessions(profile_dir, limit=limit),
        }

    def create_conversation(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if not isinstance(body, Mapping):
            raise AgentAPIError("request body must be an object", code="invalid_conversation_request")
        session_id = self._session_id(
            body.get("id")
            or body.get("session_id")
            or body.get("conversation_id")
            or uuid.uuid4().hex
        )
        title = self._conversation_title(body)
        model = self._conversation_model(profile_dir, body)
        self._create_session(profile_dir, session_id, model=model, title=title)
        return self.get_conversation(name, session_id)

    def update_conversation(
        self,
        raw_name: Any,
        raw_session_id: Any,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if not isinstance(body, Mapping):
            raise AgentAPIError("request body must be an object", code="invalid_conversation_request")
        session_id = self._session_id(raw_session_id)
        if self._session(profile_dir, session_id) is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )
        title = self._conversation_title(body)
        if title is None:
            raise AgentAPIError("name is required", code="invalid_conversation_request")
        self._update_session_title(profile_dir, session_id, title)
        return self.get_conversation(name, session_id)

    def delete_conversation(self, raw_name: Any, raw_session_id: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        session_id = self._session_id(raw_session_id)
        if self._session(profile_dir, session_id) is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )
        self._delete_session(profile_dir, session_id)
        return {
            "object": "hermes.agent_conversation_delete",
            "agent": name,
            "id": session_id,
            "deleted": True,
        }

    def get_conversation(self, raw_name: Any, raw_session_id: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        session_id = self._session_id(raw_session_id)
        session = self._session(profile_dir, session_id)
        if session is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )
        return {
            "object": "hermes.agent_conversation",
            "agent": name,
            "conversation": session,
            "messages": self._messages(profile_dir, session_id),
        }

    def list_workspace(self, raw_name: Any, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        body = body or {}
        directory = self._workspace_path(name, body.get("path") or ".", require_file=False)
        if not directory.is_dir():
            raise AgentAPIError("path is not a directory", code="invalid_workspace_path")
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            stat = child.stat()
            entries.append(
                {
                    "name": child.name,
                    "path": str(child.relative_to(self._workspace_dir(name))),
                    "type": "directory" if child.is_dir() else "file",
                    "size_bytes": stat.st_size,
                    "updated_at": stat.st_mtime,
                }
            )
        return {
            "object": "hermes.agent_workspace",
            "agent": name,
            "path": str(directory.relative_to(self._workspace_dir(name))),
            "entries": entries,
        }

    def read_workspace_file(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        file_path = self._workspace_path(name, body.get("path"), require_file=True)
        if not file_path.is_file():
            raise AgentAPIError("path is not a file", code="invalid_workspace_path")
        size = file_path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise AgentAPIError(
                f"file is too large (max {MAX_FILE_BYTES} bytes)",
                code="workspace_file_too_large",
                status=413,
            )
        content = file_path.read_bytes()
        return {
            "object": "hermes.agent_workspace_file",
            "agent": name,
            "path": str(file_path.relative_to(self._workspace_dir(name))),
            "size_bytes": len(content),
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    def write_workspace_file(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        file_path = self._workspace_path(name, body.get("path"), require_file=True)
        if "content_base64" in body:
            try:
                content = base64.b64decode(str(body["content_base64"]), validate=True)
            except Exception as exc:
                raise AgentAPIError("content_base64 is invalid", code="invalid_content") from exc
        else:
            content = self._text_value(body.get("content"), field="content", max_chars=MAX_TEXT_CHARS).encode("utf-8")
        if len(content) > MAX_FILE_BYTES:
            raise AgentAPIError(
                f"content is too large (max {MAX_FILE_BYTES} bytes)",
                code="workspace_file_too_large",
                status=413,
            )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        return {
            "object": "hermes.agent_workspace_file",
            "agent": name,
            "path": str(file_path.relative_to(self._workspace_dir(name))),
            "size_bytes": len(content),
        }

    def delete_workspace_path(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        self._require_profile(name)
        target = self._workspace_path(name, body.get("path"), require_file=False)
        if target == self._workspace_dir(name):
            raise AgentAPIError("cannot delete workspace root", code="invalid_workspace_path")
        if not target.exists():
            raise AgentAPIError("path not found", code="workspace_path_not_found", status=404)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"object": "hermes.agent_workspace_delete", "agent": name, "deleted": True}

    def read_memory(self, raw_name: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        mem_dir = profile_dir / "memories"
        return {
            "object": "hermes.agent_memory",
            "agent": name,
            "memory": self._read_text(mem_dir / "MEMORY.md"),
            "user": self._read_text(mem_dir / "USER.md"),
        }

    def write_memory(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        mem_dir = profile_dir / "memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        if "memory" in body:
            self._write_text(mem_dir / "MEMORY.md", body["memory"])
        if "user" in body:
            self._write_text(mem_dir / "USER.md", body["user"])
        return self.read_memory(name)

    def _agent_name(self, value: Any) -> str:
        name = str(value or "").strip()
        if not AGENT_NAME_RE.match(name) or ".." in name:
            raise AgentAPIError("agent name is invalid", code="invalid_agent_name")
        return name

    def _new_agent_name(self) -> str:
        for _ in range(64):
            name = secrets.choice(GENERATED_AGENT_ID_FIRST_ALPHABET) + "".join(
                secrets.choice(GENERATED_AGENT_ID_ALPHABET)
                for _ in range(GENERATED_AGENT_ID_LENGTH - 1)
            )
            if GENERATED_AGENT_NAME_RE.fullmatch(name) and self._existing_profile_dir(name) is None:
                return name
        raise AgentAPIError(
            "failed to allocate agent name",
            code="agent_name_unavailable",
            status=409,
        )

    def _skill_id(self, value: Any) -> str:
        skill_id = str(value or "").strip()
        if not SKILL_ID_RE.match(skill_id) or ".." in skill_id:
            raise AgentAPIError("skill_id is invalid", code="invalid_skill_id")
        return skill_id

    def _safe_category(self, value: Any) -> str:
        category = str(value or "").strip().strip("/")
        if not category or any(part in {"", ".", ".."} for part in category.split("/")):
            raise AgentAPIError("category is invalid", code="invalid_skill_category")
        if not all(SKILL_ID_RE.match(part) for part in category.split("/")):
            raise AgentAPIError("category is invalid", code="invalid_skill_category")
        return category

    def _session_id(self, value: Any) -> str:
        session_id = str(value or "").strip()
        if not session_id or len(session_id) > 256 or re.search(r"[\r\n\x00/\\]", session_id):
            raise AgentAPIError("conversation_id is invalid", code="invalid_conversation_id")
        return session_id

    def _agent_dir(self, name: str) -> Path:
        profile_dir = self._existing_profile_dir(name)
        if profile_dir == self._legacy_profile_dir(name):
            return self._legacy_agent_dir(name)
        return self._native_profile_dir(name)

    def _profile_dir(self, name: str) -> Path:
        return self._existing_profile_dir(name) or self._native_profile_dir(name)

    def _workspace_dir(self, name: str) -> Path:
        return self._workspace_dir_for_profile(name, self._profile_dir(name))

    def _require_profile(self, name: str) -> Path:
        profile_dir = self._profile_dir(name)
        if not profile_dir.is_dir():
            raise AgentAPIError(f"Agent not found: {name}", code="agent_not_found", status=404)
        return profile_dir

    def _native_profile_dir(self, name: str) -> Path:
        return self.profiles_root / name

    def _legacy_agent_dir(self, name: str) -> Path:
        return self.legacy_agents_root / name

    def _legacy_profile_dir(self, name: str) -> Path:
        return self._legacy_agent_dir(name) / ".profile"

    def _existing_profile_dir(self, name: str) -> Path | None:
        native = self._native_profile_dir(name)
        if native.is_dir():
            return native
        legacy = self._legacy_profile_dir(name)
        if legacy.is_dir():
            return legacy
        return None

    def _workspace_dir_for_profile(self, name: str, profile_dir: Path) -> Path:
        if profile_dir == self._legacy_profile_dir(name):
            return self._legacy_agent_dir(name) / "workspace"
        return profile_dir / "workspace"

    def _is_native_agent_profile(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        if not AGENT_NAME_RE.match(path.name) or ".." in path.name:
            return False
        return (path / METADATA_FILE).is_file() or (path / "workspace").is_dir()

    def _workspace_path(self, name: str, raw_path: Any, *, require_file: bool) -> Path:
        raw = str(raw_path or "").strip()
        if not raw:
            raise AgentAPIError("path is required", code="invalid_workspace_path")
        if raw.startswith("/"):
            raise AgentAPIError("path must be relative", code="invalid_workspace_path")
        root = self._workspace_dir(name).resolve()
        path = (root / raw).resolve()
        if path != root and root not in path.parents:
            raise AgentAPIError("path must not escape the agent workspace", code="invalid_workspace_path")
        if require_file and path == root:
            raise AgentAPIError("path must refer to a file", code="invalid_workspace_path")
        return path

    def _copy_seed_profile(self, profile_dir: Path, *, copy_credentials: bool) -> None:
        for filename in SEED_FILES:
            if not copy_credentials and filename in CREDENTIAL_FILES:
                continue
            src = self.root_profile / filename
            if src.is_file():
                shutil.copy2(src, profile_dir / filename)
        for dirname in SEED_DIRS:
            src = self.root_profile / dirname
            dst = profile_dir / dirname
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)

    def _copy_root_skills(self, profile_dir: Path, *, overwrite: bool) -> list[str]:
        skills_root = self.root_profile / "skills"
        target_root = profile_dir / "skills"
        target_root.mkdir(parents=True, exist_ok=True)
        copied = []
        if not skills_root.is_dir():
            return copied
        for skill_file in sorted(skills_root.rglob("SKILL.md")):
            source = skill_file.parent
            rel_parent = source.relative_to(skills_root)
            destination = target_root / rel_parent
            if destination.exists():
                if not overwrite:
                    continue
                shutil.rmtree(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
            copied.append(str(rel_parent))
        return copied

    def _write_workspace_cwd(self, profile_dir: Path, workspace_dir: Path) -> None:
        config = self._read_config(profile_dir)
        self._set_nested(config, ("terminal", "backend"), self._get_nested(config, ("terminal", "backend"), "local"))
        self._set_nested(config, ("terminal", "cwd"), str(workspace_dir))
        self._normalize_agent_skill_config(config)
        normalize_nine_router_config(config)
        with (profile_dir / "config.yaml").open("w", encoding="utf-8") as file:
            yaml.safe_dump(config, file, sort_keys=False, allow_unicode=False)

    def _initialize_state_db(self, profile_dir: Path) -> None:
        conn = sqlite3.connect(profile_dir / "state.db", timeout=1.0)
        try:
            self._ensure_session_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def _write_profile_manifest(
        self,
        profile_dir: Path,
        metadata: Mapping[str, Any],
    ) -> None:
        path = profile_dir / "profile.yaml"
        if path.is_file():
            return
        payload = {
            "name": str(metadata.get("profile_name") or metadata.get("name") or profile_dir.name),
            "description": str(metadata.get("description") or "Office and knowledge-work assistant"),
        }
        with path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(payload, file, sort_keys=False, allow_unicode=False)

    def _ensure_workspace_agents(self, profile_dir: Path, workspace_dir: Path) -> None:
        target = workspace_dir / "AGENTS.md"
        if target.is_file():
            return
        for source in (profile_dir / "AGENTS.md", self.root_profile / "AGENTS.md"):
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                return

    def _normalize_agent_skill_config(self, config: dict[str, Any]) -> None:
        shared_dir = str(self.root_profile / "skills")
        skills_config = config.get("skills")
        if not isinstance(skills_config, dict):
            skills_config = {}
            config["skills"] = skills_config
        external_dirs = skills_config.get("external_dirs")
        if external_dirs is None:
            external_dirs = []
        elif isinstance(external_dirs, str):
            external_dirs = [external_dirs]
        elif not isinstance(external_dirs, list):
            external_dirs = []
        external_dirs = [str(item) for item in external_dirs if str(item) != shared_dir]
        if external_dirs:
            skills_config["external_dirs"] = external_dirs
        else:
            skills_config.pop("external_dirs", None)

    def _find_agent_skill(self, profile_dir: Path, skill_id: str) -> Path | None:
        skills_root = profile_dir / "skills"
        direct = skills_root / skill_id
        if (direct / "SKILL.md").is_file():
            return direct
        if not skills_root.is_dir():
            return None
        for skill_file in skills_root.rglob("SKILL.md"):
            frontmatter = self._read_skill_frontmatter(skill_file)
            candidates = {
                skill_file.parent.name,
                str(frontmatter.get("name") or "").strip(),
                str(skill_file.parent.relative_to(skills_root)).strip(),
            }
            if skill_id in candidates:
                return skill_file.parent
        return None

    def _append_skills_from_dir(
        self,
        skills: list[dict[str, Any]],
        seen: set[str],
        root: Path,
        disabled: set[str],
    ) -> None:
        if not root.is_dir():
            return
        for skill_file in sorted(root.rglob("SKILL.md")):
            rel_parent = skill_file.parent.relative_to(root)
            skill_id = skill_file.parent.name
            frontmatter = self._read_skill_frontmatter(skill_file)
            name_value = str(frontmatter.get("name") or skill_id).strip()
            if name_value:
                skill_id = name_value
            if skill_id in seen:
                continue
            seen.add(skill_id)
            skills.append(
                {
                    "skill_id": skill_id,
                    "name": skill_id,
                    "path": str(rel_parent),
                    "description": str(frontmatter.get("description") or ""),
                    "category": str(frontmatter.get("category") or ""),
                    "installed": True,
                    "enabled": skill_id not in disabled,
                }
            )

    def _disabled_skills(self, config: Mapping[str, Any]) -> set[str]:
        raw = self._get_nested(config, ("skills", "disabled"), []) or []
        if isinstance(raw, str):
            raw = [item.strip() for item in raw.split(",") if item.strip()]
        if not isinstance(raw, list):
            return set()
        disabled = set()
        for item in raw:
            try:
                disabled.add(self._skill_id(item))
            except AgentAPIError:
                continue
        return disabled

    def _set_skill_enabled(self, profile_dir: Path, skill_id: str, enabled: bool) -> None:
        config = self._read_config(profile_dir)
        disabled = self._disabled_skills(config)
        if enabled:
            disabled.discard(skill_id)
        else:
            disabled.add(skill_id)
        self._write_disabled_skills(profile_dir, config, disabled)

    def _write_disabled_skills(
        self,
        profile_dir: Path,
        config: dict[str, Any],
        disabled: set[str],
    ) -> None:
        self._normalize_agent_skill_config(config)
        skills_config = config.get("skills")
        if not isinstance(skills_config, dict):
            skills_config = {}
            config["skills"] = skills_config
        skills_config["disabled"] = sorted(disabled)
        with (profile_dir / "config.yaml").open("w", encoding="utf-8") as file:
            yaml.safe_dump(config, file, sort_keys=False, allow_unicode=False)

    def _read_metadata(self, profile_dir: Path) -> dict[str, Any]:
        path = profile_dir / METADATA_FILE
        if not path.is_file():
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_metadata(self, profile_dir: Path, metadata: Mapping[str, Any]) -> None:
        profile_dir.mkdir(parents=True, exist_ok=True)
        with (profile_dir / METADATA_FILE).open("w", encoding="utf-8") as file:
            json.dump(dict(metadata), file, indent=2, sort_keys=True)
            file.write("\n")

    def _read_config(self, profile_dir: Path) -> dict[str, Any]:
        path = profile_dir / "config.yaml"
        if not path.is_file():
            return {}
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        return data if isinstance(data, dict) else {}

    def _ensure_router_profile(self, profile_dir: Path, model: Any = None) -> None:
        config = self._read_config(profile_dir)
        selected_model = None
        if model is not None:
            selected_model = self._nonempty_string(model, "model")
        normalize_nine_router_config(config, selected_model)
        with (profile_dir / "config.yaml").open("w", encoding="utf-8") as file:
            yaml.safe_dump(config, file, sort_keys=False, allow_unicode=False)

    def _effective_config(self, config: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
        effort = str(self._get_nested(config, ("agent", "reasoning_effort"), "medium") or "medium").lower()
        approval = self._get_nested(config, ("approvals", "mode"), "manual")
        return {
            "provider": "nine-router",
            "model": self._get_nested(config, ("model", "default"), NINE_ROUTER_DEFAULT_MODEL),
            "reasoning": effort != "none",
            "effort": effort,
            "approval_mode": "off" if approval is False or str(approval).lower() == "off" else "on",
            "system_prompt": self._get_nested(config, ("agent", "system_prompt"), ""),
        }

    def _conversation_title(self, body: Mapping[str, Any]) -> str | None:
        if "name" in body:
            return self._nullable_text(body["name"], field="name", max_chars=256)
        if "title" in body:
            return self._nullable_text(body["title"], field="title", max_chars=256)
        return None

    def _conversation_model(self, profile_dir: Path, body: Mapping[str, Any]) -> str:
        if body.get("model"):
            return self._nonempty_string(body["model"], "model")
        config = self._read_config(profile_dir)
        model = self._get_nested(config, ("model", "default"), None)
        if not model:
            model = self._get_nested(config, ("model", "model"), "")
        return str(model or "")

    def _session_db(self, profile_dir: Path):
        from hermes_state import SessionDB

        return SessionDB(db_path=profile_dir / "state.db")

    def _create_session(
        self,
        profile_dir: Path,
        session_id: str,
        *,
        model: str,
        title: str | None,
    ) -> None:
        try:
            db = self._session_db(profile_dir)
            db.create_session(
                session_id=session_id,
                source="api",
                model=model,
                user_id=None,
                parent_session_id=None,
            )
            if title is not None:
                db.set_session_title(session_id, title)
            return
        except Exception as exc:
            if "UNIQUE" in str(exc).upper() or "already" in str(exc).lower():
                raise AgentAPIError("conversation already exists", code="conversation_exists", status=409) from exc
            self._create_session_sqlite(profile_dir, session_id, model=model, title=title)

    def _create_session_sqlite(
        self,
        profile_dir: Path,
        session_id: str,
        *,
        model: str,
        title: str | None,
    ) -> None:
        db_path = profile_dir / "state.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=1.0)
        try:
            self._ensure_session_schema(conn)
            now = time.time()
            conn.execute(
                "INSERT INTO sessions (id, source, model, title, started_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, "api", model, title, now),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AgentAPIError("conversation already exists", code="conversation_exists", status=409) from exc
        finally:
            conn.close()

    def _update_session_title(self, profile_dir: Path, session_id: str, title: str) -> None:
        try:
            db = self._session_db(profile_dir)
            db.set_session_title(session_id, title)
            return
        except Exception as exc:
            if "UNIQUE" in str(exc).upper() or "already" in str(exc).lower():
                raise AgentAPIError("conversation name already exists", code="conversation_name_exists", status=409) from exc
            self._update_session_title_sqlite(profile_dir, session_id, title)

    def _update_session_title_sqlite(self, profile_dir: Path, session_id: str, title: str) -> None:
        conn = sqlite3.connect(profile_dir / "state.db", timeout=1.0)
        try:
            cursor = conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
            if cursor.rowcount == 0:
                raise AgentAPIError("conversation not found", code="conversation_not_found", status=404)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AgentAPIError("conversation name already exists", code="conversation_name_exists", status=409) from exc
        finally:
            conn.close()

    def _delete_session(self, profile_dir: Path, session_id: str) -> None:
        try:
            db = self._session_db(profile_dir)
            db.delete_session(session_id)
            return
        except Exception:
            self._delete_session_sqlite(profile_dir, session_id)

    def _delete_session_sqlite(self, profile_dir: Path, session_id: str) -> None:
        conn = sqlite3.connect(profile_dir / "state.db", timeout=1.0)
        try:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            if cursor.rowcount == 0:
                raise AgentAPIError("conversation not found", code="conversation_not_found", status=404)
            conn.commit()
        finally:
            conn.close()

    def _ensure_session_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                user_id TEXT,
                model TEXT,
                model_config TEXT,
                system_prompt TEXT,
                parent_session_id TEXT,
                started_at REAL NOT NULL,
                ended_at REAL,
                end_reason TEXT,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                reasoning_tokens INTEGER DEFAULT 0,
                billing_provider TEXT,
                billing_base_url TEXT,
                billing_mode TEXT,
                estimated_cost_usd REAL,
                actual_cost_usd REAL,
                cost_status TEXT,
                cost_source TEXT,
                pricing_version TEXT,
                title TEXT,
                api_call_count INTEGER DEFAULT 0,
                FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                timestamp REAL NOT NULL,
                token_count INTEGER,
                finish_reason TEXT,
                reasoning TEXT,
                reasoning_content TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_title_unique ON sessions(title) WHERE title IS NOT NULL")

    def _read_skill_frontmatter(self, path: Path) -> dict[str, Any]:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return {}
        if not text.startswith("---"):
            return {}
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}
        try:
            data = yaml.safe_load(parts[1]) or {}
            if isinstance(data, dict):
                metadata = data.get("metadata") or {}
                hermes = metadata.get("hermes") if isinstance(metadata, dict) else {}
                if isinstance(hermes, dict) and "category" in hermes and "category" not in data:
                    data["category"] = hermes["category"]
                return data
        except Exception:
            return {}
        return {}

    async def _run_profile_command(
        self,
        name: str,
        command: list[str],
        *,
        engine: str = "hermes",
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return await self._run_hermes_command(
            self._profile_dir(name),
            self._workspace_dir(name),
            command,
            engine=engine,
            timeout_seconds=timeout_seconds,
        )

    async def _run_hermes_command(
        self,
        hermes_home: Path,
        cwd: Path,
        command: list[str],
        *,
        engine: str = "hermes",
        timeout_seconds: int,
    ) -> dict[str, Any]:
        env = self._command_env(hermes_home, engine)
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise AgentAPIError(
                "agent command timed out",
                code="agent_command_timeout",
                status=504,
            )
        masked_command = list(command)
        if len(masked_command) >= 2 and masked_command[-2] == "-z":
            masked_command[-1] = "<message>"
        return {
            "command": masked_command,
            "exit_code": int(proc.returncode or 0),
            "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
        }

    def _command_env(self, hermes_home: Path, engine: str) -> dict[str, str]:
        env = os.environ.copy()
        env["HERMES_HOME"] = str(hermes_home)
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("HERMES_ACCEPT_HOOKS", "1")
        self._load_agent_credentials(env)
        return env

    def _hermes_binary(self) -> str:
        return os.environ.get("HERMES_CLI", "hermes")

    def _agent_config_dir(self) -> Path:
        configured = os.environ.get("AGENT_CONFIG_DIR")
        if configured:
            return Path(configured)
        return Path(os.environ.get("HOME") or str(Path.home())) / ".config" / "sandbox-agent"

    def _agent_env_file(self) -> Path:
        configured = os.environ.get("AGENT_ENV_FILE")
        if configured:
            return Path(configured)
        return self._agent_config_dir() / "credentials.env"

    def _load_agent_credentials(self, env: dict[str, str]) -> None:
        env_file = self._agent_env_file()
        try:
            lines = env_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        allowed = set(AGENT_CREDENTIAL_ENV_KEYS)
        for line in lines:
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in allowed and value:
                env[key] = value

    def _conversation_provider(self, profile_dir: Path, body: Mapping[str, Any]) -> str:
        return NINE_ROUTER_PROVIDER

    def _latest_session_ids(self, profile_dir: Path) -> dict[str, float]:
        return {
            item["id"]: self._session_sort_value(item)
            for item in self._sessions(profile_dir, limit=500)
            if item.get("id")
        }

    def _detect_changed_session(self, before: Mapping[str, float], after: Mapping[str, float]) -> str | None:
        new_ids = [session_id for session_id in after if session_id not in before]
        if new_ids:
            return max(new_ids, key=lambda session_id: after[session_id])
        changed = [
            session_id
            for session_id, started_at in after.items()
            if before.get(session_id) != started_at
        ]
        if changed:
            return max(changed, key=lambda session_id: after[session_id])
        return max(after, key=lambda session_id: after[session_id]) if after else None

    def _sessions(self, profile_dir: Path, *, limit: int) -> list[dict[str, Any]]:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return []
        conn = self._open_readonly_db(db_path)
        try:
            order_by = self._preferred_order_column(conn, "sessions")
            sql = "SELECT * FROM sessions"
            if order_by:
                sql += f" ORDER BY {order_by} DESC"
            sql += " LIMIT ?"
            rows = conn.execute(sql, (limit,)).fetchall()
            return [self._row_dict(row) for row in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def _session(self, profile_dir: Path, session_id: str) -> dict[str, Any] | None:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return None
        conn = self._open_readonly_db(db_path)
        try:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return self._row_dict(row) if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def _messages(self, profile_dir: Path, session_id: str) -> list[dict[str, Any]]:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return []
        conn = self._open_readonly_db(db_path)
        try:
            order_by = self._preferred_order_column(conn, "messages", descending=False)
            sql = "SELECT * FROM messages WHERE session_id = ?"
            if order_by:
                sql += f" ORDER BY {order_by} ASC"
            rows = conn.execute(sql, (session_id,)).fetchall()
            return [self._row_dict(row) for row in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def _open_readonly_db(self, path: Path) -> sqlite3.Connection:
        uri = f"file:{path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _preferred_order_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        *,
        descending: bool = True,
    ) -> str | None:
        try:
            columns = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
        except sqlite3.Error:
            return None
        candidates = (
            ("started_at", "updated_at", "created_at", "timestamp", "id")
            if descending
            else ("timestamp", "created_at", "id")
        )
        for column in candidates:
            if column in columns:
                return column
        return None

    def _row_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = {}
        for key in row.keys():
            value = row[key]
            if key.endswith("_config") and isinstance(value, str) and value:
                try:
                    result[key] = json.loads(value)
                    continue
                except Exception:
                    pass
            result[key] = value
        return result

    def _session_sort_value(self, item: Mapping[str, Any]) -> float:
        for field in ("started_at", "updated_at", "created_at", "timestamp"):
            value = item.get(field)
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _normalize_skill_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        if not isinstance(value, list):
            raise AgentAPIError("skills must be an array", code="invalid_skills")
        result = []
        seen = set()
        for item in value:
            skill_id = self._skill_id(item)
            if skill_id not in seen:
                seen.add(skill_id)
                result.append(skill_id)
        return result

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def _write_text(self, path: Path, value: Any) -> None:
        text = self._text_value(value, field=path.name, max_chars=MAX_TEXT_CHARS)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _text_value(self, value: Any, *, field: str, max_chars: int) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise AgentAPIError(f"{field} must be a string", code="invalid_text")
        if "\x00" in value:
            raise AgentAPIError(f"{field} must not contain NUL bytes", code="invalid_text")
        if len(value) > max_chars:
            raise AgentAPIError(f"{field} is too long", code="invalid_text", status=413)
        return value

    def _nonempty_string(self, value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AgentAPIError(f"{field} must be a non-empty string", code="invalid_text")
        result = value.strip()
        if len(result) > 256:
            raise AgentAPIError(f"{field} is too long", code="invalid_text")
        return result

    def _coerce_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise AgentAPIError("reasoning must be boolean", code="invalid_agent_config")

    def _nullable_text(self, value: Any, *, field: str, max_chars: int) -> str | None:
        if value is None:
            return None
        text = self._text_value(value, field=field, max_chars=max_chars).strip()
        return text or None

    def _approval_mode(self, value: Any) -> str:
        if isinstance(value, bool):
            return "on" if value else "off"
        mode = str(value or "").strip().lower()
        if mode in {"on", "manual", "true", "1", "yes"}:
            return "on"
        if mode in {"off", "false", "0", "no"}:
            return "off"
        raise AgentAPIError("approval_mode must be on or off", code="invalid_agent_config")

    def _get_nested(self, data: Mapping[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
        current: Any = data
        for key in keys:
            if not isinstance(current, Mapping) or key not in current:
                return default
            current = current[key]
        return current

    def _set_nested(self, data: dict[str, Any], keys: tuple[str, ...], value: Any) -> None:
        current = data
        for key in keys[:-1]:
            child = current.get(key)
            if not isinstance(child, dict):
                child = {}
                current[key] = child
            current = child
        current[keys[-1]] = value

    def _deep_merge(self, target: dict[str, Any], update: Mapping[str, Any]) -> None:
        for key, value in update.items():
            if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                self._deep_merge(target[key], value)
                continue
            target[key] = value

"""System-prompt policy for XNOBrain conversations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

MARKDOWN_RESPONSE_GUIDANCE = """# Response format
Write every user-facing final response as valid GitHub-Flavored Markdown.
Use headings, lists, tables, links, and fenced code blocks when they improve
clarity. Do not wrap the entire response in a code fence and do not use raw HTML."""

AGENT_WORKSPACE_GUIDANCE = """# Agent workspace
Your default and only project workspace is `{workspace}`.
Create and modify user-requested files only inside this directory. Prefer paths
relative to it, and do not change the working directory to another location.
The sole Python-environment exception is the current profile's exact uv venv
specified by the working overlay; user deliverables must still stay here.
This grants no arbitrary /opt writes or access to other profiles' environments.
Use dedicated Hermes tools for agent configuration, skills, memory, and schedules
instead of editing profile state directly."""


class ConversationPromptMixin:
    @staticmethod
    def _upstream_runtime_help_guidance() -> str:
        try:
            from agent.prompt_builder import HERMES_AGENT_HELP_GUIDANCE
        except (ImportError, AttributeError):
            return ""
        return str(HERMES_AGENT_HELP_GUIDANCE or "").strip()

    def _format_runtime_prompt(
        self,
        prompt: Any,
        *,
        include_response_guidance: bool = True,
        workspace_dir: Path | None = None,
    ) -> str:
        rendered = str(prompt or "")
        upstream = self._upstream_runtime_help_guidance()
        if upstream and upstream in rendered:
            rendered = rendered.replace(upstream, "")
        rendered = rendered.strip()
        if include_response_guidance and MARKDOWN_RESPONSE_GUIDANCE not in rendered:
            rendered = (
                f"{rendered}\n\n{MARKDOWN_RESPONSE_GUIDANCE}"
                if rendered
                else MARKDOWN_RESPONSE_GUIDANCE
            )
        if workspace_dir is not None:
            guidance = AGENT_WORKSPACE_GUIDANCE.format(workspace=str(workspace_dir))
            if guidance not in rendered:
                rendered = f"{rendered}\n\n{guidance}" if rendered else guidance
        return rendered

    def _workspace_context(self, profile_dir: Path, workspace_dir: Path | None) -> str:
        """Bypass first-match context discovery without changing the upstream loader."""
        workspace = workspace_dir or profile_dir / "workspace"
        sections = []
        for filename in ("AGENTS.md", "HERMES.md"):
            path = workspace / filename
            # Context must be a regular workspace file, not a symlink to secrets.
            if path.is_file() and not path.is_symlink():
                try:
                    text = path.read_text(encoding="utf-8").strip()
                except (OSError, UnicodeError):
                    continue
                if text:
                    try:
                        from agent.prompt_builder import _scan_context_content, _truncate_content
                    except ImportError:
                        # Without the embedded scanner, do not bypass its safety policy.
                        text = "[Workspace context unavailable: scanner not installed.]"
                    else:
                        text = _scan_context_content(text, filename)
                        text = _truncate_content(text, filename, read_path=str(path))
                    sections.append(f"# Workspace {filename}\n{text}")
        profile_id = (
            "big-brother"
            if profile_dir == self.root_profile
            else (profile_dir.parent.name if profile_dir.name == ".profile" else profile_dir.name)
        )
        sections.append(f"Current profile uv venv: `/opt/data/python/.{profile_id}-venv`.")
        return (
            "<!-- runtime-workspace-context -->\n"
            + "\n\n".join(sections)
            + "\n<!-- /runtime-workspace-context -->"
        )

    @staticmethod
    def _with_workspace_context(prompt: Any, context: str) -> str:
        rendered = str(prompt or "")
        start = rendered.find("<!-- runtime-workspace-context -->")
        end = rendered.find("<!-- /runtime-workspace-context -->", start)
        if start >= 0 and end >= 0:
            rendered = (
                rendered[:start] + rendered[end + len("<!-- /runtime-workspace-context -->") :]
            )
        return f"{rendered.rstrip()}\n\n{context}"

    def _apply_runtime_help_guidance_override(
        self,
        agent: Any,
        profile_dir: Path,
        workspace_dir: Path | None = None,
    ) -> None:
        """Apply product-neutral identity and Markdown response policy."""
        context = self._workspace_context(profile_dir, workspace_dir)
        if profile_dir == self.root_profile:
            workspace_dir = None
        original_build = getattr(agent, "_build_system_prompt", None)
        if callable(original_build):

            def build_system_prompt(system_message: Any = None) -> str:
                return self._format_runtime_prompt(
                    self._with_workspace_context(original_build(system_message), context),
                    workspace_dir=workspace_dir,
                )

            agent._build_system_prompt = build_system_prompt

        cached = getattr(agent, "_cached_system_prompt", None)
        if isinstance(cached, str) and cached:
            agent._cached_system_prompt = self._format_runtime_prompt(
                self._with_workspace_context(cached, context),
                workspace_dir=workspace_dir,
            )

        # This value must remain a byte-for-byte prefix of the full prompt.
        # Remove upstream identity text, but keep trailing response guidance out.
        cached_static = getattr(agent, "_cached_system_prompt_static", None)
        if isinstance(cached_static, str) and cached_static:
            agent._cached_system_prompt_static = self._format_runtime_prompt(
                cached_static,
                include_response_guidance=False,
            )

    def _override_stored_runtime_help_guidance(
        self,
        profile_dir: Path,
        session_id: str,
        workspace_dir: Path | None = None,
    ) -> None:
        """Migrate a resumed session to product-neutral Markdown guidance."""
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return
        conn = sqlite3.connect(db_path, timeout=1.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT system_prompt FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            stored = str(row["system_prompt"] or "") if row else ""
            original_stored = stored
            if profile_dir == self.root_profile:
                workspace_dir = None
            context = self._workspace_context(profile_dir, workspace_dir)
            stored = self._with_workspace_context(stored, context)
            replacement = self._format_runtime_prompt(
                stored,
                workspace_dir=workspace_dir,
            )
            if row and replacement != original_stored:
                conn.execute(
                    "UPDATE sessions SET system_prompt = ? WHERE id = ?",
                    (replacement, session_id),
                )
                conn.commit()
        except sqlite3.Error:
            pass
        finally:
            conn.close()

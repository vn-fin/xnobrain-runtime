"""Atomic Zalo channel state, profile settings, and discovery for Runtime chats."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Mapping

import yaml

from .base import StoreError

ZALO_DIR = "zalo"
LINKS_FILE = "links.json"
THREADS_FILE = "threads.json"
GROUPS_FILE = "groups.json"
GROUP_USAGE_FILE = "group_usage.json"
HISTORY_FILE = "history.jsonl"
DIRECTORY_FILE = "channel_directory.json"
SESSION_FILE = "session.json"
QR_FILE = "qr.png"
GATEWAY_PID_FILE = "gateway.pid"
GATEWAY_STATE_FILE = "gateway_state.json"
BOT_STATE_FILE = "zalo_bot_state.json"
BOT_DIR = "zalo_bot"
BOT_HISTORY_DIR = "history"
BOT_WARNINGS_DIR = "warnings"
BOT_CRM_DIR = "zalo-bot-crm"
BOT_SETTINGS_DIR = "zalo-bot-settings"
BOT_WARN_MAX = 3
BOT_WARN_EXPIRY_SECONDS = 604800
RESTART_STATE_FILE = "gateway-restart.json"
OWNER_FILE = "owner.json"
SOCIAL_TOOLS_FILE = "social_tools.json"
OWNER_METADATA_DIR = ".xnobrain"
OWNER_CHANNEL_DIR = "channels"
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_QR_BYTES = 4 * 1024 * 1024
MAX_HISTORY_LINES = 5000
MAX_BOT_HISTORY_BYTES = 5 * 1024 * 1024
MAX_BOT_HISTORY_TEXT = 4000
MAX_BOT_CRM_BYTES = 1024 * 1024
MAX_BOT_CRM_CONTACTS = 1000
DEFAULT_GROUP_DAILY_LIMIT = 20
MAX_GROUP_DAILY_LIMIT = 1000
MAX_PENDING_GROUP_REQUESTS = 20
MAX_GROUP_COUNTED_MESSAGE_IDS = 2000
_GROUP_STATUSES = {"pending", "approved"}
_BOT_CHAT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OWNER_VALUE = re.compile(r"^[^\x00-\x1f/\\]{1,200}$")


def _group_timestamp(value: Any) -> float:
    """Return one non-negative persisted epoch timestamp, or ``0``."""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return 0.0
    if timestamp < 0 or timestamp != timestamp or timestamp == float("inf"):
        return 0.0
    return timestamp


def _group_label(value: Any) -> str:
    """Collapse an untrusted display name to one bounded single line."""
    return " ".join(str(value or "").split())[:100]


def _group_daily_limit(value: Any) -> int:
    if isinstance(value, bool):
        return DEFAULT_GROUP_DAILY_LIMIT
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULT_GROUP_DAILY_LIMIT
    if not 0 <= limit <= MAX_GROUP_DAILY_LIMIT:
        return DEFAULT_GROUP_DAILY_LIMIT
    return limit


class ChannelsRepository:
    """Read bridge state and persist profile channel settings atomically."""

    def __init__(self, base):
        self.base = base

    @staticmethod
    def _profile_root(profile: Any) -> Path:
        candidate = Path(profile)
        if candidate.is_symlink():
            raise StoreError("agent profile not found", status=404, code="not_found")
        root = candidate.resolve()
        if not root.is_dir():
            raise StoreError("agent profile not found", status=404, code="not_found")
        return root

    @classmethod
    def _zalo_dir(cls, profile: Any, *, create: bool = False) -> tuple:
        root = cls._profile_root(profile)
        directory = root / ZALO_DIR
        if directory.is_symlink():
            raise StoreError(
                "agent channel directory is unsafe", status=400, code="channel_path_unsafe"
            )
        if create:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not directory.is_dir():
            return root, None
        resolved = directory.resolve()
        if resolved.parent != root:
            raise StoreError(
                "agent channel directory escapes the profile",
                status=400,
                code="channel_path_unsafe",
            )
        return root, resolved

    @staticmethod
    def _read_text(path: Path) -> str:
        if path.is_symlink() or not path.is_file():
            raise StoreError(
                "agent channel state is unsafe", status=400, code="channel_path_unsafe"
            )
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise StoreError(
                "agent channel state is unavailable", status=500, code="channel_state_unavailable"
            ) from error
        if len(payload) > MAX_STATE_BYTES:
            payload = payload[-MAX_STATE_BYTES:]
        return payload.decode("utf-8", errors="replace")

    @classmethod
    def _config_path(cls, profile: Any) -> Path:
        root = cls._profile_root(profile)
        path = root / "config.yaml"
        if path.is_symlink():
            raise StoreError(
                "agent channel config is unsafe", status=400, code="channel_path_unsafe"
            )
        return path

    def config(self, profile: Any) -> dict[str, Any]:
        root = self._profile_root(profile)
        path = root / "config.yaml"
        if not path.exists():
            return {}
        try:
            value = yaml.safe_load(self._read_text(path))
        except (ValueError, TypeError) as error:
            raise StoreError(
                "agent channel config is invalid",
                status=500,
                code="channel_config_invalid",
            ) from error
        return value if isinstance(value, dict) else {}

    def write_config(self, profile: Any, value: Mapping[str, Any]) -> None:
        path = self._config_path(profile)
        self.base.atomic_yaml(path, dict(value))

    @classmethod
    def _env_path(cls, profile: Any) -> Path:
        root = cls._profile_root(profile)
        path = root / ".env"
        if path.is_symlink():
            raise StoreError(
                "agent channel environment is unsafe", status=400, code="channel_path_unsafe"
            )
        return path

    @staticmethod
    def _parse_env(text: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in text.splitlines():
            match = _ENV_LINE.match(line)
            if not match:
                continue
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[match.group(1)] = value
        return values

    def env(self, profile: Any) -> dict[str, str]:
        path = self._env_path(profile)
        if not path.exists():
            return {}
        return self._parse_env(self._read_text(path))

    def update_env(self, profile: Any, updates: Mapping[str, str | None]) -> None:
        """Replace or remove managed keys while preserving unrelated lines."""
        if not updates:
            return
        for key, value in updates.items():
            if not _ENV_KEY.fullmatch(str(key)):
                raise StoreError("invalid channel environment key")
            if value is not None and any(char in str(value) for char in "\r\n"):
                raise StoreError("invalid channel environment value")
        path = self._env_path(profile)
        existing = self._read_text(path) if path.exists() else ""
        applied: set[str] = set()
        lines: list[str] = []
        for line in existing.splitlines():
            match = _ENV_LINE.match(line)
            key = match.group(1) if match else ""
            if key not in updates:
                lines.append(line)
                continue
            if key in applied:
                continue
            applied.add(key)
            value = updates[key]
            if value is not None:
                lines.append(f"{key}={value}")
        for key, value in updates.items():
            if key not in applied and value is not None:
                lines.append(f"{key}={value}")
        payload = "\n".join(lines)
        if payload:
            payload += "\n"
        self.base.atomic_write(path, payload.encode("utf-8"), mode=0o600)

    def qr_image(self, profile: Any) -> bytes | None:
        _, directory = self._zalo_dir(profile)
        if directory is None:
            return None
        path = directory / QR_FILE
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise StoreError("agent channel QR is unsafe", status=400, code="channel_path_unsafe")
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise StoreError(
                "agent channel QR is unavailable",
                status=500,
                code="channel_state_unavailable",
            ) from error
        if len(payload) > MAX_QR_BYTES:
            raise StoreError(
                "agent channel QR is too large",
                status=500,
                code="channel_state_unavailable",
            )
        return payload

    def delete_session(self, profile: Any) -> bool:
        _, directory = self._zalo_dir(profile)
        if directory is None:
            return False
        path = directory / SESSION_FILE
        if path.is_symlink():
            raise StoreError(
                "agent channel session is unsafe", status=400, code="channel_path_unsafe"
            )
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError as error:
            raise StoreError(
                "agent channel session is unavailable",
                status=500,
                code="channel_state_unavailable",
            ) from error
        self.base._sync_dir(path.parent)
        return True

    def delete_session_directory(self, profile: Any, directory: Any) -> bool:
        """Delete one session directory strictly beneath the profile root."""
        root = self._profile_root(profile)
        target = Path(directory)
        if target.is_symlink():
            raise StoreError(
                "agent channel session is unsafe", status=400, code="channel_path_unsafe"
            )
        try:
            resolved = target.resolve(strict=False)
        except OSError as error:
            raise StoreError(
                "agent channel session is unavailable",
                status=500,
                code="channel_state_unavailable",
            ) from error
        if resolved == root or root not in resolved.parents:
            raise StoreError(
                "agent channel session escapes the profile",
                status=400,
                code="channel_path_unsafe",
            )
        if not resolved.exists():
            return False
        if not resolved.is_dir():
            raise StoreError(
                "agent channel session is unsafe", status=400, code="channel_path_unsafe"
            )
        try:
            shutil.rmtree(resolved)
        except OSError as error:
            raise StoreError(
                "agent channel session is unavailable",
                status=500,
                code="channel_state_unavailable",
            ) from error
        self.base._sync_dir(resolved.parent)
        return True

    @staticmethod
    def gateway_pid(root: Any) -> int | None:
        """Read ``<root HERMES_HOME>/gateway.pid`` (JSON record or plain int)."""
        path = Path(root) / GATEWAY_PID_FILE
        if path.is_symlink() or not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not text:
            return None
        value: Any = text
        if text.startswith("{"):
            try:
                record = json.loads(text)
            except (ValueError, TypeError):
                return None
            value = record.get("pid") if isinstance(record, dict) else None
        try:
            pid = int(value)
        except (TypeError, ValueError):
            return None
        return pid if 1 < pid < 2**31 else None

    @staticmethod
    def _process_start_time(pid: int) -> int | None:
        """Return field 22 of ``/proc/<pid>/stat``, or None when unreadable."""
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            fields = stat[stat.rindex(")") + 1 :].split()
            return int(fields[19])
        except (OSError, ValueError, IndexError):
            return None

    @classmethod
    def gateway_state_pid(cls, root: Any) -> int | None:
        """Read a gateway PID from ``gateway_state.json`` as a conservative fallback.

        ``gateway.pid`` is authoritative, but a service manager can leave a live
        gateway with a fresh status record and no PID file. Accept the record
        only when it still names this gateway, is not stopped or failed, and,
        when available, its live ``start_time`` still matches.
        """
        path = Path(root) / GATEWAY_STATE_FILE
        if path.is_symlink() or not path.is_file():
            return None
        try:
            value = json.loads(cls._read_text(path))
        except (ValueError, TypeError, StoreError):
            return None
        if not isinstance(value, dict):
            return None
        if value.get("kind") != "hermes-gateway":
            return None
        if value.get("gateway_state") in (None, "stopped", "startup_failed"):
            return None
        try:
            pid = int(value.get("pid"))
        except (TypeError, ValueError):
            return None
        if not 1 < pid < 2**31:
            return None
        recorded_start = value.get("start_time")
        if recorded_start is not None:
            current_start = cls._process_start_time(pid)
            if current_start is not None and current_start != recorded_start:
                return None
        return pid

    def gateway_platform_state(self, root: Any, platform: str) -> str | None:
        """Read the last persisted per-platform state from ``gateway_state.json``."""
        path = Path(root) / GATEWAY_STATE_FILE
        if path.is_symlink() or not path.is_file():
            return None
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError, StoreError):
            return None
        platforms = value.get("platforms") if isinstance(value, dict) else None
        record = platforms.get(platform) if isinstance(platforms, dict) else None
        state = record.get("state") if isinstance(record, dict) else None
        text = str(state or "").strip().lower()
        return text or None

    def zalo_bot_state(self, profile: Any) -> dict[str, Any] | None:
        """Read the plugin-written per-profile Zalo Bot runtime state.

        The bundled plugin writes ``<profile>/zalo_bot_state.json`` atomically
        for the profile that owns the bot. Reject symlinks and non-files, and
        tolerate missing or malformed records so status stays conservative.
        """
        try:
            root = self._profile_root(profile)
        except StoreError:
            return None
        path = root / BOT_STATE_FILE
        if path.is_symlink() or not path.is_file():
            return None
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError, StoreError):
            return None
        if not isinstance(value, dict):
            return None
        state = str(value.get("state") or "").strip().lower()
        if state not in {"connected", "connecting", "disconnected"}:
            return None
        updated_at = value.get("updated_at")
        if not isinstance(updated_at, (int, float)) or isinstance(updated_at, bool):
            return None
        pid = value.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int):
            pid = None
        return {"state": state, "updated_at": float(updated_at), "pid": pid}

    @classmethod
    def _bot_history_dir(cls, profile: Any, *, create: bool = False) -> Path | None:
        """Return ``<profile>/zalo_bot/history`` or None when it does not exist.

        Every component is checked for symlinks and the resolved directory must
        stay directly below the profile so a crafted link cannot escape it.
        Directories are created ``0700`` only on an explicit write.
        """
        root = cls._profile_root(profile)
        bot_dir = root / BOT_DIR
        if bot_dir.is_symlink():
            raise StoreError(
                "agent bot storage is unsafe", status=400, code="channel_path_unsafe"
            )
        if create:
            bot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(bot_dir, 0o700)
        if not bot_dir.is_dir():
            return None
        resolved_bot = bot_dir.resolve()
        if resolved_bot.parent != root:
            raise StoreError(
                "agent bot storage escapes the profile",
                status=400,
                code="channel_path_unsafe",
            )
        directory = resolved_bot / BOT_HISTORY_DIR
        if directory.is_symlink():
            raise StoreError(
                "agent bot history is unsafe", status=400, code="channel_path_unsafe"
            )
        if create:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        if not directory.is_dir():
            return None
        resolved = directory.resolve()
        if resolved.parent != resolved_bot:
            raise StoreError(
                "agent bot history escapes the profile",
                status=400,
                code="channel_path_unsafe",
            )
        return resolved

    @staticmethod
    def _bot_chat_file(directory: Path, chat_id: str) -> Path:
        return directory / f"{chat_id}.jsonl"

    @classmethod
    def _read_bot_records(cls, path: Path) -> list[dict[str, Any]]:
        """Read at most the last 5 MB of one history file, oldest first."""
        if path.is_symlink() or not path.is_file():
            return []
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                start = max(0, size - MAX_BOT_HISTORY_BYTES)
                handle.seek(start)
                if start:
                    handle.readline()  # drop a line cut by the window
                payload = handle.read()
        except OSError as error:
            raise StoreError(
                "agent bot history is unavailable",
                status=500,
                code="channel_state_unavailable",
            ) from error
        records: list[dict[str, Any]] = []
        for line in payload.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

    @classmethod
    def _bot_history_files(cls, profile: Any) -> tuple[Path | None, list[Path]]:
        directory = cls._bot_history_dir(profile)
        if directory is None:
            return None, []
        files: list[Path] = []
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return directory, files
        for path in entries:
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix != ".jsonl":
                continue
            if _BOT_CHAT_ID.fullmatch(path.stem):
                files.append(path)
        return directory, files

    def bot_history(
        self,
        profile: Any,
        chat_id: Any,
        since: Any = None,
        limit: Any = 200,
    ) -> list[dict[str, Any]]:
        """Read one bot chat's stored records, oldest first."""
        identifier = str(chat_id or "")
        if not _BOT_CHAT_ID.fullmatch(identifier):
            return []
        try:
            cap = int(limit)
        except (TypeError, ValueError):
            cap = 200
        if cap <= 0:
            return []
        directory = self._bot_history_dir(profile)
        if directory is None:
            return []
        records = self._read_bot_records(self._bot_chat_file(directory, identifier))
        if since is not None:
            try:
                threshold = float(since)
            except (TypeError, ValueError):
                threshold = None
            if threshold is not None:
                filtered: list[dict[str, Any]] = []
                for record in records:
                    try:
                        timestamp = float(record.get("ts"))
                    except (TypeError, ValueError):
                        continue
                    if timestamp >= threshold:
                        filtered.append(record)
                records = filtered
        return records[-cap:]

    def bot_history_chats(self, profile: Any) -> list[dict[str, Any]]:
        """Return one summary per stored bot chat, newest conversation first."""
        _, files = self._bot_history_files(profile)
        chats: list[dict[str, Any]] = []
        for path in files:
            try:
                records = self._read_bot_records(path)
            except StoreError:
                continue
            last_ts = 0.0
            chat_type = "dm"
            for record in records:
                try:
                    timestamp = float(record.get("ts"))
                except (TypeError, ValueError):
                    continue
                if timestamp >= last_ts:
                    last_ts = timestamp
                    record_type = str(record.get("chat_type") or "").strip().lower()
                    if record_type in {"dm", "group"}:
                        chat_type = record_type
            chats.append(
                {
                    "chat_id": path.stem,
                    "chat_type": chat_type,
                    "last_ts": last_ts,
                    "message_count": len(records),
                }
            )
        chats.sort(key=lambda item: item["last_ts"], reverse=True)
        return chats

    def delete_bot_history(self, profile: Any, chat_id: Any = None) -> int:
        """Delete one stored chat, or every chat; never follow a symlink."""
        directory, files = self._bot_history_files(profile)
        if directory is None:
            return 0
        if chat_id is not None:
            identifier = str(chat_id)
            if not _BOT_CHAT_ID.fullmatch(identifier):
                return 0
            path = self._bot_chat_file(directory, identifier)
            if path.is_symlink() or not path.is_file():
                return 0
            files = [path]
        removed = 0
        for path in files:
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
        if removed:
            self.base._sync_dir(directory)
        return removed

    def append_bot_history(
        self, profile: Any, chat_id: Any, record: Mapping[str, Any]
    ) -> bool:
        """Append one outbound bot record as a single JSON line."""
        identifier = str(chat_id or "")
        if not _BOT_CHAT_ID.fullmatch(identifier):
            return False
        directory = self._bot_history_dir(profile, create=True)
        if directory is None:
            return False
        path = self._bot_chat_file(directory, identifier)
        if path.is_symlink():
            raise StoreError(
                "agent bot history is unsafe", status=400, code="channel_path_unsafe"
            )
        try:
            timestamp = float(record.get("ts"))
        except (TypeError, ValueError):
            timestamp = time.time()
        value = {
            "message_id": str(record.get("message_id") or "")[:200],
            "chat_id": identifier,
            "chat_type": "group" if record.get("chat_type") == "group" else "dm",
            "direction": "out",
            "sender_id": str(record.get("sender_id") or "")[:128],
            "sender_name": str(record.get("sender_name") or "")[:200],
            "type": str(record.get("type") or "text")[:32],
            "text": str(record.get("text") or "")[:MAX_BOT_HISTORY_TEXT],
            "ts": timestamp,
            "trust": "untrusted_external",
        }
        media_url = str(record.get("media_url") or "")
        if media_url:
            value["media_url"] = media_url[:2048]
        line = json.dumps(value, ensure_ascii=False) + "\n"
        try:
            descriptor = os.open(
                str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600
            )
            try:
                os.write(descriptor, line.encode("utf-8"))
            finally:
                os.close(descriptor)
        except OSError as error:
            raise StoreError(
                "agent bot history is unavailable",
                status=500,
                code="channel_state_unavailable",
            ) from error
        return True

    @classmethod
    def _bot_plugin_dir(cls, profile: Any, dirname: str) -> Path | None:
        """Return one ``<profile>/<dirname>`` plugin directory or None.

        The directory is never created here; writers create it ``0700`` after
        the same symlink and escape checks used by the other bot stores.
        """
        root = cls._profile_root(profile)
        directory = root / dirname
        if directory.is_symlink():
            raise StoreError(
                "agent bot storage is unsafe", status=400, code="channel_path_unsafe"
            )
        if not directory.is_dir():
            return None
        resolved = directory.resolve()
        if resolved.parent != root:
            raise StoreError(
                "agent bot storage escapes the profile",
                status=400,
                code="channel_path_unsafe",
            )
        return resolved

    def bot_contacts(self, profile: Any, chat_id: Any) -> list[dict[str, Any]]:
        """Read one chat's CRM contacts from the plugin's profile file."""
        identifier = str(chat_id or "")
        if not _BOT_CHAT_ID.fullmatch(identifier):
            return []
        directory = self._bot_plugin_dir(profile, BOT_CRM_DIR)
        if directory is None:
            return []
        path = directory / f"{identifier}_contacts.json"
        if path.is_symlink() or not path.is_file():
            return []
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise StoreError(
                "agent bot contacts are unavailable",
                status=500,
                code="channel_state_unavailable",
            ) from error
        try:
            value = json.loads(payload[-MAX_BOT_CRM_BYTES:].decode("utf-8", errors="replace"))
        except (ValueError, TypeError):
            return []
        if not isinstance(value, dict):
            return []
        contacts: list[dict[str, Any]] = []
        for user_id, record in value.items():
            if not isinstance(record, dict):
                continue
            labels = record.get("labels")
            cleaned_labels = (
                [str(item)[:64] for item in labels if isinstance(item, str)][:20]
                if isinstance(labels, (list, tuple))
                else []
            )
            try:
                updated_at = float(record.get("updated_at"))
            except (TypeError, ValueError):
                updated_at = 0.0
            contacts.append(
                {
                    "user_id": str(record.get("user_id") or user_id)[:128],
                    "phone": str(record.get("phone") or "")[:64],
                    "name": " ".join(str(record.get("name") or "").split())[:200],
                    "labels": cleaned_labels,
                    "updated_at": updated_at,
                }
            )
        contacts.sort(key=lambda item: item["updated_at"], reverse=True)
        return contacts[:MAX_BOT_CRM_CONTACTS]

    def write_bot_group_settings(
        self, profile: Any, chat_id: Any, updates: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Merge per-group plugin settings into the profile's 0600 JSON file."""
        identifier = str(chat_id or "")
        if not _BOT_CHAT_ID.fullmatch(identifier):
            raise StoreError(
                "agent bot group id is invalid", status=400, code="channel_recipient_invalid"
            )
        root = self._profile_root(profile)
        directory = root / BOT_SETTINGS_DIR
        if directory.is_symlink():
            raise StoreError(
                "agent bot settings are unsafe", status=400, code="channel_path_unsafe"
            )
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        resolved = directory.resolve()
        if resolved.parent != root:
            raise StoreError(
                "agent bot settings escape the profile",
                status=400,
                code="channel_path_unsafe",
            )
        path = resolved / f"{identifier}.json"
        if path.is_symlink():
            raise StoreError(
                "agent bot settings are unsafe", status=400, code="channel_path_unsafe"
            )
        settings: dict[str, Any] = {}
        if path.is_file():
            try:
                existing = json.loads(
                    path.read_bytes()[-MAX_BOT_CRM_BYTES:].decode(
                        "utf-8", errors="replace"
                    )
                )
            except (OSError, ValueError, TypeError):
                existing = None
            if isinstance(existing, dict):
                settings = existing
        settings.update(dict(updates))
        self.base.atomic_write(
            path,
            (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            mode=0o600,
        )
        return settings

    def _bot_warnings_path(
        self, profile: Any, chat_id: Any, *, create: bool = False
    ) -> Path:
        identifier = str(chat_id or "")
        if not _BOT_CHAT_ID.fullmatch(identifier):
            raise StoreError(
                "agent bot chat id is invalid",
                status=400,
                code="channel_recipient_invalid",
            )
        root = self._profile_root(profile)
        bot_dir = root / BOT_DIR
        directory = bot_dir / BOT_WARNINGS_DIR
        if bot_dir.is_symlink() or directory.is_symlink():
            raise StoreError(
                "agent bot warnings are unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        if create:
            bot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(bot_dir, 0o700)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        if directory.exists():
            resolved = directory.resolve()
            if resolved.parent.parent != root:
                raise StoreError(
                    "agent bot warnings escape the profile",
                    status=400,
                    code="channel_path_unsafe",
                )
            path = resolved / f"{identifier}.json"
        else:
            path = directory / f"{identifier}.json"
        if path.is_symlink():
            raise StoreError(
                "agent bot warnings are unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        return path

    def _read_bot_warnings_file(self, path: Path) -> dict[str, list[dict[str, Any]]]:
        if not path.is_file():
            return {}
        try:
            raw = json.loads(
                path.read_bytes()[-MAX_BOT_CRM_BYTES:].decode(
                    "utf-8", errors="replace"
                )
            )
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        cleaned: dict[str, list[dict[str, Any]]] = {}
        for uid, entries in raw.items():
            if not isinstance(uid, str) or not isinstance(entries, list):
                continue
            cleaned[uid] = [item for item in entries if isinstance(item, dict)]
        return cleaned

    @staticmethod
    def _prune_bot_warnings(
        entries: list[dict[str, Any]], now: float
    ) -> list[dict[str, Any]]:
        active: list[dict[str, Any]] = []
        for item in entries:
            ts = item.get("timestamp")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                if now - float(ts) < BOT_WARN_EXPIRY_SECONDS:
                    active.append(
                        {
                            "timestamp": float(ts),
                            "reason": str(item.get("reason") or "")[:200],
                            "count": int(item.get("count") or len(active) + 1),
                        }
                    )
        return active

    def bot_user_warnings(
        self, profile: Any, chat_id: Any, user_id: Any
    ) -> list[dict[str, Any]]:
        uid = str(user_id or "")
        if not _BOT_CHAT_ID.fullmatch(uid):
            raise StoreError(
                "agent bot user id is invalid",
                status=400,
                code="channel_recipient_invalid",
            )
        path = self._bot_warnings_path(profile, chat_id, create=False)
        data = self._read_bot_warnings_file(path)
        return self._prune_bot_warnings(data.get(uid, []), time.time())

    def warn_bot_user(
        self, profile: Any, chat_id: Any, user_id: Any, reason: str = ""
    ) -> dict[str, Any]:
        uid = str(user_id or "")
        if not _BOT_CHAT_ID.fullmatch(uid):
            raise StoreError(
                "agent bot user id is invalid",
                status=400,
                code="channel_recipient_invalid",
            )
        now = time.time()
        path = self._bot_warnings_path(profile, chat_id, create=True)
        data = self._read_bot_warnings_file(path)
        active = self._prune_bot_warnings(data.get(uid, []), now)
        entry = {
            "timestamp": now,
            "reason": str(reason or "")[:200],
            "count": len(active) + 1,
        }
        active.append(entry)
        data[uid] = active
        self.base.atomic_write(
            path,
            (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            mode=0o600,
        )
        action = (
            "max_warnings_reached" if entry["count"] >= BOT_WARN_MAX else "warned"
        )
        return {
            "action": action,
            "count": entry["count"],
            "max_warnings": BOT_WARN_MAX,
            "warning": entry,
        }

    def clear_bot_user_warnings(
        self, profile: Any, chat_id: Any, user_id: Any
    ) -> dict[str, Any]:
        uid = str(user_id or "")
        if not _BOT_CHAT_ID.fullmatch(uid):
            raise StoreError(
                "agent bot user id is invalid",
                status=400,
                code="channel_recipient_invalid",
            )
        path = self._bot_warnings_path(profile, chat_id, create=True)
        data = self._read_bot_warnings_file(path)
        data.pop(uid, None)
        self.base.atomic_write(
            path,
            (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            mode=0o600,
        )
        return {"action": "cleared", "count": 0, "max_warnings": BOT_WARN_MAX}

    def _restart_state_path(self) -> Path:
        return self.base.data_dir / "channels" / RESTART_STATE_FILE

    def gateway_restart(self) -> dict[str, Any] | None:
        path = self._restart_state_path()
        if path.is_symlink() or not path.is_file():
            return None
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError):
            return None
        if not isinstance(value, dict):
            return None
        try:
            pid = int(value.get("pid"))
        except (TypeError, ValueError):
            return None
        return {"pid": pid, "requested_at": value.get("requested_at")}

    def mark_gateway_restart(self, pid: int) -> None:
        import time

        self.base.atomic_json(
            self._restart_state_path(),
            {"pid": int(pid), "requested_at": time.time()},
        )

    def clear_gateway_restart(self) -> None:
        path = self._restart_state_path()
        if path.is_symlink() or not path.exists():
            return
        try:
            path.unlink()
        except OSError:
            return
        self.base._sync_dir(path.parent)

    @classmethod
    def _owner_directory(cls, profile: Any, *, create: bool = False) -> Path | None:
        root = cls._profile_root(profile)
        metadata = root / OWNER_METADATA_DIR
        if metadata.is_symlink():
            raise StoreError(
                "agent channel owner storage is unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        directory = metadata / OWNER_CHANNEL_DIR
        if directory.is_symlink():
            raise StoreError(
                "agent channel owner storage is unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        if create:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not directory.is_dir():
            return None
        resolved_metadata = metadata.resolve()
        resolved = directory.resolve()
        if resolved_metadata.parent != root or resolved.parent != resolved_metadata:
            raise StoreError(
                "agent channel owner storage escapes the profile",
                status=400,
                code="channel_path_unsafe",
            )
        return resolved

    @staticmethod
    def _owner_value(value: Any, field: str, *, required: bool) -> str:
        text = str(value or "").strip()
        if not text and not required:
            return ""
        if not _OWNER_VALUE.fullmatch(text):
            raise StoreError(
                f"agent channel owner {field} is invalid",
                code="channel_owner_invalid",
            )
        return text

    def read_owner(self, profile: Any) -> dict[str, Any] | None:
        """Read the verified owner binding that authorizes channel MCP use."""
        directory = self._owner_directory(profile)
        if directory is None:
            return None
        path = directory / OWNER_FILE
        if path.is_symlink():
            raise StoreError(
                "agent channel owner binding is unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        if not path.is_file():
            return None
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError):
            return None
        if not isinstance(value, dict):
            return None
        try:
            record = {
                "user_id": self._owner_value(value.get("user_id"), "user_id", required=True),
                "tenant_id": self._owner_value(value.get("tenant_id"), "tenant_id", required=False),
                "organization_id": self._owner_value(
                    value.get("organization_id"), "organization_id", required=False
                ),
                "agent_id": self._owner_value(value.get("agent_id"), "agent_id", required=True),
            }
        except StoreError:
            return None
        bound_at = value.get("bound_at")
        if not isinstance(bound_at, (int, float)) or isinstance(bound_at, bool):
            return None
        record["bound_at"] = float(bound_at)
        return record

    def write_owner(self, profile: Any, record: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically persist the verified owner binding, never a credential."""
        directory = self._owner_directory(profile, create=True)
        if directory is None:
            raise StoreError(
                "agent channel owner storage is unavailable",
                status=500,
                code="channel_state_unavailable",
            )
        path = directory / OWNER_FILE
        if path.is_symlink():
            raise StoreError(
                "agent channel owner binding is unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        value = {
            "user_id": self._owner_value(record.get("user_id"), "user_id", required=True),
            "tenant_id": self._owner_value(record.get("tenant_id"), "tenant_id", required=False),
            "organization_id": self._owner_value(
                record.get("organization_id"), "organization_id", required=False
            ),
            "agent_id": self._owner_value(record.get("agent_id"), "agent_id", required=True),
            "bound_at": float(record.get("bound_at") or 0),
        }
        self.base.atomic_write(
            path,
            (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
            mode=0o600,
        )
        return value

    def delete_owner(self, profile: Any) -> bool:
        directory = self._owner_directory(profile)
        if directory is None:
            return False
        path = directory / OWNER_FILE
        if path.is_symlink():
            raise StoreError(
                "agent channel owner binding is unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError as error:
            raise StoreError(
                "agent channel owner binding is unavailable",
                status=500,
                code="channel_state_unavailable",
            ) from error
        self.base._sync_dir(path.parent)
        return True

    def social_tools(self, profile: Any, default: bool = False) -> bool:
        """Return whether this profile may use tools that act on social channels."""
        return social_tools_enabled(profile, default)

    def write_social_tools(self, profile: Any, enabled: bool) -> bool:
        """Atomically persist the switch beside the owner binding."""
        directory = self._owner_directory(profile, create=True)
        if directory is None:
            raise StoreError(
                "agent channel social tools storage is unavailable",
                status=500,
                code="channel_state_unavailable",
            )
        path = directory / SOCIAL_TOOLS_FILE
        if path.is_symlink():
            raise StoreError(
                "agent channel social tools state is unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        value = {"schema_version": 1, "enabled": bool(enabled)}
        self.base.atomic_write(
            path,
            (json.dumps(value, sort_keys=True) + "\n").encode("utf-8"),
            mode=0o600,
        )
        return bool(enabled)

    def links(self, profile: Any) -> dict[str, dict[str, Any]]:
        _, directory = self._zalo_dir(profile)
        if directory is None:
            return {}
        path = directory / LINKS_FILE
        if not path.exists():
            return {}
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError) as error:
            raise StoreError(
                "agent channel links are invalid", status=500, code="channel_links_invalid"
            ) from error
        if not isinstance(value, dict):
            raise StoreError(
                "agent channel links are invalid", status=500, code="channel_links_invalid"
            )
        return {
            str(thread_id): record
            for thread_id, record in value.items()
            if isinstance(record, dict)
        }

    def write_link(
        self,
        profile: Any,
        thread_id: str,
        *,
        agent_id: str,
        conversation_id: str,
        name: str,
        timestamp: float,
    ) -> dict[str, Any]:
        _, directory = self._zalo_dir(profile, create=True)
        if directory is None:
            raise StoreError(
                "agent channel directory is unavailable",
                status=500,
                code="channel_state_unavailable",
            )
        path = directory / LINKS_FILE
        if path.is_symlink():
            raise StoreError(
                "agent channel links are unsafe", status=400, code="channel_path_unsafe"
            )
        links = self.links(profile)
        record = {
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "name": name,
            "ts": timestamp,
        }
        links[str(thread_id)] = record
        self.base.atomic_write(
            path,
            (json.dumps(links, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
            mode=0o600,
        )
        return record

    def threads(self, profile: Any) -> dict[str, dict[str, dict[str, Any]]]:
        """Read the persisted platform/thread-to-conversation mapping."""
        _, directory = self._zalo_dir(profile)
        if directory is None:
            return {}
        path = directory / THREADS_FILE
        if not path.exists():
            return {}
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError) as error:
            raise StoreError(
                "agent channel threads are invalid",
                status=500,
                code="channel_threads_invalid",
            ) from error
        platforms = value.get("platforms") if isinstance(value, dict) else None
        if not isinstance(platforms, dict):
            raise StoreError(
                "agent channel threads are invalid",
                status=500,
                code="channel_threads_invalid",
            )
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for platform, records in platforms.items():
            if not isinstance(platform, str) or not isinstance(records, dict):
                continue
            result[platform] = {
                str(thread_id): record
                for thread_id, record in records.items()
                if isinstance(record, dict)
            }
        return result

    def write_thread(
        self,
        profile: Any,
        platform: str,
        thread_id: str,
        *,
        agent_id: str,
        conversation_id: str,
        title: str,
        sender_id: str,
        timestamp: float,
    ) -> dict[str, Any]:
        """Persist one platform/thread-to-conversation mapping atomically."""
        _, directory = self._zalo_dir(profile, create=True)
        if directory is None:
            raise StoreError(
                "agent channel directory is unavailable",
                status=500,
                code="channel_state_unavailable",
            )
        path = directory / THREADS_FILE
        if path.is_symlink():
            raise StoreError(
                "agent channel threads are unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        threads = self.threads(profile)
        records = threads.setdefault(str(platform), {})
        record = {
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "title": title,
            "sender_id": sender_id,
            "ts": timestamp,
        }
        records[str(thread_id)] = record
        self.base.atomic_write(
            path,
            (
                json.dumps(
                    {"schema_version": 1, "platforms": threads},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
            mode=0o600,
        )
        return record

    @classmethod
    def _normalize_group_state(cls, value: Any) -> dict[str, Any]:
        """Return the platform-keyed group approval state without bad rows."""
        platforms: dict[str, Any] = {}
        raw_platforms = value.get("platforms") if isinstance(value, dict) else None
        if isinstance(raw_platforms, dict):
            for platform, record in raw_platforms.items():
                if not isinstance(platform, str) or not isinstance(record, dict):
                    continue
                raw_groups = record.get("groups")
                groups: dict[str, Any] = {}
                if isinstance(raw_groups, dict):
                    for group_id, entry in raw_groups.items():
                        if not isinstance(entry, dict):
                            continue
                        status = str(entry.get("status") or "").strip().lower()
                        if status not in _GROUP_STATUSES:
                            continue
                        name = _group_label(entry.get("name"))
                        groups[str(group_id)] = {
                            "status": status,
                            "name": name,
                            "last_sender_name": _group_label(entry.get("last_sender_name")),
                            "first_seen": _group_timestamp(entry.get("first_seen")),
                            "last_seen": _group_timestamp(entry.get("last_seen")),
                            "approved_at": (
                                _group_timestamp(entry.get("approved_at"))
                                if status == "approved"
                                else None
                            ),
                        }
                platforms[platform] = {
                    "daily_limit": _group_daily_limit(record.get("daily_limit")),
                    "groups": groups,
                }
        return {"schema_version": 1, "platforms": platforms}

    def groups(self, profile: Any) -> dict[str, Any]:
        """Read the persisted per-platform group approval state."""
        _, directory = self._zalo_dir(profile)
        if directory is None:
            return {"schema_version": 1, "platforms": {}}
        path = directory / GROUPS_FILE
        if not path.exists():
            return {"schema_version": 1, "platforms": {}}
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError) as error:
            raise StoreError(
                "agent channel groups are invalid",
                status=500,
                code="channel_groups_invalid",
            ) from error
        return self._normalize_group_state(value)

    def write_groups(self, profile: Any, value: Mapping[str, Any]) -> None:
        _, directory = self._zalo_dir(profile, create=True)
        if directory is None:
            raise StoreError(
                "agent channel directory is unavailable",
                status=500,
                code="channel_state_unavailable",
            )
        path = directory / GROUPS_FILE
        if path.is_symlink():
            raise StoreError(
                "agent channel groups are unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        self.base.atomic_write(
            path,
            (
                json.dumps(
                    self._normalize_group_state(value),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
            mode=0o600,
        )

    @classmethod
    def _normalize_group_usage(cls, value: Any) -> dict[str, Any]:
        """Return today's bounded per-platform group counters."""
        platforms: dict[str, Any] = {}
        raw_platforms = value.get("platforms") if isinstance(value, dict) else None
        if isinstance(raw_platforms, dict):
            for platform, record in raw_platforms.items():
                if not isinstance(platform, str) or not isinstance(record, dict):
                    continue
                counts: dict[str, dict[str, int]] = {}
                raw_counts = record.get("counts")
                if isinstance(raw_counts, dict):
                    for group_id, senders in raw_counts.items():
                        if not isinstance(senders, dict):
                            continue
                        normalized: dict[str, int] = {}
                        for sender_id, count in senders.items():
                            if isinstance(count, bool):
                                continue
                            try:
                                number = int(count)
                            except (TypeError, ValueError):
                                continue
                            if number > 0:
                                normalized[str(sender_id)] = number
                        if normalized:
                            counts[str(group_id)] = normalized
                message_ids: dict[str, list[str]] = {}
                raw_ids = record.get("message_ids")
                if isinstance(raw_ids, dict):
                    for key, values in raw_ids.items():
                        if not isinstance(values, (list, tuple)):
                            continue
                        cleaned = [
                            str(item)
                            for item in values
                            if isinstance(item, str) and item
                        ][-MAX_GROUP_COUNTED_MESSAGE_IDS:]
                        if cleaned:
                            message_ids[str(key)] = cleaned
                platforms[platform] = {
                    "date": str(record.get("date") or ""),
                    "counts": counts,
                    "message_ids": message_ids,
                }
        return {"schema_version": 1, "platforms": platforms}

    def group_usage(self, profile: Any) -> dict[str, Any]:
        """Read the persisted per-platform daily group usage."""
        _, directory = self._zalo_dir(profile)
        if directory is None:
            return {"schema_version": 1, "platforms": {}}
        path = directory / GROUP_USAGE_FILE
        if not path.exists():
            return {"schema_version": 1, "platforms": {}}
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError) as error:
            raise StoreError(
                "agent channel group usage is invalid",
                status=500,
                code="channel_group_usage_invalid",
            ) from error
        return self._normalize_group_usage(value)

    def write_group_usage(self, profile: Any, value: Mapping[str, Any]) -> None:
        _, directory = self._zalo_dir(profile, create=True)
        if directory is None:
            raise StoreError(
                "agent channel directory is unavailable",
                status=500,
                code="channel_state_unavailable",
            )
        path = directory / GROUP_USAGE_FILE
        if path.is_symlink():
            raise StoreError(
                "agent channel group usage is unsafe",
                status=400,
                code="channel_path_unsafe",
            )
        self.base.atomic_write(
            path,
            (
                json.dumps(
                    self._normalize_group_usage(value),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
            mode=0o600,
        )

    def history(self, profile: Any) -> list[dict[str, Any]]:
        _, directory = self._zalo_dir(profile)
        if directory is None:
            return []
        path = directory / HISTORY_FILE
        if not path.exists():
            return []
        lines = self._read_text(path).splitlines()
        records: list[dict[str, Any]] = []
        for line in lines[-MAX_HISTORY_LINES:]:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

    def directory(self, profile: Any) -> list[dict[str, Any]]:
        root = self._profile_root(profile)
        path = root / DIRECTORY_FILE
        if not path.exists():
            return []
        try:
            value = json.loads(self._read_text(path))
        except (ValueError, TypeError):
            return []
        platforms = value.get("platforms") if isinstance(value, dict) else None
        entries = platforms.get("zalo") if isinstance(platforms, dict) else None
        if not isinstance(entries, list):
            return []
        result: list[dict[str, Any]] = []
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("id") or "").strip():
                result.append(entry)
        return result


def social_tools_enabled(profile: Any, default: bool = False) -> bool:
    """Return the profile's social-tools switch; a missing file means ``default``.

    The switch lives beside the channel owner binding so the agent's own file
    tools cannot flip it through ``config.yaml``.
    """
    try:
        directory = ChannelsRepository._owner_directory(profile)
        if directory is None:
            return default
        path = directory / SOCIAL_TOOLS_FILE
        if path.is_symlink():
            return False
        if not path.is_file():
            return default
        value = json.loads(ChannelsRepository._read_text(path))
    except (StoreError, ValueError, TypeError):
        return False
    return isinstance(value, dict) and value.get("enabled") is True

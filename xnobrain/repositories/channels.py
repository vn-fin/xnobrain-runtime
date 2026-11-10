"""Atomic Zalo channel state, profile settings, and discovery for Runtime chats."""

from __future__ import annotations

import json
import re
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
RESTART_STATE_FILE = "gateway-restart.json"
OWNER_FILE = "owner.json"
OWNER_METADATA_DIR = ".xnobrain"
OWNER_CHANNEL_DIR = "channels"
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_QR_BYTES = 4 * 1024 * 1024
MAX_HISTORY_LINES = 5000
DEFAULT_GROUP_DAILY_LIMIT = 20
MAX_GROUP_DAILY_LIMIT = 1000
MAX_PENDING_GROUP_REQUESTS = 20
MAX_GROUP_COUNTED_MESSAGE_IDS = 2000
_GROUP_STATUSES = {"pending", "approved"}
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

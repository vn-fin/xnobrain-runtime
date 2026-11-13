"""Zalo personal-account platform adapter.

Zalo has no official bot API for personal accounts, so this adapter follows the
WhatsApp pattern: a detached Node.js bridge (``bridge/bridge.js``, built on
zca-js) owns the Zalo Web session and exposes a loopback-only HTTP API. The
Python adapter spawns the bridge, polls ``GET /messages`` for inbound messages,
and sends replies through ``POST /send``.

Auto-reply is off by default (``extra.auto_reply``): inbound messages start no
agent turn, so the personal account only acts through owner-approved tools.
When it is on, the scope is direct (1:1) messages and owner-approved groups;
group messages are mention-gated by default and skipped mentions are replayed
with the next addressed message. Inbound media is downloaded into
``<workspace>/zalo-inbound/`` after authorization and mention gating, and
announced as a workspace-file note. Every bridge request except ``GET /health``
carries the bearer token the bridge writes to ``<session dir>/bridge.token``.
"""

import asyncio
import ipaddress
import json
import logging
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from hermes_cli._subprocess_compat import windows_detach_popen_kwargs
from hermes_constants import (
    find_node_executable,
    get_hermes_home,
    with_hermes_node_path,
)

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

BRIDGE_PORT_BASE = 3110
DEFAULT_MAX_MESSAGE_LENGTH = 2000
BRIDGE_TOKEN_FILENAME = "bridge.token"
BRIDGE_TOKEN_HEADER = "Authorization"
BRIDGE_SEND_TIMEOUT_SECONDS = 30
BRIDGE_BEST_EFFORT_TIMEOUT_SECONDS = 5.0
DEFAULT_MEDIA_MAX_MB = 10
MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 20.0
MEDIA_MAX_REDIRECTS = 5
MEDIA_CHUNK_BYTES = 65536
DEFAULT_HISTORY_RETENTION_DAYS = 30
MEDIA_RETENTION_PRUNE_INTERVAL_SECONDS = 6 * 60 * 60
INBOUND_MEDIA_DIRNAME = "zalo-inbound"
MAX_PENDING_MEDIA_EVENTS = 500
MAX_REMEMBERED_THREADS = 500
MAX_GROUP_BACKLOG_MESSAGES = 50
MAX_GROUP_BACKLOG_GROUPS = 50
GROUP_BACKLOG_HEADER = "[Earlier messages in this group, not addressed to you]"
GROUP_MESSAGE_HEADER = "[Message to you]"
_MEDIA_KINDS = frozenset({"image", "file", "video", "other"})
_GROUP_THREAD_TYPES = frozenset({"group"})
# Allowlisted Zalo CDN host suffixes for inbound media downloads.
# Derived from zca-js 2.2.0 (`stc-zlogin.zdn.vn`, `*.zalo.me` in
# `bridge/node_modules/zca-js/dist/`) and Zalo attachment/CDN domains
# (`zdn.vn`, `zadn.vn`, `zaloapp.com`, `zalo.me`).
_ZALO_CDN_HOST_SUFFIXES = (
    ".zdn.vn",
    ".zadn.vn",
    ".zalo.me",
    ".zaloapp.com",
)
_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
LINKED_MESSAGE_PATH = "/xnobrain/api/runtime/v1/internal/channels/messages"
LINKED_MAX_RETRY = 100
LINKED_RETRY_INTERVAL_SECONDS = 5.0

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gateway.config import Platform, PlatformConfig  # noqa: E402
from gateway.platforms.base import (  # noqa: E402
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)


def _file_content_hash(path: Path) -> str:
    """Return the first 16 hex chars of the SHA-256 of ``path``'s contents."""
    import hashlib

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _profile_index(
    home: Optional[Path] = None,
    profiles_root: Optional[str] = None,
    root_profile: Optional[str] = None,
) -> int:
    """Return a stable per-profile index for the bridge port.

    The index is the position of the active profile name in the sorted list of
    directories under ``HERMES_PROFILES_ROOT`` (falling back to the parent of
    ``HERMES_HOME``). The root profile, an unknown profile, a missing directory
    and unreadable roots all map to 0.
    """
    active_home = Path(home) if home is not None else get_hermes_home()
    root_value = profiles_root
    if root_value is None:
        root_value = os.environ.get("HERMES_PROFILES_ROOT", "")
    root_profile_value = root_profile
    if root_profile_value is None:
        root_profile_value = os.environ.get("HERMES_ROOT_PROFILE", "")
    root_value = (root_value or "").strip()
    root_profile_value = (root_profile_value or "").strip()

    try:
        active = active_home.resolve()
    except OSError:
        active = active_home

    if root_profile_value:
        try:
            if active == Path(root_profile_value).resolve():
                return 0
        except OSError:
            pass

    if root_value:
        root = Path(root_value)
        try:
            if active.parent != root.resolve():
                # The root profile (or a home outside the profiles tree) is
                # never offset; only named profiles are.
                return 0
        except OSError:
            return 0
    else:
        root = active.parent

    try:
        names = sorted(entry.name for entry in root.iterdir() if entry.is_dir())
    except OSError:
        return 0
    try:
        return names.index(active.name)
    except ValueError:
        return 0


def _default_bridge_port() -> int:
    """Base port plus the stable profile index (plan 015 D3)."""
    return BRIDGE_PORT_BASE + _profile_index()


def _coerce_port(value: Any, default: Optional[int] = None) -> int:
    """Resolve a bridge port, refusing the Runtime API port (3000)."""
    fallback = default if default is not None else _default_bridge_port()
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    if port < 1 or port > 65535 or port == 3000:
        return fallback
    return port


def _read_bridge_token(session_path: Path) -> Optional[str]:
    """Read the bridge bearer token from disk on every request.

    The bridge regenerates the token at every start, so it is never cached.
    A missing or empty file means the bridge is not usable yet.
    """
    try:
        token = (
            Path(session_path) / BRIDGE_TOKEN_FILENAME
        ).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return token or None


def _bridge_auth_headers(token: str) -> Dict[str, str]:
    """Return the one header authenticated bridge endpoints require."""
    return {BRIDGE_TOKEN_HEADER: f"Bearer {token}"}


def _bridge_session_path(pconfig) -> Path:
    """Resolve the session directory that holds the bridge token file."""
    extra = getattr(pconfig, "extra", {}) or {}
    return Path(extra.get("session_path") or (get_hermes_home() / "zalo"))


def _thread_type_value(value: Any) -> str:
    """Map a bridge/gateway thread type to the bridge's ``user``/``group``."""
    text = str(value or "").strip().lower()
    return "group" if text in _GROUP_THREAD_TYPES else "user"


def _bridge_is_current(health: Dict[str, Any], bridge_path: Path) -> bool:
    """True when a running bridge reports the hash of the on-disk script.

    A long-lived bridge survives gateway restarts and updates; without this
    handshake it would keep serving pre-update code. Bridges that do not report
    a hash are treated as stale by definition.
    """
    running_hash = str(health.get("scriptHash") or "")
    disk_hash = _file_content_hash(bridge_path)
    return bool(running_hash and disk_hash and running_hash == disk_hash)


def _listener_pids_on_port(port: int) -> list:
    """PIDs of processes *listening* on ``port`` (POSIX), never clients."""
    pids: list = []
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            try:
                pids.append(int(line))
            except ValueError:
                pass
        if pids:
            return pids
    except FileNotFoundError:
        pass
    try:
        result = subprocess.run(
            ["ss", "-ltnHp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        for match in re.finditer(r"pid=(\d+)", result.stdout):
            pids.append(int(match.group(1)))
    except FileNotFoundError:
        pass
    return pids


def _kill_port_process(port: int) -> None:
    """Kill any process *listening* on the given TCP port (a stale bridge)."""
    try:
        if _IS_WINDOWS:
            from hermes_cli._subprocess_compat import windows_hide_flags

            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                creationflags=windows_hide_flags(),
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3] == "LISTENING":
                    if parts[1].endswith(f":{port}"):
                        try:
                            subprocess.run(
                                ["taskkill", "/PID", parts[4], "/F"],
                                capture_output=True,
                                timeout=5,
                                creationflags=windows_hide_flags(),
                            )
                        except subprocess.SubprocessError:
                            pass
        else:
            for pid in _listener_pids_on_port(port):
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
    except Exception:
        pass


def _bridge_pid_is_ours(pid: int, session_path: Path, expected_start) -> bool:
    """True only if ``pid`` is alive AND still our node bridge for this session."""
    from gateway.status import _pid_exists

    if not _pid_exists(pid):
        return False
    if expected_start is not None:
        from gateway.status import get_process_start_time

        return get_process_start_time(pid) == expected_start
    from gateway.status import _read_process_cmdline

    cmdline = _read_process_cmdline(pid)
    if not cmdline:
        return False
    return ("node" in cmdline) and (str(session_path) in cmdline)


def _kill_stale_bridge_by_pidfile(session_path: Path) -> None:
    """Kill a bridge recorded in a pidfile from a previous gateway run."""
    pid_file = session_path / "bridge.pid"
    if not pid_file.exists():
        return
    pid = None
    recorded_start = None
    try:
        lines = pid_file.read_text(encoding="utf-8").split("\n")
        pid = int(lines[0].strip())
        if len(lines) > 1 and lines[1].strip():
            recorded_start = int(lines[1].strip())
    except (ValueError, OSError, TypeError, IndexError):
        try:
            pid_file.unlink()
        except OSError:
            pass
        return
    if _bridge_pid_is_ours(pid, session_path, recorded_start):
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("[zalo] Killed stale bridge PID %d from pidfile", pid)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        pid_file.unlink()
    except OSError:
        pass


def _write_bridge_pidfile(session_path: Path, pid: int) -> None:
    """Write the bridge PID and its kernel start time for later cleanup."""
    try:
        from gateway.status import get_process_start_time

        start = get_process_start_time(pid)
        text = str(pid) if start is None else "{}\n{}".format(pid, start)
        (session_path / "bridge.pid").write_text(text, encoding="utf-8")
    except OSError:
        pass


def _terminate_bridge_process(proc, *, force: bool = False) -> None:
    """Terminate the bridge process using process-tree semantics where possible."""
    if _IS_WINDOWS:
        cmd = ["taskkill", "/PID", str(proc.pid), "/T"]
        if force:
            cmd.append("/F")
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except FileNotFoundError:
            if force:
                proc.kill()
            else:
                proc.terminate()
        return

    import psutil

    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
        if force:
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            parent.kill()
        else:
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            parent.terminate()
    except psutil.NoSuchProcess:
        return


def check_zalo_requirements() -> bool:
    """Check whether the Node.js runtime needed by the bridge is available."""
    node = find_node_executable("node")
    if not node:
        return False
    try:
        result = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _sanitize_path_segment(value: Any, fallback: str, max_len: int = 64) -> str:
    """Return a single safe path segment without traversal or separators."""
    raw = str(value or "").strip().replace("\\", "/")
    leaf = raw.rsplit("/", 1)[-1]
    cleaned = _SAFE_SEGMENT_RE.sub("_", leaf).lstrip(".")
    if not cleaned or cleaned in {".", ".."}:
        return fallback
    if len(cleaned) <= max_len:
        return cleaned
    if "." in cleaned:
        stem, ext = cleaned.rsplit(".", 1)
        if ext and len(ext) <= 16:
            keep = max(1, max_len - len(ext) - 1)
            return f"{stem[:keep]}.{ext}"
    return cleaned[:max_len]


def _is_allowlisted_zalo_host(hostname: str) -> bool:
    """Return True when ``hostname`` matches an allowlisted Zalo CDN suffix."""
    host = (hostname or "").rstrip(".").lower()
    if not host:
        return False
    return any(
        host == suffix.lstrip(".") or host.endswith(suffix)
        for suffix in _ZALO_CDN_HOST_SUFFIXES
    )


def _is_public_ip(ip_text: str) -> bool:
    """Return True only for globally routable unicast IP addresses."""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_media_url(url: str) -> Optional[Tuple[str, int]]:
    """Validate an HTTPS Zalo CDN URL and return ``(hostname, port)``."""
    if not isinstance(url, str):
        return None
    trimmed = url.strip()
    if not trimmed:
        return None
    try:
        parsed = urlsplit(trimmed)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https":
        return None
    if parsed.username or parsed.password:
        return None
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not _is_allowlisted_zalo_host(hostname):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port not in (None, 443):
        return None
    return hostname, (port or 443)


def _resolve_public_host(hostname: str, port: int) -> bool:
    """Resolve ``hostname`` via DNS and reject any non-public address."""
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for entry in infos:
        sockaddr = entry[4]
        if not sockaddr:
            return False
        ip_text = str(sockaddr[0]).split("%", 1)[0]
        if not _is_public_ip(ip_text):
            return False
    return True


def _strip_media_urls(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of ``data`` with download URLs removed from media."""
    media = data.get("media")
    if not isinstance(media, list):
        return data
    copied = dict(data)
    copied["media"] = [
        {k: v for k, v in item.items() if k != "url"} if isinstance(item, dict) else item
        for item in media
    ]
    return copied


def _ensure_no_symlink_dir(path: Path, *, enforce_mode: bool = False) -> None:
    """Create ``path`` if missing, rejecting symlinks."""
    if path.is_symlink():
        raise OSError("symlinked directory refused")
    if not path.exists():
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    elif not path.is_dir():
        raise OSError("non-directory in media path")
    if enforce_mode and not _IS_WINDOWS:
        os.chmod(path, 0o700)


class ZaloAdapter(BasePlatformAdapter):
    """Zalo adapter backed by the local zca-js bridge."""

    splits_long_messages = True

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("zalo"))
        self._bridge_port: int = _coerce_port(config.extra.get("bridge_port"))
        self._bridge_script: str = str(
            config.extra.get("bridge_script")
            or (Path(__file__).resolve().parent / "bridge" / "bridge.js")
        )
        self._profile_home: Path = Path(get_hermes_home())
        self._session_path: Path = Path(
            config.extra.get("session_path") or (self._profile_home / "zalo")
        )
        self._workspace_dir: Path = Path(
            config.extra.get("workspace_path") or (self._profile_home / "workspace")
        )
        self._bridge_process: Optional[subprocess.Popen] = None
        self._bridge_log_fh = None
        self._bridge_log: Optional[Path] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._http_session: Optional["aiohttp.ClientSession"] = None
        self._own_id: str = ""
        self._chat_names: Dict[str, str] = {}
        self._thread_types: Dict[str, str] = {}
        self._group_backlog: Dict[str, list] = {}
        self._pending_media: Dict[str, Tuple[str, str, list]] = {}
        self._last_media_prune_at: float = 0.0
        self._bridge_token_warned: bool = False
        self._shutting_down: bool = False
        self._links_cache: Dict[str, Dict[str, Any]] = {}
        self._links_mtime: Optional[float] = None
        self._pending_linked: list = []
        self._linked_retry_at: float = 0.0
        self._linked_config_warned: bool = False

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        """Start the Zalo bridge and begin polling for inbound messages."""
        if not check_zalo_requirements():
            logger.warning("[%s] Node.js not found. Zalo requires Node.js.", self.name)
            self._set_fatal_error(
                "zalo_node_missing",
                "Node.js is not installed — install Node.js and re-run `hermes gateway`.",
                retryable=False,
            )
            return False

        bridge_path = Path(self._bridge_script)
        if not bridge_path.exists():
            logger.warning("[%s] Bridge script not found: %s", self.name, bridge_path)
            self._set_fatal_error(
                "zalo_bridge_missing",
                f"Zalo bridge script missing at {bridge_path}.",
                retryable=False,
            )
            return False

        bridge_dir = bridge_path.parent
        if not (bridge_dir / "node_modules").exists():
            logger.error(
                "[%s] Zalo bridge dependencies are missing. Run "
                "`npm ci --omit=dev` in %s and restart the gateway.",
                self.name,
                bridge_dir,
            )
            self._set_fatal_error(
                "zalo_bridge_deps_missing",
                f"Zalo bridge dependencies missing — run `npm ci --omit=dev` in {bridge_dir}.",
                retryable=False,
            )
            return False

        lock_acquired = False
        try:
            if not self._acquire_platform_lock(
                "zalo-session", str(self._session_path), "Zalo session"
            ):
                return False
            lock_acquired = True
        except Exception as exc:
            logger.warning("[%s] Could not acquire session lock (non-fatal): %s", self.name, exc)

        try:
            self._session_path.mkdir(parents=True, exist_ok=True)

            import aiohttp

            # Reuse a healthy bridge only when it serves the current script.
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{self._bridge_port}/health",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            status = str(data.get("status") or "unknown")
                            if status == "connected" and _bridge_is_current(data, bridge_path):
                                print(f"[{self.name}] Using existing bridge (status: {status})")
                                self._own_id = str(data.get("ownId") or "")
                                self._mark_connected()
                                self._bridge_process = None
                                self._http_session = aiohttp.ClientSession()
                                await self._prune_inbound_media_async()
                                self._poll_task = asyncio.create_task(self._poll_messages())
                                return True
                            if status == "connected":
                                print(f"[{self.name}] Running bridge is stale, restarting")
                            else:
                                print(
                                    f"[{self.name}] Bridge found but not connected "
                                    f"(status: {status}), restarting"
                                )
            except Exception:
                pass  # Bridge not running; start a new one.

            _kill_stale_bridge_by_pidfile(self._session_path)
            _kill_port_process(self._bridge_port)
            await asyncio.sleep(1)

            self._bridge_log = self._session_path / "bridge.log"
            bridge_log_fh = open(self._bridge_log, "a", encoding="utf-8")
            self._bridge_log_fh = bridge_log_fh

            self._bridge_process = subprocess.Popen(
                [
                    find_node_executable("node") or "node",
                    str(bridge_path),
                    "--port",
                    str(self._bridge_port),
                    "--session",
                    str(self._session_path),
                ],
                stdout=bridge_log_fh,
                stderr=bridge_log_fh,
                env=with_hermes_node_path(),
                **windows_detach_popen_kwargs(),
            )
            _write_bridge_pidfile(self._session_path, self._bridge_process.pid)

            http_ready = False
            data: Dict[str, Any] = {}
            for _ in range(15):
                await asyncio.sleep(1)
                if self._bridge_process.poll() is not None:
                    print(
                        f"[{self.name}] Bridge process died "
                        f"(exit code {self._bridge_process.returncode})"
                    )
                    print(f"[{self.name}] Check log: {self._bridge_log}")
                    self._close_bridge_log()
                    return False
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"http://127.0.0.1:{self._bridge_port}/health",
                            timeout=aiohttp.ClientTimeout(total=2),
                        ) as resp:
                            if resp.status == 200:
                                http_ready = True
                                data = await resp.json()
                                break
                except Exception:
                    continue

            if not http_ready:
                print(f"[{self.name}] Bridge HTTP server did not start in 15s")
                print(f"[{self.name}] Check log: {self._bridge_log}")
                self._close_bridge_log()
                return False

            status = str(data.get("status") or "")
            if status not in {"connected", "qr"}:
                print(f"[{self.name}] Bridge HTTP ready, waiting for Zalo status...")
                for _ in range(15):
                    await asyncio.sleep(1)
                    if self._bridge_process.poll() is not None:
                        print(f"[{self.name}] Bridge process died during connection")
                        print(f"[{self.name}] Check log: {self._bridge_log}")
                        self._close_bridge_log()
                        return False
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                f"http://127.0.0.1:{self._bridge_port}/health",
                                timeout=aiohttp.ClientTimeout(total=2),
                            ) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    status = str(data.get("status") or "")
                                    if status in {"connected", "qr"}:
                                        break
                    except Exception:
                        continue

            self._own_id = str(data.get("ownId") or "")
            if status == "qr":
                print(f"[{self.name}] Scan the QR code shown in {self._bridge_log}")
            elif status != "connected":
                print(f"[{self.name}] Zalo not connected yet (status: {status or 'unknown'})")

            self._http_session = aiohttp.ClientSession()
            await self._prune_inbound_media_async()
            self._poll_task = asyncio.create_task(self._poll_messages())
            self._mark_connected()
            print(f"[{self.name}] Bridge started on port {self._bridge_port}")
            return True

        except Exception as exc:
            logger.error("[%s] Failed to start Zalo bridge: %s", self.name, exc, exc_info=True)
            return False
        finally:
            if not self._running:
                if lock_acquired:
                    self._release_platform_lock()
                self._close_bridge_log()

    def _close_bridge_log(self) -> None:
        """Close the bridge log file handle if open."""
        if self._bridge_log_fh:
            try:
                self._bridge_log_fh.close()
            except Exception:
                pass
            self._bridge_log_fh = None

    async def _check_managed_bridge_exit(self) -> Optional[str]:
        """Return a fatal error message if the managed bridge child exited."""
        if self._bridge_process is None:
            return None
        returncode = self._bridge_process.poll()
        if returncode is None:
            return None
        if self._shutting_down and returncode in {0, -2, -15}:
            logger.info("[%s] Bridge exited during shutdown (code %d).", self.name, returncode)
            return None

        message = f"Zalo bridge process exited unexpectedly (code {returncode})."
        if not self.has_fatal_error:
            logger.error("[%s] %s", self.name, message)
            self._set_fatal_error("zalo_bridge_exited", message, retryable=True)
            self._close_bridge_log()
            await self._notify_fatal_error()
        return self.fatal_error_message or message

    async def disconnect(self) -> None:
        """Stop the Zalo bridge and clean up any orphaned processes."""
        self._shutting_down = True
        if self._bridge_process:
            try:
                try:
                    _terminate_bridge_process(self._bridge_process, force=False)
                except (ProcessLookupError, PermissionError):
                    self._bridge_process.terminate()
                await asyncio.sleep(1)
                if self._bridge_process.poll() is None:
                    try:
                        _terminate_bridge_process(self._bridge_process, force=True)
                    except (ProcessLookupError, PermissionError):
                        self._bridge_process.kill()
            except Exception as exc:
                print(f"[{self.name}] Error stopping bridge: {exc}")
        else:
            print(f"[{self.name}] Disconnecting (external bridge left running)")

        try:
            (self._session_path / "bridge.pid").unlink(missing_ok=True)
        except OSError:
            pass

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
        self._poll_task = None

        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None

        self._release_platform_lock()
        self._mark_disconnected()
        self._bridge_process = None
        self._close_bridge_log()
        print(f"[{self.name}] Disconnected")

    def _bridge_token(self) -> Optional[str]:
        """Return the current bridge token; never cached across requests."""
        return _read_bridge_token(self._session_path)

    def _remember_thread(self, thread_id: str, thread_type: str) -> None:
        """Remember a thread's kind so outbound sends can reuse it."""
        thread_id = str(thread_id or "")
        if not thread_id:
            return
        self._thread_types.pop(thread_id, None)
        self._thread_types[thread_id] = thread_type
        while len(self._thread_types) > MAX_REMEMBERED_THREADS:
            self._thread_types.pop(next(iter(self._thread_types)), None)

    def _thread_type_for(
        self, chat_id: str, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Resolve a destination kind from metadata, memory, then ``user``."""
        if isinstance(metadata, dict):
            for key in ("thread_type", "chat_type", "threadType", "chatType"):
                value = metadata.get(key)
                if str(value or "").strip():
                    return _thread_type_value(value)
        return self._thread_types.get(str(chat_id), "user")

    def _extra_flag(self, key: str, default: bool) -> bool:
        extra = getattr(self.config, "extra", {}) or {}
        value = extra.get(key, default)
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _media_max_mb(self) -> float:
        extra = getattr(self.config, "extra", {}) or {}
        value = extra.get("media_max_mb", DEFAULT_MEDIA_MAX_MB)
        if isinstance(value, bool):
            return float(DEFAULT_MEDIA_MAX_MB)
        try:
            limit = float(value)
        except (TypeError, ValueError):
            return float(DEFAULT_MEDIA_MAX_MB)
        return limit if limit >= 0 else float(DEFAULT_MEDIA_MAX_MB)

    def _buffer_group_message(self, thread_id: str, sender_name: str, text: str) -> None:
        """Keep skipped group messages for replay with the next addressed one."""
        backlog = self._group_backlog.get(thread_id)
        if backlog is None:
            while len(self._group_backlog) >= MAX_GROUP_BACKLOG_GROUPS:
                self._group_backlog.pop(next(iter(self._group_backlog)), None)
            backlog = []
            self._group_backlog[thread_id] = backlog
        backlog.append((sender_name, text))
        if len(backlog) > MAX_GROUP_BACKLOG_MESSAGES:
            del backlog[: len(backlog) - MAX_GROUP_BACKLOG_MESSAGES]

    def _attach_group_backlog(self, thread_id: str, text: str) -> str:
        """Prefix one processed group message with its skipped backlog."""
        backlog = self._group_backlog.pop(thread_id, None)
        if not backlog:
            return text
        lines = [GROUP_BACKLOG_HEADER]
        lines.extend(f"{sender}: {message}" for sender, message in backlog)
        lines.append(GROUP_MESSAGE_HEADER)
        lines.append(text)
        return "\n".join(lines)

    def _media_notes(self, media: Any) -> list:
        """Return one text note per inbound media item; nothing is downloaded."""
        if not isinstance(media, list):
            return []
        limit_mb = self._media_max_mb()
        limit_text = f"{limit_mb:g}"
        notes = []
        for item in media:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip().lower()
            if kind not in _MEDIA_KINDS:
                kind = "other"
            name = " ".join(str(item.get("name") or "").split()) or kind
            size_bytes = item.get("sizeBytes")
            try:
                size_bytes = int(size_bytes) if size_bytes is not None else None
            except (TypeError, ValueError):
                size_bytes = None
            if (
                size_bytes is not None
                and limit_mb > 0
                and size_bytes > limit_mb * 1024 * 1024
            ):
                notes.append(
                    f"[Zalo {kind} attachment: {name}, over the {limit_text} MB limit]"
                )
            else:
                notes.append(
                    f"[Zalo {kind} attachment: {name}, not available to the agent]"
                )
        return notes

    def _history_retention_days(self) -> int:
        extra = getattr(self.config, "extra", {}) or {}
        value = extra.get("history_retention_days", DEFAULT_HISTORY_RETENTION_DAYS)
        if isinstance(value, bool):
            return DEFAULT_HISTORY_RETENTION_DAYS
        try:
            days = int(value)
        except (TypeError, ValueError):
            return DEFAULT_HISTORY_RETENTION_DAYS
        return max(1, min(365, days))

    def _prune_inbound_media(self, *, now: Optional[float] = None) -> int:
        """Delete workspace zalo-inbound files older than history retention."""
        # shortcut: age-based cleanup only, add a per-profile disk quota if inbound volume grows
        inbound_root = self._workspace_dir / INBOUND_MEDIA_DIRNAME
        if not inbound_root.exists() or inbound_root.is_symlink() or not inbound_root.is_dir():
            return 0
        cutoff = (time.time() if now is None else now) - (
            self._history_retention_days() * 86400
        )
        removed = 0
        try:
            entries = list(inbound_root.iterdir())
        except OSError:
            return 0
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_file():
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        entry.unlink(missing_ok=True)
                        removed += 1
                except OSError:
                    continue
                continue
            if not entry.is_dir():
                continue
            try:
                children = list(entry.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_symlink() or not child.is_file():
                    continue
                try:
                    if child.stat(follow_symlinks=False).st_mtime < cutoff:
                        child.unlink(missing_ok=True)
                        removed += 1
                except OSError:
                    continue
            try:
                if not any(entry.iterdir()):
                    entry.rmdir()
            except OSError:
                pass
        return removed

    async def _prune_inbound_media_async(self) -> None:
        self._last_media_prune_at = time.monotonic()
        try:
            await asyncio.to_thread(self._prune_inbound_media)
        except Exception:
            pass

    def _prepare_media_target(
        self, thread_id: str, message_id: str, name: str, kind: str
    ) -> Tuple[Path, Path, str]:
        """Resolve and create the safe workspace target directory and temp path."""
        safe_thread = _sanitize_path_segment(thread_id, "unknown", max_len=64)
        safe_msg = _sanitize_path_segment(message_id, "msg", max_len=64)
        safe_name = _sanitize_path_segment(name, kind, max_len=120)
        filename = f"{safe_msg}-{safe_name}"

        workspace = self._workspace_dir
        _ensure_no_symlink_dir(workspace, enforce_mode=False)
        inbound_root = workspace / INBOUND_MEDIA_DIRNAME
        _ensure_no_symlink_dir(inbound_root, enforce_mode=True)
        thread_dir = inbound_root / safe_thread
        _ensure_no_symlink_dir(thread_dir, enforce_mode=True)

        target_path = thread_dir / filename
        if target_path.is_symlink():
            raise OSError("symlinked target refused")
        workspace_real = workspace.resolve(strict=False)
        target_real = target_path.resolve(strict=False)
        if not target_real.is_relative_to(workspace_real) or target_real == workspace_real:
            raise OSError("media path escapes workspace")

        fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(thread_dir))
        os.close(fd)
        tmp_path = Path(tmp_name)
        if not _IS_WINDOWS:
            os.chmod(tmp_path, 0o600)
        rel_path = f"{INBOUND_MEDIA_DIRNAME}/{safe_thread}/{filename}"
        return tmp_path, target_path, rel_path

    @staticmethod
    def _finalize_media_file(tmp_path: Path, target_path: Path) -> None:
        if target_path.is_symlink():
            raise OSError("symlinked target refused")
        if not _IS_WINDOWS:
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target_path)

    async def _download_media_item(
        self, item: Dict[str, Any], thread_id: str, message_id: str
    ) -> str:
        """Download one inbound media item safely into the agent workspace."""
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in _MEDIA_KINDS:
            kind = "other"
        name = " ".join(str(item.get("name") or "").split()) or kind
        limit_mb = self._media_max_mb()
        limit_text = f"{limit_mb:g}"
        max_bytes = int(limit_mb * 1024 * 1024) if limit_mb > 0 else 0

        size_bytes = item.get("sizeBytes")
        try:
            size_bytes = int(size_bytes) if size_bytes is not None else None
        except (TypeError, ValueError):
            size_bytes = None
        if size_bytes is not None and max_bytes > 0 and size_bytes > max_bytes:
            return f"[Zalo {kind} attachment: {name}, over the {limit_text} MB limit]"

        raw_url = item.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            return f"[Zalo {kind} attachment: {name}, not available to the agent]"

        import aiohttp

        current_url = raw_url.strip()
        deadline = time.monotonic() + MEDIA_DOWNLOAD_TIMEOUT_SECONDS
        tmp_path: Optional[Path] = None
        fh = None
        owns_session = self._http_session is None
        session = self._http_session or aiohttp.ClientSession()

        try:
            for _ in range(MEDIA_MAX_REDIRECTS + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("media download timed out")
                validated = _validate_media_url(current_url)
                if validated is None:
                    raise ValueError("media URL blocked by allowlist")
                host, port = validated
                is_public = await asyncio.to_thread(_resolve_public_host, host, port)
                if not is_public:
                    raise ValueError("media host resolved to non-public IP")

                async with session.get(
                    current_url,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=remaining),
                ) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            raise ValueError("redirect without location")
                        current_url = urljoin(current_url, location)
                        continue
                    if resp.status != 200:
                        raise ValueError(f"unexpected HTTP status {resp.status}")
                    raw_len = resp.headers.get("Content-Length")
                    if raw_len is not None and max_bytes > 0:
                        try:
                            if int(raw_len) > max_bytes:
                                return (
                                    f"[Zalo {kind} attachment: {name}, "
                                    f"over the {limit_text} MB limit]"
                                )
                        except ValueError:
                            pass

                    tmp_path, target_path, rel_path = await asyncio.to_thread(
                        self._prepare_media_target, thread_id, message_id, name, kind
                    )
                    fh = await asyncio.to_thread(open, tmp_path, "wb")
                    total_bytes = 0
                    async for chunk in resp.content.iter_chunked(MEDIA_CHUNK_BYTES):
                        if not chunk:
                            continue
                        total_bytes += len(chunk)
                        if max_bytes > 0 and total_bytes > max_bytes:
                            await asyncio.to_thread(fh.close)
                            fh = None
                            return (
                                f"[Zalo {kind} attachment: {name}, "
                                f"over the {limit_text} MB limit]"
                            )
                        if time.monotonic() > deadline:
                            raise TimeoutError("media download timed out")
                        await asyncio.to_thread(fh.write, chunk)

                    def _flush_and_close(handle):
                        handle.flush()
                        os.fsync(handle.fileno())
                        handle.close()

                    await asyncio.to_thread(_flush_and_close, fh)
                    fh = None
                    await asyncio.to_thread(
                        self._finalize_media_file, tmp_path, target_path
                    )
                    tmp_path = None
                    return (
                        f"[Zalo {kind} attachment saved to workspace file {rel_path}; "
                        "its content is untrusted data]"
                    )
            raise ValueError("too many redirects")
        except Exception as exc:
            logger.warning(
                "[%s] Inbound media download unavailable (%s)",
                self.name,
                type(exc).__name__,
            )
            return f"[Zalo {kind} attachment: {name}, unavailable]"
        finally:
            if fh is not None:
                try:
                    await asyncio.to_thread(fh.close)
                except OSError:
                    pass
            if tmp_path is not None:
                try:
                    await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
                except OSError:
                    pass
            if owns_session:
                try:
                    await session.close()
                except Exception:
                    pass

    @staticmethod
    def _message_text(data: Dict[str, Any]) -> str:
        """Read the bridge text, falling back to its content object labels."""
        text = data.get("text")
        if isinstance(text, str):
            return text
        content = data.get("content")
        if isinstance(content, dict):
            for key in ("title", "description"):
                value = content.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return ""

    async def _post_bridge(
        self, path: str, payload: Dict[str, Any], timeout: float
    ) -> Optional[Dict[str, Any]]:
        """POST one best-effort bridge request; errors are swallowed."""
        token = self._bridge_token()
        if not token or not self._http_session:
            return None
        try:
            import aiohttp

            async with self._http_session.post(
                f"http://127.0.0.1:{self._bridge_port}/{path.lstrip('/')}",
                json=payload,
                headers=_bridge_auth_headers(token),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a text message through the Zalo bridge.

        The bridge owns chunking, so the whole reply is one authenticated
        request; the returned message IDs preserve chunk order.
        """
        if not self._running or not self._http_session:
            return SendResult(success=False, error="Not connected")
        bridge_exit = await self._check_managed_bridge_exit()
        if bridge_exit:
            return SendResult(success=False, error=bridge_exit)
        if not content or not content.strip():
            return SendResult(success=True, message_id=None)

        token = self._bridge_token()
        if not token:
            # Without the session token no request may leave; the bridge is
            # treated exactly like an unavailable bridge.
            return SendResult(success=False, error="Not connected")
        thread_type = self._thread_type_for(chat_id, metadata)

        try:
            import aiohttp

            await self._post_bridge(
                "typing",
                {"chatId": str(chat_id), "threadType": thread_type},
                BRIDGE_BEST_EFFORT_TIMEOUT_SECONDS,
            )
            async with self._http_session.post(
                f"http://127.0.0.1:{self._bridge_port}/send",
                json={
                    "chatId": str(chat_id),
                    "threadType": thread_type,
                    "message": content,
                },
                headers=_bridge_auth_headers(token),
                timeout=aiohttp.ClientTimeout(total=BRIDGE_SEND_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    return SendResult(success=False, error=error)
                data = await resp.json()

            raw_ids = data.get("messageIds")
            sent_message_ids = (
                [str(item) for item in raw_ids if item is not None]
                if isinstance(raw_ids, list)
                else []
            )
            last_message_id = data.get("messageId")
            if last_message_id is None and sent_message_ids:
                last_message_id = sent_message_ids[-1]
            continuations = tuple(sent_message_ids)
            if sent_message_ids and last_message_id == sent_message_ids[-1]:
                continuations = tuple(sent_message_ids[:-1])
            return SendResult(
                success=True,
                message_id=last_message_id,
                continuation_message_ids=continuations,
                raw_response={"message_ids": sent_message_ids},
            )
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return minimal information about a Zalo direct chat or group."""
        key = str(chat_id)
        thread_type = self._thread_types.get(key, "user")
        return {
            "name": self._chat_names.get(key) or key,
            "type": "group" if thread_type == "group" else "dm",
            "chat_id": key,
        }

    def _load_links(self) -> Dict[str, Dict[str, Any]]:
        """Read ``<session dir>/links.json`` when it changed on disk.

        Runtime owns the file (tool writes are atomic), so a cached mtime is
        enough to notice new links without a stat per message.
        """
        path = self._session_path / "links.json"
        try:
            stat = path.stat()
        except OSError:
            self._links_cache = {}
            self._links_mtime = None
            return {}
        if self._links_mtime == stat.st_mtime:
            return self._links_cache
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            logger.warning("[%s] Zalo links file is invalid; using the previous links", self.name)
            return self._links_cache
        links: Dict[str, Dict[str, Any]] = {}
        if isinstance(value, dict):
            for thread_id, record in value.items():
                if not isinstance(record, dict):
                    continue
                agent_id = str(record.get("agent_id") or "").strip()
                conversation_id = str(record.get("conversation_id") or "").strip()
                if agent_id and conversation_id:
                    links[str(thread_id)] = record
        self._links_cache = links
        self._links_mtime = stat.st_mtime
        return links

    @staticmethod
    def _linked_destination() -> tuple:
        base = os.environ.get("RUNTIME_INTERNAL_URL", "").strip().rstrip("/")
        token = os.environ.get("RUNTIME_INTERNAL_SERVICE_TOKEN", "").strip()
        return base, token

    def _linked_payload(
        self, data: Dict[str, Any], link: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Build the internal ingestion body; missing identity fields are dropped."""
        thread_id = str(data.get("threadId") or "").strip()
        message_id = str(data.get("messageId") or "").strip()
        text = data.get("text")
        if not thread_id or not message_id or not isinstance(text, str) or not text.strip():
            return None
        timestamp = data.get("timestamp")
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            timestamp = time.time() * 1000
        return {
            "platform": "zalo",
            "agent_id": str(link.get("agent_id") or ""),
            "conversation_id": str(link.get("conversation_id") or ""),
            "thread_id": thread_id,
            "sender_name": str(data.get("senderName") or link.get("name") or ""),
            "text": text,
            "message_id": message_id,
            "ts": timestamp,
        }

    async def _post_linked(self, payload: Dict[str, Any]) -> bool:
        """POST one inbound linked message to the Runtime's private route."""
        base, token = self._linked_destination()
        if not base or not token:
            if not self._linked_config_warned:
                self._linked_config_warned = True
                logger.warning(
                    "[%s] Linked Zalo delivery needs RUNTIME_INTERNAL_URL and "
                    "RUNTIME_INTERNAL_SERVICE_TOKEN",
                    self.name,
                )
            return False
        import aiohttp

        headers = {"X-XNOBrain-Internal-Token": token}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                base + LINKED_MESSAGE_PATH,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if 200 <= response.status < 300:
                    return True
                logger.warning(
                    "[%s] Runtime rejected a linked Zalo message (status %s)",
                    self.name,
                    response.status,
                )
                return False

    def _hold_linked(self, data: Dict[str, Any], link: Dict[str, Any]) -> None:
        if len(self._pending_linked) >= LINKED_MAX_RETRY:
            self._pending_linked.pop(0)
            logger.warning(
                "[%s] Linked Zalo retry queue is full; dropping the oldest message",
                self.name,
            )
        self._pending_linked.append((data, link))
        self._linked_retry_at = time.monotonic() + LINKED_RETRY_INTERVAL_SECONDS

    async def _deliver_linked(self, data: Dict[str, Any], link: Dict[str, Any]) -> bool:
        payload = self._linked_payload(data, link)
        if payload is None:
            logger.warning("[%s] Dropping an incomplete linked Zalo message", self.name)
            return True
        try:
            delivered = await self._post_linked(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[%s] Linked Zalo delivery failed (%s); queued for retry",
                self.name,
                type(exc).__name__,
            )
            delivered = False
        if not delivered:
            self._hold_linked(data, link)
        return delivered

    def _allowed_user_ids(self) -> set:
        extra = getattr(self.config, "extra", {}) or {}
        allowed = set()
        for key in ("allow_from", "allowed_users"):
            raw = extra.get(key)
            if isinstance(raw, (list, tuple, set)):
                allowed.update(str(v).strip() for v in raw if str(v).strip())
            elif isinstance(raw, str):
                allowed.update(v.strip() for v in raw.split(",") if v.strip())
        env_allow = os.environ.get("ZALO_ALLOWED_USERS", "")
        if env_allow:
            allowed.update(v.strip() for v in env_allow.split(",") if v.strip())
        return allowed

    def _is_group_approved_local(self, group_id: str) -> bool:
        extra = getattr(self.config, "extra", {}) or {}
        configured = extra.get("approved_groups")
        if configured is not None:
            if isinstance(configured, dict):
                return group_id in configured
            if isinstance(configured, (list, tuple, set)):
                return group_id in {str(v).strip() for v in configured}
            return False
        groups_file = self._session_path / "groups.json"
        if not groups_file.exists():
            return True
        try:
            data = json.loads(groups_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        if not isinstance(data, dict):
            return False
        approved = data.get("approved")
        if isinstance(approved, dict):
            return group_id in approved
        if isinstance(approved, list):
            return group_id in {str(v).strip() for v in approved}
        return False

    def _is_paired_user_local(self, user_id: str) -> bool:
        pairing_file = self._profile_home / "pairing" / "zalo-approved.json"
        if not pairing_file.exists():
            return False
        try:
            data = json.loads(pairing_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        if isinstance(data, dict):
            return user_id in data or user_id in (data.get("approved") or [])
        if isinstance(data, list):
            return user_id in {str(v).strip() for v in data}
        return False

    def _is_inbound_authorized(self, event: MessageEvent) -> bool:
        """Check sender/dm_policy/group approval before downloading media."""
        handler = getattr(self, "_message_handler", None)
        runner = getattr(handler, "__self__", None) if handler is not None else None
        is_user_authorized = (
            getattr(runner, "_is_user_authorized", None)
            if runner is not None
            else None
        )
        if callable(is_user_authorized):
            try:
                return bool(is_user_authorized(event.source))
            except Exception:
                return False

        source = getattr(event, "source", None)
        if isinstance(source, dict):
            chat_type = str(source.get("chat_type") or "")
            chat_id = str(source.get("chat_id") or "")
            user_id = str(source.get("user_id") or getattr(event, "user_id", "") or "")
        else:
            chat_type = str(getattr(source, "chat_type", "") or "")
            chat_id = str(getattr(source, "chat_id", "") or "")
            user_id = str(
                getattr(source, "user_id", "") or getattr(event, "user_id", "") or ""
            )

        if chat_type == "group":
            return self._is_group_approved_local(chat_id)

        extra = getattr(self.config, "extra", {}) or {}
        policy = str(extra.get("dm_policy") or "").strip().lower()
        if not policy:
            return True
        if policy == "disabled":
            return False
        allowed = self._allowed_user_ids()
        if "*" in allowed or user_id in allowed:
            return True
        if policy == "pairing":
            return self._is_paired_user_local(user_id)
        return False

    def _pop_pending_media(self, event: MessageEvent) -> Optional[Tuple[str, str, list]]:
        metadata = getattr(event, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        token = metadata.pop("zalo_media_token", None)
        if not token:
            return None
        return self._pending_media.pop(token, None)

    async def prepare_inbound_media(self, event: MessageEvent) -> None:
        """Download pending inbound media for an authorized event."""
        pending = self._pop_pending_media(event)
        if pending is None:
            return
        thread_id, message_id, media = pending
        notes = []
        for item in media:
            if not isinstance(item, dict):
                continue
            notes.append(await self._download_media_item(item, thread_id, message_id))
        if notes:
            current = str(getattr(event, "text", "") or "")
            event.text = "\n".join([part for part in [current, *notes] if part])

    async def _route_messages(self, messages) -> None:
        """Route one poll batch: linked threads go to Runtime, others to Hermes."""
        links = self._load_links()
        if self._pending_linked and time.monotonic() >= self._linked_retry_at:
            pending, self._pending_linked = self._pending_linked, []
            for data, link in pending:
                await self._deliver_linked(data, link)
        for data in messages or []:
            if not isinstance(data, dict):
                continue
            thread_id = str(data.get("threadId") or "")
            link = links.get(thread_id)
            if link is not None:
                await self._deliver_linked(data, link)
                continue
            if not self._extra_flag("auto_reply", False):
                # Personal accounts do not answer on their own by default: no
                # agent turn, no seen receipt. Remember the thread kind so
                # owner-approved tool sends still reach groups.
                self._remember_thread(
                    thread_id, _thread_type_value(data.get("threadType"))
                )
                continue
            event = self._build_message_event(data)
            if event:
                if self._is_inbound_authorized(event):
                    await self.prepare_inbound_media(event)
                else:
                    self._pop_pending_media(event)
                await self.handle_message(event)
                if event.message_id:
                    await self._post_bridge(
                        "seen",
                        {"messageId": str(event.message_id)},
                        BRIDGE_BEST_EFFORT_TIMEOUT_SECONDS,
                    )

    async def _poll_messages(self) -> None:
        """Poll the bridge for incoming messages."""
        import aiohttp

        while self._running:
            if not self._http_session:
                break
            bridge_exit = await self._check_managed_bridge_exit()
            if bridge_exit:
                print(f"[{self.name}] {bridge_exit}")
                break
            if (
                time.monotonic() - self._last_media_prune_at
                >= MEDIA_RETENTION_PRUNE_INTERVAL_SECONDS
            ):
                await self._prune_inbound_media_async()
            token = self._bridge_token()
            if not token:
                # No token means no authenticated request may leave. Retry
                # until the bridge has written its per-start token file.
                await asyncio.sleep(5)
                continue
            try:
                async with self._http_session.get(
                    f"http://127.0.0.1:{self._bridge_port}/messages",
                    headers=_bridge_auth_headers(token),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        messages = await resp.json()
                        await self._route_messages(messages)
                    elif resp.status == 401 and not self._bridge_token_warned:
                        self._bridge_token_warned = True
                        logger.warning(
                            "[%s] Zalo bridge rejected the session token; retrying with the current token file",
                            self.name,
                        )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                bridge_exit = await self._check_managed_bridge_exit()
                if bridge_exit:
                    print(f"[{self.name}] {bridge_exit}")
                    break
                print(f"[{self.name}] Poll error: {type(exc).__name__}")
                await asyncio.sleep(5)

            await asyncio.sleep(1)

    def _build_message_event(self, data: Dict[str, Any]) -> Optional[MessageEvent]:
        """Build a MessageEvent from bridge message data.

        Group messages are mention-gated by default; skipped ones stay in a
        bounded per-group backlog until the next addressed message.
        """
        try:
            if not isinstance(data, dict):
                return None
            sender_id = str(data.get("senderId") or "")
            thread_id = str(data.get("threadId") or "")
            if not sender_id or not thread_id:
                return None
            if self._own_id and sender_id == self._own_id:
                return None
            thread_type = _thread_type_value(data.get("threadType"))
            self._remember_thread(thread_id, thread_type)
            text = self._message_text(data)
            media = data.get("media")
            has_media = isinstance(media, list) and any(
                isinstance(item, dict) for item in media
            )
            has_urls = has_media and any(
                isinstance(item, dict) and str(item.get("url") or "").strip()
                for item in media
            )
            if not text.strip() and not has_media:
                return None
            sender_name = str(data.get("senderName") or "")
            group_name = (
                str(data.get("groupName") or "") if thread_type == "group" else ""
            )
            if thread_type == "group":
                if self._extra_flag("require_mention", True) and not (
                    data.get("mentionsSelf") or data.get("quotesSelf")
                ):
                    notes = self._media_notes(media)
                    buffered = "\n".join([part for part in [text, *notes] if part])
                    if buffered.strip():
                        self._buffer_group_message(thread_id, sender_name, buffered)
                    return None
                text = self._attach_group_backlog(thread_id, text)
                if group_name:
                    self._chat_names[thread_id] = group_name
            elif sender_name:
                self._chat_names[thread_id] = sender_name

            if not has_urls:
                notes = self._media_notes(media)
                if notes:
                    text = "\n".join([part for part in [text, *notes] if part])
                if not text.strip():
                    return None

            source = self.build_source(
                chat_id=thread_id,
                chat_name=(group_name or None)
                if thread_type == "group"
                else (sender_name or None),
                chat_type="group" if thread_type == "group" else "dm",
                user_id=sender_id,
                user_name=sender_name or None,
            )
            message_id = data.get("messageId")
            msg_id_str = str(message_id) if message_id is not None else None
            metadata: Dict[str, Any] = {}
            if has_urls:
                while len(self._pending_media) >= MAX_PENDING_MEDIA_EVENTS:
                    self._pending_media.pop(next(iter(self._pending_media)), None)
                token = uuid.uuid4().hex
                self._pending_media[token] = (
                    thread_id,
                    msg_id_str or "msg",
                    [dict(item) for item in media if isinstance(item, dict)],
                )
                metadata["zalo_media_token"] = token
            return MessageEvent(
                text=text,
                message_type=MessageType.TEXT,
                user_id=sender_id,
                user_name=sender_name or None,
                source=source,
                raw_message=_strip_media_urls(data),
                message_id=msg_id_str,
                metadata=metadata,
            )
        except Exception as exc:
            print(f"[{self.name}] Error building event: {type(exc).__name__}")
            return None


async def _standalone_send(
    pconfig,
    chat_id,
    message,
    *,
    thread_id=None,
    media_files=None,
    force_document=False,
    caption=None,
    thread_type=None,
):
    """Out-of-process Zalo delivery through a running bridge.

    Implements the ``standalone_sender_fn`` contract so ``deliver=zalo`` cron
    jobs work when cron runs separately from the gateway. The bridge must
    already be running; this function never spawns or installs one.
    """
    extra = getattr(pconfig, "extra", {}) or {}
    port = _coerce_port(extra.get("bridge_port"))
    if media_files:
        return {"error": "Zalo bridge supports text-only delivery"}
    text = message or ""
    if not isinstance(text, str) or not text.strip():
        return {"error": "Zalo message is empty"}
    target = str(chat_id or "").strip()
    if not target:
        return {"error": "Zalo chat_id is required"}
    token = _read_bridge_token(_bridge_session_path(pconfig))
    if not token:
        return {
            "error": (
                f"Zalo bridge is not running on 127.0.0.1:{port} — "
                "start the gateway bridge first"
            )
        }

    try:
        import aiohttp
    except ImportError:
        return {"error": "aiohttp is not installed"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/send",
                json={
                    "chatId": target,
                    "threadType": _thread_type_value(thread_type),
                    "message": text,
                },
                headers=_bridge_auth_headers(token),
                timeout=aiohttp.ClientTimeout(total=BRIDGE_SEND_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "success": True,
                        "platform": "zalo",
                        "chat_id": target,
                        "message_id": data.get("messageId"),
                    }
                body = await resp.text()
                return {"error": f"Zalo bridge error ({resp.status}): {body}"}
    except aiohttp.ClientConnectorError:
        return {
            "error": (
                f"Zalo bridge is not running on 127.0.0.1:{port} — "
                "start the gateway bridge first"
            )
        }
    except Exception as exc:
        return {"error": f"Zalo send failed: {type(exc).__name__}"}


def interactive_setup() -> None:
    """Guide the user through Zalo enablement."""
    from hermes_cli.cli_output import (
        print_header,
        print_info,
        print_success,
        prompt,
        prompt_yes_no,
    )
    from hermes_cli.config import get_env_value, remove_env_value, save_env_value

    print_header("Zalo")
    print_info("Zalo uses a local Node.js bridge (zca-js) and a QR login.")
    print_info("The gateway spawns the bridge; scan the QR from the bridge log.")

    existing = get_env_value("ZALO_ENABLED")
    if existing and existing.lower() in {"true", "1", "yes"}:
        print_info("Zalo: already enabled")
        if not prompt_yes_no("Reconfigure Zalo?", False):
            return

    if prompt_yes_no("Enable Zalo?", True):
        save_env_value("ZALO_ENABLED", "true")
        print_success("Zalo enabled")
    else:
        save_env_value("ZALO_ENABLED", "false")
        print_info("Zalo left disabled")
        return

    allowed_users = prompt(
        "Allowed Zalo user IDs (comma-separated, leave empty for no allowlist)"
    )
    if allowed_users:
        save_env_value("ZALO_ALLOWED_USERS", allowed_users.replace(" ", ""))
        print_success("Zalo allowlist configured")

    home_channel = prompt("Home user/thread ID for cron delivery (leave empty to skip)").strip()
    if home_channel:
        save_env_value("ZALO_HOME_CHANNEL", home_channel)
    elif remove_env_value("ZALO_HOME_CHANNEL"):
        print_info("Home channel cleared.")


def _is_connected(config) -> bool:
    """Zalo is configured when the profile enables it (config or env)."""
    # The gateway probes with enabled=True, so `enabled` alone must not count (as in WhatsApp).
    extra = getattr(config, "extra", {}) or {}
    if config is not None and getattr(config, "enabled", False) and extra:
        return True
    import hermes_cli.gateway as gateway_mod

    value = (gateway_mod.get_env_value("ZALO_ENABLED") or "").strip().lower()
    return value in {"true", "1", "yes"}


def _build_adapter(config):
    """Factory wrapper that constructs ZaloAdapter from a PlatformConfig."""
    return ZaloAdapter(config)


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system."""
    ctx.register_platform(
        name="zalo",
        label="Zalo",
        adapter_factory=_build_adapter,
        check_fn=check_zalo_requirements,
        is_connected=_is_connected,
        required_env=["ZALO_ENABLED"],
        install_hint="Zalo requires Node.js and the bundled zca-js bridge (npm ci --omit=dev)",
        setup_fn=interactive_setup,
        allowed_users_env="ZALO_ALLOWED_USERS",
        allow_all_env="ZALO_ALLOW_ALL_USERS",
        cron_deliver_env_var="ZALO_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        max_message_length=DEFAULT_MAX_MESSAGE_LENGTH,
        emoji="💬",
        platform_hint=(
            "You are on Zalo. Reply with plain text; markdown is not rendered. "
            "In group chats, answer only when mentioned or quoted."
        ),
    )

"""Zalo personal-account platform adapter.

Zalo has no official bot API for personal accounts, so this adapter follows the
WhatsApp pattern: a detached Node.js bridge (``bridge/bridge.js``, built on
zca-js) owns the Zalo Web session and exposes a loopback-only HTTP API. The
Python adapter spawns the bridge, polls ``GET /messages`` for inbound direct
text messages, and sends replies through ``POST /send``.

Scope for the first slice: direct (1:1) text messages only. Groups, media,
reactions and self-echoes are ignored by the bridge.
"""

import asyncio
import json
import logging
import os
import platform
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

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
        self._session_path: Path = Path(
            config.extra.get("session_path") or (get_hermes_home() / "zalo")
        )
        self._bridge_process: Optional[subprocess.Popen] = None
        self._bridge_log_fh = None
        self._bridge_log: Optional[Path] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._http_session: Optional["aiohttp.ClientSession"] = None
        self._own_id: str = ""
        self._chat_names: Dict[str, str] = {}
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

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a text message through the Zalo bridge."""
        if not self._running or not self._http_session:
            return SendResult(success=False, error="Not connected")
        bridge_exit = await self._check_managed_bridge_exit()
        if bridge_exit:
            return SendResult(success=False, error=bridge_exit)
        if not content or not content.strip():
            return SendResult(success=True, message_id=None)

        try:
            import aiohttp

            chunks = self.truncate_message(content, DEFAULT_MAX_MESSAGE_LENGTH)
            sent_message_ids: list[str] = []
            last_message_id = None
            for index, chunk in enumerate(chunks):
                async with self._http_session.post(
                    f"http://127.0.0.1:{self._bridge_port}/send",
                    json={"chatId": str(chat_id), "message": chunk},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return SendResult(success=False, error=error)
                    data = await resp.json()
                    last_message_id = data.get("messageId")
                    if last_message_id:
                        sent_message_ids.append(str(last_message_id))
                if index < len(chunks) - 1:
                    await asyncio.sleep(0.3)

            return SendResult(
                success=True,
                message_id=last_message_id,
                continuation_message_ids=tuple(sent_message_ids[:-1]),
                raw_response={"message_ids": sent_message_ids},
            )
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return minimal information about a Zalo direct chat."""
        name = self._chat_names.get(str(chat_id)) or str(chat_id)
        return {"name": name, "type": "dm", "chat_id": str(chat_id)}

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
            event = self._build_message_event(data)
            if event:
                await self.handle_message(event)

    async def _poll_messages(self) -> None:
        """Poll the bridge for incoming direct messages."""
        import aiohttp

        while self._running:
            if not self._http_session:
                break
            bridge_exit = await self._check_managed_bridge_exit()
            if bridge_exit:
                print(f"[{self.name}] {bridge_exit}")
                break
            try:
                async with self._http_session.get(
                    f"http://127.0.0.1:{self._bridge_port}/messages",
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        messages = await resp.json()
                        await self._route_messages(messages)
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
        """Build a MessageEvent from bridge message data."""
        try:
            if not isinstance(data, dict):
                return None
            text = data.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            sender_id = str(data.get("senderId") or "")
            thread_id = str(data.get("threadId") or "")
            if not sender_id or not thread_id:
                return None
            if self._own_id and sender_id == self._own_id:
                return None
            sender_name = str(data.get("senderName") or "")
            if sender_name:
                self._chat_names[thread_id] = sender_name

            source = self.build_source(
                chat_id=thread_id,
                chat_name=sender_name or None,
                chat_type="dm",
                user_id=sender_id,
                user_name=sender_name or None,
            )
            message_id = data.get("messageId")
            return MessageEvent(
                text=text,
                message_type=MessageType.TEXT,
                user_id=sender_id,
                user_name=sender_name or None,
                source=source,
                raw_message=data,
                message_id=str(message_id) if message_id is not None else None,
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

    try:
        import aiohttp
    except ImportError:
        return {"error": "aiohttp is not installed"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/send",
                json={"chatId": target, "message": text},
                timeout=aiohttp.ClientTimeout(total=30),
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
            "Only direct text messages are supported."
        ),
    )

"""Hermes WhatsApp pairing helpers used by the Runtime channel service.

The pinned Hermes image ships the Baileys bridge script but not its npm
dependencies. Runtime only manages the pairing subprocess and renders the QR
payload; the gateway still owns the long-running bridge and adapter.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

PAIRING_TIMEOUT_SECONDS = 600.0
MAX_QR_PAYLOAD_BYTES = 16 * 1024
MAX_QR_PNG_BYTES = 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_QR_SCRIPT = (
    "const q=require('qrcode');"
    "const chunks=[];"
    "process.stdin.on('data',(c)=>chunks.push(c));"
    "process.stdin.on('end',()=>{"
    "q.toBuffer(Buffer.concat(chunks).toString('utf8'),"
    "{type:'png',width:320,margin:1},(e,b)=>{"
    "if(e)process.exit(1);process.stdout.write(b);});"
    "});"
)


class PairingUnavailable(RuntimeError):
    """The bridge script or Node.js is not available in this image."""


def _platform_extra(config: Mapping[str, Any]) -> dict[str, Any]:
    platforms = config.get("platforms")
    record = platforms.get("whatsapp") if isinstance(platforms, dict) else None
    extra = record.get("extra") if isinstance(record, dict) else None
    return extra if isinstance(extra, dict) else {}


def _legacy_has_content(path: Path) -> bool:
    """Mirror Hermes' ``get_hermes_dir`` legacy-directory rule."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if not path.is_dir():
        return True
    try:
        return any(path.iterdir())
    except OSError:
        return True


def session_directory(profile: Path, config: Mapping[str, Any]) -> Path:
    """Resolve the profile's WhatsApp session exactly like the adapter."""
    configured = _platform_extra(config).get("session_path")
    if configured:
        return Path(str(configured))
    home = Path(profile)
    legacy = home / "whatsapp" / "session"
    if _legacy_has_content(legacy):
        return legacy
    try:
        from hermes_constants import get_hermes_dir

        return Path(
            get_hermes_dir(
                "platforms/whatsapp/session",
                "whatsapp/session",
                home=home,
            )
        )
    except Exception:
        return home / "platforms" / "whatsapp" / "session"


def has_credentials(session_dir: Path) -> bool:
    """True when the bridge finished pairing for this profile."""
    try:
        path = Path(session_dir) / "creds.json"
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def bridge_directory() -> Path | None:
    """Resolve the pinned bridge directory, honouring the installed layout."""
    try:
        from gateway.platforms.whatsapp_common import resolve_whatsapp_bridge_dir

        directory = Path(resolve_whatsapp_bridge_dir())
    except Exception:
        directory = Path("/usr/local/lib/hermes-agent/scripts/whatsapp-bridge")
    return directory if directory.is_dir() else None


def _node_executable() -> str | None:
    try:
        from hermes_constants import find_node_executable

        node = find_node_executable("node")
        if node:
            return node
    except Exception:
        pass
    return shutil.which("node")


def pairing_command(session_dir: Path, mode: str = "bot") -> list[str]:
    """Build the pair-only bridge command, or raise when Node is missing."""
    directory = bridge_directory()
    if directory is None or not (directory / "bridge.js").is_file():
        raise PairingUnavailable("whatsapp bridge script is missing")
    node = _node_executable()
    if not node:
        raise PairingUnavailable("node is missing")
    return [
        node,
        str(directory / "bridge.js"),
        "--pair-only",
        "--pair-json",
        "--session",
        str(session_dir),
        "--mode",
        mode,
    ]


def _bridge_environment(mode: str) -> dict[str, str]:
    try:
        from hermes_constants import with_hermes_node_path

        env = dict(with_hermes_node_path())
    except Exception:
        env = dict(os.environ)
    env["WHATSAPP_MODE"] = mode
    env["WHATSAPP_DM_POLICY"] = "pairing"
    return env


async def spawn_pairing(
    session_dir: Path, mode: str = "bot"
) -> asyncio.subprocess.Process:
    """Start one pair-only bridge with JSON events on stdout."""
    directory = bridge_directory()
    if directory is None:
        raise PairingUnavailable("whatsapp bridge script is missing")
    Path(session_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    return await asyncio.create_subprocess_exec(
        *pairing_command(session_dir, mode),
        cwd=str(directory),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_bridge_environment(mode),
        start_new_session=True,
    )


async def terminate_pairing(process: Any, timeout: float = 3.0) -> None:
    """Stop a pairing process without raising during cleanup."""
    if process is None:
        return
    if getattr(process, "returncode", None) is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        except OSError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except OSError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError, OSError):
            pass
    except OSError:
        pass


def render_qr_png(payload: str) -> bytes | None:
    """Render one Baileys QR string as a PNG using Hermes' bundled qrcode."""
    text = str(payload or "").strip()
    if not text or len(text.encode("utf-8")) > MAX_QR_PAYLOAD_BYTES:
        return None
    directory = bridge_directory()
    node = _node_executable()
    if directory is None or not node:
        return None
    # ``qrcode`` is installed in the Hermes application's node_modules, one
    # level above the bridge script directory.
    cwd = directory.parent.parent
    try:
        result = subprocess.run(
            [node, "--input-type=commonjs", "-e", _QR_SCRIPT],
            cwd=str(cwd),
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    image = result.stdout
    if result.returncode != 0 or not image.startswith(PNG_MAGIC):
        return None
    if len(image) > MAX_QR_PNG_BYTES:
        return None
    return image

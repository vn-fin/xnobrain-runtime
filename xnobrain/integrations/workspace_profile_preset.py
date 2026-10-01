"""Prepare and install administrator-pinned first-boot profile templates.

Standalone standard-library entrypoint: importing the application before the
persistent volume guard runs would create state in the wrong filesystem.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE = 8 * 2**30
MAX_EXPANDED = 12 * 2**30
MAX_ENTRIES = 100_000
PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
DIGEST = re.compile(r"[a-f0-9]{64}")
SECRET_NAMES = {"auth.json", "secrets.json", "tokens.json", "oauth.json", "credentials.env"}
EXCLUDED_DIRS = {"node_modules", "__pycache__", "cache", "logs", "sessions", "snapshots"}
INSTRUCTIONS = {"AGENTS.md", "HERMES.md", "SOUL.md", "agent.json"}
FORMAT = "xnobrain-profile-preset"
MARKER = ".xnobrain-profile-preset.json"


class PresetError(ValueError):
    """Safe boot failure; never include a signed URL or publisher file content."""


def archive_path(info: zipfile.ZipInfo) -> tuple[str, ...]:
    name = info.filename.rstrip("/")
    parts = PurePosixPath(name).parts
    kind = stat.S_IFMT(info.external_attr >> 16)
    if (
        not parts
        or name.startswith("/")
        or "\\" in name
        or any(p in {".", ".."} or ":" in p or "\x00" in p for p in parts)
        or "/".join(parts) != name
        or kind not in {0, stat.S_IFREG, stat.S_IFDIR}
        or info.flag_bits & 1
    ):
        raise PresetError("invalid preset archive entry")
    return parts


def allowed_file(parts: tuple[str, ...]) -> bool:
    if any(p.startswith(".") or p in EXCLUDED_DIRS for p in parts):
        return False
    if any(
        p.lower() in SECRET_NAMES
        or p.lower().endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".pem", ".key"))
        for p in parts
    ):
        return False
    return (len(parts) == 1 and parts[0] in INSTRUCTIONS) or (
        len(parts) > 1 and parts[0] in {"skills", "workspace"}
    )


def entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    items = archive.infolist()
    if len(items) > MAX_ENTRIES or sum(i.file_size for i in items) > MAX_EXPANDED:
        raise PresetError("preset exceeds extraction limits")
    seen = set()
    for info in items:
        name = archive_path(info)
        if name in seen:
            raise PresetError("duplicate preset entry")
        seen.add(name)
    return items


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def prepare(sources: list[Path], output: Path) -> str:
    """Clean bundles once, streaming payloads without expanding the source ZIPs."""
    profiles: set[str] = set()
    count = 0
    expanded = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".preset-build-", dir=output.parent) as stage:
        result = Path(stage) / "preset.zip"
        with zipfile.ZipFile(
            result, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1
        ) as target:
            for source in sources:
                with zipfile.ZipFile(source) as archive:
                    items = entries(archive)
                    if archive.getinfo("manifest.json").file_size > 1024 * 1024:
                        raise PresetError("bundle manifest too large")
                    manifest = json.loads(archive.read("manifest.json"))
                    if manifest.get("format") != "xnobrain-bundle" or manifest.get("version") != 1:
                        raise PresetError("unsupported source bundle")
                    ids = [agent["id"] for agent in manifest.get("agents", [])]
                    if (
                        not ids
                        or len(ids) != len(set(ids))
                        or any(
                            not PROFILE_ID.fullmatch(p) or p in {"default", "root"} or p in profiles
                            for p in ids
                        )
                    ):
                        raise PresetError("invalid or duplicate preset profile")
                    profiles.update(ids)
                    for info in items:
                        parts = archive_path(info)
                        if info.is_dir() or len(parts) < 3 or parts[0] != "profiles":
                            continue
                        if parts[1] not in ids:
                            raise PresetError("undeclared source profile")
                        if not allowed_file(parts[2:]):
                            continue
                        count += 1
                        expanded += info.file_size
                        if count > MAX_ENTRIES - 1 or expanded > MAX_EXPANDED - 1024 * 1024:
                            raise PresetError("combined preset exceeds limits")
                        payload = zipfile.ZipInfo(info.filename)
                        payload.compress_type = zipfile.ZIP_DEFLATED
                        payload.external_attr = (stat.S_IFREG | safe_mode(info)) << 16
                        with (
                            archive.open(info) as reader,
                            target.open(payload, "w", force_zip64=True) as writer,
                        ):
                            shutil.copyfileobj(reader, writer, 1024 * 1024)
            target.writestr(
                "preset.json",
                json.dumps({"format": FORMAT, "version": 1, "profiles": sorted(profiles)}),
            )
        if result.stat().st_size > MAX_ARCHIVE:
            raise PresetError("preset exceeds download limit")
        digest = sha256(result)
        os.chmod(result, 0o600)
        os.replace(result, output)
    return digest


def safe_mode(info: zipfile.ZipInfo) -> int:
    """Preserve script execution without publisher setuid or broad permissions."""
    return 0o700 if (info.external_attr >> 16) & 0o111 else 0o600


def sync_tree(root: Path) -> None:
    for current, _directories, files in os.walk(root, topdown=False):
        for name in files:
            with (Path(current) / name).open("rb") as handle:
                os.fsync(handle.fileno())
        descriptor = os.open(current, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def valid_download_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or any(c in url for c in "\r\n\x00")
        ):
            return False
        if parsed.scheme == "https":
            return True
        address = ipaddress.IPv4Address(parsed.hostname)
        return (
            parsed.scheme == "http"
            and not parsed.query
            and any(
                address in ipaddress.IPv4Network(network)
                for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
        )
    except ValueError:
        return False


class PresetRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        original = urllib.parse.urlsplit(req.full_url)
        target = urllib.parse.urlsplit(newurl)
        if (
            not valid_download_url(newurl)
            or (original.scheme == "https" and target.scheme != "https")
            or (
                original.scheme == "http"
                and (original.scheme, original.netloc) != (target.scheme, target.netloc)
            )
        ):
            raise PresetError("invalid preset redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, destination: Path) -> None:
    if not valid_download_url(url):
        raise PresetError("preset requires HTTPS or private IPv4 HTTP")
    deadline = time.monotonic() + 900
    total = 0
    handlers = [PresetRedirect()]
    if urllib.parse.urlsplit(url).scheme == "http":
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(url, timeout=30) as response, destination.open("xb") as writer:
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > MAX_ARCHIVE or time.monotonic() > deadline:
                    raise PresetError("preset download limit exceeded")
                writer.write(block)
    except Exception:
        raise PresetError("preset download unavailable") from None


def install(archive_pathname: Path, expected: str, root: Path, profiles: Path) -> None:
    if (
        not DIGEST.fullmatch(expected)
        or archive_pathname.stat().st_size > MAX_ARCHIVE
        or sha256(archive_pathname) != expected
    ):
        raise PresetError("preset checksum mismatch")
    if profiles.is_symlink() or not profiles.is_dir():
        raise PresetError("invalid profiles directory")
    with zipfile.ZipFile(archive_pathname) as archive:
        items = entries(archive)
        if archive.getinfo("preset.json").file_size > 1024 * 1024:
            raise PresetError("preset manifest too large")
        manifest = json.loads(archive.read("preset.json"))
        ids = manifest.get("profiles", [])
        if (
            manifest.get("format") != FORMAT
            or manifest.get("version") != 1
            or not ids
            or len(ids) != len(set(ids))
            or any(
                not isinstance(p, str) or not PROFILE_ID.fullmatch(p) or p in {"root", "default"}
                for p in ids
            )
        ):
            raise PresetError("invalid preset manifest")
        # Validate the entire archive before any destination mutation.
        for info in items:
            parts = archive_path(info)
            if parts == ("preset.json",):
                continue
            if (
                len(parts) < 3
                or parts[0] != "profiles"
                or parts[1] not in ids
                or not allowed_file(parts[2:])
            ):
                raise PresetError("unexpected preset payload")
        with tempfile.TemporaryDirectory(prefix=".preset-stage-", dir=profiles) as temporary:
            stage = Path(temporary)
            for name in ids:
                (stage / name).mkdir(mode=0o700)
            for info in items:
                parts = archive_path(info)
                if parts[0] != "profiles" or info.is_dir():
                    continue
                destination = stage.joinpath(*parts[1:])
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(info) as reader, destination.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
                destination.chmod(safe_mode(info))
            for name in ids:
                profile = stage / name
                metadata_path = profile / "agent.json"
                metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
                metadata["name"] = metadata["profile_name"] = name
                metadata_path.write_text(json.dumps(metadata, ensure_ascii=False))
                # Receive the current workspace's routing and approval defaults.
                # Publisher configs, secrets and database state are never imported.
                shutil.copyfile(root / "config.yaml", profile / "config.yaml")
                for directory in (
                    "memories",
                    "sessions",
                    "logs",
                    "plans",
                    "workspace",
                    "skills",
                    "cron",
                    "home",
                ):
                    (profile / directory).mkdir(exist_ok=True, mode=0o700)
                (profile / MARKER).write_text(json.dumps({"sha256": expected}))
                destination = profiles / name
                if destination.exists() or destination.is_symlink():
                    continue  # Existing user/importer-owned profile always wins.
                sync_tree(profile)
                os.rename(profile, destination)
            marker = stage / MARKER
            marker.write_text(json.dumps({"sha256": expected, "profiles": ids}))
            with marker.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(marker, profiles / MARKER)
            descriptor = os.open(profiles, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def seed_from_environment() -> None:
    url = os.environ.get("RUNTIME_PROFILE_PRESET_URL", "")
    digest = os.environ.get("RUNTIME_PROFILE_PRESET_SHA256", "")
    if not url and not digest:
        return
    if not url or not DIGEST.fullmatch(digest):
        raise PresetError("incomplete preset configuration")
    profiles = Path(os.environ["RUNTIME_HERMES_PROFILES_ROOT"])
    root = Path(os.environ["RUNTIME_HERMES_HOME"])
    marker = profiles / MARKER
    if marker.is_file() and json.loads(marker.read_text()).get("sha256") == digest:
        return
    with tempfile.TemporaryDirectory(prefix=".preset-download-", dir=profiles.parent) as temporary:
        archive = Path(temporary) / "preset.zip"
        download(url, archive)
        install(archive, digest, root, profiles)
    print("Workspace profile preset installed", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("prepare")
    builder.add_argument("--output", type=Path, required=True)
    builder.add_argument("sources", nargs="+", type=Path)
    commands.add_parser("install")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(prepare(args.sources, args.output))
        else:
            seed_from_environment()
    except Exception:
        print("workspace_profile_preset_unavailable", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

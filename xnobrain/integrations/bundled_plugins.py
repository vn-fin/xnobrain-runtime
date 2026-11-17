"""Stage optional, first-party plugin packages without accepting local paths."""

from pathlib import Path

from .hermes_support import AgentAPIError

PROVIDERS = frozenset({"sapo", "sepay", "kiotviet"})


def package_root() -> Path:
    source = Path(__file__).resolve().parents[2] / "runtime" / "optional-plugins"
    return source if source.is_dir() else Path("/opt/xnobrain/optional-plugins")


def package_files(plugin_id: str) -> dict[str, bytes]:
    """Read only the reviewed payload; never follow package symlinks or copy caches."""
    if plugin_id not in PROVIDERS:
        raise AgentAPIError("Unknown bundled plugin", code="invalid_plugin_source", status=422)
    root = package_root()
    package = root / plugin_id
    names = (
        "README.md",
        "LICENSE",
        "package-files.txt",
        "plugin.yaml",
        "__init__.py",
        f"skills/{plugin_id}/SKILL.md",
        f"skills/{plugin_id}/scripts/{plugin_id}.py",
        f"skills/{plugin_id}/references/setup.md",
        f"skills/{plugin_id}/references/api.md",
    )
    result = {}
    for name in names:
        path = package / name
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise AgentAPIError(
                "Bundled plugin package is unavailable",
                code="plugin_package_unavailable",
                status=503,
            )
        if path.stat().st_size > 1024 * 1024:
            raise AgentAPIError(
                "Bundled plugin file is too large", code="plugin_package_unavailable", status=503
            )
        result[name] = path.read_bytes()
    return result


def stage_package(plugin_id: str, destination: Path) -> None:
    files = package_files(plugin_id)
    destination.mkdir(mode=0o700)
    for name, content in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

"""Verify the guest descriptor against the node-installed artifact binding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def incus_installed_identity(directory: Path = Path("/etc/xnobrain")) -> dict[str, Any]:
    """Read baked identity independently of the requested update target."""
    try:
        descriptor_path = directory / "build-descriptor.json"
        artifact_path = directory / "runtime-artifact.json"
        if any(path.is_symlink() or path.stat().st_size > 16384 for path in (descriptor_path, artifact_path)):
            return {}
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        digest = canonical_digest(descriptor)
        if (
            artifact["kind"] != "incus_image"
            or artifact["build_descriptor"] != descriptor
            or artifact["build_descriptor_digest"] != digest
            or descriptor["schema_version"] != 1
            or (directory / "sha.txt").read_text(encoding="utf-8").strip() != descriptor["source_revision"]
        ):
            return {}
        return {
            "kind": "incus_image",
            "fingerprint": artifact["fingerprint"],
            "build_descriptor_digest": digest,
            "version": descriptor["version"],
            "source_commit": descriptor["source_revision"],
            "data_schema": descriptor["data_schema"],
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {}

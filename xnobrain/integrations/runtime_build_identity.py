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


SOURCE_RECEIPT = Path("/etc/xnobrain/runtime-source.json")
_SOURCE_FIELDS = (
    "source_revision",
    "git_tree",
    "manifest_digest",
    "package_digest",
    "data_schema",
)


def source_installed_identity(path: Path = SOURCE_RECEIPT) -> dict[str, Any]:
    """Read the gateway-written receipt of an in-place source update.

    The receipt is separate from the baked image identity: an in-place update
    never rewrites build-descriptor.json or sha.txt.
    """
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            return {}
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("kind") != "runtime_source_v1":
            return {}
        identity = {"kind": "runtime_source_v1"}
        for field in _SOURCE_FIELDS:
            identity[field] = receipt[field]
        return identity
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}

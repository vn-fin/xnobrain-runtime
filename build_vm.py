#!/usr/bin/env python3
"""Build the OSS Hermes Incus image and optionally copy it to a remote hub."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import time


def run(command: list[str], dry_run: bool, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(" ".join(command))
    if dry_run:
        return subprocess.CompletedProcess(command, 0, "", "")
    return subprocess.run(command, check=check, text=True, capture_output=capture)


def main() -> int:
    repository = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", default="open-lumora-hermes-runtime")
    parser.add_argument("--instance", default="open-lumora-hermes-builder")
    parser.add_argument("--image", default="images:ubuntu/24.04")
    parser.add_argument("--type", choices=("container", "virtual-machine"), default="container")
    parser.add_argument("--storage", default="")
    parser.add_argument("--remote", default="", help="optional configured Incus remote that receives the image")
    parser.add_argument("--keep-builder", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    installer = repository / "runtime" / "install_vm.sh"
    router_auth_preparer = repository / "runtime" / "prepare-nine-router-auth.sh"
    extensions = repository / "extensions"
    launcher = repository / "runtime" / "hermes-custom-gateway"
    if not installer.is_file() or not router_auth_preparer.is_file() or not extensions.is_dir() or not launcher.is_file():
        parser.error("enterprise runtime installer, extensions, and launcher are required")
    if not args.dry_run and shutil.which("incus") is None:
        print("error: incus is not installed", file=sys.stderr)
        return 2
    if not args.instance.startswith("open-lumora-hermes-builder"):
        parser.error("--instance must use the reserved open-lumora-hermes-builder prefix")

    run(["incus", "delete", args.instance, "--force"], args.dry_run, check=False)
    launch = ["incus", "launch", args.image, args.instance]
    if args.type == "virtual-machine":
        launch.append("--vm")
    if args.storage:
        launch.extend(["--storage", args.storage])
    run(launch, args.dry_run)

    if not args.dry_run:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            check = subprocess.run(["incus", "exec", args.instance, "--", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if check.returncode == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError("Incus builder did not become ready within 10 minutes")
        subprocess.run(
            ["incus", "exec", args.instance, "--", "sh", "-lc", "command -v cloud-init >/dev/null 2>&1 && cloud-init status --wait || true"],
            check=False,
        )

    # Some minimal Incus images clean /tmp during their first boot. Stage under
    # root so extension files cannot disappear between the push and installer.
    stage = "/root/open-lumora-runtime"
    run(["incus", "exec", args.instance, "--", "mkdir", "-p", stage], args.dry_run)
    run(["incus", "file", "push", str(installer), f"{args.instance}{stage}/install_vm.sh"], args.dry_run)
    run(
        ["incus", "file", "push", str(router_auth_preparer), f"{args.instance}{stage}/prepare-nine-router-auth.sh"],
        args.dry_run,
    )
    run(["incus", "file", "push", "--recursive", str(extensions), f"{args.instance}{stage}/"], args.dry_run)
    run(["incus", "file", "push", str(launcher), f"{args.instance}{stage}/hermes-custom-gateway"], args.dry_run)
    run(["incus", "exec", args.instance, "--", "chmod", "0755", f"{stage}/install_vm.sh", f"{stage}/hermes-custom-gateway"], args.dry_run)
    run(["incus", "exec", args.instance, "--", f"{stage}/install_vm.sh", stage], args.dry_run)
    run(["incus", "stop", args.instance, "--timeout", "120"], args.dry_run)
    run(["incus", "publish", args.instance, "--alias", args.alias, "--reuse"], args.dry_run)
    if args.remote:
        run(["incus", "image", "copy", args.alias, f"{args.remote}:", "--alias", args.alias], args.dry_run)
    if not args.keep_builder:
        run(["incus", "delete", args.instance, "--force"], args.dry_run)
    print(f"Hermes Incus image ready: {args.alias}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

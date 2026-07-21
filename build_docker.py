#!/usr/bin/env python3
"""Build the OSS Hermes OCI runtime image into a local container daemon."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    repository = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        default=os.environ.get("HERMES_RUNTIME_IMAGE", "open-lumora-hermes-runtime:local"),
        help="image name/tag loaded into the local daemon (kept as --path for the release contract)",
    )
    parser.add_argument("--context", type=Path, default=repository)
    parser.add_argument("--container-cli", default=os.environ.get("CONTAINER_CLI", "docker"))
    parser.add_argument("--platform", default=os.environ.get("IMAGE_PLATFORM", ""))
    parser.add_argument("--push", action="store_true", help="push the tagged image after a successful build")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.path.strip() or any(character.isspace() for character in args.path):
        parser.error("--path must be a non-empty image reference without whitespace")
    context = args.context.resolve()
    dockerfile = repository / "runtime" / "Dockerfile"
    if not dockerfile.is_file() or not (repository / "open_lumora").is_dir():
        parser.error("runtime Dockerfile and open_lumora package are required")

    command = [args.container_cli, "build", "--file", str(dockerfile), "--tag", args.path]
    if args.platform:
        command.extend(["--platform", args.platform])
    command.append(str(context))
    print(" ".join(command))
    if args.dry_run:
        return 0
    if shutil.which(args.container_cli) is None:
        print(f"error: container CLI {args.container_cli!r} is not installed", file=sys.stderr)
        return 2
    subprocess.run(command, check=True)
    if args.push:
        subprocess.run([args.container_cli, "push", args.path], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

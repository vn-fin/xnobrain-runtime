---
name: runtime-onefile-build
description: Build, change, review, or diagnose the XNOBrain Runtime Nuitka one-file API executable inside the Hermes runtime image. Use for Dockerfile.backend compiler flags, dynamic Hermes/XNOBrain imports, package data, executable startup, or compiled container and Incus image validation.
---

# Runtime Nuitka one-file build

Read [onefile-contract.md](references/onefile-contract.md) before changing the
compiler or Runtime image.

1. Compile `server.py` with Nuitka `--mode=onefile` into exactly one executable,
   copied to `/usr/local/bin/app.so`. Do not launch package modules through a
   shell/Python `-c` wrapper.
2. Explicitly include `xnobrain` and dynamically imported Hermes packages and
   modules used by the API process, plus XNOBrain package data. Preserve the
   installed Hermes environment for independent CLI, skill synchronization,
   office tools, and agent subprocesses; “one file” applies to the XNOBrain API
   endpoint artifact, not every tool in the workspace image.
3. Pin `nuitka[onefile]`, keep compiler/patchelf/ccache and compression support
   in the builder, and keep the output filename/temp extraction namespace stable
   and version-specific.
4. Do not copy first-party Python source, a compiled package-module directory,
   or an endpoint launcher into the final stage.
5. Preserve `runtime/container-entrypoint.sh` environment validation/profile
   preparation, then `exec /usr/local/bin/app.so`.
6. Run packaging tests and `make check`, build the Runtime image, inspect that
   `app.so` is one executable, and exercise the health endpoint. For managed
   packaging, also smoke the actual Incus container/VM workflow.

When an import works from source but fails only in the executable, add the
narrowest explicit include and a regression test. Do not solve it by copying the
whole source tree into the final image.

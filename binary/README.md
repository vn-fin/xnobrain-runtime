# Native application binaries

This directory contains the single-file desktop application outputs that end
users open directly. Runtime images are never built on the end-user machine.

Current release target:

- `XNOBrain-linux-x86_64.AppImage` — Linux Docker Web installer and launcher.

Future native-host builds use these names:

- `XNOBrain-windows-x86_64.exe`
- `XNOBrain-macos-aarch64.dmg`
- `XNOBrain-macos-x86_64.dmg`

Build the Linux file with `make linux-app`. Build the Brain UI container, with
the public API/Auth/Control origins and Firebase Web API key from `app/.env`,
using `make brain-app-image`.

`make linux-app` requires registry digests reachable from end-user machines.
For an isolated local-registry lifecycle test only, use
`make linux-app-local`; that artifact is not portable to another computer.

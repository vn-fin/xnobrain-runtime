# Platform support and release gate

Checked on 2026-08-03 against the current official Docker and Tauri 2
documentation.

| Platform | End-user prerequisite flow | Native artifact | Release validation |
| --- | --- | --- | --- |
| Windows x86-64 | Enable WSL 2 when absent, restart if required, then install Docker Desktop per-user | NSIS `.exe` | Windows 11 23H2+ or Windows 10 22H2, WSL 2.1.5+, native install/start/upgrade/uninstall test |
| macOS arm64/x86-64 | Verified Docker DMG, native administrator prompt, then Docker's first-run agreement | notarized `.dmg` | Current and previous two macOS major releases supported by Docker; native signing/notarization and install/start test |
| Debian/Ubuntu x86-64 | Docker's official Apt repository installs Engine, CLI, Buildx, and Compose plugin | `.deb` | Ubuntu 22.04/24.04/25.10/26.04 or Debian 11/12/13; native clean-install test |
| Fedora/RHEL/CentOS x86-64 | Docker's official RPM repository installs Engine, CLI, Buildx, and Compose plugin | `.rpm` | Declared supported distro matrix; native clean-install test |

The bundled Docker Desktop artifact is pinned to 4.84.0, build 234817, with
Docker-published SHA-256 checksums for Windows x86-64, macOS Apple silicon, and
macOS Intel. Linux deliberately installs the current stable packages from
Docker's signed repository so Engine and Compose receive distribution updates.

Windows and macOS artifacts must be built, signed, and tested on their native
hosts. A Linux cross-build is useful diagnostics but is not release evidence.
Linux packages produced by a developer build are unsigned; repository/package
signing is a separate release gate. Docker license acceptance always remains
an explicit user action.

`make` installs/checks native compiler prerequisites. Package targets run the
matching preflight script. Release automation sets `XNOBRAIN_REQUIRE_SIGNING=1`
so a missing Windows certificate or macOS signing/notarization identity stops
the build before packaging.

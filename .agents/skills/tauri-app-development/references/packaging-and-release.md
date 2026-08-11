# Packaging and release rules

## Build interface

- `make dev-app`: app development mode.
- `make win-app`: NSIS installer on a Windows build/signing host.
- `make mac-app`: app bundle and DMG on a macOS build/notarization host.
- `make rpm-app`: RPM on a supported RPM Linux build host.
- `make -C app check`: TypeScript/frontend tests plus Rust format, Clippy, and
  tests.

Keep implementation in `app/Makefile`; root Make targets only delegate.

## Native-host rule

Build and validate each release on its target OS. Windows MSI/NSIS signing and
macOS signing/notarization are release behavior, not optional documentation.
Never report a signed target artifact from `make -n`, cross-compilation alone,
or an unsigned local bundle.

## Artifact rules

- Pin Node and Rust dependencies with committed lockfiles.
- Pull runtime artifacts by immutable digest from signed manifests; never build
  runtime images on an end-user machine.
- Keep signing keys outside the repository and logs. CI receives them through
  protected secret facilities.
- Generate SBOM/provenance and third-party notices for app, helpers, and runtime.
- Publish artifacts only after native-host install, upgrade, repair, rollback,
  and uninstall tests.
- Keep user data separate from versioned runtime files. App uninstall, runtime
  uninstall, and user-data deletion are distinct choices.

## Tauri packaging

- Windows consumer artifact: NSIS first; add MSI only for a demonstrated
  enterprise deployment need.
- macOS: signed `.app` and notarized DMG, including every nested helper.
- Linux: signed RPM for the declared distro matrix. Add other formats as
  separate tested targets rather than renaming an RPM as generic Linux.
- Sign app updater artifacts separately from runtime manifests. Enforce mutual
  compatibility before activating either update and retain a health-checked
  rollback path.

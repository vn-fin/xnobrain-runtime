# 013 — Validation

Completion requires recorded evidence for the current stage. A successful
developer-machine run is not release evidence.

## 1. Stage-0 Web-installer proof

- `git diff --name-only` for an app feature contains only `app/` paths, except
  an explicitly scoped Plan 013, skill, or root Makefile contract update.
- `make -n dev-app`, `make -n win-app`, `make -n mac-app`, and
  `make -n rpm-app` delegate only to `app/`.
- Existing frontend production build passes unchanged in web mode.
- Web-installer Tauri development and production builds pass on Windows,
  macOS, and Linux.
- A healthy Docker installation opens the default browser, not a Tauri product
  window, on all three platforms.
- Browser console contains no uncaught errors and network inspection shows no
  new duplicate product API calls introduced by the desktop shell.
- Runtime-manifest schema rejects missing fields, unsupported schema versions,
  invalid digests, incompatible desktop versions, and bad signatures.
- The release/license review has an owner and a signed outcome.

## 2. Installer state-machine tests

Automated tests cover every legal transition and reject illegal transitions:

- mode is not persisted before explicit confirmation;
- Full Managed App planned cannot enter install states;
- cancel/relaunch resumes at a safe state;
- stale prerequisite state is re-inspected;
- two installers cannot mutate runtime concurrently;
- retry is idempotent after partial pull/create/start;
- repair preserves data;
- runtime uninstall preserves data by default;
- data deletion requires a separate confirmation and exact resolved target.

Property/fault tests interrupt each mutating transition and then relaunch.

## 3. Security tests

- Installer and Full Managed App command allowlists cannot execute an arbitrary
  binary, argument, shell fragment, Compose file, path, URL, or environment
  assignment.
- Privileged helper accepts only fixed operation identifiers and validated
  arguments.
- Manifest/update bad signature, wrong digest, downgrade, incompatible schema,
  and unapproved redirect all fail closed.
- Containers expose no port beyond loopback by default.
- Generated secrets differ between installs and never occur in application
  logs, UI errors, crash reports, or diagnostic bundles.
- Repair/uninstall targets only resources carrying the exact Brain4All install
  identity labels.
- External URLs cannot navigate the trusted Full Managed App webview. Docker
  Web intentionally uses the default system browser.

## 4. Docker functional matrix

Run every supported OS/architecture through:

1. Clean machine without Docker.
2. Compatible existing Docker, stopped and running variants.
3. Unsupported/old Docker or Compose.
4. Installation requiring elevation.
5. Windows installation requiring reboot.
6. Insufficient disk and insufficient memory.
7. Occupied default ports.
   Repeat with a valid custom port, invalid values, a privileged port, and a
   bind race after preflight.
8. Offline before manifest, network loss during pull, and registry error.
9. Cancel during pull and during create.
10. Runtime health timeout and one unhealthy image release.
11. Relaunch after process kill at every persistent state.
12. Repair with healthy data and with damaged runtime configuration.
13. Desktop uninstall, runtime uninstall preserving data, and separately
    confirmed full data deletion.

For each case record screenshots, structured state trace, elapsed time,
resulting Docker resources, and whether user data remains.

For every Docker Web case, inspect the effective Compose configuration and
running containers:

- Traefik has exactly one host mapping,
  `127.0.0.1:<selected-port>:5152`.
- Frontend, FastAPI/Hermes runtime, 9router, and observability containers have
  no published host ports.
- The health endpoint and browser UI are reachable through Traefik at the
  selected port; direct host access to internal service ports fails.
- Repair/relaunch continues using the persisted selected port even if the
  manifest's default changes.

## 5. Platform release matrix

The exact version matrix is pinned during Stage 0. Minimum categories:

| Platform | Architectures | Install artifacts | Required evidence |
|---|---|---|---|
| Windows | x64 initially; arm64 when runtime images support it | signed NSIS; MSI later | SmartScreen/signature, WSL/reboot resume, uninstall |
| macOS | Apple Silicon and supported Intel range | signed/notarized app + DMG | Gatekeeper, first run, update, uninstall |
| Linux | x86_64; arm64 when declared | DEB and RPM | clean package install/upgrade/remove on each named distro |

Do not use “Linux” or an architecture in release copy until its row passes.

## 6. Performance and reliability gates

Measure rather than hard-code targets before Stage 0 findings are complete:

- desktop cold and warm launch;
- manifest fetch and verification;
- cold image pull on defined network profiles;
- start-to-health time;
- installer and runtime peak disk use;
- idle shell memory and CPU;
- cancellation latency;
- rollback and repair duration.

The reviewed action-plan screen must match measured download/disk requirements
within the release tolerance recorded with the manifest.

## 7. Full Managed App gates

Each managed platform driver must pass the common state-machine, security, failure,
repair, update, rollback, and data-preservation suites plus its platform tests:

- Linux: package dependency resolution, systemd user lifecycle, distro upgrade.
- Windows WSL2: enable/reboot/import/start/update/unregister and path handling.
- macOS: signing/notarization of nested binaries and capability reporting.

Full Managed App remains unavailable for a platform until all gates pass. Its
embedded React UI also passes the WebView2/WKWebView/WebKitGTK product test
matrix, and its management functions pass lifecycle/log/update/repair/backup
tests.

## 8. MVP acceptance checklist

- [ ] Edition selection is required and Web Version is never chosen implicitly.
- [ ] Docker-first release shows Full Managed App as planned without fake install.
- [ ] Existing-Docker path makes no unintended Docker configuration changes.
- [ ] Install-Docker path uses explicit consent, official sources, and resumes
      correctly after elevation/reboot.
- [ ] Runtime images are prebuilt, signed, and pulled by immutable digest.
- [ ] Progress, cancel, retry, repair, and relaunch are deterministic.
- [ ] Ready appears only after all required health checks pass, then opens the
      Web version in the default system browser.
- [ ] Installer offers the manifest default port or a validated custom port and
      persists the selection.
- [ ] Traefik is the only Docker Web service publishing a host port; its mapping
      is loopback-only and all application/runtime ports remain internal.
- [ ] Ports are loopback-only and secrets are absent from all diagnostics.
- [ ] Runtime repair/update/uninstall preserves user data by default.
- [ ] Signed Windows installer passes the complete Windows matrix.
- [ ] macOS and Linux artifacts pass their matrices before those platforms are
      advertised.
- [ ] Existing web tests, focused desktop tests, frontend production build,
      Rust tests/lints, and repository `make check` pass.
- [ ] No app implementation changes core backend or Web UI source.

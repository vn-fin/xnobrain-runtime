# OSS-04: portable workspace bundle

Priority P1 and parallel with connector/scheduler after the format is frozen. Own `services/portability`, archive safety helpers, and focused tests.

## Format

Use a versioned `.lumora` archive with `manifest.json`, `checksums.json`, and `profiles/<agent-id>/...`. Manifest fields include format/version, source app version, creation time, selected agents, included optional data, and required capabilities. Checksums cover every payload file and the manifest is signed when produced by a managed service.

Include profile config, agent metadata, memory, skills, workspace, snapshots, cron definitions, and optionally conversations. Exclude device identity, provider credentials, OAuth tokens, runtime endpoints, PIDs, sockets, logs, caches, temporary files, and absolute host paths.

## Export and import

Export acquires a short mutation barrier, snapshots a consistent logical view, streams the archive, then releases the barrier. It must not load a multi-gigabyte workspace into memory. Optional passphrase encryption happens after archive construction through a documented format.

Import has `inspect`, `dry-run`, and `apply` phases. Inspect performs streaming size/count limits, traversal and symlink rejection, checksum verification, schema compatibility, and content classification. Dry-run reports ID collisions, approval resets, cron pause behavior, missing providers, unsupported features, and storage required. Apply writes into staging and atomically publishes only after validation.

Imported crons are paused, persistent approvals reset, providers disconnected, IDs remapped consistently, and imported custom code marked untrusted until user confirmation.

## Acceptance criteria

- Round-trip representative profiles without changing content hashes.
- Reject absolute paths, `..`, symlinks, duplicate paths, case-fold collisions, zip bombs, excessive file count, oversized file, bad checksum, and unsupported version.
- Cancellation leaves no visible partial profile.
- Export never includes known test credentials or device keys.
- Enterprise import compatibility runs against golden v1 fixtures.

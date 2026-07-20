# Portable bundle format v1

Media type: `application/vnd.open-lumora.bundle+zip; version=1`. Extension: `.lumora`.

## Layout

```text
manifest.json
checksums.json
profiles/<agent-id>/...
```

`manifest.json` contains format `open-lumora-bundle`, version `1`, source version, UTC creation time, export ID, selected agent records, included optional sections, required capabilities, and encryption metadata when applicable. `checksums.json` maps normalized relative paths to SHA-256 and size. Paths are UTF-8 forward-slash relative paths with no empty, dot, parent, absolute, drive-prefix, control, or duplicate case-folded components.

Allowed profile content is config/metadata, memories, skills, workspace regular files, immutable snapshots, cron definitions, and optional logical conversation export. Device identity, credentials, tokens, endpoints, PIDs, sockets, symlinks, logs, caches, and temporary files are forbidden.

## Import rules

Inspect before extraction. Enforce compressed bytes, expanded bytes, file count, per-file bytes, path depth, and compression ratio. Verify every checksum and reject unlisted payloads. Unsupported major versions fail; compatible minor additions are ignored unless declared required.

Apply uses staging and atomic publish. Ownership and conflicting IDs are remapped consistently, crons are paused, persistent approvals reset, providers disconnected, and custom executable content quarantined. An import report lists every transformation and warning.

Golden valid and malicious fixtures live with contract tests and are consumed by both repositories.

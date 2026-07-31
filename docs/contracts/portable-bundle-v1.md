# Portable bundle format v1

Media type: `application/zip`. Extension: `.zip`. The bundle remains versioned
by `manifest.json`; it does not use a custom filename extension or media type.

## Layout

```text
manifest.json
checksums.json
profiles/<agent-id>/...
```

`manifest.json` contains format `brain4all-bundle`, version `1`, source version, UTC creation time, export ID, selected agent records, included optional sections, required capabilities, and encryption metadata when applicable. `checksums.json` maps normalized relative paths to SHA-256 and size. Paths are UTF-8 forward-slash relative paths with no empty, dot, parent, absolute, drive-prefix, control, or duplicate case-folded components.

Optional `required_environment` lists environment-variable names needed by the
profile. `credentials_included` is always `false` for Community exports.

Every regular file beneath each selected named profile directory is included,
including conversations, logs, caches, databases, and temporary files. Secret
values are redacted in transit, and imported credential files are discarded in
favor of destination-installation credentials. Symlinks are not archive
members. The default/root profile excludes nested managed data roots so an
export cannot recursively capture sibling profiles or transfer staging data.

## Import rules

Inspect before extraction. Enforce compressed bytes, expanded bytes, file count, per-file bytes, path depth, and compression ratio. Verify every checksum and reject unlisted payloads. Unsupported major versions fail; compatible minor additions are ignored unless declared required.

Apply uses staging and atomic publish. Ownership and conflicting IDs are remapped consistently, crons are paused, persistent approvals reset, providers disconnected, and custom executable content quarantined. An import report lists every transformation and warning.

Imported profiles discard archive credentials and authentication files. The
server copies authentication and provider connection material from its default
profile, then accepts values only for environment names reported missing by
the dry run. Existing default-profile secret values cannot be read or
overwritten through the import API.

## Chunk transfer API

Large and small archives use the same fixed-size part protocol:

1. `POST /api/brain/v1/bundles/exports` prepares a file-backed export and returns an
   export ID, filename, SHA-256, byte size, chunk size, and part count.
2. `GET /api/brain/v1/bundles/exports/{id}/parts/{part}` downloads one part.
3. `DELETE /api/brain/v1/bundles/exports/{id}` removes the transfer.
4. `POST /api/brain/v1/bundles/uploads` starts an upload with filename and byte size.
5. `PUT /api/brain/v1/bundles/uploads/{id}/parts/{part}` uploads one exact-size
   binary part. Parts are idempotently replaceable and may arrive out of order.
6. `POST /api/brain/v1/bundles/uploads/{id}/complete` verifies and merges all parts,
   validates that every declared profile has a parseable Hermes `config.yaml`,
   and returns its dry-run report. Invalid profile directories are rejected
   before profile creation.
7. `POST /api/brain/v1/bundles/uploads/{id}/apply` atomically publishes the import;
   its optional `environment` object may fill only names reported missing.

The older whole-body endpoints remain v1 compatibility surfaces. Interactive
clients use the part protocol so neither HTTP uploads nor downloads require a
single multi-gigabyte request.

Golden valid and malicious fixtures live with contract tests and are consumed by both repositories.

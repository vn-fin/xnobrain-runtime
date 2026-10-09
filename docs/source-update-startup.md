# Source update startup invariants

The Runtime update checkpoint compares the complete durable file manifest after
restart. An unchanged initialization must therefore preserve file bytes and
permissions, including profile metadata and SQLite databases. Do not exclude
these files from verification to accommodate unnecessary startup writes.

`ensure_default_agent` updates `agent.json` and its timestamp only when managed
metadata changes. Updating the profile registry with identical display metadata
does not rewrite `profiles.yaml`. Opening a version-1 portability task database
does not reassign `PRAGMA user_version`; even assigning the same value changes
SQLite's file header. Actual profile repairs, metadata edits and schema creation
still persist normally.

Native VM provisioning must set `RUNTIME_UPDATE_DATA_PATH=/srv/xnobrain-data`.
The node gateway validates this data anchor before source-update drain. The
Docker default `/opt/data` is not the native volume layout.

Regression checks cover repeated profile initialization, real registry edits,
byte-identical database reopen with a pending task, and existing maintenance
lifecycle tests. Automatic local Incus verification is documented in Control's
`docs/runtime-source-rollout-local-verification.md`.

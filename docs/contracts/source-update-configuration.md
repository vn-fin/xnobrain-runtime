# Release-owned source update configuration

`deploy/source-update-config.json` belongs to Runtime and travels inside the
immutable source archive. Control's node gateway consumes it only from the
verified target package. No repository access is needed on a customer node.
The gateway has one declarative configuration adapter, not per-field repair
branches. Fields may be added in a later release without changing this adapter.

## Version 1

The document contains `schema_version: 1`, a nonempty `revision` label (maximum
80 ASCII letters/digits/dots/underscores/hyphens), and at most 64 `environment`
entries. The document is limited to 64 KiB. Unknown fields, unsupported schema
versions, duplicate setting names and invalid values fail before drain/mutation.

Each entry has a `name` and `operation`:

- `set`: apply `value`, including changes from a previously configured value.
- `default`: apply `value` only when the effective existing value is empty or
  absent. Preserve customer/operator overrides.
- `clear`: set an empty value, masking inherited profile settings. It does not
  delete a profile or a credential. Rollback restores the previous value or
  removes the local override when it was originally absent.

`set` and `default` require exactly one literal `value` or `from` binding.
Bindings support only `set` and resolve from persisted Control workspace facts:
`workspace.id`, `workspace.owner_kind`, `workspace.organization_id`,
`workspace.user_id`, `workspace.tenant_id`, `workspace.environment_name`.
A nonempty existing binding that conflicts with Control fails closed. Reserved
identity keys cannot use literal values or a different binding. This is an
identity boundary, not a migration special case.

Names are non-secret `RUNTIME_*` or `FT_ENABLE_*` application settings. Values
are UTF-8 strings of at most 1,024 bytes with no control characters. Credential,
authentication, database and durable-storage settings are excluded by the
consumer. Never include secrets or customer data in this manifest. No shell
expansion, arbitrary command execution, download URL or migration script is
accepted by this adapter. Environment values absent from the document remain
unchanged; omission does not mean deletion.

Example for a future release (illustrative application settings):

```json
{
  "schema_version": 1,
  "revision": "settings-v2",
  "environment": [
    {"name": "RUNTIME_EXAMPLE_MODE", "operation": "set", "value": "v2"},
    {"name": "FT_ENABLE_EXAMPLE", "operation": "default", "value": "true"},
    {"name": "RUNTIME_EXAMPLE_LEGACY", "operation": "clear"}
  ]
}
```

Keep any still-required entries from the preceding release when authoring a new
manifest. Desired state resolves directly from the installed container; a client
can skip release revisions without running a commit-by-commit migration chain.
The manifest currently shipped declares workspace bindings. These declarations
use exactly the same planner/apply/verify/restore path as ordinary settings.

## Execution and recovery

Preflight resolves and freezes desired state and the prior local/effective
values in the existing fenced rollout journal. Drain closes task admission;
busy workspaces yield the fleet slot while finishing existing tasks. After
service stop, unchanged-data checkpoint and source swap, the gateway installs
the target services/receipt and applies the environment with an Incus ETag.
Only a required boot-environment change restarts the container, gracefully.
Post-verification must succeed before Runtime reopens dispatch.

Incus operation tracking and persisted restart evidence reconcile lost replies.
A failed configuration application or API health check enters safe restore:
verify unchanged data, restore the old source/receipt/services and saved settings,
restart, verify, then release maintenance. Conflicting concurrent managed-setting
edits stop recovery rather than overwrite an operator's changes. Gateway restart
or retry does not discard the saved configuration or take a new rollback baseline.
No new SQL table is introduced. Journal configuration entries contain only the
manifest's non-secret fields, never the full environment.

## Compatibility and limits

Upgrade Control/Gateway together with the Runtime pin. Older target archives
without this file retain their settings; no synthetic owner repair is inserted.
New configuration revisions with schema version 1 need no new Gateway branch.
New operation types require a versioned consumer update before publishing them.

Source code uses archive replacement; systemd services/root helper permissions
continue to use the source policy's installer and baseline installer on restore.
This environment adapter does not run package managers, mutate customer data,
resize disks, weaken container isolation, or change kernels. Dependency, OS or
data-schema incompatibility still requires the appropriate image/data migration;
it must not be hidden by an unchecked source overwrite. A rollback is only safe
when its data-compatibility proof holds.

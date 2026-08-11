# Backend Service Groups

## Mandatory layout

- The Python package is `xnobrain`. New internal imports must use
  `xnobrain.*`; do not recreate a `brain4all` package alias.
- A backend capability has one stable service-group name, such as `agents`,
  `automation`, `conversations`, `mcp`, `providers`, `sandboxes`,
  `teams`, or `workspaces`.
- Use that same group name for its files in `routes/`, `models/`,
  `handlers/operations/`, `services/`, and `repositories/` whenever that
  layer is needed.
- One file owns one service group. Do not collect unrelated models, operations,
  persistence methods, or business rules in a generic API or platform module.
- A narrowly scoped helper may have its own file, such as
  `workspace_upload.py` or `team_runs.py`, but it must serve one owning group
  and must not become a second feature registry.

## Layer responsibilities

- Route group files declare endpoints and their request models.
- Model group files contain that group's Pydantic contracts.
- Operation handler group files translate a route operation into a service
  call. Resolve path and query fields lazily inside the selected operation.
- Service group files own validation, orchestration, and business rules.
- Repository group files own atomic persistence for that group.
- Integration files adapt Hermes, 9router, or another external runtime.
- Local layers call one another directly. Do not call the local HTTP API.

## Composition boundaries

- `routes/setup.py` only assembles route groups.
- `models/__init__.py` only re-exports contracts.
- `handlers/operations/__init__.py` only resolves operation groups.
- `services/platform.py` only constructs and composes services.
- `repositories/files.py` is a compatibility facade over grouped repositories.
- Composition modules must not accumulate feature-specific implementation.

## MCP boundary

- MCP is its own `mcp` service group and service object.
- Workspace owns files, directories, upload, and preview behavior only.
- Do not add MCP endpoints, contracts, handler operations, or mutations to a
  workspace file.

## Compatibility and verification

- Internal package renames do not authorize changes to public
  `/xnobrain/api/runtime/<version>` paths, deployment directory names,
  persisted keys, bundle formats, or product branding.
- Add or update focused tests in `xnobrain/tests/` for every changed group.
- Compile `xnobrain`, run the focused test modules, then run `make check`
  when practical.

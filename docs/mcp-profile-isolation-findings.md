# MCP profile isolation findings

## Result

The installed Hermes MCP APIs do not provide profile-scoped registration or
per-server unload. Namespacing server IDs is useful for dispatch: native
handlers capture the server ID and resolve its session from `_servers`, so
distinct IDs can select distinct sessions. It does not establish profile
isolation in the runtime.

## Native API evidence

Inspection of `/usr/local/lib/hermes-agent` in the cached
`xnobrain-runtime:dev` image found these constraints:

- `discover_mcp_tools()` reads `mcp_servers` from native `load_config()` and
  calls `register_mcp_servers(servers)`.
- `register_mcp_servers(servers)` accepts explicit server mappings, but it
  connects and registers them in one operation. It has no profile/scope
  parameter or connect-only mode.
- `_register_server_tools()` calls `registry.register()` from
  `tools.mcp_tool`; the registry attributes a scope from the registering
  plugin module or an explicit `scope` argument. The MCP caller supplies
  neither, so entries are global.
- `_make_tool_handler()` captures the raw server ID and resolves the
  `MCPServerTask` from the process-global `_servers` map. Namespaced IDs can
  distinguish those sessions, but the corresponding schemas remain visible
  from every profile because they are global registry entries.
- MCP disconnect and shutdown bookkeeping is process-wide. The module exposes
  a whole-registry shutdown path, but no supported per-server disconnect API
  for a profile adapter to invoke.

Using the private `_connect_server()` and `_register_server_tools()` helpers
to manually reconstruct lifecycle would duplicate native orchestration. It
would also leave dynamic refresh, OAuth/config resolution, reconnect, and
unload coupled to the same global maps. Temporarily replacing the registry or
native registration function would introduce process-global races during
concurrent profile startup.

## Required engine contract

The engine needs an explicit immutable profile key on discovery and each
handler, with these maps scoped by `(profile key, server ID)`:

- server task/session and credentials;
- registry registrations and toolset aliases;
- trust, retry, circuit-breaker, and registration provenance state;
- dynamic tool refresh and disconnect cleanup.

Conformance must overlap profiles A and B with same-named servers and distinct
credentials, dispatch calls to both sessions, verify neither profile can see
or invoke the other's tool, and unload A while B remains usable. Namespaced
IDs are still useful within that contract, but are insufficient on their own.

## Resolution

Runtime does not wait for that engine change. Catalog servers bypass native
discovery and use `xnobrain/integrations/managed_mcp.py`, which subclasses
`MCPServerTask`, passes an explicit registry `scope` on every registration,
keys sessions by `(profile scope, generation, logical name)`, and restores only
its own entries on refresh or unload.

Native conversation runs never call `discover_mcp_tools()`. A profile's own
`mcp_servers` go through the same adapter under `custom:<name>`, and fail
open per server with a retry cooldown. A custom entry with a `command` runs as a
stdio process through the same adapter, as the Runtime user with no sandbox.
The global behavior described above
now applies only to the separate `hermes` CLI processes. Each of those
serves a single profile, so it cannot mix profiles.

Conformance lives in `xnobrain/tests/test_managed_mcp.py`. It includes two
profiles that each define a server named `fake` and each receive their own
result.

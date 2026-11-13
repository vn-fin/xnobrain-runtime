# 017 — Desktop node for local MCP servers

Status: plan, not implemented. Depends on the desktop app (plan 013, Full
Managed App track) for the installer and signing, but the node itself can ship
first as a small CLI.

Some catalog plugins need software that runs on the user's own computer, so the
workspace cannot reach them. They are marked `unsupported` with reason
`desktop_application_required` today:

| Catalog id | What must run locally |
| --- | --- |
| `figma` | Figma desktop app, which serves MCP on `127.0.0.1` |
| `obsidian` | Obsidian with its Local REST API plugin, for the user's vault |
| `paper` | Paper desktop app and the open design file |
| `apple-apps` | macOS apps (Notes, Reminders, Calendar, ...) |

This plan adds a **desktop node**: a process on the user's computer that runs
those MCP servers locally and makes their tools callable from the user's
workspace agents, without opening any inbound port on the user's machine.

## Prior art: OpenClaw nodes

OpenClaw solves the same problem with "nodes"
([nodes](https://docs.openclaw.ai/nodes),
[node MCP and skills](https://docs.openclaw.ai/nodes/mcp-and-skills)):

- A node (macOS menu bar app or `openclaw node run`) connects to the Gateway
  over its WebSocket. Remote gateways are reached through an SSH tunnel or
  Tailscale. A node is a peripheral, not a second gateway.
- MCP servers are configured on the node (`nodeHost.mcp.servers`, `command` /
  `args` or `url` / `headers`). The node starts them, lists their tools and
  publishes the tool descriptors to the Gateway.
- Calls go Gateway → node through one command, `mcp.tools.call.v1`. The
  Gateway can ignore every node tool or deny the command outright.
- No OAuth on node-hosted servers; config changes need a node restart; a lost
  session withdraws that server's tools and reconnects with backoff.

We take the shape (outbound connection, servers configured on the node, one
call verb, a server-side kill switch) and fit it to XNOBrain's existing trust
boundaries instead of copying the code.

## Findings: what already exists

- **Control already proxies MCP for Runtime.** Control serves
  `ANY {api}/internal/v1/mcp/:connection_id`
  (`xnobrain-control/internal/v1/routes/Setup.go` line 67,
  `internal/v1/handlers/mcpconnection/handler.go`). A stored connection
  (`internal/services/mcpconnection/service.go`, `d.StoredConnection` with
  `TenantID`, `UserID`, `AgentID`, `UpstreamURL`) holds the upstream and its
  credentials; Runtime never sees them.
- **Runtime already calls tools through that proxy.** A managed server entry
  with `xnobrain_connection_id` becomes a `ManagedMCPServerTask` whose HTTP
  client targets the Control proxy (`xnobrain/integrations/managed_mcp.py`,
  `control_proxy_httpx_client_factory`, `reconnect_control_tasks`). Tool
  registration, per-profile scoping, status (`connected` / `unavailable`) and
  retry with backoff are all in place.
- **Catalog installability is validated in one place**
  (`xnobrain/repositories/plugin_catalog.py`, `DESKTOP_IDS`), with byte-identical
  catalog copies in Runtime and Control.
- **The UI already renders `unsupported` entries** with the "needs a desktop
  application" message (`PluginsView`).

So the only genuinely new piece is a way for Control to forward an MCP request
to a process on the user's computer. Everything from Runtime upward can stay as
it is.

## Design

### Shape

```text
Agent turn (Runtime)
  └─ ManagedMCPServerTask (existing, connection_id)
       └─ HTTP → Control /internal/v1/mcp/{connection_id}   (existing proxy)
            └─ connection.upstream = node://{device_id}/{server}   (new)
                 └─ Control node relay (new) ──WebSocket── Desktop node (new)
                                                              └─ local MCP server
                                                                 (stdio or 127.0.0.1 HTTP)
```

The node is "just another upstream" behind the Control MCP proxy. Runtime does
not learn about devices.

### Components

1. **Desktop node** (new, `xnobrain-runtime/app/` once plan 013 lands; a CLI
   first).
   - Opens one outbound WebSocket to Control with a device token. No listening
     socket on the user's machine except whatever the local MCP server itself
     opens on loopback.
   - Holds a local config of servers (same shape as profile `mcp_servers`:
     `command` / `args` / `env`, or a loopback `url`). Only loopback URLs are
     accepted; a node never becomes a path to the user's LAN or the internet.
   - Starts the servers, keeps their MCP sessions, and answers relayed requests.
     It sends MCP JSON-RPC frames unchanged; it does not reinterpret tools.
   - Shows the user what is shared ("Figma is available to: agent X") and has a
     pause switch that withdraws everything at once.
2. **Control node relay** (new, `xnobrain-control`).
   - Device pairing and device tokens, scoped to one user and tenant.
   - A WebSocket endpoint for nodes, and an in-memory registry of online
     devices. Multi-instance Control needs the registry shared (Redis pub/sub
     or sticky routing); see Open questions.
   - The existing MCP proxy gains one upstream kind: `node://{device}/{server}`.
     It forwards the request over the device's socket and streams the response
     back. If the device is offline it answers like an unreachable upstream, so
     Runtime marks the server `unavailable` and retries with its existing
     backoff.
3. **Catalog** (Runtime + Control copies).
   - New delivery for the four desktop entries, e.g.
     `mcp.transport: "node"`, with the local command or loopback URL the node
     should run. Validator rule: only ids in `DESKTOP_IDS`, exact pinned
     package versions for commands, loopback-only URLs.
   - Installability becomes **conditional**: `installable` when the user has a
     paired node, otherwise still `unsupported` with
     `desktop_application_required`. Control decides this from its device
     registry, never from a UI flag.
4. **UI** (`xnobrain-ui`).
   - A "Devices" section to pair, rename, pause and remove nodes.
   - Desktop catalog cards: "Connect" when a node is online, otherwise the
     current disabled state plus a link to install the desktop app.
   - Installing creates the Control connection with a `node://` upstream and
     then calls Runtime `install-catalog` with that `connection_id`, exactly
     like a remote API-key plugin today.

### Contracts

| Contract | Owner | Notes |
| --- | --- | --- |
| `POST {api}/v1/devices/pairing` → one-time code; `POST .../devices/pair` → device token | Control | User session auth for the first call; code is short-lived and single-use |
| `GET {api}/v1/devices`, `DELETE {api}/v1/devices/{id}` | Control | Lists only the caller's devices |
| `WS {api}/v1/devices/connect` | Control | Device token auth; one socket per device; heartbeat |
| Relay frame `{id, connection_id, server, method, headers, body}` and response/stream frames | Control ↔ node | Versioned (`v: 1`); body is the MCP JSON-RPC payload as-is |
| Connection upstream `node://{device_id}/{server_name}` | Control | Stored like any upstream; validated so it can't be set from browser input to another user's device |
| Catalog `mcp.transport: "node"` | Runtime + Control catalog | Byte-identical copies, as today |

No Runtime API changes. No new Runtime environment variables.

### Security

- **Device ownership is the boundary.** A `node://` connection may only name a
  device whose `UserID` and `TenantID` match the connection's. Checked in
  Control on create and on every proxied request.
- **No tenant or device from browser input.** The UI picks a device by id from
  the caller's own list; Control re-checks ownership. Do not accept a device id
  from the agent or from tool output.
- **The node only runs what the user configured locally.** Control can ask the
  node to run a catalog recipe, but the node shows it and the user approves it
  once per server. A compromised workspace must not be able to start arbitrary
  commands on the user's computer.
- **Loopback only.** Node-side URLs must be `127.0.0.1` / `::1` / `localhost`.
- **Kill switches.** User side: pause in the node. Server side: Control setting
  to refuse all node upstreams, and revoking a device drops its socket and
  marks its connections `unavailable`.
- **No secrets cross the relay in config.** Local API keys (for example the
  Obsidian REST key) stay in the node's local config, like OpenClaw's
  node-side headers. Control stores nothing for them.
- **Logs** record device id, connection id, method and size, never bodies.
- **OAuth is out of scope** for node servers in v1 (OpenClaw skips it too). The
  four desktop entries do not need it.

### Failure behavior

- Node offline: proxy returns an unreachable-upstream error; Runtime marks the
  server `unavailable`; the agent turn continues without those tools (custom
  and catalog servers already fail per server).
- Node reconnects: Control registry updates; the next Runtime sync or retry
  picks it up. No user action.
- Local server crashes: the node restarts it with backoff and reports
  `unavailable` for that server until it is back.
- Slow local call: the existing per-call timeout in Runtime applies; Control
  adds a relay timeout no longer than Runtime's.

## Stages

1. **Spike (no product code).** A throwaway Go relay plus a Python or Node node
   process; prove one Figma desktop tool call end to end through the existing
   Runtime proxy path with `node://` upstream. Measure latency added by the
   relay.
2. **Control relay + pairing.** Device model and migrations, pairing, WebSocket
   endpoint, registry, `node://` upstream in the proxy, ownership checks,
   kill switch. Tests: ownership, offline, revoke, cross-tenant denial.
3. **Node CLI.** `xnobrain-node pair <code>`, `run`, local config, loopback
   validation, per-server approval, pause. Linux and macOS first.
4. **Catalog + UI.** `transport: "node"` for the four entries, conditional
   installability from Control, Devices section, install flow. Tests mirror the
   current catalog and `PluginsView` tests.
5. **Desktop app integration.** Embed the node in the plan 013 Full Managed App
   (tray status, pause, auto-start).

Each stage is shippable and leaves earlier behavior unchanged when no device is
paired.

## Open questions

- Control runs more than one instance in production: share the device registry
  (Redis pub/sub) or route node sockets and proxy requests for a device to the
  same instance?
- Does the node belong to a user (all their agents can use it) or to a single
  agent? Proposal: belongs to the user; each install picks the agent, as today.
- Organization workspaces (runtime-org): may an org agent use a member's
  device? Proposal: no in v1.
- Windows support for `apple-apps` is impossible; should the catalog hide it on
  non-macOS nodes? Proposal: nodes report their platform; Control filters.
- Do we also let users add their own arbitrary local servers through the node
  (not just the four catalog entries)? Proposal: yes in the node config, but
  only after the catalog path is stable.

## Acceptance criteria

- With a paired, online node running Figma desktop, a user installs Figma from
  the catalog for an agent and that agent calls a Figma tool in chat.
- With the node offline, the same agent's turn completes and the Figma server
  shows `unavailable`; it recovers by itself when the node returns.
- A connection can never target another user's or tenant's device (tested).
- Revoking a device stops all its tools within one heartbeat interval.
- No user secret for a local server is stored in Control or Runtime.

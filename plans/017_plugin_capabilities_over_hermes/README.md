# 017 — Hermes plugin capabilities, wrapped for XNOBrain

Status: plan, not implemented. Base: `feat/zalo-bot-platform` (Runtime), which
vendors the `zalo_bot` platform plugin.

## Principle

Hermes already implements the plugin mechanism: discovery, `register(ctx)`, and
24 `register_*` capabilities (platforms, providers, memory, hooks, commands...).
XNOBrain does **not** reimplement any of them. It adds the product layer Hermes
does not have, because Hermes is a single-user CLI:

- identity, tenant and ownership checks;
- a UI instead of terminal prompts;
- credentials held by Control, never by plugin code or chat;
- scan and approval bound to the exact artifact digest;
- per-agent isolation, billing and limits;
- a lifecycle that survives container recreation.

Every item below is classified **use as-is**, **wrap**, or **forbid**.

## Findings

Verified in this repository and in the pinned Hermes (v0.20.2, container
`/usr/local/lib/hermes-agent`):

- The installer accepts only `kind: standalone`
  ([`extension_engine.py`](../../xnobrain/integrations/extension_engine.py)
  line 404). Hermes itself does not impose this; a `kind: platform` plugin such
  as `zalo_bot` is rejected as "unsupported or invalid".
- Platform plugins ship inside the image
  (`runtime/hermes-plugins/platforms/<name>`, `Dockerfile.backend`,
  `install-native-runtime.sh`) and are mounted by the root compose file. There is
  no UI or API to install one.
- Native plugins that declare `dependencies` or `requires_packages` are
  rejected. Plugins that override built-in tools are rejected.
- Extension management is disabled in organization contexts
  ([`docs/agent-extensions.md`](../../docs/agent-extensions.md), "Exposure and
  authority").
- The scanner is a review aid, not a Python sandbox (same document, "Executable
  plugin lifecycle"). Native plugin code can open sockets itself.
- Control already proxies MCP so Runtime never holds a credential:
  `app.All(".../internal/v1/mcp/:connection_id", handler.MCP.Proxy)`
  (`xnobrain-control/internal/v1/routes/Setup.go` line 67,
  `internal/v1/handlers/mcpconnection/handler.go` `Proxy`). Runtime sends only a
  short-lived Bearer grant; Control resolves the connection, injects the
  credential, refreshes OAuth on 401/403, allows GET/POST/DELETE only, caps the
  body at 4 MiB, forwards an allow-list of headers, and answers `no-store`.

## Capability matrix

| Hermes capability | Decision | XNOBrain work |
| --- | --- | --- |
| `register_tool`, `register_hook`, `register_command`, `register_skill` | use as-is | Already supported for `standalone` plugins. |
| MCP servers | use as-is | Existing catalog / plugin MCP flows and Control proxy. |
| `register_platform`, `register_slack_action_handler` | wrap | `kind: platform` support, Channels UI, token via Control, channel-to-agent binding, status. |
| `register_*_provider` (image, video, TTS, transcription, web search, browser) | wrap | Provider selection per workspace, credential via Control proxy, billing and routing. |
| `register_memory_provider`, `register_context_engine`, `register_context_reference`, `register_system_prompt_section` | wrap, later | "Exactly one active" selection; runtime memory is fixed in the image today. |
| `register_cli_command`, `register_auxiliary_task` | wrap, small | Surface in UI/CLI with approval. |
| `register_middleware`, `register_approval_transport`, `register_redaction_patterns`, `register_secret_source`, `register_dashboard_auth_provider` | **forbid** for third-party plugins | Scanner and loader reject them explicitly; only packaged XNOBrain plugins may use them. |
| `dependencies` / `requires_packages` | forbid for now | Needs an isolated environment and supply-chain scanning; out of scope. |

## Plugin proxy in Control (security)

Goal: a plugin never holds a secret and cannot reach arbitrary hosts. This
generalizes the MCP proxy pattern.

### Contract

1. A plugin declares its outbound needs in `plugin.yaml`:
   `egress: [{host, methods, auth: {kind, placement}}]`. `placement` says where
   the credential goes (Bearer header, custom header, or a path template).
2. The declaration is part of the approved artifact digest. Changing it revokes
   approval, like any other byte change.
3. Control exposes `/internal/v1/plugins/:connection_id/*`. Runtime sends only
   a short-lived grant bound to (subject, tenant, agent, connection).
4. Control checks the grant, resolves the connection and its credential, and
   forwards to the declared host only: method allow-list, body cap, header
   allow-list, no redirects to other hosts, private/loopback/link-local
   addresses blocked after DNS resolution, per-connection rate limit, and
   `Cache-Control: no-store`.
5. Control audits metadata only (connection, host, method, status, bytes), never
   content or credentials.
6. Providers that cost money also record usage through the existing billing and
   telemetry path.

### Runtime artifact authority boundary

Control's `ArtifactAuthority` port is backed by the private Runtime route
`POST /xnobrain/api/runtime/v1/internal/plugin-artifact/authority`, called with
the existing server-owned workspace transport and verified personal actor. The
request carries only `{agent_id, plugin, platform, artifact_digest}`; ownership,
tenant, workspace, hosts, paths, rules, and credentials never come from the
browser.

- Named agents require an exact durable owner marker. The logical Big Brother
  root requires the Control-published source identity or the complete managed
  workload identity plus Control's independent durable owner resolution. A
  missing root marker, the one-time source-owner enrollment attestation, and
  the legacy shared token are not ongoing artifact authority.
- Standalone plugins are proven from the actual installed inventory, current
  bounded tree digest, `COMPLETED` non-dangerous scan, explicit approval, and
  enabled state. The inventory/scan record digest and approval digest must both
  equal current bytes; a stale scan record never authorizes newer bytes.
  `caution` findings remain allowed as in execution admission, while dangerous
  or incomplete scans, pending publication/MCP recovery, and tool-override
  entries fail closed. The optional `egress` block in the approved
  `plugin.yaml` uses the same `plugin-egress-v1` schema as Control (8 rules, 16
  endpoints per rule, exact hosts, GET/POST, fixed placeholders, one credential
  placement) and is validated during package admission/scan. No egress
  declaration means `unsupported` for a connection; local plugin behavior is
  otherwise unchanged.
- The built-in `zalo_bot` is proven from the actual engine-bundled path and
  manifest, not from a per-agent approval row or a repository fallback. Control
  retains its fixed Zalo Bot rules. Runtime returns the actual tree digest and
  no rules. Source mode does not publish a release-time platform tree digest;
  this remains image/platform policy evidence, not remote cryptographic
  attestation, and no build pin is invented here.
- Runtime never executes plugin code, writes approval or owner state, cleans
  caches, or returns paths, content, secrets, or foreign owner identities.
  Control computes the canonical rules digest.

## Zalo Bot as the first user

The Bot API puts the token in the URL path (`/bot{token}/getMe`). Through the
proxy the plugin keeps only a placeholder base URL and Control substitutes the
token server-side, so the token never enters Runtime memory, logs or history.
Inbound webhooks (optional upstream mode) terminate at Control, which verifies
the webhook secret and forwards to the private Runtime route; Runtime does not
listen publicly.

### Caveat that must be closed

The proxy is a guarantee only if the plugin cannot bypass it. Native plugin code
is not sandboxed, so Phase 2 also needs a network policy on the Runtime workspace
that allows egress to Control (and the model router) and nothing else. Without
that, treat the proxy as defense in depth, not isolation.

## Phases

| Phase | Scope | Repos | Size |
| --- | --- | --- | --- |
| 0 | Classify plugins by `kind` in the installer; clear error codes instead of "invalid"; explicit forbid list in scanner | Runtime | S |
| 1 | Plugin proxy in Control for `egress` declarations; grant issuing in Runtime; audit | Control, Runtime | M–L |
| 2 | Runtime egress network policy (Compose, Swarm, Incus) | Release, Runtime | M |
| 3 | Channels: `kind: platform` install from the image catalog, Channels UI, token via Control, channel-to-agent binding, status and log | Runtime, Control, UI | L–XL |
| 4 | Providers per workspace (image, TTS, transcription, search) over the proxy with billing | Control, Runtime, UI | L |
| 5 | Memory / context engines (single active selection) | Runtime, UI | L–XL, only on demand |

Phase 0 and the Zalo Bot vendoring are independent of the proxy and can land
first. Phases 3 and 4 depend on Phase 1.

## Tests

- Installer: each `kind` accepted or rejected with the documented code; forbidden
  registrations fail the scan.
- Proxy: grant expiry and binding, host/method/header allow-lists, redirect to a
  different host, DNS rebinding to a private address, oversize body, 401 refresh,
  no secret in any response, log or audit record.
- Isolation: a plugin in tenant A cannot use tenant B's connection id.
- Zalo Bot: pairing and allow-list behaviour through the proxy, token absent from
  Runtime logs and the history directory.

## Out of scope

Pip dependencies for plugins, organization-wide plugin sharing, a public plugin
marketplace for platform plugins, auto-update.

## Open questions

1. Does the pinned engine reject or override a second platform that registers the
   same name? (`zalo` vs `zalo_bot` avoids it; not tested.)
2. Should channels be per agent or per workspace? Hermes runs one gateway per
   host; XNOBrain isolates by profile.
3. Which network layer enforces Runtime egress in each deployment (Compose,
   Swarm, Incus)?
4. Billing model for provider plugins (pass-through vs. markup).
5. Must proxy credentials share the existing MCP connection store, or get a
   separate table with a different retention policy?

## Workload identity decision (2026-11-09; Runtime consumer implemented)

Owner-bound background grants must work without a browser refresh and without
Incus-shaped fixture rows in the Docker source stack. Control owns the identity.
The existing workspace row remains the authority; add a stable opaque
`runtime_workload_id` and durable server-resolved `owner_tenant_id`. Keep the
existing instance-derived internal token contract for legacy managed traffic.
Issue a separate workload-scoped bearer for grant renewal, derived with a
distinct domain separator from the Control key; never give Runtime that key.

Both normal provisioning and source assignment persist these fields from the
verified subject. They never infer a tenant from a user ID or accept ownership
from a request body. Existing managed rows may be backfilled only from matching
server-authored provisioning identity. Unknown or conflicting ownership fails
closed. No synthetic cluster or provisioning row is created for Docker.

For the personal source Runtime, reuse the existing private runtime-secrets
mount and the Control-owned static assignment path. Atomically write one
`workload-identity.json` containing workspace/workload IDs, owner/tenant, and
the derived bearer, mode 0600. Preserve one existing owner: an attempt to bind
the same source mount to another active owner returns a conflict. Do not rotate
or replace the shared Runtime internal token. The static proxy uses the real
server-resolved workspace ID for personal routes; organization routing remains
unchanged until organization channels are supported. The identity survives
process restarts. DB commit precedes file publication; retries reuse the stable
ID, and a failed publication cannot silently reassign the mount.

Source-owner proof is one-time private enrollment, not renewal. Runtime exposes
`POST /xnobrain/api/runtime/v1/internal/source-owner/probe` only to a
Control-signed Personal actor and returns status only. An existing strict
version-1 identity file is authoritative; owner mismatch/malformed content
never falls through. A configured future identity path may be absent during
first enrollment, in which case only the paired exact operator attestation
`RUNTIME_SOURCE_OWNER_USER_ID` + `RUNTIME_SOURCE_OWNER_TENANT_ID` can bootstrap
already-existing data. No identity file, no attestation, or only matching
per-agent markers returns `legacy_unproven`; missing markers, the default Big
Brother profile, and volume-initialized markers are not freshness proof. The
probe inspects the configured root profile, direct profiles, and supported
legacy `<legacy-agents>/<name>/.profile` records with bounded pre-sort
enumeration and no-symlink reads. The root profile uses logical agent ID
`big-brother` regardless of directory basename; legacy records use their logical
name, not `.profile`. It never writes or claims state and returns no
owner/path/content/token. Control must deny this path at public relays. Remove
the bootstrap attestation after authoritative identity publication.

Grant renewal resolves current workspace ownership/status from Control's durable
record, validates the workload bearer, and rejects stopped/deleted/reassigned
workspaces. Cluster/provisioning guards remain for legacy managed identities;
new workload identities do not pretend that Docker runs on Incus. Runtime reads
the private identity file, checks it against the verified channel owner, and
keeps short-lived grants in memory. No secret enters run records, event payloads,
logs, UI responses, or chat.

Runtime consumer contract: the default private file is
`/run/xnobrain-runtime/workload-identity.json`, or the absolute
`RUNTIME_WORKLOAD_IDENTITY_FILE` override (authoritative; missing/malformed does
not fall back). The file must be version 1, regular, `0600`, owned by the
Runtime effective UID, bounded, and contain bounded printable string
`workspace_id`, `workload_id`, `user_id`, `tenant_id`, and `token`; user/tenant
must match the verified channel owner binding. Non-regular paths are opened
non-blocking and rejected promptly. The dev container Runtime runs as root, so
that deployment needs a root-owned `0600` file; other deployments must match
their Runtime UID. Complete managed env
`RUNTIME_WORKSPACE_ID`/`RUNTIME_WORKLOAD_ID`/`RUNTIME_WORKLOAD_TOKEN`/
`RUNTIME_WORKSPACE_OWNER_TENANT_ID` takes precedence; values must be bounded
printable opaque strings and any partial/malformed workload env fails closed.
The legacy installation bearer is allowed only with a managed workspace ID,
never the shared static dev token without one. Identity is read before
grant-cache reuse; workspace/workload/owner/token-rotation fingerprints key the
cache, so rotation, deletion, malformed identity, or a source-to-legacy
transition cannot reuse an old grant after a failed refresh.

Acceptance: real PostgreSQL tests for normal and source identity, restart/renewal,
wrong owner/tenant/workload/token, failed publication and conflicting source
assignment; managed provisioning contract tests; no shared-user fixture mutation;
no master key in Runtime, no fake Incus metadata, and no browser warm-up needed
after initial verified assignment. Source implementation and tests do not imply
a deployed Incus verification.

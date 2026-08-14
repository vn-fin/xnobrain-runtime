# E03 approaches — decisions and trade-offs

Five decisions. Each cites the constraint that forces or tilts it. Contract
context: [`docs/contracts/device-command-v1.md`](../../../docs/contracts/device-command-v1.md)
allows "WebSocket or long polling with HTTP polling fallback".

## A. Transport: WebSocket vs long-poll first — **long-poll first**

| | Long-poll (chosen for v1) | WebSocket |
|---|---|---|
| Proxy/NAT traversal | Plain HTTPS requests; survives every corporate proxy, TLS-inspecting middlebox, and LB | Upgrade handshake and long-lived idle connections are exactly what proxies kill (findings §7) |
| Implementation | One `httpx` call with a 25–30 s server hold; retry loop is the backoff loop | Extra dependency/state: ping/pong, half-open detection, reconnect logic distinct from HTTP retry |
| Latency | Worst case one hold interval (~30 s) for command delivery — fine for fleet ops (ping, restart, flush) | Near-instant push |
| Server cost | One parked request per device; acceptable at this fleet scale | One socket per device; comparable |

Command latency of seconds-to-half-a-minute is irrelevant for E03's command
set. The cursor + at-least-once semantics are transport-agnostic, so
upgrading to WebSocket later is additive, not a rewrite: keep the poll
endpoint as the contract's mandated fallback either way. **Decision:
long-poll in v1; WebSocket is a later optimization behind the same journal
and cursor.**

## B. Connector placement: FastAPI lifespan task vs separate process — **lifespan task**

Precedent: the kanban dispatcher (`xnobrain/app.py` starts
`dispatcher_loop()` as an `asyncio.create_task` inside the wrapped lifespan
and cancels it on shutdown). The connector follows the same shape.

- **Lifespan task (chosen):** zero new deployment surface (one process on
  :8642 stays true, as plan 009 preserved); direct in-process access to
  `PlatformService` so command routing is a method call, honoring "route
  through shared services, never direct filesystem functions"
  (`03-device-connector.md`); trivially dormant — if `ENTERPRISE_API_URL` is
  unset, `register()` simply doesn't create the task. Bounded by
  construction: one task, one in-flight command, one bounded status-retry
  queue — no unbounded task creation (spec's backpressure rule).
- **Separate process:** isolates a crashing connector from the API, and would
  let the connector observe an API restart. Rejected: a supervisor and IPC
  channel are exactly the deployment complexity the OSS product avoids, and a
  separate process would need an HTTP client back into the API to reach
  services — more surface, same capability. Crash isolation is handled
  instead by wrapping the loop body in a catch-all with backoff (a connector
  bug degrades to "device offline", which the whole design already tolerates:
  device offline never blocks local).

One consequence accepted: during `runtime.restart_gateway` the connector dies
with the process — which is why the terminal status is journaled and posted
*before* the exit signal (architecture §5), and why reconnect-with-cursor
must be idempotent anyway.

## C. Incus driver: shell out to `incus` CLI vs REST client — **REST, CLI acceptable v1**

- **REST via unix socket / HTTPS :8443 (recommended):** typed request/response
  structs in Go, machine-readable errors, async-operation handles for
  create/start (needed for rollout state tracking), no PATH/locale/parsing
  fragility, testable against a recorded transport. Incus's API is the
  product's actual interface; the CLI is a client of it. Cost: writing/vendoring
  a thin client (the `incus` Go client package exists — **verify** its module
  path and license fit in Phase 0) and TLS trust bootstrap.
- **CLI shell-out:** faster to first demo; `--format json` output helps. But
  parsing stderr for failure modes, managing concurrent invocations, and
  correlating async operations is exactly the brittleness that turns into
  3 a.m. pages, and `docs/enterprise-extension.md`'s audit/verify steps need
  reliable outcome data.

**Decision: REST client behind a Go `Driver` interface
(`internal/incus/driver.go`); a CLI-backed implementation of the same
interface is an acceptable stopgap in v1 if the client-library verification
stalls — the interface makes the swap invisible to `internal/fleet`.** The
interface is also the seam for the fake driver used in Go tests.

## D. Pre-enrollment token delivery: env at create vs first-boot fetch — **one-time token env + immediate exchange + discard**

- **Env at create (chosen):** provisioner mints a *single-use, short-TTL,
  user-bound* enrollment token (E01) and injects it as
  `XNOBRAIN_ENROLLMENT_TOKEN` instance config. On first boot the connector
  presents it during register; the server atomically consumes it and binds
  the new device to the owning user (**boots pre-enrolled/claimed**). The
  connector then never reads the env again; long-term identity is the Ed25519
  key + rotating tokens under `DATA_DIR/device/` — exactly the contract's
  identity model. Residual risk: instance env config is readable via the
  Incus API afterwards, which is why the token must be **one-time** — after
  the exchange it is dead weight (optionally the driver clears the config key
  post-claim; nice-to-have, not load-bearing).
- **First-boot fetch (e.g. metadata service or exec-in):** avoids persisting
  the token in instance config, but requires either an inbound channel into
  the container or an exec dependency at boot — both against the
  outbound-only grain, and a new bootstrap service to build and secure.

A PC install is the same code path with the token *absent*: the user pastes a
pairing token (or enrolls anonymously and claims later, per the contract)
through the Settings Enterprise panel. One register flow, two token sources.

## E. Staged rollout policy shape — **fixed canary count + health window + halt**

Options considered for the rollout record:

1. **Percentage waves** (5% → 25% → 100%): right for thousands of devices;
   over-engineered for fleets sized by `docs/plans.md` sandbox counts.
2. **Fixed canary N → all (chosen):** `{image, canary_count, health_window,
   max_failures}`. Replace N sandboxes, hold for the health window (device
   reconnected + healthy heartbeats + no failure threshold breach), then
   proceed batch-wise to the remainder; **halt** (not auto-rollback of the
   whole fleet) on gate failure, with canary rollback = replace back to the
   previous pin via the same drain sequence. Simple to reason about, simple
   to audit, and the per-device state table (architecture §6) is the same one
   percentage waves would need later — so option 1 remains a pure extension.
3. **Manual one-by-one:** the degenerate `canary_count = fleet` case; kept as
   the escape hatch (admin can PATCH a single sandbox's image), not the
   policy.

Health gate signal is deliberately only what E03 already collects: reconnect
with intact cursor + heartbeat health. No new probing machinery. Aligns with
enterprise README build-map #9 ("staged rollouts, compatibility gates against
the pinned OSS runtime") — the compatibility-gate half stays in that later
plan.

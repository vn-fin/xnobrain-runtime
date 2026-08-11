# Entitlements and quota protocol v1

This is the shared semantic contract. Transport may be an in-process interface for Community and authenticated HTTP/gRPC for enterprise.

## Entitlement document

An entitlement response contains version, edition, plan ID, subject/tenant, issued/expiry times, revision/ETag, numeric limits, capability flags, and hardware class. Numeric `-1` is unlimited and `0` is unavailable. Unknown fields are ignored by older clients; unknown required capabilities fail closed.

Initial numeric resources include agents, cron definitions, cron concurrent runs, cron runs/day, cron runs/month, provider connections/type, teams, agents/team, delegated workers, delegation depth, MCP servers, messaging channels, webhook routes/runs, browser minutes, MoA turns, bundle bytes, and storage bytes.

Capabilities include managed scheduler, managed backup, managed telemetry, hardware selection, custom plugins, advanced cron chains/scripts, collaboration, RBAC, SSO, audit export, and batch trajectories.

## Quota operations

`Check` is advisory. `Reserve` is authoritative and accepts subject, resource, quantity, UTC period, idempotency key, expiry, and correlation. `Commit` marks consumption at the defined execution boundary. `Release` returns eligible unused capacity. Responses include allowed, limit, used/reserved, remaining, reset, reservation ID/state, and stable denial code.

Daily and monthly cron resources are reserved atomically as one group. Concurrency is a renewable lease. Count limits are enforced transactionally with the underlying mutation in enterprise and under one file lock in Community.

Stable errors include `quota_exhausted`, `plan_required`, `reservation_expired`, `reservation_conflict`, and `capability_unavailable`. HTTP presentation uses `429` for exhausted consumable quota, `403` for unavailable plan capability, `401` for authentication, and `413` for upload size.

Telemetry and headers never serve as accounting state.

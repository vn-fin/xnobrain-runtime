# XNOBrain enterprise extension contract

The authoritative Free, Pro, and Enterprise feature matrix and recommended limits are defined in [plans, features, and limits](plans.md). This document defines repository and deployment ownership rather than duplicating those values.

Implementation is split into independently assignable work packages under [the OSS roadmap](implementation/README.md) and the companion `xnobrain-enterprise/docs/implementation` directory. Versioned device and portability protocols belong in this public repository so both sides can test compatibility without importing private code.

The open-source repository must remain a complete downloadable product. Its Docker Compose file builds or pulls only public XNOBrain and Hermes images; it must never require a private enterprise image or a cloud account.

The recommended downstream project is a thin private composition named `xnobrain-enterprise`, not a copy of this backend. It pins a released XNOBrain module and runtime image, implements `pkg/edition.Policy`, and adds only proprietary control-plane capabilities. If its binary imports this repository's `internal` composition packages, its Go module path must be nested under `github.com/vn-fin/xnobrain/` so Go's internal-package rules permit the import. A future public application builder can remove that constraint; the policy contract itself is already public.

## Ownership split

The shared runtime continues to own profile isolation, safe paths, snapshots,
conversations, Hermes invocation, personal blends, and service-level enforcement.
It calls the centralized LLM router directly with a workload token and does not
host router state. Control owns authentication, tenant and plan resolution,
billing entitlements, provider-connection administration, distributed quota
reservations, RBAC, audit events, managed container orchestration, and telemetry
retention. Router credentials and usage remain in the centralized router's
PostgreSQL-backed store. Neither project may introduce an ORM in Runtime.

Use separate versioned images:

- `xnobrain:<version>` is the public, self-contained local application and the base runtime contract.
- `xnobrain-enterprise:<version>` is the private API/control-plane composition.
- A pinned Hermes worker image runs agent workloads. Enterprise orchestration may select CPU, memory, storage, and accelerator classes without rebuilding the Studio API image.

Enterprise deployments may pull the public runtime image from the open-source release. The reverse dependency is forbidden: the open-source deployment never pulls the enterprise image.

## Limits and accounting

`pkg/edition.Policy` returns limits for the authenticated principal. Numeric `-1` means unlimited. The existing middleware covers total agents, total cron definitions, parallel cron runs, monthly cron runs, and additive connections per provider type. Replacement of an existing provider account remains allowed so a user can rotate credentials even when at the limit.

The local edition uses application storage and in-process concurrency because it is a single-node product. A multi-replica enterprise deployment must make create and run reservations atomic in a shared control-plane database. The control plane is the billing source of truth; HTTP headers and traces describe decisions but are not accounting records. Reservations need an idempotency key, tenant ID, resource name, quantity, UTC period, and final committed or released state.

Cloud Free, Personal Pro, and Enterprise share the private PostgreSQL control plane. Cloud Free and Pro each resolve one authenticated member in one personal tenant with different quotas and hardware classes. Enterprise adds organizations, multiple members, RBAC, SSO, audit policy, and contract-configurable entitlements. Deployment location does not change the paid-edition data model; only self-hosted Free uses the target file-only mode.

## Container hardware upgrades

Hardware upgrades are an entitlement and orchestration operation, not a rate-limit counter. Store a desired resource-class ID on the managed sandbox, validate the plan, drain active runs, apply the new CPU/RAM/storage limits through the container platform, restart or replace the worker, verify health, and emit an audit event. Keep profile data on a persistent volume so replacement does not move agent memory or skills. The local edition reports host/container resources but does not resize its own container.

## Telemetry

The shared runtime already emits OpenTelemetry traces and structured logs. Quota middleware attaches resource, limit, usage, remaining, and allowed attributes and logs denials. Enterprise should add tenant-safe resource attributes, an authenticated collector, sampling rules, retention, usage dashboards, and audit correlation. Never attach provider keys, prompts, memory, or other credentials to spans.

Enterprise principal resolution should call the existing external auth service over gRPC rather than migrating authentication code here. Tenant identity must flow into policy, repositories, storage prefixes, telemetry, and quota reservations. Enterprise migrations may extend but must not rewrite the open-source migration history.

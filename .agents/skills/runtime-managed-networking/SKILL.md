---
name: runtime-managed-networking
description: "Implement, review, or diagnose XNOBrain managed Runtime networking: private Runtime gRPC relay, Incus workspace settings, centralized router HTTP/SSE, service identity, health/readiness, deadlines, streaming, and trace propagation. Use for runtime_gateway, llm_router, managed image environment, or cloud connectivity."
---

# Runtime managed networking

Read `AGENTS.md`, root `../proto/README.md`, and
[transport-boundaries.md](references/transport-boundaries.md).

1. Preserve the public flow: browser HTTP terminates at Control; Control and the
   node gateway reach Runtime through the private streaming gRPC contract.
2. Keep the Runtime gRPC service an authenticated adapter to the existing
   FastAPI/Hermes process. Never trust browser headers as service identity or
   workspace ownership.
3. Keep Hermes inference on the router's supported OpenAI-compatible HTTP/SSE
   contract until `xnobrain-router` owns a maintained gRPC server. Do not claim
   the reserved router protobuf is implemented.
4. Bound connection establishment and external operations, propagate
   cancellation and W3C trace context, validate frame order/chunk bounds, and
   never retry after request or response commitment.
5. Separate process liveness, Runtime readiness, workspace availability, and
   router dependency health. A router outage must not make the Runtime process
   appear dead.
6. Use `RUNTIME_*` configuration and stable private DNS/service endpoints; do
   not introduce node-specific bridge addresses as production contracts.
7. Test authentication, malformed streams, unavailable loopback/route,
   cancellation, startup/shutdown, and redaction.

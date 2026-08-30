---
name: runtime-fastapi-api
description: Add, change, review, or diagnose XNOBrain Runtime FastAPI routes, Pydantic contracts, response envelopes, uploads, downloads, and SSE streams. Use for xnobrain/routes, models, handlers, or public Runtime API behavior; use runtime-skill for embedded Hermes extensions.
---

# Runtime FastAPI API

Read `AGENTS.md` and [api-workflow.md](references/api-workflow.md) before editing
an API contract.

1. Select one service-group name and trace its route declaration, Pydantic
   model, operation handler, service, repository/integration, caller, and tests.
2. Add routes in the group's `xnobrain/routes/<group>.py`; keep
   `routes/setup.py` as assembly and compatibility adaptation only.
3. Keep request/response translation in handlers, rules in services, atomic
   file operations in repositories, and Hermes/router protocols in integrations.
4. Preserve `/xnobrain/api/runtime/v1`, `APIEnvelope`, OpenAPI generation,
   streaming cancellation, bounded uploads, and profile/path isolation.
5. Define stable request shapes with Pydantic instead of untyped dictionaries
   at the public boundary. Return safe errors without upstream bodies or user
   content.
6. Add focused tests under `xnobrain/tests/`; update `docs/api.md`, contract
   fixtures, and intentional UI/Control consumers when observable behavior changes.

Use `$runtime-managed-networking` for private gRPC, router, or managed-workspace
transport and `$runtime-verification` to choose completion checks.

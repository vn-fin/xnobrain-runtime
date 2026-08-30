# XNOBrain Runtime agent instructions

## Mission

This repository owns the private Python/FastAPI Runtime, original Hermes
integration, profile-local persistence, Runtime packaging, and private
Control/node-gateway adapter. The React workspace UI is in `../xnobrain-ui`;
managed tenant APIs are in `../xnobrain-control`; centralized provider routing
is in `../xnobrain-router`.

Prefer small, explicit changes that preserve the original Hermes behavior and
stable XNOBrain contracts. Do not copy upstream engine code or move managed
control-plane behavior into a workspace Runtime.

## Instruction and contract precedence

1. The current user request.
2. The workspace root `../AGENTS.md`, this file, and closer agent instructions.
3. Current code, tests, versioned contracts, and release metadata.

Inspect the working tree before editing and preserve unrelated changes. Read
`.agents/rules/01-start-here.md` and only the additional rules relevant to the
task.

## Use repository skills

Select and read the smallest relevant set under `.agents/skills/`:

- `$xnobrain-runtime` — general feature ownership and end-to-end Runtime work.
- `$runtime-fastapi-api` — FastAPI routes, Pydantic contracts, envelopes,
  uploads/downloads, and SSE.
- `$xnobrain-backend` — Python service-group implementation and refactoring.
- `$runtime-managed-networking` — private gRPC relay, Incus settings, router
  HTTP/SSE, service identity, health, and trace propagation.
- `$runtime-skill` — mandatory for Hermes tools, plugins, hooks, commands,
  skills, memory, profiles, or embedded-engine extensions.
- `$runtime-onefile-build` — Nuitka one-file API compilation, dynamic Hermes
  imports, package data, image startup, and compiled Incus validation.
- `$runtime-verification` — focused tests, smoke, image, proto, and Incus checks.
- `$tauri-app-development` — only for the separate `app/` tree.

Legacy `$xnobrain-frontend` and `$xnobrain-ui` guidance applies only to historic
Runtime UI review contexts; authored web UI work belongs in `../xnobrain-ui`.

## Architecture and dependency rules

```text
routes -> operation handlers -> services -> repositories/integrations
                                  |             |-- atomic profile files
                                  |             |-- Hermes adapters
                                  |             `-- router/private gRPC adapters
                                  `-> models define Pydantic public contracts
```

- `xnobrain/routes/setup.py` is the only XNOBrain route assembly point.
- Use one stable service-group name across routes, models, operations, services,
  and repositories. Composition modules remain composition only.
- Handlers own HTTP/SSE translation; services own rules; repositories own
  atomic local files; integrations own external protocols.
- Local layers call directly, never through the local HTTP API.
- Preserve the original Hermes FastAPI host and one Runtime/Hermes process per
  workspace. Do not add Go, PostgreSQL, an ORM, or another API process.
- Managed images expose FastAPI privately on `8642` and optional Runtime gRPC on
  `3001`; local container packaging may map its private FastAPI port differently.
  Do not publish workspace APIs directly to the browser.
- Runtime/Hermes uses the centralized router's supported OpenAI-compatible
  HTTP/SSE endpoint with a scoped key. Do not install the router in a workspace
  or treat the reserved router protobuf as implemented.
- Runtime configuration uses `RUNTIME_*`. Keep memory dependencies in the image
  or installer; do not add root-stack Redis/vector services.

## Persistence, security, and streams

- Profile data belongs below `DATA_DIR/profiles/<agent-id>`; skills belong below
  that profile's `skills/<skill-id>/SKILL.md`.
- Resolve paths beneath the profile root and reject traversal and symlink
  escapes.
- Snapshot before promised persistent mutations and write mutable files with
  temp-file, fsync, and rename semantics.
- Preserve profile isolation, streaming event order, cancellation, stop
  behavior, approval flow, and graceful optional-dependency failure.
- Never log or trace credentials, headers, prompts, request bodies, provider
  payloads, tool arguments, tool output, or user file content.

## Change workflow

1. Trace the existing caller, route, model, operation, service,
   repository/integration, tests, and relevant docs.
2. Define observable paths/shapes, errors, persistence keys, auth/service
   identity, streaming, environment, and compatibility before changing them.
3. Implement the smallest complete slice in the owning service group.
4. Update `docs/api.md`, `docs/contracts/`, UI/Control consumers, packaging, and
   release compatibility only when their contracts intentionally change.
5. Add focused tests, then use `$runtime-verification`. Run `make check` when
   practical and report exact observed commands/results.

Do not edit generated protobufs, `dist/`, sibling repositories, or `app/`
without explicit scope. FastAPI generates OpenAPI at runtime; do not add a
static OpenAPI file.

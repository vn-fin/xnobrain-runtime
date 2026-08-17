# XNOBrain Agent Guide

XNOBrain is an open-source FastAPI/Hermes runtime. The React UI lives in the
sibling `xnobrain-ui` repository. Read this
file, `.agents/rules/01-start-here.md`, and applicable additional `.agents/rules/`
files before every task. For roadmap work,
also read the assigned specification and referenced versioned contracts.

Working code only. Finish the requested job and verify it; plausibility is not
correctness.

## Operating principles

- Inspect the relevant code, callers, tests, and configuration before editing.
- Never fabricate paths, APIs, commands, results, or repository state. Check
  them directly.
- State a short plan before non-trivial work and define how completion will be
  verified.
- Resolve ambiguity from the code when possible. Ask only when two reasonable
  interpretations would materially change the result.
- Make the smallest coherent change that fulfills the request. Avoid unrelated
  refactors, formatting, abstractions, or speculative features.
- Match the repository's existing naming, layout, error handling, and testing
  patterns.
- Preserve user changes in a dirty worktree and clean up only artifacts created
  by the current change.
- Run focused checks while iterating and `make check` before final handoff when
  the full suite is practical. Never report success without reading the result.
- Changes may span related repositories when the user explicitly places them in
  scope. Before writing in another repository, read and follow that repository's
  `AGENTS.md` and `.agents` rules as well.

## Project context

- Backend: Python, FastAPI, Pydantic, the original Hermes agent runtime, and
  OmniRoute.
- Runtime: one combined backend/agent image and one provider runtime process.
- Local persistence: atomic files below `DATA_DIR`; no application database.
- Optional managed features: the separate Enterprise API configured through
  `ENTERPRISE_API_URL`.

## Commands

- Run the Docker application: `make run`
- Run backend tests: `make test`
- Run all repository checks: `make check`
- Run the backend directly: `make backend`
- Build Docker images: `make build`
- Run the API smoke test: `make smoke-api`

Prefer a focused Python test or `npm test -- <test>` during iteration.
Run UI tests and builds in the sibling `xnobrain-ui` repository.

## Architecture rules

- Do not add Go, PostgreSQL, an ORM, or another application API process.
- Keep one FastAPI/Hermes process on private port 3000 and one OmniRoute
  process. Traefik is the only published web port in the root development
  stack.
- Preserve the original Hermes core and native FastAPI routes. Extend them from
  `xnobrain` instead of copying or forking Hermes.
- `xnobrain/routes/setup.py` is the only XNOBrain route assembly point.
- Handlers own HTTP translation, services own rules, repositories own atomic
  files, integrations adapt Hermes CLI and OmniRoute, and models are Pydantic.
- Use one consistently named service-group file per backend layer. Keep
  registries, `services/platform.py`, and repository facades limited to
  composition. MCP is its own group and does not belong to workspace.
- Local layers call each other directly rather than through HTTP.
- Resolve user paths beneath their profile root and reject traversal and symlink
  escapes.

## Persistence and security

- Agent-owned data belongs under `DATA_DIR/profiles/<agent-id>/`.
- Agent-created skills belong under
  `DATA_DIR/profiles/<agent-id>/skills/<skill-id>/SKILL.md`.
- Every memory, skill, or config mutation that promises persistence creates an
  immutable snapshot before success and writes mutable state atomically using
  temp-file, fsync, and rename semantics.
- Never return, log, or trace credentials, authorization headers, request bodies,
  prompts, provider keys, tool arguments, or tool output.
- Preserve structured metadata logs, OpenTelemetry propagation, streaming run
  events, stop behavior, and the Hermes approval path.

## Deployment boundaries

- Local OSS access is unlimited. Enterprise behavior is optional; an Enterprise
  API outage must not restrict local features.
- This repository builds the combined backend/agent runtime image. The UI image
  is built by `xnobrain-ui`.
- Managed control-plane services remain in `xnobrain-enterprise`; coordinate
  versioned contracts when a feature changes both repositories.
- Incus and cloud runtime packaging are maintained in this repository. The
  managed control plane consumes released, versioned runtime artifacts and
  must not rebuild the runtime.

## Project learnings

- Use the `.yaml` extension for Compose files.
- Do not add a static `docs/openapi.yaml`; FastAPI generates OpenAPI at runtime.
- Keep the runtime Compose build context at this repository root; the UI image
  is built from the sibling `xnobrain-ui` repository.

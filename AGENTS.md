# Open Lumora Agent Guide

Open Lumora is an open-source FastAPI/Hermes and React application. Read
`.agents/rules/01-start-here.md` before changing code. For roadmap work, also
read the assigned specification and referenced versioned contracts.

## Non-negotiable rules

- Write only inside this repository. Related repositories are read-only references.
- Do not add Go, PostgreSQL, an ORM, or another application API process.
- Keep one FastAPI/Hermes process on port 8642 and one 9router process.
- Preserve the original Hermes core and native FastAPI routes; extend them from
  `open_lumora` rather than copying or forking Hermes.
- `open_lumora/routes/setup.py` is the only Open Lumora route assembly point.
- Handlers own HTTP translation, services own rules, repositories own atomic
  files, integrations adapt Hermes CLI and 9router, and models are Pydantic.
- Agent-owned data belongs under `DATA_DIR/profiles/<agent-id>/`.
- Agent-created skills belong under
  `DATA_DIR/profiles/<agent-id>/skills/<skill-id>/SKILL.md`.
- Every memory, skill, or config mutation that promises persistence creates an
  immutable snapshot before success and writes mutable state atomically.
- Never return or log credentials, request bodies, prompts, or provider keys.
- Preserve structured metadata logs, OpenTelemetry propagation, streaming run
  events, stop, and the Hermes approval path.
- Local OSS access is unlimited. Enterprise behavior is optional and reached
  only through `ENTERPRISE_API_URL`; its outage cannot restrict local features.
- This repository builds only the combined backend/runtime Docker image and UI
  image. Incus/cloud runtime packaging is owned by the enterprise repository.

## Validation

Run `make check`. For focused work run the Python tests and
`cd src && npm test && npm run build`.

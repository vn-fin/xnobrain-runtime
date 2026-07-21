# Open Lumora Agent Guide

Open Lumora is an open-source Python/FastAPI and React application built around
the original Hermes Agent CLI/core. Read `.agents/rules/01-start-here.md` before
changing code. For roadmap work, also read the relevant specification and every
referenced versioned contract under `docs/contracts/`.

## Non-negotiable rules

- Write only inside this repository. Related repositories are read-only references.
- Never introduce an ORM or Community application database. Community state is
  stored in atomic profile/configuration files; Hermes and 9router may retain
  their native embedded local stores.
- Keep one backend package named `open_lumora`; do not add separate
  `hermes_api`, `studio_api`, or `open_lumora.data` packages.
- Keep boundaries truthful: `repositories` owns persistence, `integrations`
  owns Hermes/9router adapters, `services` owns business behavior, `handlers`
  owns HTTP behavior, `models` owns Pydantic contracts, and `routes/setup.py`
  is the only Open Lumora route assembly point.
- The root backend entrypoint is `server.py`; it must start one FastAPI server
  on port 8642 and keep Swagger/OpenAPI enabled.
- Preserve upstream Hermes core/CLI behavior and native routes. Do not start a
  separate API server per profile.
- Agent-owned data belongs under `DATA_DIR/profiles/<agent-id>/`.
- Agent-created skills belong under
  `DATA_DIR/profiles/<agent-id>/skills/<skill-id>/SKILL.md`; never write them to
  the root/shared profile.
- Every memory or skill mutation creates an immutable local snapshot before
  returning success. Persistent approvals belong atomically in that agent's
  `config.yaml`.
- Self-hosted OSS access is unlimited for agents, profiles, skills, memory,
  MCP, providers, teams, and local cron. Subscription limits apply only to
  managed resources and authenticated Enterprise API features.
- `ENTERPRISE_API_URL` is optional. Enterprise loss must never restrict local
  Hermes behavior, and Enterprise must not carry a private copy of the
  `open_lumora` package.
- Preserve structured logs, redacted OpenTelemetry spans/propagation, SSE run
  stop, and the Hermes approval-core path.
- Docker has two application images: the React UI and the unified
  FastAPI/Hermes/9router runtime. Traefik is the edge router. Incus deployment
  packaging belongs to the Enterprise deployment repository, not this OSS tree.
- `make build` builds the two application images and their split OCI transfer
  bundle under `bin/images`; every part stays below 50 MB.

## Validation

Run `make check`. For focused backend work, run the Python tests under
`open_lumora/tests`. For frontend work, run `cd src && npm test && npm run build`.
For deployment changes, build both images, start Compose, and run
`make smoke-api` through Traefik.

# Development and verification

## Requirements

- Go 1.26 or newer
- Node.js 22 or newer and npm
- Hermes CLI for real chat runs

Copy `.env.example` to `.env`. `npm run dev` starts Vite on port 5173 and Fiber on port 3000. Vite proxies API traffic to Fiber. `make check` formats and verifies Go, runs backend and frontend tests, and produces the browser bundle.

The Community process is file-backed and has no database, Redis, Kafka, login, or cloud dependency:

```bash
go run cmd/main.go
```

## Important checks

After creating an agent, verify a skill at `DATA_DIR/profiles/<id>/skills/<skill>/SKILL.md` and confirm it does not appear under `DATA_DIR/root/skills`. Resolve a memory or skill approval using `choice=session`, restart the service, and inspect `profiles/<id>/config.yaml`; its persisted choice and disabled write prompt must remain.

The provider page needs the local 9router process at `NINE_ROUTER_URL`. Chat additionally needs at least one valid provider connection. A missing provider should produce a visible runtime error rather than silently using a shared credential.

## Windows

Docker Compose is the supported zero-toolchain route. For native development use `scripts/dev.ps1` from PowerShell after installing Go, Node/npm, and Hermes.

## Latest local evidence

On 2026-07-20, `make check` and the container build passed: Go and contract tests/vet, Python Hermes extension tests/compilation, frontend tests, TypeScript compilation, and the Vite production bundle. The managed import contract was also exercised end-to-end by the enterprise test against a real loopback Studio process, including authenticated stage, commit, visibility, idempotency, and rollback. `npm audit --audit-level=high --omit=dev` reported zero known production dependency vulnerabilities. The complete local Compose stack was then exercised on Docker Engine 29 with Traefik 3.6.16, ClickHouse, Studio, the enterprise gateway, scheduler, frontend, and Hermes runtime healthy. UI routing, aggregate dashboard queries, dependency graph data, per-agent approval persistence, and `profiles/<agent>/skills/<skill>/SKILL.md` placement passed live smoke checks.

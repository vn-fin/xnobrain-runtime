# Open Lumora

Open Lumora is a self-hosted, low-code workspace for building Hermes AI agents. The React UI and Go API ship as one application; every agent gets an isolated file-backed profile with its own config, memory, skills, workspace, sessions, cron state, and immutable snapshots.

## Start

The portable path for Windows, macOS, and Linux is Docker Compose. This public
repository builds only the React frontend and Go Studio backend. Start the
enterprise-owned `open-lumora-gateway` and runtime stack first, then start the
public UI/API without exposing either application container directly:

```bash
make build
make install
```

Open <http://localhost>. The open-source edition has no login. Its default local quotas are four agents, four cron definitions, one parallel cron run, 10 cron runs per UTC day, 200 cron runs per UTC month, and one connection per provider type. The values are explicit environment settings so a redistributor can choose its local edition defaults.

For local development, install Go 1.26+, Node.js 22+, npm, and Hermes, then run:

```bash
npm run dev
```

The backend alone runs from the repository root with `go run cmd/main.go`.

Validation:

```bash
make check
```

## Profile safety

Agent `abc123` owns only `DATA_DIR/profiles/abc123`. Skills are always written to `profiles/abc123/skills/<skill-id>/SKILL.md`; memory is stored in `profiles/abc123/memories`. “Allow and remember” changes the selected agent's `config.yaml`, so the permission survives browser sessions and application restarts. Root defaults are never modified by an agent approval.

Provider credentials are delegated through `open-lumora-gateway` to the
enterprise-built 9router/Hermes runtime and are not stored by Studio.

Current limits and usage are available at `GET /api/v1/limits`. Limited write endpoints return HTTP `429` with `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Policy`, and, for monthly usage, `RateLimit-Reset` headers.

See [plans and limits](docs/plans.md), [implementation roadmap](docs/implementation/README.md), [architecture](docs/architecture.md), [development and verification](docs/development.md), [API guide](docs/api.md), [OpenAPI contract](docs/openapi.yaml), and [enterprise extension contract](docs/enterprise-extension.md).

Image builds, offline split bundles, domains, and local/cloud startup are
documented in [deployment](docs/deployment.md).

## License

Add the project license before public distribution.

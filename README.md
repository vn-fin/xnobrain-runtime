# Open Lumora

Open Lumora is a self-hosted, low-code workspace for building Hermes AI agents. The React UI and Go API ship as one application; every agent gets an isolated file-backed profile with its own config, memory, skills, workspace, sessions, cron state, and immutable snapshots.

## Start

The portable path for Windows, macOS, and Linux is Docker Compose. This public
repository builds the React frontend, Go Studio backend, and extended
Hermes/9router runtime. Compose also pulls the Enterprise API image for optional
authenticated features; no separate repository must be started first:

```bash
make build
make install
```

Open <http://localhost>. Signed-out self-hosting has unrestricted local access
to agents, profiles, skills, memory, MCP, providers, teams, and cron. Signing in
adds Enterprise API features based on the account plan without limiting local
Hermes access.

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

Provider credentials are delegated to the private OSS 9router/Hermes runtime
and are not stored by Studio.

Managed cloud and Enterprise API limits return HTTP `429` with standard
rate-limit metadata. Self-hosted Hermes operations resolve unlimited values.

See [plans and limits](docs/plans.md), [implementation roadmap](docs/implementation/README.md), [architecture](docs/architecture.md), [development and verification](docs/development.md), [API guide](docs/api.md), [OpenAPI contract](docs/openapi.yaml), and [enterprise extension contract](docs/enterprise-extension.md).

Image builds, offline split bundles, domains, and local/cloud startup are
documented in [deployment](docs/deployment.md).

## License

Add the project license before public distribution.

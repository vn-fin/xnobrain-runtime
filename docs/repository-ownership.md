# Repository and artifact ownership

| Repository | Builds | Persistent ownership |
|---|---|---|
| `open-lumora` | React frontend and unified FastAPI/Hermes/9router Docker runtime | Local profiles, skills, memory, sessions, cron, teams, snapshots, provider state |
| `open-lumora-enterprise` | Enterprise API/control plane and Incus deployment packaging | Accounts, tenants, plans, managed resources, retained telemetry |

Open Lumora Community is usable without the Enterprise repository, an account,
or PostgreSQL. It exposes an optional `ENTERPRISE_API_URL` seam; the dependency
never points from OSS into Enterprise source code.

```text
browser -> Traefik -> frontend / unified runtime
                              |
                              +-> Enterprise API (optional authenticated APIs)
runtime -> optional OTel Collector -> Enterprise API
```

The public runtime preserves upstream Hermes core behavior and adds only the
`open_lumora` Python package. Enterprise must consume a released artifact and
must not fork that package. Neither repository may introduce an ORM.

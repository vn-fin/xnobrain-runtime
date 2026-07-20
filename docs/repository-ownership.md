# Repository and artifact ownership

| Repository | Builds | Owns persistent data |
|---|---|---|
| `open-lumora` | Frontend, Studio API, Hermes/9router Docker image, Hermes/9router Incus image | Profiles, skills, memory, conversations, local cron, provider configuration |
| `open-lumora-enterprise` | `open-lumora-gateway` only | Accounts/tenants/plans in PostgreSQL; traces and metrics in ClickHouse |

The public repository is the single source of truth for `extensions/`,
`runtime/`, `build_docker.py`, and `build_vm.py`. Enterprise deployments pull a
released OSS runtime image or launch the corresponding OSS Incus image.

The self-hosted Compose stack contains Traefik, frontend, Studio API, OSS Hermes
runtime, PostgreSQL, and a pulled Enterprise API image. Only Traefik publishes a
host port. Studio talks directly to Hermes on the private control network. The
Enterprise API is used only for authenticated extensions such as observability.

```text
browser -> Traefik -> frontend / Studio API -> OSS Hermes runtime
                                  |
                                  +-> Enterprise API (authenticated features)
Studio/runtime -> OTel Collector -> Enterprise API -> ClickHouse
```

Signed-out self-hosting remains fully functional without the Enterprise API or
Internet. Signing in enables plan-scoped extensions but never imposes limits on
local Hermes capabilities. Cloud always requires login and uses managed Incus
runtimes; its resource limits are enforced by Enterprise services.

Build ownership:

```bash
# public repository
make build
python build_docker.py --path open-lumora-hermes-runtime:local
python build_vm.py

# enterprise repository
make build
```

Neither repository may introduce an ORM.

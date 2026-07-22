# Repository and artifact ownership

| Repository | Builds | Persistent data |
|---|---|---|
| `brain4all` | React image and combined FastAPI/Hermes/9router Docker image | Local profiles, skills, memory, sessions, cron, teams, and provider configuration |
| `brain4all-enterprise` | Enterprise API and managed/Incus cloud packaging | Accounts, tenants, plans, managed metadata, and hosted telemetry |

The OSS server communicates with Enterprise only through the public HTTP and
telemetry contracts and `ENTERPRISE_API_URL`. It does not import enterprise
code or require an Enterprise database. Enterprise consumes the released OSS
container/runtime interface and must not fork the `brain4all` application
package.

# Repository and artifact ownership

| Repository | Builds | Persistent data |
|---|---|---|
| `xnobrain-runtime` | Combined FastAPI/agent/provider runtime image | Local profiles, skills, memory, sessions, cron, teams, and provider configuration |
| `xnobrain-ui` | React/Vite UI image | Browser application assets and client API contracts |
| `xnobrain-enterprise` | Enterprise API and managed/Incus cloud packaging | Accounts, tenants, plans, managed metadata, and hosted telemetry |

The OSS server communicates with Enterprise only through the public HTTP and
telemetry contracts and `ENTERPRISE_API_URL`. It does not import enterprise
code or require an Enterprise database. Enterprise consumes the released OSS
container/runtime interface and must not fork the `xnobrain` application
package.

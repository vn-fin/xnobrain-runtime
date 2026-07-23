# Local OpenTelemetry collector

Brain4All has no observability dashboard and no managed telemetry service.
Telemetry is disabled by default.

To enable the bundled local collector:

```bash
OTEL_ENABLED=true docker compose --profile otel up -d
```

The runtime exports only metadata-only FastAPI spans. Prompts, responses,
files, credentials, request headers, and request bodies are excluded. The
collector accepts OTLP on the internal Compose network and emits basic span
summaries to its container logs:

```bash
docker compose logs otel-collector
```

The runtime rejects non-local OTLP endpoints. Use the default
`http://otel-collector:4317` in Compose or a loopback endpoint when running the
server directly.

# OSS-05: safe telemetry instrumentation

Priority P1 and parallel with portability. Own tracing/logging redaction and telemetry tests; do not build a managed storage backend here.

## Modes

- Self-hosted default: structured logs with seven-day local rotation; tracing instrumentation active but remote OTLP export off unless configured.
- Connected/cloud: authenticated OTLP export to the plan endpoint with bounded queues, batching, backoff, and a disk cap.
- Enterprise on-premise: customer endpoint and retention policy; no required XNO connectivity.

Allowed attributes include service/version, hashed tenant/device/agent identifiers, route template, status, latency, cron ID hash, quota resource/decision, runtime class, tool category, and error code. Forbidden content includes prompts, responses, memories, skills, file content/path outside normalized category, provider names tied to credentials, tokens, headers, tool arguments, command plaintext, and user-entered names.

Telemetry loss never blocks agent or cron execution and never affects the quota ledger. Local buffer exhaustion drops oldest telemetry and increments one local metric. Sampling occurs after security redaction.

## Acceptance criteria

- Redaction tests inject secrets into every request/event field and find none in exported spans/logs.
- Offline operation remains responsive and bounded in disk/memory.
- Trace IDs correlate API, quota, scheduler command, Hermes run, and result without copying content.
- Disabling telemetry stops remote network calls and preserves essential local error logs.

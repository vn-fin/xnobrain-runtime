# Managed transport boundaries

```text
Browser HTTP -> Control
Control -> NodeGatewayService.Proxy (streaming gRPC)
Node gateway -> RuntimeGatewayService.Proxy (streaming gRPC)
Runtime gateway -> loopback FastAPI
Runtime/Hermes -> router OpenAI-compatible HTTP/SSE
```

Canonical sources are root `proto/xnobrain/runtime/v1/runtime_gateway.proto`,
`xnobrain/integrations/runtime_gateway.py`, and
`xnobrain/integrations/llm_router*.py`. Generated protobuf files are regenerated
from the root contract, never edited manually. Internal metadata may contain a
scoped service identity and trace context, but not browser credentials, prompts,
tool content, or provider secrets.

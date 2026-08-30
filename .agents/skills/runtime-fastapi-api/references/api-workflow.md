# Runtime API workflow

## Maintained path

```text
xnobrain/routes/<group>.py
  -> xnobrain/models/<group>.py
  -> xnobrain/handlers/operations/<group>.py
  -> xnobrain/services/<group>.py
  -> xnobrain/repositories/<group>.py or xnobrain/integrations/<capability>.py
```

`xnobrain/routes/setup.py`, model/operation `__init__` files,
`services/platform.py`, and repository facades are composition boundaries.
Special upload/download/SSE adapters may remain in setup/handlers but feature
validation belongs to the owning service.

## Contract checks

Test success, malformed input, missing resources, path traversal/symlink escape,
profile isolation, safe external failure, and persistence/stream cancellation as
applicable. FastAPI generates OpenAPI at runtime; do not add a hand-authored
`docs/openapi.yaml`.

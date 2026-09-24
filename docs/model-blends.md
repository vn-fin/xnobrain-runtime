# Personal model blends: execution and current limitations

Personal blend definitions live in Runtime's `DATA_DIR/blends.json`. Runtime
resolves a saved blend name to a physical model before calling the centralized
router with the existing scoped workload identity. It does not copy personal
blends into router management storage.

The existing `/xnobrain/api/runtime/v1/blends` CRUD API, model IDs, and persisted
schema are unchanged by the execution fixes described here.

## Streaming conversation execution

- **Fallback:** selects the first member and passes the remaining members, in
  order, to the embedded agent's native inference-failure chain. The chain uses
  the same router URL and scoped key. It does not replay an entire agent turn or
  its completed tool actions. Retry eligibility and exhaustion remain owned by
  the embedded engine.
- **Round-robin:** selects each member for the configured global `sticky_limit`
  number of turn selections before advancing. With no limit configured, it
  rotates each turn. Counters survive Runtime client restarts.
- **Smart Route:** selects a task tier, applies that tier's reasoning, and
  re-evaluates at subsequent inference steps. Its request-builder wrapper must
  preserve the pinned engine's optional `tools_for_api` argument, including an
  explicit empty list. Ordinary fallback and round-robin blends must not install
  Smart Route classifiers or replace the agent's configured reasoning.

Conversation context retains the virtual blend name as `route` and the actual
physical model as `model`.

## Known limitations

- The UI keeps blends hidden by default. The source-development and local Compose
  profiles opt in with
  `FEATURE_ENABLE_UI_BLENDS=true` (overridable in root `.env`), exposing
  `/settings/blends` and the chat blend picker for testing. Other hidden features
  remain disabled; saved selections and server authorization are unchanged.
- **Fusion is incomplete:** the current Runtime selects the configured judge
  model. It does not fan out to every member and synthesize their answers, despite
  the legacy editor's description. Do not treat judge-only output as fusion.
- The ordered fallback chain above is installed by the embedded streaming
  session runner. The separate one-shot CLI path resolves the initial member,
  but does not yet receive the remaining candidates. CLI workers and full fusion
  require separate execution work; neither is claimed fixed here.

## Source verification

Run these focused checks in a Runtime source-development container with the
pinned Hermes source on `PYTHONPATH`:

```bash
python -m unittest \
  xnobrain.tests.test_blends \
  xnobrain.tests.test_blend_execution \
  xnobrain.tests.test_llm_router
ruff check xnobrain/integrations/blends.py \
  xnobrain/integrations/conversation_runner.py \
  xnobrain/tests/test_blends.py xnobrain/tests/test_blend_execution.py
ruff format --check xnobrain/integrations/blends.py \
  xnobrain/integrations/conversation_runner.py \
  xnobrain/tests/test_blends.py xnobrain/tests/test_blend_execution.py
```

These tests cover local persistence, sticky rotation, fallback-chain wiring,
physical/Auto route compatibility, and native request-builder arguments. Mocked
provider responses do not establish deployed or live-provider success.

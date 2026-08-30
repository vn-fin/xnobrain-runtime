# Runtime verification matrix

| Change | Minimum evidence |
|---|---|
| Service/repository/integration | Focused `xnobrain/tests/test_*.py`, then `make test` |
| Public API/envelope/SSE | Focused route tests, `make test`, source API smoke when runnable when runnable |
| Profile files/snapshots | Isolation, traversal, atomic-write, and recovery tests |
| Hermes extension | `$runtime-skill` verifier plus focused Runtime tests |
| Private gRPC/proto | `test_runtime_gateway.py`, root Buf generation/lint, Control consumer tests |
| Image/entrypoint/dependency | Stop ordinary verification; use `$xnobrain-onboard-build` only after an explicit image/onboarding/release request |
| Incus VM/container | Smoke an already available pinned image; rebuilding requires explicit `$xnobrain-onboard-build` |

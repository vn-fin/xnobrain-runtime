# Runtime verification matrix

| Change | Minimum evidence |
|---|---|
| Service/repository/integration | Focused `xnobrain/tests/test_*.py`, then `make check` |
| Public API/envelope/SSE | Focused route tests, `make check`, `make smoke-api` when runnable |
| Profile files/snapshots | Isolation, traversal, atomic-write, and recovery tests |
| Hermes extension | `$runtime-skill` verifier plus focused Runtime tests |
| Private gRPC/proto | `test_runtime_gateway.py`, root Buf generation/lint, Control consumer tests |
| Image/entrypoint/dependency | `make check` and relevant Docker build/smoke |
| Incus VM/container | Root local-dev health and explicit Incus smoke target |

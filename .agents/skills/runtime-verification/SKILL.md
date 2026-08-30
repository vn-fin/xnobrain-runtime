---
name: runtime-verification
description: Verify XNOBrain Runtime changes with focused unittest modules, compile checks, API smoke tests, container builds, and managed gRPC/Incus scenarios. Use after implementation, during diagnosis, or when reviewing completion evidence.
---

# Runtime verification

Read [verification-matrix.md](references/verification-matrix.md), select the
smallest checks proving the requested behavior, then broaden by risk.

- Run focused tests with the repository-selected Python and Hermes source
  `PYTHONPATH`; do not substitute a different environment silently.
- For ordinary backend work, finish with `make check` when practical.
- Add `make smoke-api` for public route/envelope changes and a container build
  for dependency, entrypoint, or packaging changes.
- For gRPC/protobuf changes, run Runtime contract tests and the root Buf checks;
  validate the Control consumer in the coordinated workspace.
- For Incus packaging, use the root local-development workflow and report the
  actual container/VM target. Do not claim Incus evidence from unit tests.
- Report exact commands, observed results, skipped checks, and environment
  limitations. Never call a skipped or unrun suite passed.

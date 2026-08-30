---
name: runtime-verification
description: Verify ordinary XNOBrain Runtime development by running source-level unittest modules, source API smoke behavior, and contract checks without compiling Nuitka artifacts or building images. The root xnobrain-onboard-build skill alone owns explicitly requested local/dev/staging/prod image builds and packaging.
---

# Runtime verification

Read [verification-matrix.md](references/verification-matrix.md), select the
smallest checks proving the requested behavior, then broaden by risk.

- Run focused tests with the repository-selected Python and Hermes source
  `PYTHONPATH`; do not substitute a different environment silently.
- For ordinary backend work, run focused unittest modules and `make test`; run
  the API from source with `make backend` when runtime behavior needs a smoke
  check. Do not run `make build`, Docker build targets, Nuitka, or packaging.
- If the requested change cannot be verified without a compiled image, report
  that boundary. Invoke the root `$xnobrain-onboard-build` only after an explicit request for
  local/dev/staging/prod onboarding or images.
- For gRPC/protobuf changes, run Runtime contract tests and the root Buf checks;
  validate the Control consumer in the coordinated workspace.
- For ordinary Incus-facing development, use an already available image and
  report its version. Do not rebuild it implicitly or claim Incus evidence from
  unit tests.
- Report exact commands, observed results, skipped checks, and environment
  limitations. Never call a skipped or unrun suite passed.

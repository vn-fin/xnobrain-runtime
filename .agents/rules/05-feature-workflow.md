# Runtime Feature Workflow

1. Choose one service group and trace its route, model, operation, service,
   repository or integration, and tests.
2. Keep public changes under `/xnobrain/api/runtime/<version>` and update
   `docs/api.md`, contract docs, UI consumers, and smoke tests when required.
3. Put business rules in services, atomic persistence in repositories, and
   Hermes/OmniRoute calls in integrations. Keep composition modules small.
4. Preserve profile isolation, path validation, snapshots, atomic writes,
   streaming events, approvals, and optional-dependency failure behavior.
5. Add focused tests in `xnobrain/tests/`, run them, then run `make check`.

Do not implement a runtime feature in the control or AI repository, and do not
edit the separate `app/` tree unless it is explicitly in scope.

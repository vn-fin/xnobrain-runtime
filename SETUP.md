# XNOBrain runtime setup

The complete local stack is coordinated from the workspace root:

```bash
cp .env.example .env
make dev
```

The root `Makefile` starts the UI from `xnobrain-ui`, the managed control
plane, and this runtime API. For runtime-only development:

```bash
make install-local
make dev
```

The runtime API listens on private port `3000` and the private provider runtime on
port `20128`. UI development and production image instructions are in
[`../xnobrain-ui/README.md`](../xnobrain-ui/README.md).

Run `make check` in this repository for Python tests and compilation checks.

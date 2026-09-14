# Development and verification

The UI lives in the sibling `xnobrain-ui` repository. This repository contains
the Python application in the `xnobrain/` package and the agent/provider
installation tooling.

On Linux or macOS, install the local toolchain with `make install-local`. It
creates `.tools/python` for the project, installs Hermes and the Python
requirements, and provisions Node.js/npm and agent tooling. The
macOS target skips the optional office-tool and browser-engine downloads to
keep setup fast. Copy the workspace root `../.env.example` to `../.env` when
local overrides are needed.

```bash
make -C ../xnobrain-ui dev # Vite UI on 5173
make dev                  # runtime API using the configured centralized router
make backend              # API only, using the selected environment
make check                # Python tests and compile checks
```

The UI's Vite proxy targets the canonical `/xnobrain/api/runtime/v1` namespace
on private port 3000. Swagger is at
`http://127.0.0.1:5173/xnobrain/api/runtime/swagger_docs` through Traefik.

The runtime development script supervises only the API and stops it on Ctrl-C.
The API uses private port 3000; provider routing is centralized outside Runtime.

The Python tests use isolated temporary profile roots. For a real chat smoke
test, use an existing Hermes profile without changing its config and send a
short prompt through the conversation SSE endpoint. Never print credentials or
copy a user's `.env` into test output.

Container verification:

```bash
make image
docker compose up -d
docker compose ps
make smoke-api
```

After a profile mutation, verify its files remain under
`DATA_DIR/profiles/<id>`, mutable content was atomically replaced, and an
immutable snapshot exists where required.

## Disposable custom-page persistence verification (source only)

The workspace-root `scripts/dev/verify_custom_page_volume.sh` reuses the image ID of
an existing source Runtime container. It creates a fresh disposable named volume,
starts isolated readonly/network-disabled source-test containers and verifies stored
pages across separate container instances, update checkpoint/post-verify, and an
abrupt process exit. No production build or image pull. Only the volume/containers
created by the script are removed; existing development volumes are not mounted.

Docker source Compose requires `RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT=/opt/data`. For
unit tests using temporary fixture directories, run them with an empty value rather
than redirecting them into the real workspace volume. The volume verifier uses the
actual strict mount check. Native/Incus root storage needs its own explicit storage
policy and is not inferred durable solely from a Docker source test.

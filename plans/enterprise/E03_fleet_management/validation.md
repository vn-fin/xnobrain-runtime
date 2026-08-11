# E03 validation — acceptance checklist with evidence

Two blocks. Block S maps **1:1 to the "Security acceptance tests" section of
[`docs/contracts/device-command-v1.md`](../../../docs/contracts/device-command-v1.md)** —
those rows are non-negotiable contract conformance. Block F covers the fleet
deliverables from the [README](README.md) definition of done. "Evidence"
names the artifact that proves the row; test paths per
[implementation.md](implementation.md).

## Block S — device-command-v1 security acceptance (contract §Security acceptance tests)

| # | Contract requirement (quoted) | Check | Evidence |
|---|---|---|---|
| S-1 | "Invalid signature, wrong device, unknown agent, expired TTL, replay, and altered payload are rejected." | Fake control plane sends each malformed envelope variant: bad signature; `device_id` of another device; unknown `agent_id`; `expires_at` in the past (including a case just outside the skew window); duplicate `command_id` after completion; payload not matching `payload_sha256`. Each is journaled `rejected` with the matching error code, **and a spy on `PlatformService` proves no service call happened** (also satisfies `03-device-connector.md`: "Invalid commands cannot call Hermes or mutate profiles"). | `brain4all/tests/test_device_connector.py::test_rejects_*` (six cases), CI log |
| S-2 | "Reconnect delivers no duplicate execution." | Dispatch command, ack lost (fake drops the status post), connector restarted with same `DATA_DIR`; fake re-delivers the same `command_id` on cursor reconnect; journal returns the recorded terminal state; execution counter == 1. | `test_device_connector.py::test_reconnect_cursor_no_duplicate` |
| S-3 | "Revocation closes the stream and rejects token refresh." | Fake marks device revoked mid-session: next poll gets the revoked error, loop stops, subsequent `refresh()` is rejected; Go side: revocation closes an already-parked poll request. | `test_device_connector.py::test_revocation`; Go `internal/fleet/revoke_test.go` |
| S-4 | "Logs and spans do not contain the access token, ciphertext plaintext, prompt, or signing material." | Run the full fake-server sequence with log capture at DEBUG; scan captured logs (and heartbeat/status request bodies recorded by the fake) for the token value, private-key bytes/base64, and payload plaintext markers — zero hits. Heartbeat body additionally asserted to contain **no hostname, image name, OS string, or filesystem path** (allow-list of [architecture.md](architecture.md) §4, per `05-telemetry.md`). | `test_device_connector.py::test_no_secrets_in_logs`, `::test_heartbeat_allow_list` |
| S-5 | "A fake server can drive the complete register, connect, dispatch, acknowledge, reconnect, and revoke sequence." | The fake control plane in the test module executes exactly this sequence end-to-end in one test; it is the harness every other S-row reuses. | `test_device_connector.py::test_full_contract_sequence` |

Supporting contract-conformance rows (same source document, other sections):

| # | Requirement | Check | Evidence |
|---|---|---|---|
| S-6 | Journal states written **before** ack ("records received, accepted, running, and terminal state before acknowledging transitions") | Fake asserts the journal file's state at the moment each ack/status arrives (fake reads the journal path injected by the test). | `test_device_connector.py::test_journal_before_ack` |
| S-7 | Private key under `DATA_DIR/device/`, mode 0600; never leaves | Stat the key file; register request body contains public key only. | `test_device_connector.py::test_key_permissions` |
| S-8 | Dormancy: `ENTERPRISE_API_URL` unset ⇒ no task, no sockets, no `DATA_DIR/device/` | App started without the env: lifespan creates no connector task; directory absent; local API fully functional. Enterprise server *down* with env set: local API still fully functional (program principle). | `test_device_connector.py::test_dormant_without_env`, `::test_outage_never_blocks_local` |
| S-9 | Cross-language signatures: Go signer output verifies against Python verifier | Frozen Phase-0 test-vector file consumed by both suites. | vector file in `docs/contracts/` addendum; both CI logs |

## Block F — fleet management deliverables

| # | Acceptance item (README definition of done) | Check | Evidence |
|---|---|---|---|
| F-1 | **Provision → pre-enrolled boot → heartbeat → fleet list.** Admin provisions an Incus container for a user; it boots pre-enrolled and claimed (one-time token consumed), heartbeats, and appears in `GET /admin/v1/fleet` with user, version, `runtime_type: "incus"`, resource class, and health. | Staging Incus host, released `brain4all:<version>` image; provision via `POST /admin/v1/sandboxes`; fleet row appears within the heartbeat window; enrollment token verified consumed (second use rejected). | staging run transcript + fleet API response capture; Go `provision_test.go` (fake driver) |
| F-2 | **Remote restart with drain.** `POST /admin/v1/fleet/{id}/restart` on the Incus device runs the `docs/enterprise-extension.md` sequence: drain in-flight, restart, health-verify via reconnect + heartbeat, audit event emitted. Device reconnects with intact cursor; no command executes twice (ties to S-2). | Issue restart while a slow command is in flight; observe drain wait, restart, healthy heartbeat, audit row. | staging transcript; Go `replace_test.go` (drain waits; volume never deleted) |
| F-3 | **PC-installed user enrolls manually into the same fleet.** Non-Incus install enrolls via the Settings Enterprise panel (pairing token) and appears in the same `GET /admin/v1/fleet` list with `runtime_type: "pc"`, `sandbox_id: null`. `runtime.restart_gateway` to it without `supervised_restart` capability returns `rejected`/`unsupported_capability`. | Manual enroll on a bare install; fleet list shows both device kinds in one response (architecture §7 example shape). | staging transcript; `test_device_connector.py::test_restart_rejected_unsupervised` |
| F-4 | **Unpair erases only cloud credentials.** Unpair from the Settings panel removes `DATA_DIR/device/` contents (key, token, journal, cursor); profiles, agents, memories, skills byte-identical before/after; device drops from active fleet (goes stale → revoked/removed per E01 policy); local features keep working. | Seed a profile tree, hash it, unpair, re-hash; assert `device/` gone and hashes equal. | `test_device_connector.py::test_unpair_erases_only_cloud_creds`; UI walkthrough |
| F-5 | **Version rollout gated on health.** Rollout `brain4all:<new>` with canary N=1 over a fleet of ≥3 sandboxes: canary replaced with drain, health window passes → remainder proceeds. Second run with a deliberately broken image: canary fails the gate → rollout **halts**, remaining sandboxes stay on the old pin, canary rolled back. Profile data survives every replacement (persistent volume). | Two staging rollout runs; per-device rollout states inspected via `GET /admin/v1/rollouts/{id}`; a marker file in a profile on the canary's `DATA_DIR` volume survives the replace. | staging transcripts; Go `rollout_test.go` |
| F-6 | **Stale detection.** Stop a device's process: fleet row transitions `online → stale → offline` at the configured thresholds; restart: back to `online` with cursor intact. | Timed staging check. | staging transcript; Go `inventory_test.go` |
| F-7 | **Plan limits enforced on provisioning.** Provisioning beyond the `docs/plans.md` managed-sandbox count (1/1/5/custom) for the user's plan returns `429`; entitled request succeeds. | Go test with plan fixtures; one staging spot check. | Go `provision_test.go::limit_cases` |
| F-8 | **Heartbeat ingest is allow-list-strict server-side.** Ingest rejects/strips fields outside the architecture §4 allow-list (defense in depth against a modified client). | Go ingest test posts a heartbeat with `hostname` and path fields; stored row contains neither. | Go `heartbeats_test.go` |

## Exit

- All S rows green in CI (Python + Go); all F rows evidenced from the staging
  run.
- `make check` green in this repo; enterprise repo CI green.
- Phase-0 "verify" marks in [findings.md](findings.md) §6/§8 replaced with
  probed facts; the frozen contract-slice addendum committed under
  `docs/contracts/`.
- E03 checked off in the program checklist
  ([`../README.md`](../README.md)).

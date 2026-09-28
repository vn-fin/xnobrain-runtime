# Native profile Router authentication

## Symptom and scope

A named Hermes profile can be created with model `cx/gpt-6-astra` and Router
endpoint `http://llm-router:8090/v1`, yet a later `agent -p PROFILE -z ...`
invocation can receive HTTP 401. Profile creation does not exercise Router
inference. The parent chat and the native CLI run in separate processes and
may resolve their credentials differently.

## Contract

- Control supplies a workspace-scoped Router user key to Runtime. A native
  `agent` invocation must use the current workspace key for its Router model.
- Runtime exposes that key to native CLI children through
  `RUNTIME_LLM_API_KEY_FILE`. Container startup writes the provisioned
  `RUNTIME_LLM_API_KEY` to a mode 0600 file under a mode 0700 runtime directory
  when no file is already configured.
- `/usr/local/bin/agent` reads the configured file for each invocation and
  exports `RUNTIME_LLM_API_KEY` only to the Hermes process it starts. A missing
  configured file clears any inherited key. With no configured file, an
  existing environment key remains available for legacy installations.
- No Router key is stored in a named profile's `.env` or on the CLI command
  line. Existing profile selection and CLI arguments are passed through.

## Verification

1. Create a named profile using the Router model, then invoke
   `agent -p PROFILE -z 'Reply exactly OK.'` from the parent agent terminal.
   The result should be `OK`, without an HTTP 401.
2. Replace the runtime key file with a new valid key and repeat the CLI
   invocation. It must use the new value without changing the profile.
3. Remove the configured key file. The CLI must not fall back to a stale
   inherited key.
4. Confirm that the profile `.env` does not contain the Router key. Check
   Router logs for a successful authorized request without printing secrets.

The source test covers the launcher contract and key rotation. A staging
deployment must be verified against its actual provisioned Router key and
request logs after the new Runtime image is deployed.

## Native profile visibility

Hermes CLI creates named profiles under `HERMES_ROOT_PROFILE/profiles`, while
XNOBrain lists agents from `HERMES_PROFILES_ROOT`. The Runtime launcher links
the CLI path to the XNOBrain path when the CLI path is empty. If the CLI path
already contains profiles, startup leaves them untouched and reports that an
explicit migration is required. An operator must verify that no destination
name conflicts, move each existing profile intact, then replace the empty CLI
directory with a link to `HERMES_PROFILES_ROOT`.

Runtime detects changes to the canonical profile directory and invalidates its
agent list cache. The UI refreshes its list when the activity stream reports an
agent ID that is not yet present. This lets a profile created by Big Brother's
CLI appear in the sidebar without restarting the browser.

# Agent Maker automatic acceptance and certification recovery

## Behavior

When a user requests an agent in an authenticated chat, the maker collects missing
requirements, saves a complete blueprint and invokes the native build tool. The
platform automatically accepts the exact revision, creates one paused child,
certifies it and activates it if every case passes. A case creates a chat session
inside that child; it does not create another agent. The UI displays automatic
acceptance separately from manual approval.

The native integration supplies identity from the current run. Terminal commands
and model-authored headers are not a lifecycle credential. Existing permissions,
work context, payer, protected-write policy, cron and MCP constraints still apply.
Marketplace publication is separate. The automatic certification request defaults
to USD 0.10, with a USD 1 maximum per attempt; existing Runtime cost accounting and
certification enforcement remain in use.

## Certification failure reported on 2026-09-27

The executor supplied the fixed title `Agent Maker certification` for every case,
while the session database enforces title uniqueness. Case two could therefore
fail before inference. Session titles now include their unique session IDs.

An already failed certification can be retried on the existing child with a new
idempotency key. Previous evidence is retained, and repeating an old key never
starts another certification. Successful certification and profile drift checks
are still required before activation. The UI offers **Retry certification** and
uses a stable key derived from the failed attempt for transport retries.

## Ownership and compatibility

- Runtime owns the lifecycle coordinator, native tools, identity binding,
  certification executor, and additive `approval.mode` / `certification_history`.
- UI owns retry interaction, approval attribution, launch copy and typed mapping.
- Control keeps authenticating the same public API; no new public path or header.
- Existing manual API consumers continue to work. Old records default to manual
  approval and empty certification history.

## Verification scope

Focused tests dispatch through the real embedded tool registry, use real profile
files and session databases, and replace model execution with synthetic responses.
They cover one-child/four-session execution, retry/replay, failed certification,
identity/context rejection and stopping before activation. UI tests cover retry
behavior and stable request keys. Paid inference and deployment are separate from
these source checks.

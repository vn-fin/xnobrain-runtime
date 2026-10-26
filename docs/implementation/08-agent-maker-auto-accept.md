# Agent Maker automatic acceptance and direct activation

## Behavior

When a user requests an agent in an authenticated chat, the maker collects missing
requirements, saves a complete blueprint and invokes the native build tool. The
platform automatically accepts the blueprint, scaffolds one child and activates it
directly. Certification and test sessions are not part of creation. The UI displays
automatic acceptance separately from manual approval.

The native integration supplies identity from the current run. Terminal commands
and model-authored headers are not a lifecycle credential. Existing permissions,
work context, payer, protected-write policy, cron and MCP constraints still apply.
Marketplace publication is separate. Build requires only a blueprint ID and
idempotency key. Revision and digest are optional explicit preconditions; edits
also no longer require a preliminary revision inspection. Acceptance cases are
optional. Legacy build certification arguments are ignored for compatibility.

Direct activation also resumes existing scaffolded or certification-failed agents.
Previous certification evidence remains readable but is not a prerequisite and is
never marked passed by the build tool. The explicit certification API is retained
for callers requesting evaluation. Supplying a certification digest to activation
still opts into validating that evidence. Rollback supports direct activations
without inventing a certification digest.

## Certification failure reported on 2026-09-27

The executor supplied the fixed title `Agent Maker certification` for every case,
while the session database enforces title uniqueness. Case two could therefore
fail before inference. Session titles now include their unique session IDs.

An already failed certification can be retried on the existing child with a new
idempotency key. Previous evidence is retained, and repeating an old key never
starts another certification. This applies to the optional explicit certification
API. The current UI offers activation directly instead of retrying certification.

## Ownership and compatibility

- Runtime owns the lifecycle coordinator, native tools, identity binding,
  certification executor, and additive `approval.mode` / `certification_history`.
- UI owns direct activation, approval attribution, launch copy and typed mapping.
- Control keeps authenticating the same public API; no new public path or header.
- Existing manual API consumers continue to work. Old records default to manual
  approval and empty certification history.

## Verification scope

Focused tests dispatch through the real embedded tool registry, use real profile
files and session databases, and replace model execution with synthetic responses.
They cover one-child creation with no test sessions or certification calls,
retry/replay, legacy requests, resuming failed certification, rollback,
identity/context rejection and stopping before activation. UI tests cover direct
activation without a certification digest. Paid inference and deployment are
separate from these source checks.

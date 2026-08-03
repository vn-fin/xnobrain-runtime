# Tauri app coding style

## Rust

- Use stable Rust, `rustfmt`, and `clippy -D warnings`.
- Keep `main.rs` minimal and wire modules/commands in `lib.rs`.
- Prefer explicit domain types and enums over stringly typed states.
- Return typed serializable errors with stable codes and safe user messages;
  retain redacted technical context for diagnostics.
- Put blocking filesystem/process work outside async executor threads. Use
  bounded timeouts, cancellation, and structured progress channels.
- Inject driver traits into services so tests use fakes without Docker, WSL,
  systemd, launchd, elevation, or network access.
- Avoid `unwrap`/`expect` outside startup invariants and tests. Never panic on
  user, network, daemon, manifest, or runtime input.
- Resolve and validate paths before mutations. Atomically persist mutable state
  with temp file, sync, and rename semantics.

## TypeScript and React

- Enable strict TypeScript. Do not use `any` at IPC boundaries.
- Keep `invoke`/channel/event calls inside `src/bridge/`; export typed methods.
- Drive the wizard from an explicit reducer/state machine. Components render
  state and dispatch intent; they do not orchestrate installation.
- Use accessible semantic controls, visible focus, keyboard operation, and
  reduced-motion support. Progress must not rely on color alone.
- Never display raw command output by default. Map stable message/error codes to
  UI copy and provide redacted details separately.
- Clean up event/channel listeners on unmount and prevent duplicate commands
  from React development remounts or repeated clicks.

## IPC

- Use narrow command-specific request/response structs.
- Use commands for request/response and channels for ordered streaming
  progress. Reserve events for loose application notifications.
- Validate enums, lengths, numeric ranges, identifiers, paths, URLs, and state
  preconditions in Rust even when TypeScript validates them.
- Never accept executable names, shell fragments, raw arguments, Compose YAML,
  arbitrary URLs/paths, environment maps, or privilege instructions from UI.
- Use stable snake_case command names and camelCase JSON fields consistently;
  serialize deliberately and cover shapes with contract tests.

## Security and diagnostics

- Grant least privilege per edition/window in Tauri capabilities.
- Permit only approved HTTPS origins and loopback product URLs.
- Verify signed manifests/updates and immutable digests before activation.
- Never log secrets, tokens, Docker auth, environment dumps, prompts, chat
  content, request bodies, or raw tool output.
- Label and target only Brain4All-owned resources. Preserve user data unless a
  separate exact-target deletion confirmation is completed.

## Tests

- Domain: state transitions, invariants, serialization, manifest validation.
- Service: fake-driver success, cancellation, retry, interruption, rollback.
- Command: validation and safe error translation.
- React: reducer, duplicate-action protection, progress, recovery, accessibility.
- Platform: native-host clean/existing/failure/repair/uninstall matrix.

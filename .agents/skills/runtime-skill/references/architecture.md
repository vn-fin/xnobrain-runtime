# Hermes architecture — module map & extension points

Source of truth in this repo: `.tools/hermes-agent/`. Docs:
https://hermes-agent.nousresearch.com/docs/developer-guide/architecture

## Execution modes (entry points)

All of these drive the **same** `AIAgent` engine — extend the engine once and
every surface benefits.

| Mode | Entry file | Purpose |
|---|---|---|
| CLI | `cli.py` | Interactive terminal + TUI (`--tui`) |
| Python library | `run_agent.py` (`AIAgent`) | Embed in your own code (XNOBrain backend) |
| Gateway | `gateway/run.py` (`GatewayRunner`) | Messaging-platform server (~20 adapters) |
| ACP adapter | `acp_adapter/` | Editor integration over JSON-RPC (VS Code, Zed, JetBrains) |
| Batch runner | `batch_runner.py` | Trajectory / dataset generation |
| Headless serve | `hermes serve` | JSON-RPC/WebSocket backend |

## The central agent loop — `run_agent.py`

`AIAgent` orchestrates the conversation. It handles:

- **Prompt construction** → `prompt_builder.py`
- **Provider/model selection** → `runtime_provider.py` (+ adapters in `agent/`:
  `anthropic_adapter.py`, `codex_responses_adapter.py`, `gemini_native_adapter.py`,
  `bedrock_adapter.py`, chat-completion path)
- **Tool dispatch** → `model_tools.py`, which queries `tools/registry.py`
- **Three API modes**: chat completion, codex response, Anthropic Messages
- **Turn/loop mechanics** → `agent/conversation_loop.py`, `agent/turn_context.py`

The loop runs up to `max_iterations` (default 90) tool-calling turns.

## Tool system — `tools/`

- **Central registry**: `tools/registry.py`. Every tool file calls
  `registry.register(...)` at **module import time**. `model_tools.py` queries
  the registry — there is no parallel list to maintain.
- **Auto-discovery**: `discover_builtin_tools()` imports every `tools/*.py` that
  contains a top-level `registry.register(...)` call (cheap text prefilter, then
  AST check). So: create a file, call `registry.register(...)` at module level,
  and it is found automatically.
- Scale: ~70+ tools across ~28 toolsets. Terminal orchestration supports 6
  backends (local, Docker, SSH, Modal, Daytona, Singularity).
- Notable tool files: `terminal_tool.py`/`file_tools.py`/`file_operations.py`,
  `web_tools.py`, `browser_tool.py`, `code_execution_tool.py`, `mcp_tool.py`,
  `memory_tool.py`, `delegate_tool.py`, `kanban_tools.py`, `cronjob_tools.py`.

See [building-tools.md](building-tools.md) for the full contract.

## Toolsets — `toolsets.py`

Tools are grouped into **toolsets** (the `toolset=` arg to `register`). Toolsets
are the unit users enable/disable (`--toolsets`, `/toolsets`, `enabled_toolsets`
/ `disabled_toolsets` in the library). A `check_fn` decides whether a toolset's
tools are exposable in the current environment.

## Session & state — `hermes_state.py`

SQLite persistence with FTS5 full-text search. Tracks session lineage
(parent/child via `/branch`), per-platform isolation, atomic writes.

## Memory & context — `agent/`

- `context_engine.py` — pluggable context abstraction (swappable via plugin)
- `context_compressor.py` / `conversation_compression.py` — lossy summarization
  past thresholds (also `/compress`)
- `prompt_caching.py` — Anthropic prefix caching
- `memory_manager.py` — orchestrates memory providers
- `tools/memory_tool.py` — the `memory` tool; writes `MEMORY.md` / `USER.md`,
  injected as a **frozen snapshot** at session start

See [skills-and-memory.md](skills-and-memory.md).

## Skills — `hermes_cli/skill_commands.py`, `hermes_cli/skills_config.py`

Skills attach context + tool guidance (Markdown `SKILL.md` with frontmatter).
Managed by the `/skills` command and `hermes skills …`. Bundled skills live in
`.tools/hermes-agent/skills/`.

## Profiles & isolation

`hermes -p <name>` gives each profile its own `HERMES_HOME`, config, memory,
sessions, and gateway PID → concurrent multi-profile operation. See
[slash-and-profile-commands.md](slash-and-profile-commands.md).

## Plugin system (the primary third-party extension surface)

Discovery order:

1. **Entry-point plugins** — pip packages exposing `hermes_agent.plugins`
2. **User plugins** — `~/.hermes/plugins/<name>/`
3. **Project plugins** — `./.hermes/plugins/<name>/` (opt-in)

Each directory plugin = `plugin.yaml` manifest **+** `__init__.py` with a
`register(ctx)` function. Two specialized single-selection categories:

- Memory providers — `plugins/memory/`
- Context engines — `plugins/context_engine/`

Full API in [plugins.md](plugins.md).

## Other subsystems worth knowing

| Subsystem | File | Note |
|---|---|---|
| Cron / scheduling | `cron/scheduler.py` | Agent-native task scheduling, JSON storage |
| Trajectories | `agent/trajectory.py` | ShareGPT-format generation |
| Gateway | `gateway/run.py` | 20 platform adapters, auth/pairing, slash dispatch, hooks |
| MCP | `tools/mcp_tool.py`, `mcp_serve.py` | Model Context Protocol client/server |
| Kanban | `hermes_cli/kanban_db.py`, `tools/kanban_tools.py` | Multi-profile task board |

## Where to make a change (decision order)

1. **A new callable action** → new tool module (or plugin tool). Never bolt logic
   onto an unrelated tool.
2. **Cross-cutting behavior** (logging, redaction, gating, auto-cleanup) →
   plugin **hook**, not a core edit.
3. **A new provider backend** → `backend`/`exclusive` plugin, not a hardcoded branch.
4. **Only touch core files** when no extension point fits — and in this repo,
   prefer extending from `xnobrain/` per `AGENTS.md`.

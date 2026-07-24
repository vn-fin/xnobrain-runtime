# Hermes plugins — tools, hooks, commands, providers, platforms

Plugins are the primary way to extend Hermes **without** editing core. Per this
repo's `AGENTS.md`, prefer a plugin over a core edit.

Verify the live API before writing:
```bash
sed -n '339,600p'  .tools/hermes-agent/hermes_cli/plugins.py   # PluginContext (ctx)
sed -n '135,215p'  .tools/hermes-agent/hermes_cli/plugins.py   # VALID_HOOKS
cat .tools/hermes-agent/plugins/disk-cleanup/__init__.py        # a real full plugin
```

## Anatomy

A directory plugin is a folder containing:

```
<name>/
├── plugin.yaml     # manifest (required)
└── __init__.py     # must define register(ctx) (required)
```

Discovery order (all gated by `plugins.enabled` unless bundled/backend):

1. **Entry-point** — pip package exposing the `hermes_agent.plugins` group
2. **User** — `~/.hermes/plugins/<name>/`
3. **Project** — `./.hermes/plugins/<name>/` (opt-in)

## `plugin.yaml` manifest

```yaml
name: my-plugin
version: 1.0.0
description: "One line the user sees in `hermes plugins list`."
author: "you"
kind: standalone            # standalone | backend | exclusive | platform
requires_env: []            # e.g. [MY_API_KEY]
provides_tools: []          # documentation hints
provides_hooks: []
```

### `kind` semantics (from `PluginManifest`)

| kind | Meaning | Loading |
|---|---|---|
| `standalone` (default) | Own tools/hooks/commands | Opt-in via `plugins.enabled` |
| `backend` | Pluggable backend for a core tool (web search, image gen…) | Bundled auto-load; user-installed gated |
| `exclusive` | Category with exactly one active provider (memory) | Selected via `<category>.provider`; category owns loading |
| `platform` | Gateway messaging adapter | Bundled auto-load; user-installed gated |

## `register(ctx)` — the entry point

`__init__.py` must define `register(ctx)`. `ctx` is a `PluginContext` facade. It
runs at load time; wire everything here.

```python
def register(ctx) -> None:
    ctx.register_tool(name="my_tool", toolset="my_plugin", schema=SCHEMA, handler=HANDLER)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_command("my-plugin", handler=_slash, description="Do the thing.")
```

## The `ctx` API (PluginContext methods)

| Method | Registers |
|---|---|
| `register_tool(name, toolset, schema, handler, check_fn=, requires_env=, is_async=, description=, emoji=, override=)` | A model-callable tool (delegates to `registry.register`) |
| `register_hook(hook_name, callback)` | A lifecycle callback (see VALID_HOOKS) |
| `register_command(name, handler, description="", args_hint="")` | In-session slash command `/name`; handler `fn(raw_args:str)->str|None` |
| `register_cli_command(...)` | A `hermes <subcommand>` terminal command |
| `register_skill(name, path, description="")` | Read-only skill resolvable as `<plugin>:<name>` |
| `register_middleware(kind, callback)` | Middleware |
| `register_context_engine(engine)` | Context engine (exclusive) |
| `register_memory` (via memory plugin category) | Memory provider (exclusive) |
| `register_web_search_provider(provider)` | Web search backend |
| `register_image_gen_provider(provider)` | Image generation backend |
| `register_video_gen_provider(provider)` | Video generation backend |
| `register_tts_provider(provider)` / `register_transcription_provider(provider)` | Voice backends |
| `register_browser_provider(provider)` | Browser backend |
| `register_secret_source(source)` | External secret source |
| `register_dashboard_auth_provider(provider)` | Dashboard auth |
| `register_platform(...)` | Gateway messaging platform |
| `register_slack_action_handler(...)` | Slack interactive action |
| `register_auxiliary_task(...)` | Background auxiliary task |

`register_tool` fields match `registry.register` exactly — see
[building-tools.md](building-tools.md) for the handler contract.

## Hooks — `VALID_HOOKS`

Register with `ctx.register_hook(name, callback)`. Callbacks take `**kwargs`
(accept `**_` to be forward-compatible). Full list from source:

**Tool lifecycle**
- `pre_tool_call` — before a tool runs. Can **block** a tool (return a blocking
  decision). kwargs include `tool_name`, `args`, `task_id`, `session_id`.
- `post_tool_call` — after a tool runs. Observe/track. kwargs add `result`,
  `tool_call_id`.
- `transform_terminal_output` — rewrite terminal output.
- `transform_tool_result` — rewrite a tool's result before the model sees it.

**LLM lifecycle**
- `transform_llm_output` — rewrite the assistant's text before it reaches the
  user (first non-None string wins). Good for vocabulary/personality transforms.
- `pre_llm_call` / `post_llm_call`
- `pre_verify` — verification-loop gate; fired once/turn when the agent edited
  code and is about to finish. Return `{"action":"continue","message":"…"}` (or
  the Claude-Code `{"decision":"block","reason":"…"}` shape) to keep it going.
  Bounded by `agent.max_verify_nudges`.

**API lifecycle**
- `pre_api_request` / `post_api_request` / `api_request_error`

**Session lifecycle**
- `on_session_start` / `on_session_end` / `on_session_finalize` /
  `on_session_reset`
- `subagent_start` / `subagent_stop`

**Gateway**
- `pre_gateway_dispatch` — before auth/dispatch of an inbound message. Return
  `{"action":"skip"|"rewrite"|"allow", …}`.

**Approval (observe only, cannot veto — use `pre_tool_call` to block)**
- `pre_approval_request` / `post_approval_response`

**Kanban (observe only, fired after commit)**
- `kanban_task_claimed` / `kanban_task_completed` / `kanban_task_blocked`

## Slash commands from a plugin

```python
def _slash(raw_args: str) -> str | None:
    argv = raw_args.strip().split()
    if not argv or argv[0] in {"help", "-h"}:
        return "usage: /my-plugin status|run"
    ...
    return "done"

def register(ctx):
    ctx.register_command("my-plugin", handler=_slash,
                         description="…", args_hint="status|run")
```

Handler returns a string (shown to user) or `None`. Async handlers are supported.
Names conflicting with built-in commands are rejected.

## Backend/provider plugins

For pluggable backends (web search, image gen, memory, etc.), set the matching
`kind` and call the corresponding `register_*_provider`. Look at
`plugins/web/exa/` (web search) and `plugins/image_gen/` for the provider object
shape — copy the closest one.

## Enabling & inspecting

```bash
hermes plugins list                 # what's discovered/enabled
hermes plugins enable my-plugin
hermes plugins disable my-plugin
HERMES_PLUGINS_DEBUG=1 hermes …     # verbose discovery + per-plugin registration log
```

Config lives under `plugins:` in `config.yaml` (`plugins.enabled`,
`plugins.disabled`, `plugins.entries.<id>.*`).

## Verification

```bash
python3 agents/skills/hermes-agent/scripts/verify_extension.py --plugin my-plugin
cd .tools/hermes-agent && uv run pytest tests/ -q -k plugin
```

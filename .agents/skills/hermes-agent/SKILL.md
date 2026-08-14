---
name: hermes-agent
description: >-
  Code inside and extend the Hermes agent core (NousResearch/hermes-agent) —
  the runtime that powers XNOBrain's backend. Use this skill whenever a task
  involves adding or changing a Hermes tool, writing a Hermes plugin (tools,
  hooks, slash commands, providers, platforms), embedding the AIAgent Python
  library, wiring toolsets/skills/memory/profiles, or navigating the Hermes CLI
  and slash/profile commands. Triggers: "hermes tool", "hermes plugin",
  "register a tool", "AIAgent", "run_agent", "hermes core", "extend hermes",
  "toolset", "hermes hook", "hermes slash command".
version: 1.0.0
metadata:
  hermes:
    tags: [hermes, core, tools, plugins, extension, python-library, agent-runtime]
---

# Coding & extending the Hermes agent core

Hermes (`NousResearch/hermes-agent`) is the agent runtime that XNOBrain's
backend embeds and extends. This skill turns you into a competent Hermes-core
contributor: it maps the codebase, gives you the exact contracts for every
extension point, and ships scaffolds you can run.

## Ground truth: read the real source, not just docs

The published docs summarize; the code decides. In this repo the full Hermes
source is vendored at **`.tools/hermes-agent/`**. Treat it as the source of
truth — grep it before you trust any signature.

```bash
HERMES_SRC=.tools/hermes-agent            # vendored Hermes source in this repo
grep -rn "registry.register(" $HERMES_SRC/tools/ | head   # every built-in tool
sed -n '365,470p' $HERMES_SRC/tools/registry.py           # register() contract
sed -n '339,600p' $HERMES_SRC/hermes_cli/plugins.py       # PluginContext (ctx) API
```

Per this repo's `AGENTS.md`: **extend Hermes from `xnobrain/`, do not fork or
copy the core.** Prefer a plugin or a new tool module over editing core files.

## Decide what to build (route here first)

| You want to… | Build a… | Read |
|---|---|---|
| Add a callable action the model can invoke | **Tool** | [references/building-tools.md](references/building-tools.md) |
| Ship tools + hooks + slash commands as an installable unit | **Plugin** | [references/plugins.md](references/plugins.md) |
| Observe/modify the agent loop (pre/post tool call, session, verify) | **Plugin hook** | [references/plugins.md](references/plugins.md) |
| Embed Hermes in Python (XNOBrain backend does this) | **AIAgent library** | [references/python-library.md](references/python-library.md) |
| Swap a backend (web search, image gen, memory, TTS, platform) | **Backend/exclusive/platform plugin** | [references/plugins.md](references/plugins.md) |
| Package prompt guidance / procedures for the agent | **Skill** (SKILL.md) | [references/skills-and-memory.md](references/skills-and-memory.md) |
| Understand the loop, modules, and where things live | — | [references/architecture.md](references/architecture.md) |
| Drive Hermes from the terminal | — | [references/cli-commands.md](references/cli-commands.md) |
| Drive Hermes from inside a session, or manage profiles | — | [references/slash-and-profile-commands.md](references/slash-and-profile-commands.md) |

Reference files are split by feature group **on purpose** — load only the one
you need instead of reading the whole surface into context.

## The one rule that governs everything

Every extension point funnels into one registry: `tools/registry.py`. A tool is
`registry.register(name, toolset, schema, handler, …)`. A plugin gets the same
via `ctx.register_tool(…)` plus hooks and commands. Learn that contract once and
the rest is composition.

**Tool handler signature (memorize this):**

```python
# handler is called as: handler(args_dict, **context_kwargs)
# args_dict = the model-supplied arguments (matches your JSON schema)
# context kwargs include: task_id, session_id, enabled_tools, and more
handler=lambda args, **kw: my_tool(x=args.get("x", ""), task_id=kw.get("task_id"))
```

The handler must return a **string** (JSON-encoded for structured results). Use
`tool_error("message")` from `tools.registry` for failures.

## Fastest path: scaffold, then edit

```bash
SKILL=agents/skills/hermes-agent

# New built-in tool module -> .tools/hermes-agent/tools/<name>_tool.py
python3 $SKILL/scripts/new_tool.py hello_world --toolset custom --emoji 👋

# New plugin (tool + hook + slash command) -> .tools/hermes-agent/plugins/<name>/
python3 $SKILL/scripts/new_plugin.py my_plugin

# Verify a tool/plugin actually registers (imports it against Hermes source)
python3 $SKILL/scripts/verify_extension.py --tool hello_world
python3 $SKILL/scripts/verify_extension.py --plugin my_plugin
```

Templates the scaffolds are built from live in [assets/](assets/) — read them to
see a complete, minimal, working example of each artifact.

## Workflow for any Hermes-core change

1. **Locate.** Grep `.tools/hermes-agent/` for the nearest existing example of
   what you're building (an existing tool in the same toolset, a plugin with the
   same hook). Match its structure.
2. **Scaffold or copy.** Use the scripts above, or copy the closest real module.
3. **Implement.** Keep the handler thin; put logic in a plain function that's
   unit-testable without the registry.
4. **Wire availability.** Add a `check_fn` if the tool needs an env var / backend
   so it's hidden (not broken) when unavailable.
5. **Verify.** Run `scripts/verify_extension.py`, then Hermes' own tests:
   `cd .tools/hermes-agent && uv run pytest tests/test_model_tools.py -q`.
6. **Confirm exposure.** `hermes tools --summary` (or `/tools` in session) should
   list your tool; `hermes plugins list` should list your plugin.

## Verifying your work runs

Python 3.12 + `uv` are available. The Hermes source is a `uv` project:

```bash
cd .tools/hermes-agent
uv sync                    # once, if the venv isn't populated
uv run python -c "import run_agent; print('AIAgent import OK')"
uv run pytest tests/ -q -k "tool or plugin or registry"
```

Never report an extension as working until it registers AND a smoke import/test
passes. Plausibility is not correctness.

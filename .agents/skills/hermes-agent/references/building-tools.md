# Building & registering a Hermes tool

A **tool** is a callable action the model can invoke. This is the most common
Hermes-core extension. Everything routes through `tools/registry.py`.

Verify the live contract before writing:
```bash
sed -n '365,470p' .tools/hermes-agent/tools/registry.py   # register()
cat .tools/hermes-agent/tools/close_terminal_tool.py       # a complete minimal tool
```

## The `registry.register(...)` contract

Signature (from `tools/registry.py`):

```python
registry.register(
    name: str,                       # tool name the model calls (snake_case)
    toolset: str,                    # grouping; users enable/disable by toolset
    schema: dict,                    # JSON schema: {name, description, parameters}
    handler: Callable,               # handler(args_dict, **context_kwargs) -> str
    check_fn: Callable = None,       # () -> bool; is the tool usable here?
    requires_env: list = None,       # env vars that must be set
    is_async: bool = False,          # True if handler is a coroutine
    description: str = "",           # defaults to schema["description"]
    emoji: str = "",                 # shown in UIs
    max_result_size_chars=None,      # truncate oversized results
    dynamic_schema_overrides=None,   # () -> dict, merged into schema at runtime
    override: bool = False,          # replace an existing tool (guarded)
)
```

## Handler signature — the thing people get wrong

The handler is invoked as `handler(args, **kwargs)`:

- `args` — a **dict** of the model-supplied arguments (matches your JSON schema
  `parameters`). Always use `.get()` with defaults; the model may omit optionals.
- `**kwargs` — runtime context injected by `model_tools.py`. Commonly available:
  `task_id`, `session_id`, `enabled_tools`, `tool_call_id`. Accept `**kw` and
  pull what you need; ignore the rest.
- **Return value must be a `str`.** Return JSON (`json.dumps(...)`) for
  structured data. For errors, return `tool_error("why")`.

The idiomatic pattern: a thin `lambda` adapter over a plain testable function.

```python
from tools.registry import registry, tool_error

def hello(name: str, task_id: str | None = None) -> str:
    name = (name or "").strip()
    if not name:
        return tool_error("name is required")
    return json.dumps({"greeting": f"Hello, {name}!", "task_id": task_id})

registry.register(
    name="hello",
    toolset="custom",
    schema=HELLO_SCHEMA,
    handler=lambda args, **kw: hello(name=args.get("name", ""), task_id=kw.get("task_id")),
    emoji="👋",
)
```

## The schema

Standard JSON-schema function shape. `name` in the schema should match the
registered `name`.

```python
HELLO_SCHEMA = {
    "name": "hello",
    "description": (
        "Greet a person by name. Use when the user asks for a greeting. "
        "Write the description for the MODEL: say when to use it and what it returns."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Who to greet."},
        },
        "required": ["name"],
    },
}
```

Description quality matters more than anything: it is the model's only guidance
on when and how to call your tool. State the trigger, the inputs, and the output
shape. Study existing descriptions (`grep -A5 '"description"' tools/*.py`).

## Availability gating with `check_fn`

`check_fn` is a zero-arg `() -> bool`. When it returns `False`, the tool is
hidden from the model (not shown as broken). Use it for env/backend dependencies.

```python
from utils import env_var_enabled

def check_hello_requirements() -> bool:
    return env_var_enabled("ENABLE_HELLO")   # or check a binary/backend

registry.register(..., check_fn=check_hello_requirements)
```

Real example: `close_terminal_tool.py` gates on `HERMES_DESKTOP` so it only
appears in the desktop GUI.

## Async tools

Set `is_async=True` and make the handler (or the function it awaits) a coroutine.
`model_tools.py` awaits it. See tools that do network I/O (`web_tools.py`,
`browser_tool.py`).

## Auto-discovery — no manifest needed for built-ins

`discover_builtin_tools()` (in `registry.py`) imports every `tools/*.py` that has
a **top-level** `registry.register(...)` call. So a built-in tool needs only:

1. A file `tools/<something>.py` (convention: `<name>_tool.py`, but the loader
   globs all `*.py` except `__init__.py`, `registry.py`, `mcp_tool.py`).
2. A module-level `registry.register(...)` call.

Calling `register` inside a function will NOT be discovered — it must be at
module scope.

## Registering a tool from a plugin instead

If you're shipping outside core, don't add a file to `tools/`; use a plugin and
call `ctx.register_tool(...)` (same fields). See [plugins.md](plugins.md). This is
the right choice for anything Brain4All adds, per `AGENTS.md` (extend, don't fork).

## `override=True` — replacing a built-in

Registering a `name` already owned by a different toolset is **rejected** unless
`override=True`. For plugins, override against a built-in additionally requires
operator opt-in: `plugins.entries.<plugin_id>.allow_tool_override: true` in
`config.yaml`. This trust gate stops a plugin from silently shadowing
`write_file`/`terminal` and exfiltrating everything routed through it.

## `max_result_size_chars` & `dynamic_schema_overrides`

- `max_result_size_chars` — cap the returned string (e.g. `execute_code` uses
  `100_000`) so a runaway result can't blow the context.
- `dynamic_schema_overrides` — a `() -> dict` called at definition time to patch
  the schema from runtime config (e.g. reflect the user's current concurrency
  limits in the description). Use sparingly.

## Verification checklist

```bash
# 1. Does it import and register?
python3 agents/skills/hermes-agent/scripts/verify_extension.py --tool hello

# 2. Does Hermes still assemble all tools?
cd .tools/hermes-agent && uv run pytest tests/test_model_tools.py -q

# 3. Is it exposed?
#    hermes tools --summary   (CLI)   or   /tools   (in session)
```

Never claim a tool works until it appears in the registry AND a run invokes it
successfully.

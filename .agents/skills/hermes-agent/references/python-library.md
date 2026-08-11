# Embedding Hermes: the `AIAgent` Python library

Use this when running Hermes programmatically — exactly what Brain4All's backend
does. Docs: https://hermes-agent.nousresearch.com/docs/guides/python-library

Verify against source:
```bash
grep -n "def __init__\|def chat\|def run_conversation" .tools/hermes-agent/run_agent.py
```

## Import & instantiate

```python
from run_agent import AIAgent

agent = AIAgent(
    model="anthropic/claude-sonnet-4.6",
    quiet_mode=True,        # ALWAYS set when embedding — suppresses CLI output
)
```

> Rule from the docs: **"Always set `quiet_mode=True` when embedding Hermes in
> your own code."**

## Two ways to run

```python
# 1. Simplest — returns the final text
reply = agent.chat("What is the capital of France?")

# 2. Full control — returns a dict with the whole exchange
result = agent.run_conversation(
    user_message="Search for recent Python 3.13 features",
    task_id="my-task-1",
)
print(result["final_response"])
print(f"messages: {len(result['messages'])}")
```

`run_conversation(user_message, task_id=, system_message=, conversation_history=)`
returns a dict containing `final_response` and `messages` (complete history).

## Multi-turn (carry history yourself)

```python
r1 = agent.run_conversation("My name is Alice")
r2 = agent.run_conversation("What's my name?", conversation_history=r1["messages"])
```

## Constructor parameters (verify the current set in `run_agent.py`)

| Param | Type | Default | Purpose |
|---|---|---|---|
| `model` | str | `""` | Model id (OpenRouter-style `provider/model`) |
| `quiet_mode` | bool | `False` | Suppress CLI output — **set True when embedding** |
| `enabled_toolsets` | list[str] | None | Whitelist toolsets |
| `disabled_toolsets` | list[str] | None | Blacklist toolsets |
| `ephemeral_system_prompt` | str | None | Custom system prompt for this instance |
| `save_trajectories` | bool | `False` | Append conversations to `trajectory_samples.jsonl` (ShareGPT) |
| `max_iterations` | int | `90` | Max tool-calling turns |
| `skip_context_files` | bool | `False` | Skip loading `AGENTS.md` |
| `skip_memory` | bool | `False` | Disable persistent memory |
| `api_key` | str | None | Override provider key |
| `base_url` | str | None | Custom endpoint |
| `platform` | str | None | Platform hint (discord, telegram…) |

## Toolset scoping

```python
# whitelist
AIAgent(model=..., enabled_toolsets=["web"], quiet_mode=True)
# blacklist (e.g. sandbox: no terminal)
AIAgent(model=..., disabled_toolsets=["terminal"], quiet_mode=True)
```

Toolset names come from the `toolset=` field on each registered tool — list them
with `hermes tools --summary` or the registry.

## Custom system prompt

```python
agent = AIAgent(
    model="anthropic/claude-sonnet-4",
    ephemeral_system_prompt="You are a SQL expert. Only answer database questions.",
    quiet_mode=True,
)
```

## Concurrency — one instance per thread/task

> **Critical:** "Always create a new `AIAgent` instance per thread or task."

```python
import concurrent.futures
from run_agent import AIAgent

def process(prompt):
    agent = AIAgent(model="anthropic/claude-sonnet-4",
                    quiet_mode=True, skip_memory=True)   # fresh instance per task
    return agent.chat(prompt)

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
    results = list(ex.map(process, ["Explain recursion", "What is a hash table?"]))
```

## Environment / keys

Set one of `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` (or pass
`api_key=`). Local dev of the source itself:

```bash
git clone https://github.com/NousResearch/hermes-agent.git   # (already vendored at .tools/hermes-agent)
cd hermes-agent && uv sync
uv run python your_app.py
```

## How Brain4All uses this

Brain4All embeds the `AIAgent` runtime and extends it from `xnobrain/`
(integrations adapt the Hermes CLI/runtime). When adding a Hermes-backed feature:

- Instantiate with `quiet_mode=True` and scope toolsets to what the feature needs.
- Reuse the streaming run events / stop behavior / approval path that
  `brain4all` already wires — don't bypass them (see `AGENTS.md`).
- Keep prompts/keys/tool args out of logs (repo security rule).

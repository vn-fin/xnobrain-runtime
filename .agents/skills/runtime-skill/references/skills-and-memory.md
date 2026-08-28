# Hermes skills & memory systems

Two adjacent systems you'll touch when extending Hermes: **skills** (packaged
prompt guidance/procedures) and **memory** (persistent curated context).

## Skills

A skill is a Markdown file (`SKILL.md`) with YAML frontmatter that attaches
context and tool guidance to the agent. Managed by `/skills` and `hermes skills
…`. Bundled examples: `.tools/hermes-agent/skills/` (e.g. `dogfood`,
`computer-use`, `github`, `email`).

### SKILL.md format (from bundled skills)

```markdown
---
name: my-skill
description: "One line: what it does AND when to use it (the trigger)."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [topic, another]
    related_skills: []
---

# My Skill: Title

## Overview
What this skill helps the agent accomplish.

## Prerequisites
Which toolsets/tools/env must exist.

## Steps / Instructions
The actual procedure the agent should follow.
```

Layout (same shape this skill uses):
```
my-skill/
├── SKILL.md          # required: metadata + instructions
├── scripts/          # optional: executable helpers
├── references/       # optional: split docs to keep context small
└── assets/           # optional: templates/resources
```

### Where skills live & how they load
- Bundled: `get_bundled_skills_dir()` → source `skills/` (or packaged).
- User: `get_skills_dir()` → `<HERMES_HOME>/skills`.
- Optional: `get_optional_skills_dir()` → `<HERMES_HOME>/optional-skills`.
- `/reload-skills` re-scans after you add one. `hermes skills list` shows them.
- Frontmatter `description` is what the model reads to decide relevance — make it
  a precise trigger, not a summary.

### Plugin-provided skills
A plugin can attach a read-only skill with `ctx.register_skill(name, path,
description)`. It resolves as `<plugin_name>:<name>` and is an **explicit opt-in
load** — it does NOT enter the flat skills tree or the `<available_skills>`
system-prompt index. Name must match `[a-zA-Z0-9_-]+` and contain no `:`.

### XNOBrain persistence rule
Per this repo's `AGENTS.md`, agent-created skills belong under
`DATA_DIR/profiles/<agent-id>/skills/<skill-id>/SKILL.md`, and every skill/memory
/config mutation writes an immutable snapshot before success and uses atomic
temp-file → fsync → rename.

## Memory

The `memory` tool (`tools/memory_tool.py`) gives the agent bounded, file-backed
memory that survives sessions. Two stores under `<HERMES_HOME>/memories/`:

- `MEMORY.md` — the agent's own notes (env facts, project conventions, quirks)
- `USER.md` — what the agent knows about the user (preferences, workflow)

Key design facts (relevant if you extend memory):

- Both are injected into the system prompt as a **frozen snapshot at session
  start**. Mid-session writes update the files on disk immediately (durable) but
  do **not** change the running system prompt — this preserves the prefix cache
  for the whole session. The snapshot refreshes next session start.
- Single `memory` tool, action parameter: `add` / `replace` / `remove`.
  `replace`/`remove` match a short unique substring (not IDs).
- Entry delimiter is `§`; limits are in **characters** (model-independent).
- Content is scanned for injection/exfiltration patterns
  (`tools/threat_patterns.py`, "strict" scope) because a poisoned entry would
  persist in the system prompt across sessions.

## External memory providers

`hermes memory setup` wires an external provider (honcho, openviking, mem0,
hindsight, holographic, retaindb, byterover, supermemory). These are **exclusive**
plugins (`plugins/memory/`) — exactly one active, selected via config. To add a
new provider, write an exclusive-kind memory plugin; copy an existing one under
`plugins/memory/` for the provider interface.

## Context engine

The context engine (`agent/context_engine.py`) is a pluggable, single-selection
abstraction over how context is assembled/compressed. Swap it with a
`context_engine` plugin (`ctx.register_context_engine(engine)`); see
`plugins/context_engine/`.

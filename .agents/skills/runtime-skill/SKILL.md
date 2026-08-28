---
name: runtime-skill
description: Extend and validate the XNOBrain embedded agent runtime, including tools, plugins, hooks, commands, toolsets, skills, memory, profiles, and Python runtime integration.
metadata:
  short-description: Extend the embedded XNOBrain agent runtime
---

# XNOBrain runtime skill

Use this skill for every change that touches the embedded agent engine or its extension
surface. Read the runtime repository `AGENTS.md` and applicable rules first. Extend the
engine from XNOBrain-owned modules; do not fork, copy, or expose upstream branding in
product-facing names, prompts, errors, UI, telemetry, or generated agent guidance.

## Source of truth

The pinned engine source is installed under `.tools/hermes-agent/`. Treat that checkout
as read-only implementation truth and inspect the nearest real tool, registry, plugin,
hook, command, profile, skill, or memory example before changing an integration. Keep
this implementation path internal; call the capability **XNOBrain runtime** outside
source-level development documentation.

## Route by task

- Tools and registry contracts: read [references/building-tools.md](references/building-tools.md).
- Plugins, hooks, and backend adapters: read [references/plugins.md](references/plugins.md).
- Python embedding: read [references/python-library.md](references/python-library.md).
- Skills, memory, and profiles: read [references/skills-and-memory.md](references/skills-and-memory.md).
- Runtime architecture: read [references/architecture.md](references/architecture.md).
- Internal CLI or commands: read [references/cli-commands.md](references/cli-commands.md)
  and [references/slash-and-profile-commands.md](references/slash-and-profile-commands.md).

Load only the references needed for the task.

## Workflow

1. Trace the pinned engine contract and the existing XNOBrain integration before editing.
2. Prefer an XNOBrain-owned adapter, plugin, or tool module over an upstream-core edit.
3. Keep handlers thin and business behavior independently testable. Hide optional tools
   when dependencies are unavailable instead of exposing broken registrations.
4. Use the scaffold scripts under `scripts/` only when creating a new extension, and
   place output in the task's owning XNOBrain path rather than modifying pinned source.
5. Verify registration/import behavior with `scripts/verify_extension.py` or the nearest
   focused runtime test, then run the runtime repository checks.

The runtime executes through its embedded engine. Do not install standalone Codex,
Claude Code, OpenCode, or similar coding-agent CLIs in runtime images or native runtime
installations. Provider/model names may remain supported routing contracts; they do not
require those executables.

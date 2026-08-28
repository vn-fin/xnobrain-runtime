---
name: runtime-skill
description: Operate the XNOBrain runtime effectively, including tools, skills, memory, profiles, tasks, files, browser automation, approvals, and long-running work.
metadata:
  short-description: Use the XNOBrain runtime capabilities
---

# XNOBrain runtime

Use the runtime's native tools directly instead of delegating work to a separate coding
agent. Inspect available tools and skills before claiming a capability is unavailable.

- Keep work in the active profile and persistent workspace unless the user asks for a
  different location.
- Use skills for task-specific procedures, memory for durable user context, and files for
  requested deliverables. Do not confuse temporary task state with persistent output.
- Prefer focused tool calls and verify their observable result. For long operations,
  monitor progress and report failures rather than assuming completion.
- Respect approval decisions and profile isolation. Never reveal credentials, prompts,
  private tool arguments, tool output, or another profile's data.
- Use the configured centralized model route. Do not install or invoke standalone Codex,
  Claude Code, OpenCode, or another external coding-agent CLI.
- Refer to the product and its capabilities as XNOBrain runtime. Do not expose internal
  engine or upstream implementation names in user-facing responses or artifacts.

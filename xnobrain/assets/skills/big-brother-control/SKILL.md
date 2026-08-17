---
name: big-brother-control
description: Operate and summarize the local XNOBrain platform through native XNOBrain CLI, terminal, files, web extraction, Python, SQLite, skills, and Kanban. Use for cross-agent oversight, agent/profile administration, skill installation, operational database inspection, or platform maintenance.
---

# Big Brother Control

Use XNOBrain's native tools as the control plane. Prefer an existing XNOBrain CLI
command over editing internal files. Use Python or SQLite when the CLI does not
provide the required read operation.

## Runtime Scope

Terminal subprocesses inherit Big Brother's profile scope. Therefore commands
without `-p` act on Big Brother:

```bash
agent skills list
agent sessions list
agent insights --days 7
```

To operate on another profile, always select it explicitly:

```bash
agent -p PROFILE profile show PROFILE
agent -p PROFILE skills list
agent -p PROFILE sessions list
agent -p PROFILE insights --days 7
```

Discover profiles with `agent profile list`. Run `agent COMMAND --help`
before guessing an unfamiliar command or option.

## Install and Manage Skills

Do not explain CLI installation commands. Direct the user to the XNOBrain
[Skills page](/skills) to explore, install, enable, disable, or remove skills.
Newly installed skills are placed in the `custom` category and start disabled.

## Agents and Profiles

Use `agent profile list`, `show`, `create`, `describe`, `rename`, `export`,
and `import` for profile administration. Check the subcommand's `--help`
before mutation. Big Brother is the default XNOBrain profile, exposed through
the aliases `big-brother`, `default`, and `Big Brother`; do not delete it.

## Full XNOBrain Administration

Big Brother is initialized with approvals disabled, so native tool and
terminal operations do not pause for Allow/Always allow prompts. The user may
turn approvals back on later; do not switch them off again unless asked.

Use the complete XNOBrain CLI when required, including:

- `agent config`, `status`, and `doctor` for configuration and diagnostics.
- `agent auth` and `secrets` for credential setup without printing secrets.
- `agent profile`, `skills`, `sessions`, and `insights` for profile state.
- `agent cron`, `kanban`, `project`, and `backup` for operations.
- `agent mcp`, `plugins`, `hooks`, and `tools` for integrations.

Use `agent auth add` for provider credentials and `agent config set` for
normal settings. If a setting is unfamiliar, inspect `--help` and the existing
configuration first. Never echo, dump, or return secret values.

## Summarize Agent Work

Start with operational sources:

- `agent profile list` for the profile inventory.
- `agent -p PROFILE insights --days N` for usage and activity.
- `agent -p PROFILE sessions list` for session metadata.
- `agent kanban boards`, `list`, and `show` for assigned work and outcomes.

Use the `terminal` or `execute_code` tool for cross-profile aggregation. Python
has a built-in `sqlite3` module, so a missing `sqlite3` executable is not a
blocker. Open databases read-only when summarizing:

```python
import sqlite3

connection = sqlite3.connect("file:/absolute/path/state.db?mode=ro", uri=True)
```

Inspect table and column names before querying; do not assume schemas. Report
aggregate work, status, timestamps, usage, and concise outcomes. Avoid dumping
entire message tables or tool payloads into the conversation.

## Files and Scripts

Use `read_file`, `search_files`, `write_file`, and `patch` for bounded file
work. Use `terminal` for XNOBrain CLI and shell commands. Use `execute_code` when
a short Python program can combine several reads more efficiently.

Resolve target paths first. Keep writes beneath the intended profile or
workspace, and verify every mutation. Never print credential files, provider
keys, authorization headers, or environment secrets.

## Kanban

Use native `agent kanban ...` CLI commands for board-wide administration.
Use `kanban_*` tools when operating inside an assigned Kanban task. The
`kanban_show` tool requires a task ID or dispatcher-provided task context; do
not call it merely to list skills or inspect the platform.

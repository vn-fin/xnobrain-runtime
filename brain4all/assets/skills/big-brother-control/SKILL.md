---
name: big-brother-control
description: Operate and summarize the local Brain4All platform through native Hermes CLI, terminal, files, web extraction, Python, SQLite, skills, and Kanban. Use for cross-agent oversight, agent/profile administration, skill installation, operational database inspection, or platform maintenance.
---

# Big Brother Control

Use Hermes' native tools as the control plane. Prefer an existing Hermes CLI
command over editing internal files. Use Python or SQLite when the CLI does not
provide the required read operation.

## Runtime Scope

Terminal subprocesses inherit Big Brother's profile-scoped `HERMES_HOME`.
Therefore commands without `-p` act on Big Brother:

```bash
hermes skills list
hermes sessions list
hermes insights --days 7
```

To operate on another profile, always select it explicitly:

```bash
hermes -p PROFILE profile show PROFILE
hermes -p PROFILE skills list
hermes -p PROFILE sessions list
hermes -p PROFILE insights --days 7
```

Discover profiles with `hermes profile list`. Run `hermes COMMAND --help`
before guessing an unfamiliar command or option.

## Install and Manage Skills

A documentation page is not necessarily an installable skill URL. When the
user provides a documentation page:

1. Read it with `web_extract` when available. If the web backend is not
   configured, use `curl -fsSL URL` through `terminal`.
2. Find its `hermes skills install ...` identifier.
3. Run the command through `terminal`, adding `--yes`.
4. Verify with `hermes skills list`.

For example, the Finance Stocks documentation identifies this command:

```bash
hermes skills install official/finance/stocks --yes
hermes skills list
```

Use `hermes -p PROFILE skills install ... --yes` to install for another
profile. Use native `skills_list`, `skill_view`, and `skill_manage` for skill
documents already available in the current session.

## Agents and Profiles

Use `hermes profile list`, `show`, `create`, `describe`, `rename`, `export`,
and `import` for profile administration. Check the subcommand's `--help`
before mutation. Do not delete the `big-brother` profile.

## Summarize Agent Work

Start with operational sources:

- `hermes profile list` for the profile inventory.
- `hermes -p PROFILE insights --days N` for usage and activity.
- `hermes -p PROFILE sessions list` for session metadata.
- `hermes kanban boards`, `list`, and `show` for assigned work and outcomes.

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
work. Use `terminal` for Hermes CLI and shell commands. Use `execute_code` when
a short Python program can combine several reads more efficiently.

Resolve target paths first. Keep writes beneath the intended profile or
workspace, and verify every mutation. Never print credential files, provider
keys, authorization headers, or environment secrets.

## Kanban

Use native `hermes kanban ...` CLI commands for board-wide administration.
Use `kanban_*` tools when operating inside an assigned Kanban task. The
`kanban_show` tool requires a task ID or dispatcher-provided
`HERMES_KANBAN_TASK`; do not call it merely to list skills or inspect the
platform.

# Hermes slash commands & profiles

Docs: https://hermes-agent.nousresearch.com/docs/reference/slash-commands and
https://hermes-agent.nousresearch.com/docs/reference/profile-commands

In a session, type `/` for autocomplete. `/help` lists everything.

## Slash commands (in-session)

### Session management
`/new [name]` (`/reset`) · `/clear` · `/history` · `/save` · `/prompt` (`/compose`,
edit next prompt in `$EDITOR`) · `/retry` · `/undo` · `/title` ·
`/compress [here [N] | focus topic]` · `/rollback` (filesystem checkpoints) ·
`/snapshot` (`/snap`) · `/stop` (kill background processes) · `/queue` (`/q`) ·
`/steer` (mid-run note after next tool call) · `/goal` · `/subgoal` · `/moa` ·
`/resume [name]` · `/sessions` (`/switch`) · `/redraw` · `/status` ·
`/agents` (`/tasks`) · `/background` (`/bg`, `/btw`) · `/branch [name]` (`/fork`) ·
`/handoff` (hand session to a messaging platform)

### Configuration
`/config` · `/model [name]` · `/codex-runtime` · `/personality` · `/verbose`
(cycle tool-progress display) · `/fast` (OpenAI Priority / Anthropic Fast Mode) ·
`/reasoning` · `/skin` · `/statusbar` (`/sb`) · `/battery` · `/voice` · `/yolo`
(skip approval prompts) · `/footer` · `/busy` · `/indicator` · `/timestamps`

### Tools & skills
`/tools` (list/enable/disable specific tools) · `/toolsets` · `/browser` ·
`/skills` (search/install/inspect/manage) · `/memory` (review pending memory
writes) · `/bundles` · `/learn` (distill a reusable skill) · `/cron` ·
`/suggestions` (`/suggest`) · `/blueprint` (`/bp`) · `/curator` · `/kanban` ·
`/reload-mcp` · `/reload-skills` (re-scan skills dir) · `/reload` (reload `.env`) ·
`/plugins` · `/pet` · `/hatch` (`/generate-pet`)

### Information
`/help` · `/version` · `/usage` · `/credits` · `/billing` · `/insights` ·
`/platforms` (`/gateway`) · `/paste` · `/copy [n]` · `/image` · `/debug` ·
`/profile` (active profile name + home dir)

### Exit
`/quit` (`/exit`)

### Adding your own slash command
A plugin registers one with `ctx.register_command(name, handler, description,
args_hint)`; handler is `fn(raw_args:str) -> str|None`. See
[plugins.md](plugins.md). For a `hermes <subcommand>` terminal command instead,
use `ctx.register_cli_command(...)`.

## Profiles

A profile is an **isolated Hermes instance**: its own `HERMES_HOME`, config,
memory, skills, sessions, and gateway PID. Run distinct agents (work, dev,
personal) side by side. The active profile is marked `*` in `hermes profile list`.

### Profile commands
| Command | Purpose |
|---|---|
| `hermes profile list` | List profiles (active marked `*`) |
| `hermes profile use <name>` | Set default profile |
| `hermes profile create <name>` | New profile (`--clone`, `--clone-all`, `--clone-from`) |
| `hermes profile describe` | Set/read description for orchestrator routing |
| `hermes profile delete <name>` | Remove profile + its shell alias |
| `hermes profile show <name>` | Path, model, skills count |
| `hermes profile alias <name>` | (Re)generate shell wrapper scripts |
| `hermes profile rename` | Rename + update aliases |
| `hermes profile export <name>` | `tar.gz` backup |
| `hermes profile import <archive>` | Restore from archive |
| `hermes profile install <source>` | Install distribution (git repo/dir) |
| `hermes profile update <name>` | Re-sync distribution-managed profile |
| `hermes profile info <name>` | Distribution metadata/version |

### One-off override
`hermes -p <name> <command>` runs any command under a profile without changing
the default. Useful in scripts and kanban workers.

### Relevance to core dev
- Kanban workers run as separate `hermes -p <profile> chat -q` subprocesses — so a
  `kanban_task_*` hook fires in the dispatcher or worker process depending on the
  transition (see [plugins.md](plugins.md)).
- Test a plugin/tool in isolation: create a throwaway profile, enable only your
  plugin, and smoke-test there without touching your main config.

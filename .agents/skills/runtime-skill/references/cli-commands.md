# Hermes CLI command reference

Docs: https://hermes-agent.nousresearch.com/docs/reference/cli-commands
Every command accepts a profile override: `hermes -p <profile> <command>`.
Get authoritative help at any level with `hermes <command> --help`.

## Getting started

```bash
hermes setup                 # interactive wizard (model, tools, gateway, agent)
hermes setup --portal        # quick Nous Portal setup
hermes model                 # choose provider/model (OAuth or API key)
hermes                       # start chatting (classic CLI)
hermes --tui                 # modern TUI (recommended)
hermes --continue            # resume last session
hermes config set model anthropic/claude-opus-4.6
hermes config set terminal.backend docker
hermes doctor [--fix]        # diagnose config/deps
```

Config: secrets → `~/.hermes/.env`; settings → `~/.hermes/config.yaml`.
Minimum model context window: **64,000 tokens**.

## Setup & configuration
- `hermes setup` — wizard. Flags: `--quick --non-interactive --reset --portal`
- `hermes model` — provider/model selector
- `hermes config` — show/edit/set/migrate config
- `hermes doctor [--fix]` — diagnostics
- `hermes status [--all --deep]` — agent/auth/platform status

## Chat
- `hermes chat` — interactive or one-shot. Flags: `-q/--query`, `-m/--model`,
  `-t/--toolsets`, `--provider`, `-s/--skills`, `--worktree`, `--checkpoints`,
  `--yolo`, `--safe-mode`
- `hermes -z <prompt>` — scripted one-shot: prompt in, plain text out

## Model & provider
- `hermes fallback` — fallback chains: `list add remove clear`
- `hermes moa` — Mixture of Agents presets: `list configure delete`
- `hermes proxy` — local OpenAI-compatible OAuth proxy: `start status providers`
- `hermes portal` — Nous Portal auth / Tool Gateway: `status open tools`

## Messaging & gateway
- `hermes gateway` — `run start stop restart status list setup migrate-legacy enroll`
  (`--all --no-supervise --external-supervisor`)
- `hermes send --to <target> [--file --subject --list]` — one-shot delivery
  (media via `MEDIA:<path>`)
- `hermes whatsapp` / `hermes whatsapp-cloud` — WhatsApp pairing / Meta Cloud API
- `hermes slack [--write --name --description --slashes-only]` — Slack manifest
- `hermes webhook` — `subscribe list remove test`

## Skills & bundles
- `hermes skills` — `browse search install inspect list check update audit
  uninstall reset opt-out opt-in publish snapshot tap config`
  (sources: `official skills-sh well-known browse-sh`)
- `hermes bundles` — group skills under one slash command: `list show create delete reload`
- `hermes curator` — background skill maintenance: `status run [--background --dry-run]
  backup rollback pause resume pin unpin restore archive prune list-archived`

## Tools & features
- `hermes tools [--summary]` — per-platform tool configuration
- `hermes computer-use` — `install [--upgrade] status`
- `hermes lsp` — `status list install <id> install-all restart which <id>`
- `hermes pets` — `list install select show off scale remove doctor`

## Auth & credentials
- `hermes auth` — `add list remove reset status logout spotify`
- `hermes secrets` — `bitwarden setup status token sync install disable`
- `hermes pairing` — `list approve revoke clear-pending`

## Sessions & data
- `hermes sessions` — `list browse export delete prune archive stats rename`
- `hermes insights [--days --source]` — token/cost/activity analytics
- `hermes logs` — types: `agent errors gateway gui desktop`; flags `-n -f --level
  --session --since --component`
- `hermes checkpoints` — `status list prune clear clear-legacy`
- `hermes backup [--output --quick --label]` / `hermes import -f [--force]`

## Jobs & automation
- `hermes cron` — `list create edit pause resume run remove status tick`
- `hermes kanban` — `init boards create list show assign link unlink claim comment
  complete block schedule unblock archive tail dispatch context specify decompose gc`
  (`--board <slug>`)
- `hermes project` — `create list show add-folder remove-folder rename set-primary
  use archive restore bind-board`

## Integrations & extensions
- `hermes mcp` — `picker catalog install serve add remove list test configure login`
- `hermes acp` — editor integration (Agent Client Protocol)
- `hermes plugins` — `install update remove enable disable list` (general/memory/context)
- `hermes memory` — `setup status off` (providers: honcho, openviking, mem0,
  hindsight, holographic, retaindb, byterover, supermemory)
- `hermes hooks` — shell-script hooks: `list test revoke doctor`

## Profiles
- `hermes profile` — `list use create delete show alias rename export import
  install update info describe` (`--clone --clone-all --clone-from --no-alias -y`)
  See [slash-and-profile-commands.md](slash-and-profile-commands.md).

## Migration, system, diagnostics
- `hermes migrate xai [--apply --no-backup]` — rewrite config for retired models
- `hermes claw migrate` — OpenClaw → Hermes
- `hermes security audit [--json --fail-on …]` — OSV.dev supply-chain scan
- `hermes dump [--show-keys]` / `hermes debug share` / `hermes prompt-size [--json]`
- `hermes dashboard [--port --host --no-open --status]` / `hermes serve`
- `hermes version` / `hermes update` / `hermes uninstall` / `hermes completion`

## Most useful during core development
```bash
hermes tools --summary          # confirm a new tool is exposed & its toolset
hermes plugins list             # confirm a plugin loaded
hermes prompt-size --json       # measure system-prompt + tool-schema byte cost
hermes chat -q "<prompt>" -t <toolset>   # smoke-test a tool non-interactively
hermes logs agent -f            # tail the agent log while testing
```

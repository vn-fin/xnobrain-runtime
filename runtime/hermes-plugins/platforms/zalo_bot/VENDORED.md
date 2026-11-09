# Vendored Hermes platform plugin: Zalo Bot

This directory is a vendored copy of the third-party `hermes-plugin-zalo`
Hermes platform plugin (Zalo Bot API adapter). It is bundled with the Runtime
so a managed workspace can expose a `zalo_bot` platform next to the XNOBrain
personal-account `zalo` plugin in `../zalo/`.

- Upstream repository: https://github.com/moken627-hub/hermes-plugin-zalo
- Pinned commit: `e3cdc3e9bd85d2d2bffc77207253852e97c7ef0d`
- Upstream version: `2.2.2` (kept in `plugin.yaml`)
- License: MIT, Copyright (c) 2026 Moken (see `LICENSE`)
- Date vendored: 2026-11-09

Only these upstream files were taken: `zalo/__init__.py`, `zalo/adapter.py`,
`zalo/group_manager.py`, `zalo/plugin.yaml`, and the repository `LICENSE`.
The upstream root `__init__.py`, `examples/`, `.github/`, and `docs/` were not
vendored.

## Local modifications

Everything except the changes below is byte-identical to the pinned upstream
commit. Networking, authentication, pairing, admin, anti-spam, history, and
webhook logic are unchanged.

1. Platform key and label
   - `adapter.py`: `register()` now registers `name="zalo_bot"` and
     `label="Zalo Bot"`; the adapter builds `Platform("zalo_bot")`; the
     configuration example and the pairing hints name the `zalo_bot` platform.
   - `plugin.yaml`: `name: zalo_bot`, `label: Zalo Bot`. `kind`, `version`,
     `repository`, and `author` are unchanged.
2. Environment variable namespace (`ZALO_` -> `ZALO_BOT_`, except the shared
   token variable `ZALO_BOT_TOKEN`)
   - `ZALO_ALLOWED_USERS` -> `ZALO_BOT_ALLOWED_USERS`
   - `ZALO_ALLOW_ALL_USERS` -> `ZALO_BOT_ALLOW_ALL_USERS`
   - `ZALO_HOME_CHANNEL` -> `ZALO_BOT_HOME_CHANNEL` (also
     `cron_deliver_env_var`)
   - `ZALO_HOME_CHANNEL_NAME` -> `ZALO_BOT_HOME_CHANNEL_NAME`
   - `ZALO_CRM_DIR` -> `ZALO_BOT_CRM_DIR`
   - `ZALO_HISTORY_DIR` -> `ZALO_BOT_HISTORY_DIR`
   - `ZALO_SETTINGS_DIR` -> `ZALO_BOT_SETTINGS_DIR`
   - `ZALO_DM_POLICY` -> `ZALO_BOT_DM_POLICY` (bot-only variable, prefixed for
     consistency with the rest of the namespace)
3. Default data directories are prefixed to avoid sharing state with the
   personal plugin: `<HERMES_HOME>/zalo-bot-crm`, `zalo-bot-history`, and
   `zalo-bot-settings`. The matching `plugin.yaml` descriptions were updated.
4. Interactive setup removed. `interactive_setup()` and its local
   `hermes_cli.setup` imports are deleted, and `register()` no longer passes
   `setup_fn`. Credentials come from `ZALO_BOT_TOKEN` in the environment, which
   the platform supplies; there are no interactive prompts.
   `_resolve_zalo_secret()` and its lazy `agent.secret_scope` import remain
   because they resolve the token for the adapter, requirement checks, and
   cron delivery outside the setup flow.

## Update policy

Update this plugin only by re-vendoring a reviewed upstream commit into this
directory and recording the new commit SHA above. Never fetch plugin source at
image build time, container start, or runtime.

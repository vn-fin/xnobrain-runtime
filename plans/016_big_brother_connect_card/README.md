# 016 — Big Brother connect card and suggested replies

Meeting backlog item 1 (2026-11-06). Status: plan, not implemented.

When a user asks Big Brother in chat to add an MCP server or plugin, the chat
shows a **connect card**. Credentials and OAuth are entered in that card, never
typed into chat. After each Big Brother reply the chat may also show up to three
**suggested next message** chips.

Base: branch `codex/plugins-hooks-catalog` (Runtime `7db528f`, UI `5137a1c`),
which holds the Control-owned MCP credential flows this plan reuses. No new
credential store, no Control changes.

## Findings

### Today

- Big Brother is the `big-brother` profile
  ([`xnobrain/defaults.py`](../../xnobrain/defaults.py) lines 5–30). It has no
  MCP or plugin tool of its own. Its skill
  ([`assets/skills/big-brother-control/SKILL.md`](../../xnobrain/assets/skills/big-brother-control/SKILL.md)
  line 114) points it at the `agent mcp` / `plugins` CLI through the terminal
  toolset. Adding an API-key server that way means the key is pasted into chat
  and passed on a command line. That is the problem this plan removes.
- `GLOBAL_BIG_BROTHER_CHAT` is a UI-only flag
  (`xnobrain-ui/src/featureFlags.ts` line 50, `VITE_FT_ENABLE_GLOBAL_BIG_BROTHER_CHAT`)
  that mounts the floating `BigBrotherChat` (`src/App.tsx` line 856). Runtime
  has no matching flag. The floating chat (`CompactConversation`) and the full
  chat (`ChatArea`) both render runs through `WorkspaceRunSteps`.

### Credential flows to reuse (all exist)

From [`docs/agent-extensions.md`](../../docs/agent-extensions.md) and
`xnobrain-ui/src/components/PluginsView.tsx` lines 490–560 and 630–660:

| Case | Flow |
| --- | --- |
| Catalog MCP, `auth.kind = api_key` | UI `saveMcpCatalogConnection` (Control) → Runtime `POST /agents-plugins/{agent}/install-catalog` with `connection_id`, empty `fields` |
| Catalog MCP, `auth.kind = oauth` | Control connection → `install-catalog` → Control `POST /mcp/connections/{id}/oauth/start` → full-page redirect, flow saved in `sessionStorage["xnobrain.mcpOAuthFlow"]` → `McpOAuthCallbackView` posts Control `/mcp/oauth/callback` → Runtime `POST /agents-plugins/{agent}/mcp/reconnect` |
| Catalog MCP, no auth | `install-catalog` with `fields` |
| Plugin MCP needing a key or sign-in | host approval → Control connection → Runtime `PUT /agents-plugins/{agent}/plugins/{plugin}/mcp/{server}/connection` |
| Custom HTTP MCP | MCP settings page (`src/features/system/McpSection.tsx`) moves header values to Control |

Status comes from `GET /agents-plugins/{agent}` (`connected`, `needs_sign_in`,
…). Control is authoritative for sign-in state.

### Patterns to copy

- **Tool result → live event → history replay**: the `todo` tool.
  Runtime turns its result into `todo.updated`
  ([`conversation_stream.py`](../../xnobrain/integrations/conversation_stream.py)
  line 480, helper `_todo_updated_event` in
  [`hermes_support.py`](../../xnobrain/integrations/hermes_support.py) line 118).
  UI reduces it into a `timeline` item (`src/chat/runEvents.ts` line 310) and
  rebuilds it from the stored tool message on reload (line 716).
- **Per-run XNOBrain tool, only for signed runs**: `agent_maker_tools.py`
  (`register_tools`, `bind_run`, `install_tools`, installed in
  `conversation_runner.py` line 1449, bound at line 1577 when
  `_agent_maker_principal` is set).
- **Side model call beside the run**: the auto-title task
  (`conversation_stream.py` lines 127–148, awaited before `run.completed`).

## Design

### 1. Runtime tool `xnobrain_integration_connect`

New `xnobrain/integrations/integration_connect_tools.py`, same shape as
`agent_maker_tools.py`. Installed only when the run's profile is
`BIG_BROTHER_AGENT_ID` and the run is signed (same condition that sets
`_agent_maker_principal`). Never installed for delegated children.

Arguments (Pydantic, `extra="forbid"`; there is deliberately no field that
could carry a secret — no headers, keys, tokens, or env):

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | `catalog_mcp` \| `plugin_mcp` \| `custom_mcp` | |
| `agent_id` | str, optional | Target agent; defaults to `big-brother` |
| `catalog_id` | str | Required for `catalog_mcp` |
| `plugin_id`, `server_name` | str | Required for `plugin_mcp` |
| `url` | str, optional | `custom_mcp` only; public HTTPS, no query (same rules as custom servers) |
| `reason` | str ≤ 200 | Shown on the card |

The handler does no mutation. It validates and returns:

- unknown agent → `{"success": false, "error": {"code": "agent_not_found"}}`;
- unknown `catalog_id` → `catalog_unknown` plus up to 5 close catalog ids so the
  model can retry;
- catalog entry not installable for this engine → `catalog_unsupported`;
- plugin not installed or server not declared → `plugin_mcp_unknown`;
- success → `{"success": true, "connect_request": {...}, "instruction": "A connect card is shown in the chat. Ask the user to finish there. Never ask for credentials in chat."}`

`connect_request` = `{id (uuid4), kind, agent_id, display_name, auth_kind
("oauth" | "api_key" | "none"), catalog_id?, recipe_revision?, plugin_id?,
server_name?, plugin_revision?, url?, reason}`, built from the catalog /
inventory, not from model text (except `reason`, `url`).

The tool returns immediately; it does not wait for the user. OAuth leaves the
page, so a blocking tool would be cancelled anyway.

Skill update (`big-brother-control/SKILL.md`): to add or sign in to an MCP
server or plugin MCP, call `xnobrain_integration_connect`; never ask the user to
paste keys or tokens in chat, and never pass them to `agent mcp add`.

### 2. Event contract

**`integration.connect_requested`** — emitted after `tool.completed` of
`xnobrain_integration_connect` when the result validates (helper
`_integration_connect_event` in `hermes_support.py`, mirroring
`_todo_updated_event`; malformed or failed results emit nothing):

```json
{
  "event": "integration.connect_requested",
  "run_id": "…",
  "timestamp": 1793950000.0,
  "tool_call_id": "call_…",
  "request": {
    "id": "6f1c…",
    "kind": "catalog_mcp",
    "agent_id": "big-brother",
    "display_name": "Notion",
    "auth_kind": "oauth",
    "catalog_id": "notion",
    "recipe_revision": 2,
    "reason": "You asked to connect Notion."
  }
}
```

The same JSON sits in the stored tool message, so history replay rebuilds the
card without new persistence. The durable-run relay in
`services/conversation_runs.py` forwards it unchanged (verify in tests).

**`run.suggestions`** — see section 4.

The UI treats every field as untrusted: it re-reads the catalog entry and
inventory by id and never uses event text for URLs or auth decisions.

### 3. UI connect card

- `src/types.ts`: `ChatConnectRequest`; `RunTimelineItem` gains
  `{ kind: 'connect'; request: ChatConnectRequest }`; `ChatRun` gains
  `suggestions?: string[]`.
- `src/chat/runEvents.ts`: reduce `integration.connect_requested` into a
  timeline item (dedupe by `request.id`); in history replay, parse tool
  messages from `xnobrain_integration_connect` the way `todo` is parsed.
- `src/features/assistant-chat/runParts.ts`: skip `connect` items in
  `runMessage` and hide the successful connect tool step (as for `todo`).
- New `src/features/assistant-chat/IntegrationConnectCard.tsx`, rendered by
  `WorkspaceRunSteps` for each `connect` item, so both the floating and full
  Big Brother chat get it:
  - Loads the catalog and `GET /agents-plugins/{agent_id}`; shows
    "unavailable" if the agent is not in the user's agent list or the id is
    not in the catalog / inventory.
  - Buttons by state: **Connect** (OAuth), **Enter API key** (opens the
    existing `ConnectDialog` / `PluginMcpKeyDialog`), **Add** (no auth),
    **Sign in again** (`needs_sign_in`), **Connected** (done).
    `custom_mcp` shows **Open MCP settings** (link only).
  - Errors use the existing `friendlyMcpError`.
  - Nothing typed in the card is ever sent to chat. After success the card
    offers one chip, "Connected {name}. Continue.", sent only on click.
- Extract the connect logic in `PluginsView.tsx` (`installCatalog`,
  `connectCatalog`, plugin-MCP connect at lines 630–660) into
  `src/features/plugins/useMcpConnect.ts`; `PluginsView` and the card both use
  it. Export `ConnectDialog` and `PluginMcpKeyDialog`.
- OAuth return: the saved flow gets an optional `return_to` (relative path:
  starts with `/`, not `//`, no scheme). `McpOAuthCallbackView` / `App.tsx`
  return there on success, else to `/plugins` as today. The card sets it to the
  current conversation URL.
- i18n: `chat.connect.*` and `chat.suggestions.*` in `src/locales/en.json`,
  `vi.json`, `zh.json`.

### 4. Suggested next message chips

Source: one small side model call in Runtime, after the answer is final. Same
model routing as the auto-title; Big Brother interactive runs only.

- In `conversation_stream.py`, after `run.completed` and before `done`, await
  `_suggest_next_messages(user_message, output, model)` with a 5 s timeout.
  On success emit:

  ```json
  { "event": "run.suggestions", "run_id": "…", "timestamp": 1793950001.0,
    "suggestions": ["Connect Google Drive too", "Show my installed plugins"] }
  ```

- Sanitized: at most 3, each one line and ≤ 80 chars, deduplicated. Timeout,
  error, or empty output emits nothing. Not persisted: chips show only under
  the latest live reply and disappear after reload.
- UI: `SuggestionChips` under the last completed assistant reply when
  `run.suggestions` is set and no run is active; click calls the same submit
  as the composer (`CompactConversation` `onSubmit`, `ChatArea` `submit`).
  Hidden once the user types or a new run starts.

`run.completed` already marks the run done in the UI, so the answer is not
delayed; only `done` waits up to 5 s. Verify the composer unlocks on
`run.completed`.

## Files to touch

Runtime:

- `xnobrain/integrations/integration_connect_tools.py` (new)
- `xnobrain/integrations/conversation_runner.py` (install + bind for Big Brother)
- `xnobrain/integrations/conversation_stream.py` (two events)
- `xnobrain/integrations/hermes_support.py` (`_integration_connect_event`)
- `xnobrain/services/extensions.py` (read-only lookup helper if missing)
- `xnobrain/assets/skills/big-brother-control/SKILL.md`
- `docs/agent-extensions.md` (connect card section)

UI:

- `src/types.ts`, `src/chat/runEvents.ts`, `src/features/assistant-chat/runParts.ts`
- `src/features/assistant-chat/WorkspaceRunSteps.tsx`
- `src/features/assistant-chat/IntegrationConnectCard.tsx` (new)
- `src/features/assistant-chat/SuggestionChips.tsx` (new), wired in
  `CompactConversation.tsx` and `ChatArea.tsx`
- `src/features/plugins/useMcpConnect.ts` (new, extracted), `src/components/PluginsView.tsx`
- `src/components/McpOAuthCallbackView.tsx`, `src/App.tsx` (`return_to`)
- `src/locales/{en,vi,zh}.json`

## Tests

Runtime (`xnobrain/tests/`):

- `test_integration_connect_tools.py` (new): schema rejects extra fields
  (`headers`, `api_key`); unknown agent, unknown catalog id (with candidates),
  unsupported entry, unknown plugin server; success payload built from the
  catalog; tool absent for non-Big-Brother and unsigned runs.
- Stream tests next to the `approval.request` cases in `test_llm_router.py`:
  connect tool result emits `integration.connect_requested`; malformed result
  emits nothing; `run.suggestions` with a stubbed model, none on timeout, none
  for other agents.
- `test_agent_default_skills.py` if it pins skill text.

UI (Vitest):

- `src/chat/runEvents.test.ts`: event → timeline item, dedupe, history replay,
  `run.suggestions`.
- `IntegrationConnectCard.test.tsx` (new): OAuth calls Control connection →
  `install-catalog` → `oauth/start` and saves `return_to`; non-HTTPS
  authorization URL rejected; API-key path never calls chat submit; connected
  state from inventory; unknown agent or catalog id shows unavailable.
- `McpOAuthCallbackView` test: `return_to` honored, `//evil.example` rejected.
- `PluginsView.test.tsx` still passes after the extraction.
- `SuggestionChips` test: click submits the text; hidden while a run is active.

## Out of scope / follow-ups

- Plugin install from chat (git/marketplace operations need digest approval);
  a card for that is a follow-up.
- Prefilling the custom MCP form from `url`.
- Persisting suggestions across reloads.
- Connect cards in normal agent chats (tool is Big Brother only for now).

## Open questions

1. Suggestions add one small model call per Big Brother turn. Ship always-on,
   or behind a Runtime switch (e.g. `RUNTIME_BIG_BROTHER_SUGGESTIONS`)?
2. Should the terminal `agent mcp add --header …` path be blocked for Big
   Brother, or is the skill rule enough for now?

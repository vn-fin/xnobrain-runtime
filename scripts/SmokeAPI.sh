#!/usr/bin/env bash
# <Summary>
# Exercises every safe public read/status API and all provider connection-start
# contracts through Traefik without changing credentials or user data.
# </Summary>
set -euo pipefail

base_url="${XNOBRAIN_SMOKE_URL:-http://127.0.0.1:5173}"
domain="${XNOBRAIN_DOMAIN:-localhost}"
api_prefix="/xnobrain/api/runtime/v1"

request() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local output
  local arguments=(--max-time 20 --fail-with-body --silent --show-error
    -H "Host: $domain" -H 'Content-Type: application/json'
    -X "$method")
  if [[ -n "$body" ]]; then
    arguments+=(--data-raw "$body")
  fi
  output="$(curl "${arguments[@]}" "$base_url$path")"
  jq -e '.success == true' <<<"$output" >/dev/null
  printf '%-6s %s\n' "$method" "$path" >&2
  printf '%s' "$output"
}

request GET "$api_prefix/health" >/dev/null
request GET "$api_prefix/limits" >/dev/null
request GET "$api_prefix/system/deployment" >/dev/null
notifications="$(request GET "$api_prefix/notifications")"
teams="$(request GET "$api_prefix/teams/")"
request GET "$api_prefix/ping" >/dev/null
agents="$(request GET "$api_prefix/agents")"
request GET "$api_prefix/agents-configs/global" >/dev/null
default_skills="$(request GET "$api_prefix/agents-skills")"
crons="$(request GET "$api_prefix/cron/jobs")"
request GET "$api_prefix/sandboxes/info" >/dev/null
request GET "$api_prefix/sandboxes/metrics" >/dev/null
request GET "$api_prefix/sandboxes/stats" >/dev/null
request GET "$api_prefix/sandboxes/health" >/dev/null

providers="$(request GET "$api_prefix/providers")"
for provider in claude codex antigravity openai anthropic gemini deepseek moonshot qwen openai-like; do
  request GET "$api_prefix/providers/$provider/connect" >/dev/null
  request GET "$api_prefix/providers/$provider/models" >/dev/null
  request GET "$api_prefix/providers/$provider/models/auto/reasoning" >/dev/null
  request POST "$api_prefix/providers/$provider/test" >/dev/null
done
for provider in claude codex antigravity; do
  started="$(request POST "$api_prefix/providers/$provider/connect")"
  jq -e '.data.login_url | type == "string" and length > 0' <<<"$started" >/dev/null
done
for provider in openai anthropic gemini deepseek moonshot qwen openai-like; do
  started="$(request POST "$api_prefix/providers/$provider/connect")"
  jq -e '.data.required_client_action == "submit_text"' <<<"$started" >/dev/null
done

agent_id="$(jq -r '.data[0].id // empty' <<<"$agents")"
if [[ -n "$agent_id" ]]; then
  request GET "$api_prefix/agents/$agent_id/detail" >/dev/null
  request POST "$api_prefix/agents/$agent_id/test" >/dev/null
  request GET "$api_prefix/agents/$agent_id/runtime" >/dev/null
  request GET "$api_prefix/agents/$agent_id/memory" >/dev/null
  request GET "$api_prefix/agents/$agent_id/snapshots" >/dev/null
  request GET "$api_prefix/agents-skills/$agent_id" >/dev/null
  workspace="$(request GET "$api_prefix/agents-workspaces/$agent_id")"
  workspace_file="$(jq -r '.data.entries[]? | select(.type == "file") | .path' <<<"$workspace" | head -1)"
  if [[ -n "$workspace_file" ]]; then
    request POST "$api_prefix/agents-workspaces/$agent_id/read" "$(jq -cn --arg path "$workspace_file" '{path:$path}')" >/dev/null
  fi
  sessions="$(request GET "$api_prefix/sessions?agent=$agent_id")"
  conversation_id="$(jq -r '.data.conversations[0].id // empty' <<<"$sessions")"
  if [[ -n "$conversation_id" ]]; then
    request GET "$api_prefix/sessions/$conversation_id/detail?agent=$agent_id" >/dev/null
    request GET "$api_prefix/sessions/$conversation_id/messages?agent=$agent_id" >/dev/null
    request GET "$api_prefix/sessions/$conversation_id/usage?agent=$agent_id" >/dev/null
  fi
fi

team_id="$(jq -r '.data[0].id // empty' <<<"$teams")"
if [[ -n "$team_id" ]]; then
  request GET "$api_prefix/teams/$team_id" >/dev/null
fi

# Mutation/status companions are covered by focused tests. These collections
# are still decoded here so schema regressions cannot masquerade as HTTP 200s.
jq -e '.data | type == "array"' <<<"$crons" >/dev/null
jq -e '.data | type == "array"' <<<"$notifications" >/dev/null
jq -e '.data | type == "array"' <<<"$default_skills" >/dev/null

jq -e '
  ["claude", "codex", "antigravity", "openai", "anthropic", "gemini",
   "deepseek", "moonshot", "qwen", "openai-like"] as $required
  | ($required - [.data[].id]) | length == 0
' <<<"$providers" >/dev/null
printf 'Public API smoke checks passed.\n'

#!/usr/bin/env bash
# <Summary>
# Exercises every safe public read/status API and all provider connection-start
# contracts through Traefik without changing credentials or user data.
# </Summary>
set -euo pipefail

base_url="${OPEN_LUMORA_SMOKE_URL:-http://127.0.0.1}"
domain="${OPEN_LUMORA_DOMAIN:-localhost}"

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

request GET /api/v1/health >/dev/null
request GET /api/v1/limits >/dev/null
request GET /api/v1/system/deployment >/dev/null
request GET '/api/v1/dashboard/overview?window=24h' >/dev/null
request GET '/api/v1/dashboard/dependencies?window=24h' >/dev/null
notifications="$(request GET /api/v1/notifications)"
request GET /api/v1/device >/dev/null
teams="$(request GET /api/v1/teams/)"
request GET /agent-gateway/v1/ping >/dev/null
agents="$(request GET /agent-gateway/v1/agents)"
request GET /agent-gateway/v1/agents-configs/global >/dev/null
crons="$(request GET /agent-gateway/v1/cron/jobs)"
request GET /sandboxes/v1/me/sandboxes/info >/dev/null
request GET /sandboxes/v1/me/sandboxes/metrics >/dev/null
request GET /sandboxes/v1/me/sandboxes/stats >/dev/null
request GET /sandboxes/v1/me/sandboxes/health >/dev/null

providers="$(request GET /agent-gateway/v1/providers)"
for provider in claude codex antigravity openai anthropic gemini; do
  request GET "/agent-gateway/v1/providers/$provider/connect" >/dev/null
  request GET "/agent-gateway/v1/providers/$provider/models" >/dev/null
  request GET "/agent-gateway/v1/providers/$provider/models/auto/reasoning" >/dev/null
  request POST "/agent-gateway/v1/providers/$provider/test" >/dev/null
done
for provider in claude codex antigravity; do
  started="$(request POST "/agent-gateway/v1/providers/$provider/connect")"
  jq -e '.data.login_url | type == "string" and length > 0' <<<"$started" >/dev/null
done
for provider in openai anthropic gemini; do
  started="$(request POST "/agent-gateway/v1/providers/$provider/connect")"
  jq -e '.data.required_client_action == "submit_text"' <<<"$started" >/dev/null
done

agent_id="$(jq -r '.data[0].id // empty' <<<"$agents")"
if [[ -n "$agent_id" ]]; then
  request GET "/agent-gateway/v1/agents/$agent_id/detail" >/dev/null
  request POST "/agent-gateway/v1/agents/$agent_id/test" >/dev/null
  request GET "/agent-gateway/v1/agents/$agent_id/runtime" >/dev/null
  request GET "/agent-gateway/v1/agents/$agent_id/memory" >/dev/null
  request GET "/agent-gateway/v1/agents/$agent_id/snapshots" >/dev/null
  request GET "/agent-gateway/v1/agents-skills/$agent_id" >/dev/null
  workspace="$(request GET "/agent-gateway/v1/agents-workspaces/$agent_id")"
  workspace_file="$(jq -r '.data.entries[]? | select(.type == "file") | .path' <<<"$workspace" | head -1)"
  if [[ -n "$workspace_file" ]]; then
    request POST "/agent-gateway/v1/agents-workspaces/$agent_id/read" "$(jq -cn --arg path "$workspace_file" '{path:$path}')" >/dev/null
  fi
  conversations="$(request GET "/conversations/v1/conversations?agent=$agent_id")"
  conversation_id="$(jq -r '.data.conversations[0].id // empty' <<<"$conversations")"
  if [[ -n "$conversation_id" ]]; then
    request GET "/conversations/v1/conversations/$conversation_id/detail?agent=$agent_id" >/dev/null
    request GET "/conversations/v1/conversations/$conversation_id/messages?agent=$agent_id" >/dev/null
    request GET "/conversations/v1/conversations/$conversation_id/usage?agent=$agent_id" >/dev/null
  fi
fi

team_id="$(jq -r '.data[0].id // empty' <<<"$teams")"
if [[ -n "$team_id" ]]; then
  request GET "/api/v1/teams/$team_id" >/dev/null
fi

# Mutation/status companions are covered by focused tests. These collections
# are still decoded here so schema regressions cannot masquerade as HTTP 200s.
jq -e '.data | type == "array"' <<<"$crons" >/dev/null
jq -e '.data | type == "array"' <<<"$notifications" >/dev/null

jq -e '.data | length == 6' <<<"$providers" >/dev/null
printf 'Public API smoke checks passed.\n'

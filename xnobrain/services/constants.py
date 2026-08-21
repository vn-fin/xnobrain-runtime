"""Shared immutable service policy constants."""

import re

OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS = {
    "xai": {"display_name": "xAI", "description": "Grok models through xAI's OpenAI-compatible API.", "base_url": "https://api.x.ai/v1"},
    "openrouter": {"display_name": "OpenRouter", "description": "Multiple model providers through OpenRouter's OpenAI-compatible API.", "base_url": "https://openrouter.ai/api/v1"},
    "groq": {"display_name": "Groq", "description": "Fast inference through Groq's OpenAI-compatible API.", "base_url": "https://api.groq.com/openai/v1"},
    "deepseek": {"display_name": "DeepSeek", "description": "DeepSeek models through its OpenAI-compatible API.", "base_url": "https://api.deepseek.com/v1"},
    "moonshot": {"display_name": "Moonshot AI", "description": "Moonshot and Kimi models through its OpenAI-compatible API.", "base_url": "https://api.moonshot.cn/v1"},
    "qwen": {"display_name": "Qwen", "description": "Qwen models through Alibaba Cloud's OpenAI-compatible API.", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "openai-like": {"display_name": "OpenAI-compatible", "description": "Connect any OpenAI-compatible endpoint with a base URL and API key.", "base_url": ""},
}
SUBSCRIPTION_PROVIDER_DEFINITIONS = {
    "codex": {"display_name": "OpenAI Codex", "description": "Use an existing ChatGPT or Codex subscription."},
    "claude": {"display_name": "Claude Code", "description": "Use an existing Claude Code subscription."},
    "github": {"display_name": "GitHub Copilot", "description": "Use an existing GitHub Copilot subscription."},
    "cursor": {"display_name": "Cursor", "description": "Use an existing Cursor subscription."},
    "grok-cli": {"display_name": "Grok Build", "description": "Use an existing xAI Grok Build subscription."},
    "xai-oauth": {"display_name": "xAI Grok", "description": "Use an existing SuperGrok or X Premium+ subscription."},
    "kimi-coding": {"display_name": "Kimi Code", "description": "Use an existing Kimi Coding Plan subscription."},
    "cline": {"display_name": "Cline", "description": "Use an existing Cline account."},
    "kilocode": {"display_name": "Kilo Code", "description": "Use an existing Kilo Code account."},
    "kiro": {"display_name": "Kiro", "description": "Use an existing Kiro or AWS Builder ID account."},
    "amazon-q": {"display_name": "Amazon Q Developer", "description": "Use an existing Amazon Q Developer or AWS Builder ID account."},
    "clinepass": {"display_name": "ClinePass", "description": "Use an existing ClinePass subscription."},
    "antigravity": {"display_name": "Google Antigravity", "description": "Use an existing Google Antigravity account."},
}
PROVIDER_DEFINITIONS = {
    **SUBSCRIPTION_PROVIDER_DEFINITIONS,
    "opencode-go": {"display_name": "OpenCode Go", "description": "OpenCode Go models from your coding subscription.", "base_url": ""},
    "opencode": {"display_name": "OpenCode Zen", "description": "OpenCode Zen models using your Zen API key.", "base_url": ""},
    **OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS,
}
SUPPORTED_PROVIDERS = (
    "codex", "claude", "github", "cursor", "grok-cli", "xai-oauth",
    "kimi-coding", "cline", "kilocode", "kiro", "amazon-q", "clinepass",
    "antigravity",
    "openai", "anthropic", "gemini",
    "opencode-go", "opencode", *OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS,
)
DEVICE_CODE_PROVIDERS = frozenset({
    "github", "grok-cli", "kimi-coding", "kilocode", "kiro", "amazon-q",
})
IMPORT_TOKEN_PROVIDERS = frozenset({"cursor"})
API_KEY_PROVIDERS = frozenset({"openai", "anthropic", "gemini", "opencode-go", "opencode", *OPENAI_COMPATIBLE_PROVIDER_DEFINITIONS})
NO_AUTH_PROVIDERS = frozenset()
FREE_MODEL_PROVIDERS = frozenset()
SAFE_TOOLSETS = frozenset({
    "browser", "code_execution", "computer_use", "context_engine", "file",
    "image_gen", "session_search", "skills", "terminal", "todo", "tts",
    "video", "video_gen", "vision", "web", "x_search",
})
DEFAULT_TEAM_COORDINATOR_PROMPT = "Plan the workflow and give every stage clear, actionable execution guidance."
DEFAULT_TEAM_SYNTHESIS_PROMPT = "Synthesize all completed stage outputs into one clear, accurate final answer."
EVERY_SCHEDULE = re.compile(r"^@every\s+(\d+)([smhd])$")

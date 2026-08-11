"""Grouped ConversationTitles behavior for 9router."""

from .nine_router_support import (
    Mapping,
    NINE_ROUTER_DEFAULT_MODEL,
)


class ConversationTitlesMixin:
    async def generate_conversation_title(self, message: str, model: str) -> str:
        """Generate one small title through the same local model router."""
        payload = await self._request(
            "POST",
            "/v1/chat/completions",
            {
                "model": model or NINE_ROUTER_DEFAULT_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Write a concise title for this conversation. Use 3 to 7 words and "
                            "at most 48 characters. Return only the title, without quotes, a "
                            "label, or ending punctuation."
                        ),
                    },
                    {"role": "user", "content": str(message)[:4000]},
                ],
                "max_tokens": 48,
                "stream": False,
            },
        )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            return ""
        response = choices[0].get("message")
        if not isinstance(response, Mapping):
            return ""
        content = response.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, Mapping)
            )
        return ""

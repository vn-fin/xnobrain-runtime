"""Public conversation failure text; never return raw provider payloads."""

import re
from collections.abc import Mapping

from .public_text import public_error_message


def conversation_failure(value: object) -> str:
    for _ in range(5):
        if not isinstance(value, Mapping):
            break
        value = value.get("message") or value.get("error") or value.get("detail")
    if not isinstance(value, str):
        return "Chat run failed."
    text = public_error_message(value)
    if re.search(
        r"https?://|bearer\s|sk-[\w-]+|(?:api[_ -]?key|token|password|secret)\s*[:=]|"
        r"traceback|request[_ ]body|response[_ ]body|prompt\s*[:=]",
        text,
        re.I,
    ):
        return "Chat run failed. Review the provider connection or choose another model."
    return text[:500]

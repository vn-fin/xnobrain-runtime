"""Skills eligible for task execution, independent of direct-chat defaults."""

from collections.abc import Iterable

from .defaults import BIG_BROTHER_SKILL_ID


def task_skill_ids(skills: Iterable[str] | None) -> list[str]:
    """Keep task skills in order; the platform coordinator is chat-only.

    Apply this again at worker launch for tasks saved by older clients.
    Other unknown or disabled skills still pass through normal validation.
    """
    return list(
        dict.fromkeys(
            skill
            for value in skills or []
            if (skill := str(value or "").strip()) and skill != BIG_BROTHER_SKILL_ID
        )
    )

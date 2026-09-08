"""Bounded calendar-cron previews with explicit wall-clock DST policy."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def preview_calendar_runs(
    expression: str, zone_name: str, after: datetime, *, count: int = 5
) -> list[dict[str, str]]:
    """Skip nonexistent wall times and select the earlier instant of a fold.

    This evaluates only five-field calendar cron. Interval and absolute one-shot
    schedules have different semantics and must not pass through this adapter.
    """
    from croniter import CroniterBadCronError, CroniterBadDateError, croniter

    if after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("preview cutoff must be timezone-aware")
    if not 1 <= count <= 20:
        raise ValueError("preview count must be between 1 and 20")
    if len(expression) > 256 or len(expression.split()) != 5:
        raise ValueError("five-field calendar cron is required")
    if any(
        re.search(r"(?:^|,)[rh](?:$|[,(/])", field, re.IGNORECASE)
        for field in expression.split()
    ):
        raise ValueError("random or hashed calendar fields are unsupported")
    zone = ZoneInfo(zone_name)
    try:
        cutoff = after.astimezone(timezone.utc)
        wall = cutoff.astimezone(zone).replace(tzinfo=None)
    except (OverflowError, ValueError) as error:
        raise ValueError("preview cutoff is outside the supported calendar range") from error
    try:
        iterator = croniter(expression, wall, max_years_between_matches=8)
    except (CroniterBadCronError, ValueError) as error:
        raise ValueError("invalid calendar cron expression") from error
    result = []
    for _ in range(10_000):
        try:
            candidate = iterator.get_next(datetime)
        except (CroniterBadDateError, OverflowError, ValueError) as error:
            raise ValueError("no occurrence within the supported preview horizon") from error
        instants = set()
        for fold in (0, 1):
            try:
                instant = candidate.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                round_trip = instant.astimezone(zone).replace(tzinfo=None)
            except (OverflowError, ValueError) as error:
                raise ValueError("occurrence is outside the supported calendar range") from error
            if round_trip == candidate:
                instants.add(instant)
        if not instants:
            continue
        instant = min(instants)
        if instant <= cutoff:
            continue
        result.append({
            "utc": instant.isoformat().replace("+00:00", "Z"),
            "local": instant.astimezone(zone).isoformat(),
            "timezone": zone_name,
        })
        if len(result) == count:
            return result
    raise ValueError("no bounded schedule preview available")

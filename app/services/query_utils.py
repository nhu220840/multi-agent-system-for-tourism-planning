from __future__ import annotations

import re


_DAY_PATTERN = re.compile(r"(\d+)\s*ng[aà]y", re.IGNORECASE)


def extract_trip_days(
    query: str,
    *,
    default: int | None = 1,
    max_days: int = 7,
) -> int | None:
    match = _DAY_PATTERN.search(query or "")
    if not match:
        return default
    try:
        days = int(match.group(1))
    except Exception:
        return default
    return max(1, min(days, max_days))

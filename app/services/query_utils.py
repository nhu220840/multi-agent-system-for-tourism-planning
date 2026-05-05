from __future__ import annotations

import re


_DAY_PATTERN = re.compile(r"(\d+)\s*ng[aà]y", re.IGNORECASE)


def extract_trip_days(
    query: str,
    *,
    default: int | None = 1,
    max_days: int = 7,
) -> int | None:
    matches = list(_DAY_PATTERN.finditer(query or ""))
    if not matches:
        return default
    try:
        days = int(matches[-1].group(1))
    except Exception:
        return default
    return max(1, min(days, max_days))

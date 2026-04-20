from __future__ import annotations

import re
from typing import Any

from app.agents.intake_agent import evaluate_intake
from app.services.place_metadata import fold_text
from app.services.query_utils import extract_trip_days

_BEACH_MARKERS = ("bien", "bai bien", "beach", "my khe", "an bang", "son tra", "cu lao")
_CULTURE_MARKERS = (
    "bao tang",
    "museum",
    "di tich",
    "heritage",
    "historic",
    "van hoa",
    "thanh dia",
    "chua",
    "dinh",
    "nha tho",
)


def validate_itinerary_plan(query: str, plan: str, places: list[dict[str, Any]]) -> dict[str, Any]:
    raw_plan = plan or ""
    normalized_plan = fold_text(raw_plan)
    raw_plan_lower = raw_plan.lower()
    total_days = extract_trip_days(query) or 1
    interests = _extract_interest_tags(query)
    day_count = len(re.findall(r"^\s*ngay\s+\d+", raw_plan_lower, flags=re.MULTILINE))
    meal_lines = [
        line
        for line in raw_plan.splitlines()
        if fold_text(line).startswith(("• sang:", "• trua:", "• toi:", "sang:", "trua:", "toi:"))
    ]
    self_service_count = sum(1 for line in meal_lines if "tu tuc" in fold_text(line))
    real_meal_count = sum(1 for line in meal_lines if "tu tuc" not in fold_text(line))
    long_legs = [float(match) for match in re.findall(r"~(\d+(?:\.\d+)?)\s*km", raw_plan_lower)]
    has_morning = len([line for line in meal_lines if "sang:" in fold_text(line)]) >= total_days
    has_afternoon = raw_plan_lower.count("• chieu:") >= total_days or raw_plan_lower.count("chieu:") >= total_days
    has_beach_signal = any(marker in normalized_plan for marker in _BEACH_MARKERS)
    has_culture_signal = any(marker in normalized_plan for marker in _CULTURE_MARKERS)
    has_real_food_signal = real_meal_count > 0

    issues: list[str] = []
    if day_count and day_count != total_days:
        issues.append("daily_count_mismatch")
    if not has_morning or not has_afternoon:
        issues.append("missing_daily_structure")
    if "bien" in interests and not has_beach_signal:
        issues.append("missing_beach_alignment")
    if any(tag in interests for tag in ("di_tich_lich_su", "bao_tang", "tam_linh")) and not has_culture_signal:
        issues.append("missing_culture_alignment")
    if "am_thuc" in interests and not has_real_food_signal:
        issues.append("missing_food_alignment")
    if self_service_count > max(1, total_days):
        issues.append("too_many_self_service_meals")
    if len([value for value in long_legs if value > 18.0]) > max(1, total_days - 1):
        issues.append("too_many_long_legs")
    if long_legs and max(long_legs) > 25.0:
        issues.append("extreme_leg_distance")
    if not places:
        issues.append("empty_place_pool")

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "metrics": {
            "days_expected": total_days,
            "days_rendered": day_count,
            "self_service_count": self_service_count,
            "real_meal_count": real_meal_count,
            "long_leg_count_gt_18km": len([value for value in long_legs if value > 18.0]),
            "max_leg_km": round(max(long_legs), 1) if long_legs else 0.0,
        },
    }


def build_retry_query(query: str, validation: dict[str, Any]) -> str:
    issues = {str(item).strip().lower() for item in validation.get("issues", [])}
    additions: list[str] = []
    if "missing_beach_alignment" in issues:
        additions.append("uu tien manh hon bai bien bo bien my khe son tra")
    if "missing_culture_alignment" in issues:
        additions.append("uu tien bao tang di tich van hoa tam linh")
    if "missing_food_alignment" in issues or "too_many_self_service_meals" in issues:
        additions.append("uu tien nha hang gan diem tham quan han che tu tuc")
    if "too_many_long_legs" in issues or "extreme_leg_distance" in issues:
        additions.append("gom diem theo cung khu vuc tranh chang xa tren 15km")
    suffix = " ".join(additions).strip()
    if not suffix:
        return query
    return f"{query} {suffix}".strip()


def should_retry_itinerary(validation: dict[str, Any], retry_attempted: bool) -> bool:
    if retry_attempted or validation.get("passed"):
        return False
    retryable = {
        "missing_beach_alignment",
        "missing_culture_alignment",
        "missing_food_alignment",
        "too_many_self_service_meals",
        "too_many_long_legs",
        "extreme_leg_distance",
    }
    return any(issue in retryable for issue in validation.get("issues", []))

def _extract_interest_tags(query: str) -> set[str]:
    intake = evaluate_intake(query or "")
    raw = str(intake.collected.get("interests") or "")
    return {token.strip().lower() for token in raw.split(",") if token.strip()}

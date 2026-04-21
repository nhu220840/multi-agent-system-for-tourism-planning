from __future__ import annotations

from dataclasses import dataclass

from app.services.place_metadata import fold_text as _fold
from app.services.query_utils import extract_trip_days


@dataclass
class IntakeResult:
    is_complete: bool
    collected: dict[str, str]
    missing_fields: list[str]
    follow_up_questions: list[str]


def evaluate_intake(message: str) -> IntakeResult:
    text = (message or "").strip()
    lowered = text.lower()
    folded = _fold(lowered)

    days = _extract_days(lowered)
    interests = _extract_interests(folded)
    destination = _extract_destination(folded)

    collected = {
        "destination": destination,
        "days": days or "",
        "interests": interests or "",
    }

    required = ["destination", "days", "interests"]
    missing = [k for k in required if not collected[k]]
    questions = [_question_for_field(k) for k in missing]
    return IntakeResult(
        is_complete=len(missing) == 0,
        collected=collected,
        missing_fields=missing,
        follow_up_questions=questions,
    )


def _extract_destination(folded: str) -> str:
    """Da Nang / Hoi An / Quang Nam — mac dinh hub Da Nang."""
    mentions: list[tuple[int, str]] = []
    for label, tokens in (
        ("Da Nang", ("da nang", "danang")),
        ("Hoi An", ("hoi an",)),
        ("Quang Nam", ("quang nam",)),
    ):
        positions = [folded.find(token) for token in tokens if token in folded]
        if positions:
            mentions.append((min(positions), label))
    if mentions:
        ordered: list[str] = []
        seen: set[str] = set()
        for _, label in sorted(mentions):
            if label in seen:
                continue
            seen.add(label)
            ordered.append(label)
        if len(ordered) == 1:
            return ordered[0]
        return " / ".join(ordered)
    return "Da Nang"


def _extract_days(text: str) -> str:
    days = extract_trip_days(text, default=None)
    if days is None:
        return ""
    return str(days)


def _extract_interests(folded: str) -> str:
    interest_aliases: dict[str, list[str]] = {
        "am_thuc": ["am thuc", "an uong", "dac san", "nha hang", "food", "anuong"],
        "bien": ["bien", "tam bien", "bo bien", "beach"],
        "bao_tang": ["bao tang", "museum", "trien lam"],
        "di_tich_lich_su": ["di tich", "lich su", "van hoa", "co kinh", "historic", "heritage"],
        "tam_linh": ["tam linh", "chua", "den", "pagoda", "linh ung"],
        "mua_sam": ["mua sam", "shopping", "cho dem", "mall", "mua"],
        "cafe_chill": ["cafe", "ca phe", "chill", "song ao"],
        "thien_nhien": ["thien nhien", "nui", "rung", "trekking", "leo nui", "doi"],
        "giai_tri_dem": ["bar", "pub", "nightlife", "di dem", "dem"],
        "gia_dinh_tre_em": ["gia dinh", "tre em", "be", "kid-friendly"],
    }
    picked: list[str] = []
    for canonical, aliases in interest_aliases.items():
        if any(a in folded for a in aliases):
            picked.append(canonical)
    return ", ".join(picked) if picked else ""


def _question_for_field(field: str) -> str:
    prompts = {
        "destination": "Ban tap trung khu vuc nao? (Da Nang / Hoi An / Quang Nam — mac dinh Da Nang)",
        "days": "Ban di may ngay?",
        "interests": (
            "Ban uu tien trai nghiem gi? (VD: am thuc, di tich lich su, bao tang, bien, "
            "mua sam, cafe chill, tam linh, thien nhien...)"
        ),
    }
    return prompts[field]

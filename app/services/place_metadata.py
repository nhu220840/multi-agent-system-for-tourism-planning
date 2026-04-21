from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any


_AREA_ORDER = (
    "hai chau",
    "son tra",
    "ngu hanh son",
    "thanh khe",
    "cam le",
    "lien chieu",
    "hoa vang",
    "hoi an",
    "tam ky",
    "dien ban",
    "duy xuyen",
    "dai loc",
    "thang binh",
    "tien phuoc",
    "nui thanh",
)

_AREA_TO_CITY_KEY = {
    "hai chau": "da_nang",
    "son tra": "da_nang",
    "ngu hanh son": "da_nang",
    "thanh khe": "da_nang",
    "cam le": "da_nang",
    "lien chieu": "da_nang",
    "hoa vang": "da_nang",
    "hoi an": "hoi_an",
    "tam ky": "quang_nam",
    "dien ban": "quang_nam",
    "duy xuyen": "quang_nam",
    "dai loc": "quang_nam",
    "thang binh": "quang_nam",
    "tien phuoc": "quang_nam",
    "nui thanh": "quang_nam",
}

_DENSE_AREAS = {"hai chau", "son tra", "ngu hanh son", "thanh khe", "hoi an"}
_MEDIUM_AREAS = {"cam le", "lien chieu", "dien ban"}

_INTENT_ORDER = (
    "am_thuc",
    "bien",
    "bao_tang",
    "di_tich",
    "tam_linh",
    "mua_sam",
    "cafe",
    "thien_nhien",
    "dem",
    "gia_dinh",
)


def fold_text(text: str) -> str:
    base = (text or "").replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", base)
    no_marks = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", no_marks).strip().lower()


def normalize_address_text(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip(" ,")
    if not value:
        return ""

    parts = [part.strip(" ,") for part in value.split(",")]
    parts = [part for part in parts if part]
    normalized = ", ".join(parts)
    normalized = re.sub(r"\(\s*,\s*", "(", normalized)
    normalized = re.sub(r",\s*,+", ", ", normalized)
    normalized = re.sub(r"\(\s*\)", "", normalized)
    return normalized.strip(" ,")


def city_key_from_text(text: str) -> str:
    folded = fold_text(text)
    if "hoi an" in folded:
        return "hoi_an"
    if "da nang" in folded or "danang" in folded:
        return "da_nang"
    if "quang nam" in folded:
        return "quang_nam"
    for area_key, city_key in _AREA_TO_CITY_KEY.items():
        if area_key in folded:
            return city_key
    return ""


def place_city_key(place: dict[str, Any]) -> str:
    explicit = str(place.get("city_key") or "").strip().lower()
    if explicit:
        return explicit
    primary_area = str(place.get("primary_area_key") or "").strip().lower()
    if primary_area in _AREA_TO_CITY_KEY:
        return _AREA_TO_CITY_KEY[primary_area]
    for area_key in extract_admin_area_keys(place):
        if area_key in _AREA_TO_CITY_KEY:
            return _AREA_TO_CITY_KEY[area_key]
    blob = " ".join(
        [
            str(place.get("city") or ""),
            str(place.get("address") or ""),
            str(place.get("district") or ""),
        ]
    )
    return city_key_from_text(blob)


def extract_admin_area_keys(place: dict[str, Any]) -> list[str]:
    existing = place.get("admin_area_keys")
    if isinstance(existing, list):
        keys = [str(item).strip().lower() for item in existing if str(item).strip()]
        if keys:
            return _sorted_area_keys(keys)

    blob = " ".join(
        [
            str(place.get("district") or ""),
            str(place.get("ward") or ""),
            str(place.get("address") or ""),
            str(place.get("city") or ""),
        ]
    )
    folded = fold_text(blob)
    keys = [area for area in _AREA_ORDER if area in folded]
    return _sorted_area_keys(keys)


def primary_admin_area_key(place: dict[str, Any]) -> str:
    keys = extract_admin_area_keys(place)
    if not keys:
        return ""
    return keys[0]


def enrich_place_record(place: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(place)
    for field in ("address", "formatted_address", "map_formatted_address", "google_formatted_address"):
        if field in enriched:
            enriched[field] = normalize_address_text(str(enriched.get(field) or ""))
    area_keys = extract_admin_area_keys(enriched)
    planner_role = infer_planner_role(enriched)
    intent_tags = infer_intent_tags(enriched)
    enriched["city_key"] = place_city_key(enriched)
    enriched["admin_area_keys"] = area_keys
    enriched["primary_area_key"] = area_keys[0] if area_keys else ""
    enriched["planner_role"] = planner_role
    enriched["intent_tags"] = intent_tags
    enriched["density_bucket"] = infer_density_bucket(enriched)
    enriched["verification_status"] = infer_verification_status(enriched)
    enriched["place_id"] = build_place_id(enriched)
    return enriched


def enrich_places(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_place_record(place) for place in places]


def infer_planner_role(place: dict[str, Any]) -> str:
    category = str(place.get("category") or "").strip().lower()
    source = fold_text(str(place.get("source") or ""))
    blob = _place_blob(place)

    if category == "restaurant":
        return "restaurant"
    if category == "accommodation":
        return "stay"
    if category == "entertainment":
        return "entertainment"
    if "vcgt" in source:
        return "entertainment"
    if category in {"museum", "viewpoint", "beach"}:
        return "tourism"

    entertainment_markers = (
        "cong vien",
        "giai tri",
        "vui choi",
        "asia park",
        "sun world",
        "helio",
        "cho dem",
        "marina",
        "water park",
        "lotte cinema",
    )
    tourism_markers = (
        "bao tang",
        "museum",
        "di tich",
        "heritage",
        "historic",
        "lang co",
        "thanh dia",
        "nha tho",
        "dinh",
        "chua",
        "pagoda",
        "ban dao",
        "thac",
        "nui",
    )

    entertainment_hits = sum(1 for marker in entertainment_markers if marker in blob)
    tourism_hits = sum(1 for marker in tourism_markers if marker in blob)
    if entertainment_hits > tourism_hits:
        return "entertainment"
    return "tourism"


def infer_intent_tags(place: dict[str, Any]) -> list[str]:
    category = str(place.get("category") or "").strip().lower()
    blob = _place_blob(place)
    short_blob = fold_text(
        " ".join(
            [
                str(place.get("name") or ""),
                str(place.get("category") or ""),
                str(place.get("destination_type") or ""),
                str(place.get("address") or ""),
                str(place.get("description") or "")[:260],
            ]
        )
    )
    tags: set[str] = set()

    if category == "restaurant":
        tags.add("am_thuc")
    if any(token in short_blob for token in ("bai bien", "bo bien", "beach", "my khe", "an bang", "cu lao", "vo nguyen giap", "hoang sa")):
        tags.update({"bien", "thien_nhien"})
    if any(token in short_blob for token in ("bao tang", "museum", "trung bay", "trien lam")):
        tags.update({"bao_tang", "di_tich"})
    if any(token in short_blob for token in ("di tich", "heritage", "historic", "unesco", "lang co", "thanh dia", "tuong dai", "nha trung bay")):
        tags.add("di_tich")
    if any(token in short_blob for token in ("pagoda", "linh ung", "nha tho", "thanh duong", "tu vien", "phat giao", "den tho")):
        tags.update({"tam_linh", "di_tich"})
    if any(token in short_blob for token in ("mua sam", "shopping", "cho dem", "market", "night market")):
        tags.add("mua_sam")
    if any(token in short_blob for token in ("cafe", "ca phe", "coffee")):
        tags.add("cafe")
    if any(token in short_blob for token in ("sinh thai", "thien nhien", "nui", "rung", "suoi", "thac", "dao", "ban dao", "cu lao")):
        tags.add("thien_nhien")
    if any(token in short_blob for token in ("bar", "pub", "nightlife", "skybar", "beer")):
        tags.add("dem")
    if any(token in short_blob for token in ("gia dinh", "tre em", "kid", "thieu nhi")):
        tags.add("gia_dinh")

    ordered = [tag for tag in _INTENT_ORDER if tag in tags]
    return ordered


def infer_density_bucket(place: dict[str, Any]) -> str:
    area_key = primary_admin_area_key(place)
    if area_key in _DENSE_AREAS:
        return "dense"
    if area_key in _MEDIUM_AREAS:
        return "medium"
    if area_key:
        return "sparse"
    return "unknown"


def infer_verification_status(place: dict[str, Any]) -> str:
    source = str(place.get("source") or "").strip().lower()
    if not source:
        return "unknown"
    if source == "csdl_vietnamtourism":
        return "official_source"
    if source.startswith("local-processed:") or source.startswith("local-json:"):
        return "catalog_source"
    if source.startswith("nominatim") or source.startswith("overpass"):
        return "map_source"
    if source.startswith("fallback"):
        return "placeholder"
    return "catalog_source"


def build_place_id(place: dict[str, Any]) -> str:
    if str(place.get("place_id") or "").strip():
        return str(place.get("place_id")).strip()
    payload = {
        "name": str(place.get("name") or "").strip(),
        "address": str(place.get("address") or "").strip(),
        "category": str(place.get("category") or "").strip().lower(),
        "source": str(place.get("source") or "").strip().lower(),
    }
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return digest


def _place_blob(place: dict[str, Any]) -> str:
    return fold_text(
        " ".join(
            [
                str(place.get("name") or ""),
                str(place.get("category") or ""),
                str(place.get("description") or ""),
                str(place.get("detail_content") or ""),
                str(place.get("list_snippet") or ""),
                str(place.get("destination_type") or ""),
                str(place.get("address") or ""),
                str(place.get("district") or ""),
                str(place.get("city") or ""),
            ]
        )
    )


def _sorted_area_keys(keys: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for key in keys:
        normalized = str(key).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return sorted(
        deduped,
        key=lambda item: (_AREA_ORDER.index(item) if item in _AREA_ORDER else len(_AREA_ORDER), item),
    )

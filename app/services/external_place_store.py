from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.place_metadata import enrich_place_record, is_user_facing_place_name

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "cache"
EXTERNAL_PLACES_JSON = CACHE_DIR / "external_places.json"
EXTERNAL_PLACES_JSONL = CACHE_DIR / "external_places.jsonl"


def load_external_places() -> list[dict[str, Any]]:
    if not EXTERNAL_PLACES_JSON.exists():
        return []
    try:
        payload = json.loads(EXTERNAL_PLACES_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [
        item for item in payload
        if isinstance(item, dict) and is_user_facing_place_name(str(item.get("name") or ""))
    ]


def cache_external_places(places: list[dict[str, Any]]) -> int:
    normalized = [_normalize_external_place(place) for place in places if isinstance(place, dict)]
    normalized = [place for place in normalized if place]
    if not normalized:
        return 0

    existing = load_external_places()
    merged: dict[str, dict[str, Any]] = {}
    for item in existing:
        place_id = str(item.get("place_id") or "").strip()
        if place_id:
            merged[place_id] = dict(item)

    added = 0
    for item in normalized:
        place_id = str(item.get("place_id") or "").strip()
        if not place_id:
            continue
        current = merged.get(place_id)
        if current is None:
            merged[place_id] = item
            added += 1
            continue
        updated = _merge_records(current, item)
        if updated != current:
            merged[place_id] = updated

    rows = sorted(
        merged.values(),
        key=lambda row: (
            str(row.get("city_key") or ""),
            str(row.get("category") or ""),
            str(row.get("primary_area_key") or ""),
            str(row.get("name") or ""),
        ),
    )
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    EXTERNAL_PLACES_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    EXTERNAL_PLACES_JSONL.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    _clear_runtime_caches()
    return added


def _normalize_external_place(place: dict[str, Any]) -> dict[str, Any] | None:
    name = str(place.get("name") or "").strip()
    category = str(place.get("category") or "").strip().lower()
    address = str(place.get("address") or "").strip()
    if not name or not category:
        return None
    if not is_user_facing_place_name(name):
        return None

    record: dict[str, Any] = {
        "name": name,
        "category": category,
        "address": address,
        "city": str(place.get("city") or "").strip(),
        "district": str(place.get("district") or "").strip(),
        "lat": _coerce_float(place.get("lat")),
        "lon": _coerce_float(place.get("lon")),
        "source": str(place.get("source") or "external-cache").strip(),
        "osm_class": str(place.get("osm_class") or "").strip(),
        "osm_type": str(place.get("osm_type") or "").strip(),
        "query_used": str(place.get("query_used") or "").strip(),
        "city_key": str(place.get("city_key") or "").strip(),
        "admin_area_keys": place.get("admin_area_keys") if isinstance(place.get("admin_area_keys"), list) else [],
        "accommodation_type": str(place.get("accommodation_type") or "").strip(),
        "star_rating": str(place.get("star_rating") or "").strip(),
    }
    description = str(place.get("description") or "").strip()
    if not description:
        description = _fallback_description(record)
    if description:
        record["description"] = description
    list_snippet = str(place.get("list_snippet") or "").strip()
    if not list_snippet:
        list_snippet = _fallback_list_snippet(record)
    if list_snippet:
        record["list_snippet"] = list_snippet
    return enrich_place_record(record)


def _fallback_description(record: dict[str, Any]) -> str:
    category_label = {
        "restaurant": "dia diem an uong",
        "accommodation": "dia diem luu tru",
        "destination": "diem tham quan",
        "entertainment": "diem giai tri",
    }.get(str(record.get("category") or "").strip().lower(), "dia diem")
    pieces = [f"{category_label} lay tu nguon ngoai Nominatim"]
    address = str(record.get("address") or "").strip()
    if address:
        pieces.append(f"dia chi: {address}")
    osm_class = str(record.get("osm_class") or "").strip()
    osm_type = str(record.get("osm_type") or "").strip()
    if osm_class or osm_type:
        pieces.append(f"phan loai ban do: {osm_class}/{osm_type}".strip("/"))
    query_used = str(record.get("query_used") or "").strip()
    if query_used:
        pieces.append(f"tim thay tu truy van: {query_used}")
    return ". ".join(piece for piece in pieces if piece).strip()


def _fallback_list_snippet(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("name") or "").strip(),
        str(record.get("address") or "").strip(),
    ]
    return " - ".join(part for part in parts if part).strip()


def _merge_records(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for key, value in incoming.items():
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = value
    if _score_record(incoming) > _score_record(current):
        for key, value in incoming.items():
            if key in {"description", "list_snippet", "address", "lat", "lon"} and value not in (None, "", [], {}):
                merged[key] = value
    return enrich_place_record(merged)


def _score_record(record: dict[str, Any]) -> int:
    score = 0
    for field in ("description", "list_snippet", "address", "lat", "lon", "query_used"):
        if record.get(field) not in (None, "", [], {}):
            score += 1
    return score


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _clear_runtime_caches() -> None:
    try:
        from app.services.place_repository import clear_place_caches

        clear_place_caches()
    except Exception:
        pass
    try:
        from app.tools.elasticsearch_tool import _load_unified_catalog

        _load_unified_catalog.cache_clear()
    except Exception:
        pass
    try:
        from app.services.vector_rag import _load_unified_places_by_id

        _load_unified_places_by_id.cache_clear()
    except Exception:
        pass

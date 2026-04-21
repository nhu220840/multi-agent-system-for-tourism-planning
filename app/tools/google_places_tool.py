from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config.settings import get_settings


@dataclass(frozen=True)
class GooglePlaceResolution:
    place_id: str
    display_name: str = ""
    formatted_address: str = ""
    lat: float | None = None
    lon: float | None = None
    google_maps_uri: str = ""
    business_status: str = ""
    primary_type: str = ""
    types: list[str] = field(default_factory=list)
    match_score: float = 0.0
    query_used: str = ""
    moved_place_id: str = ""
    coordinate_source: str = ""
    coordinate_confidence: str = ""


def google_places_available() -> bool:
    settings = get_settings()
    return bool(str(settings.google_maps_api_key or "").strip())


def resolve_place_record(_: dict[str, Any]) -> GooglePlaceResolution | None:
    # Optional integration. When no API key/tooling is configured, preprocessing
    # still succeeds and simply skips Google-based enrichment.
    return None

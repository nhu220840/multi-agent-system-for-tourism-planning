from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config.settings import get_settings
from app.services.place_metadata import extract_admin_area_keys, fold_text, place_city_key


# DEPRECATED: Google Places field masks - không còn dùng (thay bằng Nominatim)
# Giữ lại cho reference
_SEARCH_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.googleMapsUri",
        "places.businessStatus",
        "places.primaryType",
        "places.types",
    ]
)

_DETAIL_FIELD_MASK = ",".join(
    [
        "id",
        "displayName",
        "formattedAddress",
        "location",
        "googleMapsUri",
        "businessStatus",
        "primaryType",
        "types",
        "movedPlace",
        "movedPlaceId",
    ]
)

_CATEGORY_TYPE_HINTS: dict[str, set[str]] = {
    "restaurant": {
        "restaurant",
        "food",
        "meal_takeaway",
        "meal_delivery",
        "cafe",
    },
    "accommodation": {
        "lodging",
        "hotel",
        "resort_hotel",
        "motel",
        "hostel",
        "inn",
    },
    "destination": {
        "tourist_attraction",
        "museum",
        "park",
        "hindu_temple",
        "church",
        "beach",
        "historical_landmark",
    },
    "entertainment": {
        "tourist_attraction",
        "park",
        "movie_theater",
        "amusement_park",
        "night_club",
        "shopping_mall",
    },
}

_AREA_CENTROIDS: dict[str, tuple[float, float]] = {
    "hai chau": (16.0544, 108.2207),
    "son tra": (16.0975, 108.2637),
    "ngu hanh son": (16.0207, 108.2522),
    "thanh khe": (16.0678, 108.1960),
    "cam le": (16.0162, 108.2045),
    "lien chieu": (16.0749, 108.1482),
    "hoa vang": (15.9967, 108.0678),
    "hoi an": (15.8801, 108.3380),
    "tam ky": (15.5736, 108.4740),
    "dien ban": (15.8927, 108.2538),
    "duy xuyen": (15.7897, 108.1204),
    "dai loc": (15.8796, 107.9806),
    "thang binh": (15.6940, 108.3283),
    "tien phuoc": (15.4697, 108.2847),
    "nui thanh": (15.4330, 108.6187),
    "da_nang": (16.0544, 108.2022),
    "hoi_an_city": (15.8801, 108.3380),
    "quang_nam": (15.5394, 108.0191),
}

_AREA_SEARCH_LABELS: dict[str, str] = {
    "hai chau": "Hai Chau, Da Nang",
    "son tra": "Son Tra, Da Nang",
    "ngu hanh son": "Ngu Hanh Son, Da Nang",
    "thanh khe": "Thanh Khe, Da Nang",
    "cam le": "Cam Le, Da Nang",
    "lien chieu": "Lien Chieu, Da Nang",
    "hoa vang": "Hoa Vang, Da Nang",
    "hoi an": "Hoi An, Quang Nam",
    "tam ky": "Tam Ky, Quang Nam",
    "dien ban": "Dien Ban, Quang Nam",
    "duy xuyen": "Duy Xuyen, Quang Nam",
    "dai loc": "Dai Loc, Quang Nam",
    "thang binh": "Thang Binh, Quang Nam",
    "tien phuoc": "Tien Phuoc, Quang Nam",
    "nui thanh": "Nui Thanh, Quang Nam",
}

_CITY_SEARCH_LABELS: dict[str, str] = {
    "da_nang": "Da Nang, Viet Nam",
    "hoi_an": "Hoi An, Quang Nam, Viet Nam",
    "quang_nam": "Quang Nam, Viet Nam",
}

_CATEGORY_PREFIX_PATTERNS: dict[str, tuple[str, ...]] = {
    "restaurant": (r"^\s*nh[aà]\s+h[aà]ng\s+", r"^\s*restaurant\s+"),
    "accommodation": (r"^\s*kh[aá]ch\s+s[aạ]n\s+", r"^\s*hotel\s+"),
}

_NEARBY_MAX_RADIUS_KM: dict[str, float] = {
    "dense": 3.0,
    "medium": 6.0,
    "sparse": 12.0,
    "unknown": 8.0,
}


@dataclass(frozen=True)
class GooglePlaceResolution:
    """Kết quả giải quyết địa điểm (sử dụng Nominatim - miễn phí, không dùng Google Places API)"""
    place_id: str
    display_name: str
    formatted_address: str
    lat: float | None
    lon: float | None
    google_maps_uri: str
    business_status: str
    primary_type: str
    types: list[str]
    match_score: float
    query_used: str
    moved_place_id: str = ""
    coordinate_source: str = "nominatim_search"
    coordinate_confidence: str = "resolved_place"


def google_places_available() -> bool:
    """Free Nominatim resolver luôn có sẵn (không cần API key)."""
    return True


def resolve_place_record(record: dict[str, Any]) -> GooglePlaceResolution | None:
    """
    Giải quyết tọa độ chính xác cho một địa điểm.
    
    Sử dụng Nominatim (OpenStreetMap) API - miễn phí, không cần Google Places API key.
    
    Args:
        record: Dict với keys: name, address, city, category
        
    Returns:
        GooglePlaceResolution với lat/lon chính xác hoặc None
    """
    exact_match = _resolve_best_candidate(
        record=record,
        queries=_candidate_queries(record),
        min_score=12.0,
        prefer_center=None,
        bounded=False,
        resolution_source="nominatim_search",
        resolution_confidence="resolved_place",
    )
    if exact_match:
        return exact_match

    area_match = _resolve_best_candidate(
        record=record,
        queries=_area_biased_queries(record),
        min_score=7.0,
        prefer_center=_expected_center(record),
        bounded=False,
        resolution_source="nominatim_area_bias",
        resolution_confidence="area_matched_place",
    )
    if area_match:
        return area_match

    return _resolve_nearby_fallback(record)


def _candidate_queries(record: dict[str, Any]) -> list[str]:
    category = str(record.get("category") or "").strip().lower()
    names = _name_query_variants(record)
    addresses = _address_query_variants(record)
    area_labels = _area_context_queries(record)

    queries: list[str] = []
    for name in names:
        if addresses:
            queries.append(f"{name}, {addresses[0]}")
        if area_labels:
            queries.append(f"{name}, {area_labels[0]}")
        queries.append(name)
        if category == "restaurant":
            queries.append(f"{name} restaurant")
        elif category == "accommodation":
            queries.append(f"{name} hotel")

    queries.extend(addresses[:3])
    queries.extend(area_labels[:2])
    return _dedupe_queries(queries, limit=14)


def _area_biased_queries(record: dict[str, Any]) -> list[str]:
    names = _name_query_variants(record)
    addresses = _address_query_variants(record)
    area_labels = _area_context_queries(record)
    queries: list[str] = []
    for name in names[:4]:
        for area in area_labels[:2]:
            queries.append(f"{name}, {area}")
        if addresses:
            queries.append(f"{name}, {addresses[0]}")
        queries.append(name)
    queries.extend(addresses[:4])
    queries.extend(area_labels[:3])
    return _dedupe_queries(queries, limit=16)


def _resolve_best_candidate(
    record: dict[str, Any],
    queries: list[str],
    min_score: float,
    prefer_center: tuple[float, float] | None,
    bounded: bool,
    resolution_source: str,
    resolution_confidence: str,
) -> GooglePlaceResolution | None:
    best_candidate: dict[str, Any] | None = None
    best_score = float("-inf")
    best_query = ""

    for query in queries:
        payload = _search_text(
            query=query,
            prefer_center=prefer_center,
            bounded=bounded,
            limit=8,
        )
        for index, candidate in enumerate(payload):
            score = _candidate_score(
                candidate=candidate,
                record=record,
                rank=index,
                prefer_center=prefer_center,
            )
            if score > best_score:
                best_score = score
                best_candidate = candidate
                best_query = query

    if not best_candidate or best_score < min_score:
        return None
    return _build_resolution(
        candidate=best_candidate,
        score=best_score,
        query_used=best_query,
        coordinate_source=resolution_source,
        coordinate_confidence=resolution_confidence,
    )


def _resolve_nearby_fallback(record: dict[str, Any]) -> GooglePlaceResolution | None:
    center = _expected_center(record)
    if not center:
        return None

    queries = _nearby_fallback_queries(record)
    best_candidate: dict[str, Any] | None = None
    best_score = float("-inf")
    best_query = ""

    for query in queries:
        payload = _search_text(query=query, prefer_center=center, bounded=False, limit=12)
        for index, candidate in enumerate(payload):
            score = _nearby_candidate_score(
                candidate=candidate,
                record=record,
                rank=index,
                center=center,
            )
            if score > best_score:
                best_score = score
                best_candidate = candidate
                best_query = query

    if not best_candidate:
        return None

    lat, lon = _extract_location(best_candidate)
    if lat is None or lon is None:
        return None
    if _distance_km(center[0], center[1], lat, lon) > _max_nearby_radius_km(record):
        return None

    return _build_resolution(
        candidate=best_candidate,
        score=max(best_score, 1.0),
        query_used=best_query,
        coordinate_source="nominatim_nearby_fallback",
        coordinate_confidence="nearby_map_point",
    )


def _candidate_score(
    candidate: dict[str, Any],
    record: dict[str, Any],
    rank: int,
    prefer_center: tuple[float, float] | None = None,
) -> float:
    name = fold_text(str(record.get("name") or ""))
    address = fold_text(str(record.get("address") or ""))
    city = fold_text(str(record.get("city") or ""))
    category = str(record.get("category") or "").strip().lower()
    area_keys = _record_area_keys(record)
    city_key = place_city_key(record)

    candidate_name = fold_text(_nested_text(candidate.get("displayName")))
    candidate_address = fold_text(str(candidate.get("formattedAddress") or ""))
    candidate_types = {fold_text(str(item)) for item in (candidate.get("types") or []) if str(item).strip()}
    business_status = str(candidate.get("businessStatus") or "").strip().upper()

    score = 0.0
    if name:
        if candidate_name == name:
            score += 18.0
        elif name in candidate_name or candidate_name in name:
            score += 12.0
        score += min(8.0, 2.0 * _token_overlap(name, candidate_name))

    if address:
        if address in candidate_address or candidate_address in address:
            score += 10.0
        score += min(8.0, 1.5 * _token_overlap(address, candidate_address))

    if city and city in candidate_address:
        score += 5.0

    area_bonus, area_hits = _area_match_bonus(candidate_address, area_keys=area_keys, city_key=city_key)
    score += area_bonus
    if prefer_center:
        score += _center_distance_bonus(candidate=candidate, center=prefer_center)
    elif area_hits:
        score += 2.0

    if category:
        hints = _CATEGORY_TYPE_HINTS.get(category, set())
        if candidate_types.intersection(hints):
            score += 5.0
        elif category in {"restaurant", "accommodation"}:
            score -= 4.0

    if business_status == "CLOSED_PERMANENTLY":
        score -= 20.0
    elif business_status:
        score += 1.0

    score += max(0.0, 4.0 - float(rank))
    return score


def _nearby_candidate_score(
    candidate: dict[str, Any],
    record: dict[str, Any],
    rank: int,
    center: tuple[float, float],
) -> float:
    candidate_address = fold_text(str(candidate.get("formattedAddress") or ""))
    area_bonus, area_hits = _area_match_bonus(
        candidate_address,
        area_keys=_record_area_keys(record),
        city_key=place_city_key(record),
    )
    score = area_bonus + _center_distance_bonus(candidate=candidate, center=center) + max(0.0, 2.0 - float(rank))

    address = fold_text(str(record.get("address") or ""))
    if address:
        score += min(4.0, 1.0 * _token_overlap(address, candidate_address))

    category = str(record.get("category") or "").strip().lower()
    candidate_types = {fold_text(str(item)) for item in (candidate.get("types") or []) if str(item).strip()}
    hints = _CATEGORY_TYPE_HINTS.get(category, set())
    if hints and candidate_types.intersection(hints):
        score += 2.0

    if area_hits == 0:
        score -= 6.0
    return score


def _follow_place_details(place_id: str) -> dict[str, Any] | None:
    """Lấy chi tiết địa điểm (Nominatim không hỗ trợ moved places)"""
    return _place_details(place_id=place_id)


def _search_text(
    query: str,
    prefer_center: tuple[float, float] | None = None,
    bounded: bool = False,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Tìm kiếm địa điểm dùng Nominatim (OpenStreetMap) API - miễn phí"""
    if not query.strip():
        return []
    settings = get_settings()
    params = {
        "q": query,
        "format": "json",
        "limit": max(1, int(limit)),
        "countrycodes": settings.places_resolver_country_codes or "vn",
        "addressdetails": 1,
    }
    if prefer_center:
        left, top, right, bottom = _viewbox_for_center(prefer_center)
        params["viewbox"] = f"{left:.6f},{top:.6f},{right:.6f},{bottom:.6f}"
        if bounded:
            params["bounded"] = 1
    base_url = (settings.places_resolver_base_url or "https://nominatim.openstreetmap.org").rstrip("/")
    url = f"{base_url}/search?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": settings.places_resolver_user_agent or "multi-agent-travel/0.1 (free-places-resolver)"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=max(3, int(settings.places_resolver_request_timeout_s or 10))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    # Chuyển đổi Nominatim format sang schema nội bộ hiện có để giữ compatibility.
    results = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            continue

        place_type = _nominatim_type_to_place_type(
            item.get("class", ""),
            item.get("type", "")
        )
        lat = _safe_float(item.get("lat"))
        lon = _safe_float(item.get("lon"))
        display_name = str(item.get("name") or "").strip()
        if not display_name:
            display_name = str(item.get("display_name") or "").split(",")[0].strip()
        result = {
            "id": str(item.get("osm_id", idx)),
            "displayName": {
                "text": display_name
            },
            "formattedAddress": str(item.get("display_name", "")),
            "location": {
                "latitude": lat,
                "longitude": lon,
            },
            "googleMapsUri": _osm_point_uri(lat=lat, lon=lon),
            "businessStatus": "OPERATIONAL",
            "primaryType": place_type,
            "types": [place_type],
            "movedPlaceId": "",
            "nominatimData": item,
        }
        results.append(result)

    return results


def _place_details(place_id: str) -> dict[str, Any] | None:
    """Lấy chi tiết địa điểm dùng Nominatim - miễn phí."""
    if not place_id.strip():
        return None

    if "," in place_id:
        try:
            parts = place_id.split(",")
            lat, lon = float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            return None
    else:
        # Thử lookup bằng OSM ID
        try:
            osm_id = int(place_id)
        except ValueError:
            return None
        return None

    settings = get_settings()
    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "zoom": 18,
        "addressdetails": 1,
    }
    base_url = (settings.places_resolver_base_url or "https://nominatim.openstreetmap.org").rstrip("/")
    url = f"{base_url}/reverse?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": settings.places_resolver_user_agent or "multi-agent-travel/0.1 (free-places-resolver)"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=max(3, int(settings.places_resolver_request_timeout_s or 10))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    place_type = _nominatim_type_to_place_type(
        data.get("class", ""),
        data.get("type", "")
    )
    
    result = {
        "id": str(data.get("osm_id", place_id)),
        "displayName": {
            "text": str(data.get("name", ""))
        },
        "formattedAddress": str(data.get("display_name", "")),
        "location": {
            "latitude": float(data.get("lat", 0)),
            "longitude": float(data.get("lon", 0)),
        },
        "googleMapsUri": _osm_point_uri(lat=float(data.get("lat", 0)), lon=float(data.get("lon", 0))),
        "businessStatus": "OPERATIONAL",
        "primaryType": place_type,
        "types": [place_type],
        "movedPlaceId": "",
    }
    return result


def _request_json(
    url: str,
    method: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any] | None:
    """Gửi request JSON (DEPRECATED - giữ cho backward compatibility)"""
    return None


def _nominatim_type_to_place_type(osm_class: str, osm_type: str) -> str:
    """Chuyển đổi Nominatim class/type sang type nội bộ gần với Places."""
    class_lower = str(osm_class).lower()
    type_lower = str(osm_type).lower()

    if class_lower == "amenity":
        if type_lower in ("restaurant", "cafe", "fast_food", "bar", "pub"):
            return "restaurant"
        elif type_lower in ("hotel", "hostel", "guest_house", "motel", "apartment"):
            return "lodging"
        elif type_lower in ("museum", "cinema", "theatre", "nightclub"):
            return "entertainment"
        elif type_lower in ("attraction", "viewpoint", "monument"):
            return "tourist_attraction"
    
    elif class_lower == "tourism":
        if type_lower in ("attraction", "viewpoint", "monument", "museum"):
            return "tourist_attraction"
        elif type_lower in ("hotel", "guest_house", "apartment", "hostel"):
            return "lodging"
        elif type_lower == "artwork":
            return "tourist_attraction"
    
    elif class_lower == "natural":
        if type_lower in ("beach", "water", "park", "forest"):
            return "natural_feature"
    
    elif class_lower == "landuse":
        if type_lower == "forest":
            return "park"
    
    elif class_lower == "historic":
        return "historical_landmark"
    
    elif class_lower == "place":
        if type_lower in ("city", "town", "village"):
            return "locality"
    
    # Default
    if class_lower:
        return class_lower
    if type_lower:
        return type_lower
    return "point_of_interest"


def _osm_point_uri(lat: float | None, lon: float | None) -> str:
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return ""
    return (
        f"https://www.openstreetmap.org/?mlat={float(lat):.6f}&mlon={float(lon):.6f}"
        f"#map=18/{float(lat):.6f}/{float(lon):.6f}"
    )


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _extract_location(place: dict[str, Any]) -> tuple[float | None, float | None]:
    location = place.get("location") or {}
    lat = location.get("latitude")
    lon = location.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)
    return None, None


def _nested_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return str(value or "")


def _token_overlap(a: str, b: str) -> int:
    a_tokens = {token for token in a.split() if len(token) >= 2}
    b_tokens = {token for token in b.split() if len(token) >= 2}
    return len(a_tokens.intersection(b_tokens))


def _name_query_variants(record: dict[str, Any]) -> list[str]:
    name = str(record.get("name") or "").strip()
    category = str(record.get("category") or "").strip().lower()
    if not name:
        return []

    variants: list[str] = [name]
    simplified = re.sub(r"\s*[–—-]\s*", " - ", name)
    if " - " in simplified:
        head = simplified.split(" - ", 1)[0].strip(" -,")
        tail = simplified.split(" - ", 1)[1].strip(" -,")
        if head:
            variants.append(head)
        if tail and len(tail) >= 4:
            variants.append(tail)

    for pattern in _CATEGORY_PREFIX_PATTERNS.get(category, ()):
        stripped = re.sub(pattern, "", name, flags=re.IGNORECASE).strip(" -,")
        if stripped and stripped != name:
            variants.append(stripped)
            if category == "restaurant":
                variants.append(f"{stripped} restaurant")
            elif category == "accommodation":
                variants.append(f"{stripped} hotel")

    ascii_name = _ascii_query(name)
    if ascii_name and ascii_name != name:
        variants.append(ascii_name)
    return _dedupe_queries(variants, limit=8)


def _address_query_variants(record: dict[str, Any]) -> list[str]:
    city = str(record.get("city") or "").strip()
    address_hints: list[str] = []
    raw_address = str(record.get("address") or "").strip()
    if raw_address:
        address_hints.append(raw_address)
    address_hints.extend(_extract_address_hints(record))

    queries: list[str] = []
    for address in address_hints:
        cleaned = _clean_address(address)
        if not cleaned:
            continue
        queries.append(cleaned)
        if city and city not in cleaned:
            queries.append(f"{cleaned}, {city}")
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
        if len(parts) >= 2:
            queries.append(", ".join(parts[:2]))
        if parts:
            street = parts[0]
            if city and city not in street:
                queries.append(f"{street}, {city}")
    return _dedupe_queries(queries, limit=8)


def _area_context_queries(record: dict[str, Any]) -> list[str]:
    queries: list[str] = []
    for area_key in _record_area_keys(record):
        label = _AREA_SEARCH_LABELS.get(area_key)
        if label:
            queries.append(label)
    city_key = place_city_key(record)
    city_label = _CITY_SEARCH_LABELS.get(city_key)
    if city_label:
        queries.append(city_label)
    city = str(record.get("city") or "").strip()
    if city:
        queries.append(city)
    return _dedupe_queries(queries, limit=5)


def _nearby_fallback_queries(record: dict[str, Any]) -> list[str]:
    queries: list[str] = []
    queries.extend(_address_query_variants(record))
    queries.extend(_area_context_queries(record))
    name_variants = _name_query_variants(record)
    if name_variants:
        queries.append(name_variants[0])
    return _dedupe_queries(queries, limit=10)


def _extract_address_hints(record: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for raw in (
        str(record.get("list_snippet") or ""),
        str(record.get("detail_content") or ""),
    ):
        if not raw:
            continue
        match = re.search(
            r"(?:Địa chỉ|Dia chi)\s*:\s*(.+?)(?:Điện thoại|Dien thoai|Email|Website|\n|$)",
            raw,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            hints.append(match.group(1).strip(" ,.;"))
    return _dedupe_queries(hints, limit=4)


def _clean_address(address: str) -> str:
    cleaned = re.sub(r"\s+", " ", address or "").strip(" ,")
    cleaned = cleaned.replace(" ,", ",")
    cleaned = re.sub(r",\s*,+", ", ", cleaned)
    return cleaned.strip(" ,")


def _dedupe_queries(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = fold_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(value.strip())
        if len(ordered) >= limit:
            break
    return ordered


def _ascii_query(text: str) -> str:
    return re.sub(r"\s+", " ", fold_text(text or "")).strip()


def _record_area_keys(record: dict[str, Any]) -> list[str]:
    return [key for key in extract_admin_area_keys(record) if key]


def _expected_center(record: dict[str, Any]) -> tuple[float, float] | None:
    for area_key in _record_area_keys(record):
        center = _AREA_CENTROIDS.get(area_key)
        if center:
            return center
    city_key = place_city_key(record)
    if city_key == "hoi_an":
        return _AREA_CENTROIDS.get("hoi_an_city")
    return _AREA_CENTROIDS.get(city_key)


def _viewbox_for_center(center: tuple[float, float]) -> tuple[float, float, float, float]:
    lat, lon = center
    lat_delta = 0.06
    lon_delta = 0.06 / max(0.2, math.cos(math.radians(lat)))
    return (lon - lon_delta, lat + lat_delta, lon + lon_delta, lat - lat_delta)


def _area_match_bonus(
    candidate_address: str,
    area_keys: list[str],
    city_key: str,
) -> tuple[float, int]:
    hits = sum(1 for area in area_keys if area and area in candidate_address)
    bonus = 4.0 * hits
    if city_key:
        city_hint = fold_text(_CITY_SEARCH_LABELS.get(city_key, city_key.replace("_", " ")))
        if city_hint and city_hint.split(",")[0] in candidate_address:
            bonus += 3.0
    return bonus, hits


def _center_distance_bonus(candidate: dict[str, Any], center: tuple[float, float]) -> float:
    lat, lon = _extract_location(candidate)
    if lat is None or lon is None:
        return -6.0
    km = _distance_km(center[0], center[1], lat, lon)
    if km <= 0.5:
        return 7.0
    if km <= 1.5:
        return 5.0
    if km <= 3.0:
        return 3.0
    if km <= 8.0:
        return 1.0
    return max(-8.0, 1.0 - km * 0.5)


def _max_nearby_radius_km(record: dict[str, Any]) -> float:
    area_keys = _record_area_keys(record)
    if any(area in {"hai chau", "son tra", "ngu hanh son", "thanh khe", "hoi an"} for area in area_keys):
        return _NEARBY_MAX_RADIUS_KM["dense"]
    if any(area in {"cam le", "lien chieu", "dien ban"} for area in area_keys):
        return _NEARBY_MAX_RADIUS_KM["medium"]
    if area_keys:
        return _NEARBY_MAX_RADIUS_KM["sparse"]
    return _NEARBY_MAX_RADIUS_KM["unknown"]


def _build_resolution(
    candidate: dict[str, Any],
    score: float,
    query_used: str,
    coordinate_source: str,
    coordinate_confidence: str,
) -> GooglePlaceResolution | None:
    place_id = str(candidate.get("id") or "").strip()
    if not place_id:
        return None
    details = _follow_place_details(place_id=place_id)
    selected = details or candidate
    lat, lon = _extract_location(selected)
    return GooglePlaceResolution(
        place_id=str(selected.get("id") or place_id).strip(),
        display_name=str(_nested_text(selected.get("displayName")) or "").strip(),
        formatted_address=str(selected.get("formattedAddress") or "").strip(),
        lat=lat,
        lon=lon,
        google_maps_uri=str(selected.get("googleMapsUri") or "").strip(),
        business_status=str(selected.get("businessStatus") or "").strip(),
        primary_type=str(selected.get("primaryType") or "").strip(),
        types=[str(item).strip() for item in (selected.get("types") or []) if str(item).strip()],
        match_score=round(score, 2),
        query_used=query_used,
        moved_place_id=str(selected.get("movedPlaceId") or "").strip(),
        coordinate_source=coordinate_source,
        coordinate_confidence=coordinate_confidence,
    )


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rad = math.pi / 180.0
    d_lat = (lat2 - lat1) * rad
    d_lon = (lon2 - lon1) * rad
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin(d_lon / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(max(0.0, a)))

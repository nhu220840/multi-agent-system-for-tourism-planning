from __future__ import annotations

import logging
import json
import urllib.parse
import urllib.request
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Any, Optional

from app.config.settings import get_settings


_TEXTSEARCH_BASE_URL = "https://maps.track-asia.com/api/v2/place/textsearch/json"
_REVERSE_GEOCODE_BASE_URL = "https://maps.track-asia.com/api/v2/geocode/json"
_LOGGER = logging.getLogger(__name__)
_CACHE_LOCK = Lock()
_RATE_LIMIT_LOCK = Lock()
_RESPONSE_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}
_REQUEST_TIMESTAMPS: deque[float] = deque()
_ROUTE_MODE_ALIASES = {
    "car": "car",
    "driving": "car",
    "truck": "truck",
    "scooter": "scooter",
    "motorcycle": "scooter",
    "motorcycling": "scooter",
    "bike": "pedestrian",
    "walk": "pedestrian",
    "walking": "pedestrian",
    "pedestrian": "pedestrian",
}


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float


@dataclass(frozen=True)
class RouteEstimate:
    distance_m: float
    travel_time_s: int
    traffic_delay_s: int


def configured_route_modes() -> tuple[str, ...]:
    settings = get_settings()
    raw = str(settings.trackasia_route_modes or "").strip() or "car"
    modes: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        mode = _normalize_route_mode(token)
        if not mode or mode in seen:
            continue
        seen.add(mode)
        modes.append(mode)
    return tuple(modes or ["car"])


def geocode_address(query: str, *, limit: int = 5) -> list[dict]:
    """Resolve a text query (full address or place name) into lat/lon via TrackAsia Text Search.

    Used for places that exist in the local catalog but have an address without coordinates
    (or where OpenStreetMap/Nominatim cannot locate them). Nominatim remains the fallback for
    places that are not in our DB.
    """
    query = (query or "").strip()
    if not query:
        return []

    settings = get_settings()
    if not bool(settings.trackasia_enabled) or not bool(settings.trackasia_geocode_enabled):
        return []
    api_key = str(settings.trackasia_api_key or "").strip()
    if not api_key:
        return []

    params: dict[str, str] = {
        "query": query,
        "key": api_key,
    }
    if bool(settings.trackasia_new_admin):
        params["new_admin"] = "true"
        params["include_old_admin"] = "true"

    url = f"{_TEXTSEARCH_BASE_URL}?{urllib.parse.urlencode(params, safe=',')}"
    data = _load_json(
        url=url,
        timeout_s=max(1, int(settings.trackasia_request_timeout_s or 8)),
        scope="textsearch",
    )
    if not isinstance(data, dict):
        return []
    if str(data.get("status") or "").upper() not in {"OK", ""}:
        return []

    results = data.get("results") or []
    if not isinstance(results, list):
        return []

    out: list[dict] = []
    for item in results[: max(1, int(limit))]:
        if not isinstance(item, dict):
            continue
        geometry = item.get("geometry") if isinstance(item.get("geometry"), dict) else {}
        location = geometry.get("location") if isinstance(geometry.get("location"), dict) else {}
        lat = _coerce_float(location.get("lat"))
        lon = _coerce_float(location.get("lng"))
        if lat is None or lon is None:
            continue
        address = str(
            item.get("formatted_address")
            or item.get("old_formatted_address")
            or item.get("name")
            or ""
        ).strip()
        name = str(item.get("name") or "").strip() or (address.split(",")[0].strip() if address else query)
        out.append(
            {
                "name": name,
                "lat": lat,
                "lon": lon,
                "address": address,
                "place_id": str(item.get("place_id") or "").strip(),
                "source": "trackasia_textsearch",
            }
        )
    return out


def reverse_geocode_point(
    lat: float,
    lon: float,
    *,
    radius_m: int = 100,
    limit: int = 5,
    result_type: str | None = None,
) -> list[dict]:
    """Snap a (lat, lon) point onto the nearest TrackAsia-indexed places.

    Useful for catalog entries that carry approximate DB coordinates: the first result
    represents the actual on-map position TrackAsia will use for markers, distance
    matrix, and routing, so downstream consumers can trust it for drawing segments.
    """
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return []

    settings = get_settings()
    if not bool(settings.trackasia_enabled) or not bool(settings.trackasia_geocode_enabled):
        return []
    api_key = str(settings.trackasia_api_key or "").strip()
    if not api_key:
        return []

    params: dict[str, str] = {
        "latlng": f"{lat_f:.7f},{lon_f:.7f}",
        "key": api_key,
        "radius": str(max(1, int(radius_m))),
        "size": str(max(1, int(limit))),
    }
    if result_type:
        params["result_type"] = str(result_type).strip()
    if bool(settings.trackasia_new_admin):
        params["new_admin"] = "true"
        params["include_old_admin"] = "true"

    url = f"{_REVERSE_GEOCODE_BASE_URL}?{urllib.parse.urlencode(params, safe=',')}"
    data = _load_json(
        url=url,
        timeout_s=max(1, int(settings.trackasia_request_timeout_s or 8)),
        scope="reverse_geocode",
    )
    if not isinstance(data, dict):
        return []
    if str(data.get("status") or "").upper() not in {"OK", ""}:
        return []

    results = data.get("results") or []
    if not isinstance(results, list):
        return []

    out: list[dict] = []
    for item in results[: max(1, int(limit))]:
        if not isinstance(item, dict):
            continue
        geometry = item.get("geometry") if isinstance(item.get("geometry"), dict) else {}
        location = geometry.get("location") if isinstance(geometry.get("location"), dict) else {}
        resolved_lat = _coerce_float(location.get("lat"))
        resolved_lon = _coerce_float(location.get("lng"))
        if resolved_lat is None or resolved_lon is None:
            continue
        address = str(
            item.get("formatted_address")
            or item.get("old_formatted_address")
            or item.get("name")
            or ""
        ).strip()
        name = str(item.get("name") or "").strip() or (address.split(",")[0].strip() if address else "")
        out.append(
            {
                "name": name,
                "lat": resolved_lat,
                "lon": resolved_lon,
                "address": address,
                "place_id": str(item.get("place_id") or "").strip(),
                "location_type": str((geometry.get("location_type") if isinstance(geometry, dict) else "") or ""),
                "source": "trackasia_reverse_geocode",
            }
        )
    return out


def estimate_route(
    origin: GeoPoint,
    destination: GeoPoint,
    travel_mode: str = "car",
    *,
    waypoints: list[GeoPoint] | None = None,
) -> Optional[RouteEstimate]:
    settings = get_settings()
    if not bool(settings.trackasia_enabled) or not bool(settings.trackasia_routing_enabled):
        return None
    api_key = str(settings.trackasia_api_key or "").strip()
    if not api_key:
        return None

    params = {
        "origin": _format_directions_point(origin),
        "destination": _format_directions_point(destination),
        "mode": _map_directions_mode(travel_mode),
        "key": api_key,
    }
    if bool(settings.trackasia_new_admin):
        params["new_admin"] = "true"
    if waypoints:
        rendered = [ _format_directions_point(point) for point in waypoints ]
        if rendered:
            params["waypoints"] = "|".join(rendered)

    base = str(settings.trackasia_directions_base_url or "").rstrip("/") or "https://maps.track-asia.com/route/v2/directions"
    url = f"{base}/json?{urllib.parse.urlencode(params, safe='|,')}"

    data = _load_json(
        url=url,
        timeout_s=max(1, int(settings.trackasia_request_timeout_s or 8)),
        scope="directions",
    )
    if not isinstance(data, dict):
        return None
    if str(data.get("status") or "").upper() not in {"OK", ""}:
        return None

    routes = data.get("routes") or []
    if not isinstance(routes, list) or not routes:
        return None
    route = routes[0] if isinstance(routes[0], dict) else {}
    legs = route.get("legs") or []
    if not isinstance(legs, list) or not legs:
        return None

    total_distance_m = 0.0
    total_duration_s = 0
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        distance_value = _coerce_float(((leg.get("distance") or {}).get("value") if isinstance(leg.get("distance"), dict) else None))
        duration_value = _coerce_int(((leg.get("duration") or {}).get("value") if isinstance(leg.get("duration"), dict) else None))
        if distance_value is None or duration_value is None:
            continue
        total_distance_m += distance_value
        total_duration_s += duration_value

    if total_distance_m <= 0 or total_duration_s <= 0:
        return None

    return RouteEstimate(
        distance_m=total_distance_m,
        travel_time_s=total_duration_s,
        traffic_delay_s=0,
    )


def _load_json(*, url: str, timeout_s: int, scope: str) -> dict | list | None:
    settings = get_settings()
    cached = _get_cached_json(
        scope=scope,
        url=url,
        ttl_s=max(0, int(settings.trackasia_cache_ttl_s or 0)),
    )
    if cached is not None:
        return cached
    if not _allow_trackasia_request(settings=settings):
        _LOGGER.warning("TrackAsia request skipped because rate limit was exceeded for %s", scope)
        return None
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "multi-agent-travel/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
            _store_cached_json(
                scope=scope,
                url=url,
                ttl_s=max(0, int(settings.trackasia_cache_ttl_s or 0)),
                payload=payload,
            )
            return payload
    except Exception:
        return None


def _normalize_route_mode(value: object) -> str:
    return _ROUTE_MODE_ALIASES.get(str(value or "").strip().lower(), "")


def _get_cached_json(*, scope: str, url: str, ttl_s: int) -> dict | list | None:
    if ttl_s <= 0:
        return None
    cache_key = (scope, url)
    now = monotonic()
    with _CACHE_LOCK:
        cached = _RESPONSE_CACHE.get(cache_key)
        if cached is None:
            return None
        expires_at, payload = cached
        if expires_at <= now:
            _RESPONSE_CACHE.pop(cache_key, None)
            return None
        return deepcopy(payload)


def _store_cached_json(*, scope: str, url: str, ttl_s: int, payload: dict | list) -> None:
    if ttl_s <= 0:
        return
    cache_key = (scope, url)
    with _CACHE_LOCK:
        _RESPONSE_CACHE[cache_key] = (monotonic() + ttl_s, deepcopy(payload))


def _allow_trackasia_request(*, settings: Any) -> bool:
    max_calls = max(0, int(getattr(settings, "trackasia_rate_limit_max_calls", 0) or 0))
    window_s = max(0, int(getattr(settings, "trackasia_rate_limit_window_s", 0) or 0))
    if max_calls <= 0 or window_s <= 0:
        return True

    now = monotonic()
    cutoff = now - window_s
    with _RATE_LIMIT_LOCK:
        while _REQUEST_TIMESTAMPS and _REQUEST_TIMESTAMPS[0] <= cutoff:
            _REQUEST_TIMESTAMPS.popleft()
        if len(_REQUEST_TIMESTAMPS) >= max_calls:
            return False
        _REQUEST_TIMESTAMPS.append(now)
    return True


def _format_directions_point(point: GeoPoint) -> str:
    return f"{point.lat:.6f},{point.lon:.6f}"


def _map_directions_mode(travel_mode: str) -> str:
    mode_map = {
        "car": "driving",
        "truck": "truck",
        "scooter": "motorcycling",
        "motorcycle": "motorcycling",
        "bike": "walking",
        "walk": "walking",
        "pedestrian": "walking",
    }
    return mode_map.get(str(travel_mode or "").strip().lower(), "driving")


def _coerce_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(round(float(value)))
    except Exception:
        return None


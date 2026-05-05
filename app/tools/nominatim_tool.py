from __future__ import annotations

import json
import urllib.parse
import urllib.request
from copy import deepcopy
from threading import Lock
from time import monotonic


_UA = "multi-agent-travel/0.1 (contact: local-dev)"
_DEFAULT_TIMEOUT_S = 6
_CACHE_TTL_S = 600
_CACHE_LOCK = Lock()
_RESPONSE_CACHE: dict[str, tuple[float, object]] = {}


def _load_json(url: str, *, timeout_s: int = _DEFAULT_TIMEOUT_S) -> object | None:
    now = monotonic()
    with _CACHE_LOCK:
        cached = _RESPONSE_CACHE.get(url)
        if cached is not None:
            expires_at, payload = cached
            if expires_at > now:
                return deepcopy(payload)
            _RESPONSE_CACHE.pop(url, None)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    with _CACHE_LOCK:
        _RESPONSE_CACHE[url] = (monotonic() + _CACHE_TTL_S, deepcopy(payload))
    return payload


def search_places(query: str, limit: int = 5) -> list[dict]:
    if not query.strip():
        return []
    qs = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": max(1, min(int(limit), 20))})
    data = _load_json(f"https://nominatim.openstreetmap.org/search?{qs}")
    if data is None:
        return []
    out: list[dict] = []
    if not isinstance(data, list):
        return out
    for item in data:
        name = str(item.get("display_name") or "").split(",")[0].strip()
        lat = item.get("lat")
        lon = item.get("lon")
        if not name or lat is None or lon is None:
            continue
        out.append(
            {
                "name": name,
                "lat": float(lat),
                "lon": float(lon),
                "address": str(item.get("display_name") or ""),
                "osm_class": str(item.get("class") or ""),
                "osm_type": str(item.get("type") or ""),
                "source": "nominatim",
            }
        )
    return out


def reverse_geocode(lat: float, lon: float, zoom: int = 18) -> dict | None:
    qs = urllib.parse.urlencode(
        {
            "lat": f"{float(lat):.7f}",
            "lon": f"{float(lon):.7f}",
            "format": "jsonv2",
            "zoom": max(3, min(int(zoom), 18)),
        }
    )
    payload = _load_json(f"https://nominatim.openstreetmap.org/reverse?{qs}")
    if payload is None:
        return None
    item = payload
    if not isinstance(item, dict):
        return None
    raw_lat = item.get("lat")
    raw_lon = item.get("lon")
    display_name = str(item.get("display_name") or "").strip()
    if raw_lat is None or raw_lon is None or not display_name:
        return None
    name = str(item.get("name") or "").strip() or display_name.split(",")[0].strip()
    try:
        return {
            "name": name,
            "lat": float(raw_lat),
            "lon": float(raw_lon),
            "address": display_name,
            "osm_class": str(item.get("class") or ""),
            "osm_type": str(item.get("type") or ""),
            "source": "nominatim_reverse",
        }
    except (TypeError, ValueError):
        return None

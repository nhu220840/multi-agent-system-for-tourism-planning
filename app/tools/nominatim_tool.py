from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    display_name: str


_UA = "multi-agent-travel/0.1 (contact: local-dev)"


def geocode(query: str) -> Optional[GeoPoint]:
    if not query.strip():
        return None
    qs = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 1})
    req = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/search?{qs}",
        headers={"User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    item = data[0]
    lat = item.get("lat")
    lon = item.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return GeoPoint(lat=float(lat), lon=float(lon), display_name=str(item.get("display_name") or query))
    except Exception:
        return None


def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    qs = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "jsonv2"})
    req = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/reverse?{qs}",
        headers={"User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return str(data.get("display_name") or "").strip() or None


def search_places(query: str, limit: int = 5) -> list[dict]:
    if not query.strip():
        return []
    qs = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": max(1, limit)})
    req = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/search?{qs}",
        headers={"User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
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


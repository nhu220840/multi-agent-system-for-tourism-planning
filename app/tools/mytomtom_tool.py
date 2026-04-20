from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from app.config.settings import get_settings


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float


@dataclass(frozen=True)
class RouteEstimate:
    distance_m: float
    travel_time_s: int
    traffic_delay_s: int


def geocode_address(address: str) -> Optional[GeoPoint]:
    settings = get_settings()
    api_key = (settings.mytomtom_api_key or "").strip()
    if not api_key or not address.strip():
        return None

    base = (settings.mytomtom_base_url or "https://api.tomtom.com").rstrip("/")
    query = urllib.parse.quote(address.strip(), safe="")
    url = f"{base}/search/2/geocode/{query}.json?key={api_key}&limit=1"

    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    results = data.get("results") or []
    if not results:
        return None

    pos = results[0].get("position") or {}
    lat = pos.get("lat")
    lon = pos.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return GeoPoint(lat=float(lat), lon=float(lon))


def estimate_route(origin: GeoPoint, destination: GeoPoint, travel_mode: str = "car") -> Optional[RouteEstimate]:
    settings = get_settings()
    api_key = (settings.mytomtom_api_key or "").strip()
    if not api_key:
        return None

    mode_map = {
        "car": "car",
        "scooter": "motorcycle",
        "bike": "bicycle",
        "walk": "pedestrian",
        "pedestrian": "pedestrian",
    }
    route_type = mode_map.get(travel_mode, "car")
    base = (settings.mytomtom_base_url or "https://api.tomtom.com").rstrip("/")
    points = f"{origin.lat},{origin.lon}:{destination.lat},{destination.lon}"
    url = f"{base}/routing/1/calculateRoute/{points}/json?key={api_key}&travelMode={route_type}&traffic=true"

    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    routes = data.get("routes") or []
    if not routes:
        return None
    summary = routes[0].get("summary") or {}
    length_m = summary.get("lengthInMeters")
    travel_s = summary.get("travelTimeInSeconds")
    traffic_s = summary.get("trafficDelayInSeconds", 0)
    if not isinstance(length_m, (int, float)) or not isinstance(travel_s, int):
        return None
    return RouteEstimate(
        distance_m=float(length_m),
        travel_time_s=travel_s,
        traffic_delay_s=traffic_s if isinstance(traffic_s, int) else 0,
    )


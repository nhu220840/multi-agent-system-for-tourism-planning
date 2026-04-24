from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from math import asin, cos, radians, sin, sqrt
from typing import Any
from urllib.parse import quote

from app.services.place_metadata import (
    city_key_from_text,
    extract_admin_area_keys,
    fold_text,
    normalize_address_text,
    place_city_key,
)
from app.tools.trackasia_tool import geocode_address, reverse_geocode_point

_DB_SNAP_RADIUS_M = 150
# Upper bound on how far a Text Search hit is allowed to drift from the catalog
# coordinate before we reject it. Keeps a wrong-city hotel with a similar name
# from silently replacing the original pin. 5 km comfortably covers DB entries
# whose lat/lon was sourced from Nominatim (often the venue's gate) while still
# excluding same-name places in another district/province.
_TEXTSEARCH_FALLBACK_RADIUS_KM = 5.0
# Text Search results at shared addresses can contain many neighbouring shops
# (e.g. five tenants living at the same street number). Ask for more rows so
# we have enough signal for the name-overlap pass.
_TEXTSEARCH_CANDIDATE_LIMIT = 10

# Tokens that carry no identity information for a venue. Stripping them before
# scoring keeps "Nha hang X" vs "Quan X" from showing a spurious overlap, and
# keeps the city / district tokens that appear in every address from boosting
# unrelated shops.
_NAME_STOPWORDS: frozenset[str] = frozenset(
    {
        # Business-type tokens.
        "nha", "hang", "quan", "restaurant", "restaurants",
        "cafe", "coffee", "bar", "pub", "bistro", "eatery",
        "hotel", "hostel", "motel", "resort", "villa", "homestay",
        "khach", "san",
        # Administrative / address tokens.
        "duong", "pho", "ngo", "hem", "street", "road", "avenue",
        "phuong", "ward", "huyen", "xa", "thon", "thon",
        "thanh", "tinh", "city", "province",
        # Place-name tokens that appear in almost every local address.
        "da", "nang", "danang", "hoi", "an", "quang", "nam",
        "tp", "tphcm",
        # English connectors.
        "the", "and", "or", "of", "in", "at", "on", "for",
    }
)
# A small alphanumeric-ish token has too little entropy to be useful as an
# identity signal, and allowing them would cause "54" from street number to
# match every shop at that number.
_NAME_TOKEN_MIN_LENGTH = 3

_TRACKASIA_HOST = "https://maps.track-asia.com"
_TRACKASIA_DEFAULT_MODE = "driving"
_TRACKASIA_DEFAULT_PLACE_ZOOM = 16

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
    "quang_nam": (15.5394, 108.0191),
}


@dataclass(frozen=True)
class ResolvedMapLocation:
    lat: float
    lon: float
    label: str
    address: str
    source: str


def resolve_location_for_map(
    place: dict[str, Any] | None,
    *,
    prefer_search: bool = False,
) -> ResolvedMapLocation | None:
    """Resolve a place to coordinates + label ready for the TrackAsia map view.

    Priority:
      1. When `prefer_search=True`, ask TrackAsia Text Search first (used for
         disambiguation when two catalog entries share coordinates).
      2. Trust the coordinates that came with the catalog entry, then **snap** them to the
         nearest TrackAsia-indexed point (`api/v2/geocode/json`). The snapped coordinates
         are the ones TrackAsia will use for markers and routing, so
         using them here guarantees the distance between any two catalog stops and the
         segment we draw on the map come from the same coordinate space.
      2b. When the snapped POI's name clearly does not match the catalog name,
          or the snap finds nothing inside the radius, run a Text Search that
          also uses the *address* and then re-picks by name-token overlap. This
          stops the hosted SPA from labelling "Nha hang Akataiyo Mat Troi Do"
          with its neighbour ("Quan An Bac Trung Nam") just because both live
          at 54 Nguyen Du, and also keeps pins off the generic admin label
          ("Viet Nam") when the snap radius missed every indexed POI.
      3. If the catalog entry only has an address, resolve it via TrackAsia Text
         Search, again preferring results that share identity tokens with the
         catalog name.
      4. Fallback to district/city centroid.

    Nominatim is intentionally NOT called here: it is reserved for places that are not
    part of our catalog (see `app/agents/itinerary_builder.py`).
    """
    if not place:
        return None
    city_key = place_city_key(place)
    queries = _build_geocode_queries(place)

    if prefer_search:
        name_match = _trackasia_name_aware_geocode(
            place=place,
            queries=queries,
            expected_city_key=city_key,
        )
        if name_match is not None:
            return name_match
        for query in queries:
            resolved = _trackasia_geocode_cached(query=query, expected_city_key=city_key)
            if resolved:
                return resolved

    lat = place.get("lat")
    lon = place.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        db_address = str(
            place.get("address")
            or place.get("map_formatted_address")
            or place.get("google_formatted_address")
            or ""
        ).strip()
        anchor = (float(lat), float(lon))
        snapped = _trackasia_snap_point_cached(lat=float(lat), lon=float(lon))

        if snapped is not None and _snap_matches_place_name(place=place, snapped=snapped):
            snapped_address = snapped.address or db_address or _best_query_label(place)
            return ResolvedMapLocation(
                lat=snapped.lat,
                lon=snapped.lon,
                label=_display_label(place=place, fallback_address=snapped_address),
                address=snapped_address,
                source=snapped.source or "trackasia_reverse_geocode",
            )

        # Either (a) snap returned nothing, or (b) snap grabbed a POI whose
        # identity tokens don't overlap with the catalog name. In both cases
        # ask Text Search for all venues near this address and pick the one
        # whose name shares tokens with the DB entry.
        name_match = _trackasia_name_aware_geocode(
            place=place,
            queries=queries,
            expected_city_key=city_key,
            anchor=anchor,
            fallback_address=db_address,
            source_suffix=(
                "name_match:snap_override" if snapped is not None else "name_match:snap_fallback"
            ),
        )
        if name_match is not None:
            return name_match

        # No evidence-backed name match: prefer the snap (TrackAsia-native
        # coordinate) over raw DB coordinates so routing stays
        # in the same coordinate space.
        if snapped is not None:
            snapped_address = snapped.address or db_address or _best_query_label(place)
            return ResolvedMapLocation(
                lat=snapped.lat,
                lon=snapped.lon,
                label=_display_label(place=place, fallback_address=snapped_address),
                address=snapped_address,
                source=(snapped.source or "trackasia_reverse_geocode") + ":nearest",
            )

        return ResolvedMapLocation(
            lat=float(lat),
            lon=float(lon),
            label=_display_label(place=place, fallback_address=db_address),
            address=db_address or _best_query_label(place),
            source="db_coordinates",
        )

    name_match = _trackasia_name_aware_geocode(
        place=place,
        queries=queries,
        expected_city_key=city_key,
        anchor=None,
    )
    if name_match is not None:
        return name_match

    for query in queries:
        resolved = _trackasia_geocode_cached(query=query, expected_city_key=city_key)
        if resolved:
            return resolved

    for area in extract_admin_area_keys(place):
        if area in _AREA_CENTROIDS:
            lat0, lon0 = _AREA_CENTROIDS[area]
            return ResolvedMapLocation(
                lat=lat0,
                lon=lon0,
                label=_display_label(place=place),
                address=_best_query_label(place),
                source=f"area_centroid:{area}",
            )

    if city_key and city_key in _AREA_CENTROIDS:
        lat0, lon0 = _AREA_CENTROIDS[city_key]
        return ResolvedMapLocation(
            lat=lat0,
            lon=lon0,
            label=_display_label(place=place),
            address=_best_query_label(place),
            source=f"city_centroid:{city_key}",
        )
    return None


def resolve_point_for_map(place: dict[str, Any] | None) -> tuple[float, float] | None:
    resolved = resolve_location_for_map(place)
    if not resolved:
        return None
    return resolved.lat, resolved.lon


def resolve_segment_points(
    a: dict[str, Any] | None,
    b: dict[str, Any] | None,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    a_loc, b_loc = resolve_segment_locations(a, b)
    a_point = (a_loc.lat, a_loc.lon) if a_loc else None
    b_point = (b_loc.lat, b_loc.lon) if b_loc else None
    return a_point, b_point


def resolve_segment_locations(
    a: dict[str, Any] | None,
    b: dict[str, Any] | None,
) -> tuple[ResolvedMapLocation | None, ResolvedMapLocation | None]:
    a_loc = resolve_location_for_map(a)
    b_loc = resolve_location_for_map(b)
    if not a_loc or not b_loc:
        return a_loc, b_loc
    if _same_place(a, b) or not _same_coordinates(a_loc, b_loc):
        return a_loc, b_loc

    a_alt = resolve_location_for_map(a, prefer_search=True)
    if a_alt and not _same_coordinates(a_alt, b_loc):
        a_loc = a_alt
    b_alt = resolve_location_for_map(b, prefer_search=True)
    if b_alt and not _same_coordinates(a_loc, b_alt):
        b_loc = b_alt
    return a_loc, b_loc


def place_map_url(place: dict[str, Any] | None) -> str:
    """Deep-link straight to TrackAsia's hosted map viewer focused on a single place."""
    resolved = resolve_location_for_map(place)
    if not resolved:
        return ""
    return _trackasia_place_url(resolved)


def osm_directions_url(
    stops: list[dict | None],
    engine: str = "fossgis_osrm_car",  # kept for backwards compatibility
) -> str:
    """Return a direct TrackAsia route URL for the first → last viable stop.

    TrackAsia's hosted SPA only supports two endpoints per URL, so for multi-stop
    itineraries we link the extremes and let `segment_map_url` handle per-segment
    links. The `engine` argument is kept for backwards compatibility and is ignored.
    """
    del engine
    resolved_stops: list[ResolvedMapLocation] = []
    for stop in stops:
        resolved = resolve_location_for_map(stop)
        if not resolved:
            continue
        if resolved_stops and _same_coordinates(resolved_stops[-1], resolved):
            continue
        resolved_stops.append(resolved)
    if len(resolved_stops) < 2:
        return ""
    return _trackasia_route_url(resolved_stops[0], resolved_stops[-1])


def segment_map_url(
    a: dict | None,
    b: dict | None,
    engine: str = "fossgis_osrm_car",  # kept for backwards compatibility
) -> str:
    """TrackAsia hosted URL that opens the map with 2 pins + route between them."""
    del engine
    a_loc, b_loc = resolve_segment_locations(a, b)
    if not a_loc or not b_loc or _same_coordinates(a_loc, b_loc):
        return ""
    return _trackasia_route_url(a_loc, b_loc)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = radians(lat1)
    p2 = radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _build_geocode_queries(place: dict[str, Any]) -> list[str]:
    name = str(place.get("name") or "").strip()
    address = normalize_address_text(
        str(
            place.get("address")
            or place.get("map_formatted_address")
            or place.get("google_formatted_address")
            or ""
        )
    )
    district = str(place.get("district") or "").strip()
    city = str(place.get("city") or "").strip()

    candidates = [
        ", ".join(part for part in [name, address, district, city] if part),
        ", ".join(part for part in [name, address, city] if part),
        ", ".join(part for part in [name, district, city] if part),
        ", ".join(part for part in [address, district, city] if part),
        ", ".join(part for part in [name, city] if part),
        ", ".join(part for part in [address, city] if part),
    ]

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = normalize_address_text(candidate)
        if not normalized:
            continue
        key = fold_text(normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _best_query_label(place: dict[str, Any] | None) -> str:
    if not place:
        return ""
    for candidate in _build_geocode_queries(place):
        if candidate:
            return candidate
    return str(place.get("name") or "").strip()


def _trackasia_place_url(loc: ResolvedMapLocation) -> str:
    """Mirror TrackAsia SPA's `place/<toUrl>#map=<zoom>/<lat>/<lng>` schema."""
    token = _trackasia_point_token(loc)
    lat = f"{loc.lat:.7f}"
    lng = f"{loc.lon:.7f}"
    return f"{_TRACKASIA_HOST}/place/{token}#map={_TRACKASIA_DEFAULT_PLACE_ZOOM}/{lat}/{lng}"


def _trackasia_route_url(
    origin: ResolvedMapLocation,
    destination: ResolvedMapLocation,
    *,
    mode: str = _TRACKASIA_DEFAULT_MODE,
) -> str:
    """Mirror TrackAsia SPA's `/routes/?mode=…&origin=…&destination=…` schema.

    The SPA auto-computes + renders the polyline. Tokens already contain
    percent-encoded names, so build the query string manually to avoid a second
    round of encoding.
    """
    query = (
        f"mode={quote(str(mode), safe='')}"
        f"&origin={_trackasia_point_token(origin)}"
        f"&destination={_trackasia_point_token(destination)}"
    )
    return f"{_TRACKASIA_HOST}/routes/?{query}"


def _trackasia_point_token(loc: ResolvedMapLocation) -> str:
    """Encode a point the way TrackAsia's SPA does: `latlon:<lat6>:<lng6>@<name>`.

    The SPA decodes the trailing `@<name>` back into the sidebar label, so keep the
    friendly human name (URL-encoded) when we have one.
    """
    lat = f"{loc.lat:.6f}"
    lng = f"{loc.lon:.6f}"
    token = f"latlon:{lat}:{lng}"
    name = (loc.label or "").strip()
    if name:
        token = f"{token}@{quote(name, safe='')}"
    return token


def _snap_matches_place_name(
    *,
    place: dict[str, Any],
    snapped: "ResolvedMapLocation",
) -> bool:
    """Return True when the snapped POI can reasonably stand in for `place`.

    Two cases count as "matching":
      * The catalog name has no distinctive tokens (rare, e.g. the entry stores
        only an address). We have nothing to compare against, so trust the
        snap. The SPA will at worst show the nearest POI name which is
        strictly better than admin-level reverse geocode output.
      * At least one identity token from the catalog name survives in the
        snapped POI's label. We intentionally do NOT mix in the snapped
        address here because street names ("Nguyen Du") repeat on both sides
        of unrelated shops and would silently create false matches.
    """
    db_tokens = _distinctive_name_tokens(str(place.get("name") or ""))
    if not db_tokens:
        return True
    snapped_tokens = _distinctive_name_tokens(snapped.label)
    return bool(db_tokens & snapped_tokens)


def _strip_branch_suffix(name: str) -> str:
    """Drop the " - <branch>" trailing segment common in Vietnamese POI names.

    Examples we want to collapse so the identity token set stays clean:
      * "Nha hang Akataiyo Mat Troi Do - Nguyen Du" -> "Nha hang Akataiyo Mat Troi Do"
      * "Toan Luxury Mobile - 54 Nguyen Du"         -> "Toan Luxury Mobile"

    Without this step the shared street token ("nguyen") makes two unrelated
    shops look like a match just because they both carry the street as a
    branch marker.
    """
    base = (name or "").strip()
    if not base:
        return base
    cut = base.find(" - ")
    return base[:cut].strip() if cut >= 0 else base


def _distinctive_name_tokens(text: str) -> set[str]:
    """Split `text` into ASCII-folded identity tokens.

    Drops administrative / generic business words so that two shops sharing only
    "nha hang ... da nang" produce zero overlap, while something distinctive
    like "akataiyo" or "madame" still survives. Branch-suffixes are stripped
    upstream via `_strip_branch_suffix` so the street name that labels a
    branch is not counted as an identity token.
    """
    folded = fold_text(_strip_branch_suffix(text or ""))
    if not folded:
        return set()
    return {
        token
        for token in re.split(r"[^a-z0-9]+", folded)
        if len(token) >= _NAME_TOKEN_MIN_LENGTH and token not in _NAME_STOPWORDS
    }


def _name_overlap_score(db_tokens: set[str], candidate_tokens: set[str]) -> int:
    """Count how many identity tokens survive on both sides.

    Pure set intersection is enough because both sides have already been filtered
    through `_distinctive_name_tokens`.
    """
    if not db_tokens or not candidate_tokens:
        return 0
    return len(db_tokens & candidate_tokens)


def _trackasia_name_aware_geocode(
    *,
    place: dict[str, Any],
    queries: list[str],
    expected_city_key: str,
    anchor: tuple[float, float] | None = None,
    anchor_radius_km: float = _TEXTSEARCH_FALLBACK_RADIUS_KM,
    fallback_address: str = "",
    source_suffix: str = "",
) -> ResolvedMapLocation | None:
    """Run Text Search over `queries`, then pick the hit whose name tokens best
    match ``place["name"]``.

    The TrackAsia Text Search endpoint often returns several tenants sharing the
    same street number (e.g. querying "54 Nguyen Du, Hai Chau" yields both
    "Akataiyo Sushi" and "54A Duong Nguyen Du"). Picking the first row causes
    the map pin to snap to a neighbour with a visually identical address, which
    then shows up on the SPA sidebar under the wrong business name.

    Ranking rules (highest priority first):
      1. Candidate matches the expected city key when one is known.
      2. More identity-token overlap with the DB name wins.
      3. Closer to the catalog anchor wins (nearest POI inside the search hit
         list, used as a tie-breaker and as a cheap proxy for "same building").

    When the DB name has distinctive tokens but no candidate shares any of them,
    this helper returns `None`. That is intentional: falling back to
    ``db_coordinates`` and letting the SPA reverse-geocode is safer than silently
    swapping the pin onto an unrelated shop at the same address.
    """
    db_name = str(place.get("name") or "").strip()
    db_tokens = _distinctive_name_tokens(db_name)

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()

    for query in queries:
        raw = _trackasia_textsearch_results_cached(
            query=query,
            limit=_TEXTSEARCH_CANDIDATE_LIMIT,
        )
        for item in raw:
            lat = item.get("lat")
            lon = item.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            key = (round(float(lat), 5), round(float(lon), 5))
            if key in seen:
                continue
            if anchor is not None:
                distance_km = haversine_km(anchor[0], anchor[1], float(lat), float(lon))
                if distance_km > anchor_radius_km:
                    continue
            seen.add(key)
            candidates.append(item)

    if not candidates:
        return None

    scored: list[tuple[int, int, float, dict[str, Any]]] = []
    for item in candidates:
        candidate_name = str(item.get("name") or "")
        candidate_address = str(item.get("address") or "")
        # Match on the candidate NAME only. Mixing the address in would let a
        # shared street token (e.g. every shop on Nguyen Du) look like a name
        # match, which is exactly the bug we are trying to eliminate.
        name_score = _name_overlap_score(
            db_tokens, _distinctive_name_tokens(candidate_name)
        )
        city_score = 0
        if expected_city_key:
            blob = fold_text(f"{candidate_name} {candidate_address}")
            city_score = 1 if city_key_from_text(blob) == expected_city_key else 0
        if anchor is not None:
            distance_km = haversine_km(
                anchor[0], anchor[1], float(item["lat"]), float(item["lon"])
            )
        else:
            distance_km = 0.0
        # Negate distance so sorting descending prefers the closer candidate.
        scored.append((city_score, name_score, -distance_km, item))

    scored.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    best_city, best_name, _, best = scored[0]

    # If the DB name offers identity tokens but none of the candidates share any
    # of them, we have no evidence that this is the same venue. Back off to the
    # caller's next fallback rather than committing to a wrong shop.
    if db_tokens and best_name == 0:
        return None

    lat = float(best["lat"])
    lon = float(best["lon"])
    address_text = str(best.get("address") or "").strip()
    candidate_name = str(best.get("name") or "").strip()
    resolved_address = address_text or fallback_address or _best_query_label(place)
    label = _display_label(
        place=place,
        fallback_name=candidate_name,
        fallback_address=resolved_address,
    )
    raw_source = str(best.get("source") or "trackasia_textsearch")
    source = f"{raw_source}:name_match" if not source_suffix else f"{raw_source}:{source_suffix}"
    return ResolvedMapLocation(
        lat=lat,
        lon=lon,
        label=label,
        address=resolved_address,
        source=source,
    )


@lru_cache(maxsize=4096)
def _trackasia_textsearch_results_cached(query: str, limit: int) -> tuple[dict[str, Any], ...]:
    """Tuple-returning wrapper so LRU cache works with hashable values.

    Returning a tuple (not a list) is what makes caching safe here. The tuple
    elements are plain dicts; callers treat them as read-only.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return ()
    results = geocode_address(cleaned, limit=max(1, int(limit)))
    return tuple(results) if results else ()


@lru_cache(maxsize=4096)
def _trackasia_snap_point_cached(lat: float, lon: float) -> ResolvedMapLocation | None:
    """Return the first TrackAsia-indexed place near the given DB coordinate.

    Cached so every stop only triggers one reverse-geocode lookup per session. If the
    API is unavailable, the caller falls back to the DB coordinates untouched.
    """
    try:
        db_lat = round(float(lat), 6)
        db_lon = round(float(lon), 6)
    except (TypeError, ValueError):
        return None
    hits = reverse_geocode_point(db_lat, db_lon, radius_m=_DB_SNAP_RADIUS_M, limit=1)
    if not hits:
        return None
    pick = hits[0]
    try:
        resolved_lat = float(pick.get("lat"))
        resolved_lon = float(pick.get("lon"))
    except (TypeError, ValueError):
        return None
    address_text = str(pick.get("address") or "").strip()
    label_text = str(pick.get("name") or "").strip() or (
        address_text.split(",")[0].strip() if address_text else ""
    )
    return ResolvedMapLocation(
        lat=resolved_lat,
        lon=resolved_lon,
        label=label_text,
        address=address_text,
        source=str(pick.get("source") or "trackasia_reverse_geocode"),
    )


@lru_cache(maxsize=2048)
def _trackasia_geocode_cached(query: str, expected_city_key: str) -> ResolvedMapLocation | None:
    """Geocode `query` through TrackAsia Text Search, biased toward `expected_city_key`."""
    if not query.strip():
        return None
    results = geocode_address(query, limit=5)
    if not results:
        return None
    pick = results[0]
    if expected_city_key:
        for item in results:
            blob = fold_text(
                " ".join(
                    [
                        str(item.get("name") or ""),
                        str(item.get("address") or ""),
                    ]
                )
            )
            if city_key_from_text(blob) == expected_city_key:
                pick = item
                break
    lat = pick.get("lat")
    lon = pick.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    address_text = str(pick.get("address") or "").strip()
    label_text = (
        str(pick.get("name") or "").strip()
        or (address_text.split(",")[0].strip() if address_text else "")
        or query
    )
    return ResolvedMapLocation(
        lat=float(lat),
        lon=float(lon),
        label=label_text,
        address=address_text or query,
        source=str(pick.get("source") or "trackasia_textsearch"),
    )


def _display_label(
    *,
    place: dict[str, Any],
    fallback_name: str = "",
    fallback_address: str = "",
) -> str:
    name = str(place.get("name") or "").strip()
    if name:
        return name
    if fallback_name:
        return fallback_name
    if fallback_address:
        return fallback_address.split(",")[0].strip()
    return _best_query_label(place)


def _same_coordinates(a: ResolvedMapLocation, b: ResolvedMapLocation) -> bool:
    return round(a.lat, 6) == round(b.lat, 6) and round(a.lon, 6) == round(b.lon, 6)


def _same_place(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    a_name = fold_text(str((a or {}).get("name") or ""))
    b_name = fold_text(str((b or {}).get("name") or ""))
    return bool(a_name and b_name and a_name == b_name)

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
    is_user_facing_place_name,
    normalize_address_text,
    place_city_key,
)
from app.tools.nominatim_tool import search_places as nominatim_search_places
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
# When TrackAsia cannot snap or text-match a named POI, Nominatim (keyword overlap +
# distance) is tried before falling back to raw DB coordinates.
_NOMINATIM_MAP_FALLBACK_MAX_KM = 25.0

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
_GENERIC_MAP_LABELS: frozenset[str] = frozenset(
    {
        "viet nam",
        "vietnam",
        "da nang",
        "thanh pho da nang",
        "quang nam",
        "thanh pho hoi an",
        "hoi an",
        "hai chau",
        "son tra",
        "ngu hanh son",
        "thanh khe",
        "cam le",
        "lien chieu",
        "hoa vang",
        "xa hoa vang",
        "phuong hoa vang",
        "phuong hai chau",
        "phuong son tra",
        "quan hai chau",
        "quan son tra",
    }
)
_GENERIC_LABEL_PREFIXES: tuple[str, ...] = (
    "duong ",
    "pho ",
    "phuong ",
    "quan ",
    "huyen ",
    "xa ",
    "thon ",
    "thanh pho ",
    "tinh ",
)
_PRECISE_ADDRESS_MARKERS: tuple[str, ...] = (
    "duong",
    "pho",
    "tran hung dao",
    "nguyen van linh",
    "vo nguyen giap",
    "dong da",
)

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
      2. Trust the catalog coordinate, then **snap** via TrackAsia reverse geocode,
         but only keep a hit when it is not an admin/country placeholder *and* its
         name tokens overlap the catalog title (otherwise the hosted map sidebar
         replaces your label with a random neighbour POI).
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
      4. Keyword-aligned **Nominatim** search near the catalog coordinate when
         TrackAsia still cannot produce a named hit (keeps coastal/ocean pins from
         collapsing to country-level labels in the hosted viewer).
      5. Raw DB coordinates + catalog label as a last resort (never a mismatched
         neighbour snap).
      6. District/city centroid rows still use the centroid path when no lat/lon exist.

    Nominatim here is map-display only; routing stays on TrackAsia first.
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
            if resolved and _resolved_location_is_safe_for_place(place, resolved):
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
        hits = _trackasia_reverse_hits_cached(anchor[0], anchor[1])
        snapped = _snap_point_from_hits(hits, place, anchor)

        if snapped is not None:
            snapped_address = snapped.address or db_address or _best_query_label(place)
            return ResolvedMapLocation(
                lat=snapped.lat,
                lon=snapped.lon,
                label=_display_label(place=place, fallback_address=snapped_address),
                address=snapped_address,
                source=snapped.source or "trackasia_reverse_geocode",
            )

        name_match = _trackasia_name_aware_geocode(
            place=place,
            queries=queries,
            expected_city_key=city_key,
            anchor=anchor,
            fallback_address=db_address,
            source_suffix="name_match:snap_override" if hits else "name_match:snap_fallback",
        )
        if name_match is not None:
            return name_match

        nearby = _nearby_clear_anchor_for_place(
            place,
            lat=float(lat),
            lon=float(lon),
            require_name_match=True,
        )
        if nearby:
            return nearby

        nearby_loose = _nearby_clear_anchor_for_place(
            place,
            lat=float(lat),
            lon=float(lon),
            require_name_match=False,
        )
        if nearby_loose:
            return nearby_loose

        nom = _nominatim_map_resolve(place, anchor)
        if nom is not None:
            return nom

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
        if (
            resolved
            and not _is_generic_map_location(resolved)
            and _resolved_location_is_safe_for_place(place, resolved)
        ):
            return resolved

    nom = _nominatim_map_resolve(place, anchor=None)
    if nom is not None:
        return nom

    for area in extract_admin_area_keys(place):
        if area in _AREA_CENTROIDS:
            lat0, lon0 = _AREA_CENTROIDS[area]
            nearby = _nearby_clear_anchor_for_place(place, lat=lat0, lon=lon0)
            if nearby:
                return nearby
            return ResolvedMapLocation(
                lat=lat0,
                lon=lon0,
                label=_display_label(place=place),
                address=_best_query_label(place),
                source=f"area_centroid:{area}",
            )

    if city_key and city_key in _AREA_CENTROIDS:
        lat0, lon0 = _AREA_CENTROIDS[city_key]
        nearby = _nearby_clear_anchor_for_place(place, lat=lat0, lon=lon0)
        if nearby:
            return nearby
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

    if not _same_place(a, b) and _same_coordinates(a_loc, b_loc):
        a_alt = resolve_location_for_map(a, prefer_search=True)
        if a_alt and not _same_coordinates(a_alt, b_loc):
            a_loc = a_alt
        b_alt = resolve_location_for_map(b, prefer_search=True)
        if b_alt and not _same_coordinates(a_loc, b_alt):
            b_loc = b_alt

    if not _location_matches_place_keywords(a, a_loc):
        a_alt = resolve_location_for_map(a, prefer_search=True)
        a_loc = a_alt if a_alt and _location_matches_place_keywords(a, a_alt) else None
    if not _location_matches_place_keywords(b, b_loc):
        b_alt = resolve_location_for_map(b, prefer_search=True)
        b_loc = b_alt if b_alt and _location_matches_place_keywords(b, b_alt) else None
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

    name_variants = _searchable_name_variants(name)
    address_variants = _searchable_address_variants(address)

    candidates = [
        ", ".join(part for part in [name, address, district, city] if part),
        ", ".join(part for part in [name, address, city] if part),
        ", ".join(part for part in [name, district, city] if part),
        ", ".join(part for part in [address, district, city] if part),
        ", ".join(part for part in [name, city] if part),
        ", ".join(part for part in [address, city] if part),
    ]
    for name_variant in name_variants:
        candidates.extend(
            [
                ", ".join(part for part in [name_variant, address, city] if part),
                ", ".join(part for part in [name_variant, city] if part),
            ]
        )
        for address_variant in address_variants[:2]:
            candidates.append(", ".join(part for part in [name_variant, address_variant, city] if part))
    for address_variant in address_variants:
        candidates.append(", ".join(part for part in [address_variant, city] if part))

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
    name = _route_token_label(loc)
    if name:
        token = f"{token}@{quote(name, safe='')}"
    return token


def _route_token_label(loc: ResolvedMapLocation) -> str:
    label = (loc.label or "").strip()
    address = normalize_address_text(loc.address)
    if label and address and fold_text(address) not in fold_text(label):
        return f"{label} - {address}"
    return label or address


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
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", folded)
        if len(token) >= _NAME_TOKEN_MIN_LENGTH and token not in _NAME_STOPWORDS
    }
    if "ba na" in folded:
        tokens.add("bana")
    if "nui chua" in folded:
        tokens.add("nuichua")
    return tokens


def _searchable_name_variants(name: str) -> list[str]:
    variants: list[str] = []
    cleaned = re.sub(
        r"\b(?:nha hang|nhà hàng|khach san|khách sạn|quan|quán|restaurant|hotel)\b",
        " ",
        name or "",
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-")
    for candidate in (cleaned, fold_text(cleaned)):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return variants


def _searchable_address_variants(address: str) -> list[str]:
    normalized = normalize_address_text(address)
    if not normalized:
        return []
    variants = [normalized]
    folded = fold_text(normalized)
    if folded and folded not in variants:
        variants.append(folded)
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if parts:
        street_part = parts[0]
        if street_part and street_part not in variants:
            variants.append(street_part)
        folded_street = fold_text(street_part)
        if folded_street and folded_street not in variants:
            variants.append(folded_street)
    return variants


def _is_generic_map_location(loc: ResolvedMapLocation) -> bool:
    label = str(loc.label or "").strip()
    address = str(loc.address or "").strip()
    folded_label = fold_text(label)
    folded_address = fold_text(address)
    if not folded_label and not folded_address:
        return True
    if _has_precise_address(folded_label) or _has_precise_address(folded_address):
        return False
    if folded_label in _GENERIC_MAP_LABELS:
        return True
    primary = folded_label.split(",")[0].strip()
    if primary in {"viet nam", "vietnam"}:
        return True
    if "xa hoa vang" in folded_label or folded_label.startswith("xa hoa vang "):
        return True
    if any(folded_label.startswith(prefix) for prefix in _GENERIC_LABEL_PREFIXES):
        return True
    if not _distinctive_name_tokens(label):
        return True
    return False


def _nominatim_map_resolve(
    place: dict[str, Any],
    anchor: tuple[float, float] | None,
) -> ResolvedMapLocation | None:
    """Pick a Nominatim hit aligned with the catalog row.

    When `anchor` exists we keep the hit near that coordinate. Without an anchor,
    rank by city/name/category signal so named hotels without DB coordinates do not
    collapse to a district centroid.
    """
    db_tokens = _distinctive_name_tokens(str(place.get("name") or ""))
    if not db_tokens:
        return None
    db_address = str(
        place.get("address")
        or place.get("map_formatted_address")
        or place.get("google_formatted_address")
        or ""
    ).strip()
    city_key = place_city_key(place)
    seen: set[tuple[float, float]] = set()
    scored: list[tuple[int, int, int, float, dict[str, Any]]] = []
    for query in _build_geocode_queries(place)[:4]:
        if not query.strip():
            continue
        for item in nominatim_search_places(query, limit=6):
            lat = item.get("lat")
            lon = item.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            key = (round(float(lat), 5), round(float(lon), 5))
            if key in seen:
                continue
            seen.add(key)
            label = str(item.get("name") or "").strip()
            address = str(item.get("address") or "").strip()
            loc = ResolvedMapLocation(
                lat=float(lat),
                lon=float(lon),
                label=label,
                address=address,
                source=str(item.get("source") or "nominatim"),
            )
            if _is_generic_map_location(loc):
                continue
            blob = fold_text(f"{label} {address}")
            blob_city = city_key_from_text(blob)
            if city_key and blob_city and blob_city != city_key:
                continue
            name_score = _name_overlap_score(db_tokens, _distinctive_name_tokens(label))
            if name_score == 0:
                continue
            city_score = 1 if (not city_key or blob_city == city_key or not blob_city) else 0
            category_score = _nominatim_category_score(place, item)
            if anchor is not None:
                dist_km = haversine_km(anchor[0], anchor[1], float(lat), float(lon))
                if dist_km > _NOMINATIM_MAP_FALLBACK_MAX_KM:
                    continue
            else:
                dist_km = 0.0
            scored.append((city_score, name_score, category_score, -dist_km, item))

    if not scored:
        return None
    scored.sort(key=lambda row: (row[0], row[1], row[2], row[3]), reverse=True)
    _, _, _, _, best = scored[0]
    address_text = str(best.get("address") or "").strip()
    nearby_name = str(best.get("name") or "").strip()
    resolved_address = address_text or db_address or _best_query_label(place)
    label = _display_label(
        place=place,
        fallback_name=nearby_name,
        fallback_address=resolved_address,
    )
    return ResolvedMapLocation(
        lat=float(best["lat"]),
        lon=float(best["lon"]),
        label=label,
        address=resolved_address,
        source="nominatim:keyword_match",
    )


def _nominatim_category_score(place: dict[str, Any], item: dict[str, Any]) -> int:
    category = str(place.get("category") or "").strip().lower()
    osm_class = fold_text(str(item.get("osm_class") or ""))
    osm_type = fold_text(str(item.get("osm_type") or ""))
    if category == "accommodation":
        return 2 if osm_type in {"hotel", "hostel", "guest_house", "motel", "resort"} else 0
    if category == "restaurant":
        return 2 if osm_type in {"restaurant", "fast_food", "cafe"} or osm_class == "amenity" else 0
    return 0


def _snap_point_from_hits(
    hits: tuple[dict[str, Any], ...],
    place: dict[str, Any],
    anchor: tuple[float, float],
) -> ResolvedMapLocation | None:
    """Choose a reverse-geocode hit that is specific (not admin/country) and matches the catalog name."""
    if not hits:
        return None
    db_tokens = _distinctive_name_tokens(str(place.get("name") or ""))
    anchor_lat, anchor_lon = anchor
    scored: list[tuple[int, float, dict[str, Any]]] = []
    for item in hits:
        try:
            candidate_lat = float(item.get("lat"))
            candidate_lon = float(item.get("lon"))
        except (TypeError, ValueError):
            continue
        address_text = str(item.get("address") or "").strip()
        label_text = str(item.get("name") or "").strip() or (
            address_text.split(",")[0].strip() if address_text else ""
        )
        candidate = ResolvedMapLocation(
            lat=candidate_lat,
            lon=candidate_lon,
            label=label_text,
            address=address_text,
            source=str(item.get("source") or "trackasia_reverse_geocode"),
        )
        if _is_generic_map_location(candidate):
            continue
        dist_km = haversine_km(anchor_lat, anchor_lon, candidate_lat, candidate_lon)
        if db_tokens:
            name_score = _name_overlap_score(db_tokens, _distinctive_name_tokens(label_text))
            if name_score == 0:
                continue
        else:
            name_score = 1
        scored.append((name_score, -dist_km, item))

    if not scored:
        return None
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    _, _, pick = scored[0]
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


def _has_precise_address(folded_text: str) -> bool:
    if not folded_text:
        return False
    has_number = bool(re.search(r"\b\d+[a-z]?(?:\s*[-/]\s*\d+[a-z]?)?\b", folded_text))
    has_street = any(marker in folded_text for marker in _PRECISE_ADDRESS_MARKERS)
    has_numbered_street_like_text = bool(
        re.search(r"\b\d+[a-z]?(?:\s*[-/]\s*\d+[a-z]?)?\s+[a-z][a-z\s]{4,}", folded_text)
    )
    return has_number and (has_street or has_numbered_street_like_text)


def _name_overlap_score(db_tokens: set[str], candidate_tokens: set[str]) -> int:
    """Count how many identity tokens survive on both sides.

    Pure set intersection is enough because both sides have already been filtered
    through `_distinctive_name_tokens`.
    """
    if not db_tokens or not candidate_tokens:
        return 0
    return len(db_tokens & candidate_tokens)


def _nearby_clear_anchor_for_place(
    place: dict[str, Any],
    *,
    lat: float,
    lon: float,
    require_name_match: bool = False,
) -> ResolvedMapLocation | None:
    hits = reverse_geocode_point(float(lat), float(lon), radius_m=1500, limit=10)
    if not hits:
        return None

    place_name = str(place.get("name") or "").strip()
    enforce_name_match = require_name_match or is_user_facing_place_name(place_name)
    db_tokens = _distinctive_name_tokens(str(place.get("name") or ""))
    expected_city_key = place_city_key(place)
    candidates: list[tuple[int, int, float, dict[str, Any]]] = []
    for item in hits:
        item_lat = item.get("lat")
        item_lon = item.get("lon")
        if not isinstance(item_lat, (int, float)) or not isinstance(item_lon, (int, float)):
            continue
        label = str(item.get("name") or "").strip()
        address = str(item.get("address") or "").strip()
        loc = ResolvedMapLocation(
            lat=float(item_lat),
            lon=float(item_lon),
            label=label,
            address=address,
            source=str(item.get("source") or "trackasia_reverse_geocode"),
        )
        if _is_generic_map_location(loc):
            continue
        blob = fold_text(f"{label} {address}")
        city_score = 1 if expected_city_key and city_key_from_text(blob) == expected_city_key else 0
        name_score = _name_overlap_score(db_tokens, _distinctive_name_tokens(label))
        if enforce_name_match and db_tokens and name_score == 0:
            continue
        distance_km = haversine_km(float(lat), float(lon), float(item_lat), float(item_lon))
        candidates.append((city_score, name_score, -distance_km, item))

    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    _, _, _, best = candidates[0]
    address_text = str(best.get("address") or "").strip()
    nearby_name = str(best.get("name") or "").strip()
    db_label = _display_label(place=place, fallback_name=nearby_name, fallback_address=address_text)
    label = f"{db_label} (moc gan: {nearby_name})" if nearby_name and nearby_name != db_label else db_label
    return ResolvedMapLocation(
        lat=float(best["lat"]),
        lon=float(best["lon"]),
        label=label,
        address=address_text or _best_query_label(place),
        source=str(best.get("source") or "trackasia_reverse_geocode") + ":nearby_anchor",
    )


def _location_matches_place_keywords(
    place: dict[str, Any] | None,
    loc: ResolvedMapLocation | None,
) -> bool:
    if not place or not loc:
        return False
    place_name = str(place.get("name") or "").strip()
    if not is_user_facing_place_name(place_name):
        return False
    db_tokens = _distinctive_name_tokens(place_name)
    if not db_tokens:
        return True
    label_tokens = _distinctive_name_tokens(f"{loc.label} {loc.address}")
    return bool(db_tokens & label_tokens)


def _resolved_location_is_safe_for_place(
    place: dict[str, Any] | None,
    loc: ResolvedMapLocation | None,
) -> bool:
    if not loc:
        return False
    if not place:
        return not _is_generic_map_location(loc)
    place_name = str(place.get("name") or "").strip()
    if not is_user_facing_place_name(place_name):
        return not _is_generic_map_location(loc)
    return _location_matches_place_keywords(place, loc)


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

    scored: list[tuple[int, int, int, float, dict[str, Any]]] = []
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
        candidate_loc = ResolvedMapLocation(
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            label=candidate_name,
            address=candidate_address,
            source=str(item.get("source") or "trackasia_textsearch"),
        )
        clarity_score = 0 if _is_generic_map_location(candidate_loc) else 1
        # Negate distance so sorting descending prefers the closer candidate.
        scored.append((city_score, name_score, clarity_score, -distance_km, item))

    scored.sort(key=lambda row: (row[0], row[1], row[2], row[3]), reverse=True)
    _, best_name, best_clarity, _, best = scored[0]

    # If the DB name offers identity tokens but none of the candidates share any
    # of them, we have no evidence that this is the same venue. Back off to the
    # caller's next fallback rather than committing to a wrong shop.
    if db_tokens and best_name == 0:
        return None
    if best_clarity == 0:
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
def _trackasia_reverse_hits_cached(lat: float, lon: float) -> tuple[dict[str, Any], ...]:
    """Cached TrackAsia reverse-geocode rows near the DB coordinate (hashable key)."""
    try:
        db_lat = round(float(lat), 6)
        db_lon = round(float(lon), 6)
    except (TypeError, ValueError):
        return ()
    hits = reverse_geocode_point(db_lat, db_lon, radius_m=_DB_SNAP_RADIUS_M, limit=8)
    return tuple(hits) if hits else ()


@lru_cache(maxsize=2048)
def _trackasia_geocode_cached(query: str, expected_city_key: str) -> ResolvedMapLocation | None:
    """Geocode `query` through TrackAsia Text Search, biased toward `expected_city_key`."""
    if not query.strip():
        return None
    results = geocode_address(query, limit=_TEXTSEARCH_CANDIDATE_LIMIT)
    if not results:
        return None
    scored: list[tuple[int, int, int, dict[str, Any]]] = []
    for item in results:
        lat = item.get("lat")
        lon = item.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        label_text = str(item.get("name") or "").strip()
        address_text = str(item.get("address") or "").strip()
        loc = ResolvedMapLocation(
            lat=float(lat),
            lon=float(lon),
            label=label_text,
            address=address_text,
            source=str(item.get("source") or "trackasia_textsearch"),
        )
        clarity_score = 0 if _is_generic_map_location(loc) else 1
        precision_score = 1 if _has_precise_address(fold_text(f"{label_text} {address_text}")) else 0
        city_score = 0
        if expected_city_key:
            blob = fold_text(f"{label_text} {address_text}")
            city_score = 1 if city_key_from_text(blob) == expected_city_key else 0
        scored.append((city_score, clarity_score, precision_score, item))

    if not scored:
        return None
    scored.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    _, clarity_score, _, pick = scored[0]
    if clarity_score == 0:
        return None
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

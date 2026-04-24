from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
import re
from typing import Any, List, Optional

from app.agents.intake_agent import evaluate_intake
from app.agents.context_builder_agent import build_context_from_places
from app.agents.llm_agent import generate_answer
from app.agents.itinerary_builder import (
    _planned_city_keys_per_day,
    build_trip_plan_payload,
)
from app.services.place_fit_scoring import local_catalog_sufficient
from app.services.query_utils import extract_trip_days
from app.services.route_utils import (
    place_map_url,
    resolve_point_for_map,
    resolve_segment_points,
    segment_map_url,
)
from app.services.vector_rag import format_chunk_context, retrieve_chunk_hits
from app.tools.elasticsearch_tool import search_processed_places
from app.tools.local_catalog_tool import (
    MIN_LOCAL_FIT_TO_SKIP_EXTERNAL,
    LOCAL_FETCH_MULTIPLIER,
)
from app.tools.trackasia_tool import GeoPoint, configured_route_modes, estimate_route

_AREA_TOKENS = (
    "hai chau",
    "son tra",
    "ngu hanh son",
    "thanh khe",
    "cam le",
    "hoa vang",
    "hoi an",
    "tam ky",
)
_DA_NANG_AREA_KEYS = {
    "hai chau",
    "son tra",
    "ngu hanh son",
    "thanh khe",
    "cam le",
    "lien chieu",
    "hoa vang",
}
_QUANG_NAM_AREA_KEYS = {
    "hoi_an",
    "hoi an",
    "tam_ky",
    "tam ky",
    "dien_ban",
    "dien ban",
    "duy_xuyen",
    "duy xuyen",
    "dai_loc",
    "dai loc",
    "thang_binh",
    "thang binh",
    "tien_phuoc",
    "tien phuoc",
    "nui_thanh",
    "nui thanh",
}


@dataclass
class RetrievalArtifacts:
    places: List[dict]
    trace: List[str]
    local_candidates_considered: int
    weather: Optional[dict] = None
    guide: str = ""
    transport: Optional[List[str]] = None
    recommended_hotel: Optional[dict] = None
    mobility_plan: Optional[dict] = None


@dataclass
class SourceArtifacts:
    sources: List[dict]
    verified_places: List[dict]
    route_plan: List[dict]
    grounding: dict[str, Any]


def retrieve_trip_artifacts(
    query: str,
    category: str | None = None,
    top_k: int = 5,
    with_plan: bool = False,
) -> RetrievalArtifacts:
    trace: List[str] = []

    fetch_k = max(top_k * LOCAL_FETCH_MULTIPLIER, top_k + 4, 8)
    trace.append("elasticsearch_index")
    trace.append("hybrid_vector_rag")
    if category == "restaurant":
        local_ranked = search_processed_places(query=query, source_kind="restaurants", top_k=fetch_k)
    else:
        local_ranked = search_processed_places(query=query, source_kind="destinations", top_k=fetch_k)
    if category:
        local_ranked = [p for p in local_ranked if str(p.get("category") or "").lower() == category.lower()]

    local_enough = local_catalog_sufficient(
        local_ranked,
        top_k=top_k,
        min_fit=MIN_LOCAL_FIT_TO_SKIP_EXTERNAL,
    )
    if local_enough:
        trace.append("db_only_local_sufficient")
    else:
        trace.append("db_only_local_limited_results")
    places = local_ranked[:top_k]
    weather = None
    guide = ""

    if category:
        places = [p for p in places if str(p.get("category") or "").lower() == category.lower()]
    if with_plan and not category:
        # Plan rule: pick attractions first, then add restaurants only if attractions are sufficient.
        days = extract_trip_days(query) or 1
        need_tourism = max(1, days)
        need_entertainment = max(1, days)
        target_city_keys = _target_city_keys_from_query(query)
        city_day_counts = _city_day_counts_from_query(query=query, days=days, target_city_keys=target_city_keys)
        if len(target_city_keys) > 1:
            tourism_pool = _collect_multi_city_places(
                query=query,
                source_kind="destinations",
                city_day_counts=city_day_counts,
                suffix="diem den du lich",
                per_day_need=1,
                extra_per_city=3,
                fallback_top_k=max(need_tourism + 8, top_k),
            )
            entertainment_pool = _collect_multi_city_places(
                query=query,
                source_kind="entertainment",
                city_day_counts=city_day_counts,
                suffix="vui choi giai tri",
                per_day_need=1,
                extra_per_city=3,
                fallback_top_k=max(need_entertainment + 8, top_k),
            )
        else:
            tourism_pool = search_processed_places(
                query=f"{query} diem den du lich",
                source_kind="destinations",
                top_k=max(need_tourism + 6, top_k),
            )
            entertainment_pool = search_processed_places(
                query=f"{query} vui choi giai tri",
                source_kind="entertainment",
                top_k=max(need_entertainment + 6, top_k),
            )
        target_city = target_city_keys[0] if len(target_city_keys) == 1 else ""
        if target_city_keys:
            tourism_pool = [p for p in tourism_pool if _city_match_any(p, target_city_keys)]
            entertainment_pool = [p for p in entertainment_pool if _city_match_any(p, target_city_keys)]
        if len(target_city_keys) > 1:
            tourism = _take_unique(
                _take_segmented_city_candidates(
                    items=tourism_pool,
                    city_day_counts=city_day_counts,
                    per_city_extra=1,
                ),
                limit=need_tourism + len(city_day_counts),
            )
            entertainment = _take_unique(
                _take_segmented_city_candidates(
                    items=entertainment_pool,
                    city_day_counts=city_day_counts,
                    per_city_extra=1,
                ),
                limit=need_entertainment + len(city_day_counts),
            )
        else:
            tourism = _take_unique(tourism_pool, limit=need_tourism + 2)
            entertainment = _take_unique(entertainment_pool, limit=need_entertainment + 2)

        if len(tourism) < need_tourism or len(entertainment) < need_entertainment:
            trace.append("db_only_not_enough_attractions")
            places = _take_unique(tourism + entertainment, limit=max(top_k, days * 2))
        else:
            trace.append("db_only_attractions_ready")
            if len(target_city_keys) > 1:
                restaurant_pool = _collect_multi_city_places(
                    query=query,
                    source_kind="restaurants",
                    city_day_counts=city_day_counts,
                    suffix="nha hang am thuc",
                    per_day_need=3,
                    extra_per_city=4,
                    fallback_top_k=max(top_k, days * 4),
                )
                accommodation_pool = _collect_multi_city_places(
                    query=query,
                    source_kind="accommodations",
                    city_day_counts=city_day_counts,
                    suffix="khach san luu tru",
                    per_day_need=1,
                    extra_per_city=3,
                    fallback_top_k=max(top_k, days * 2, 8),
                )
            else:
                restaurant_pool = search_processed_places(
                    query=f"{query} nha hang am thuc",
                    source_kind="restaurants",
                    top_k=max(top_k, days * 3),
                )
                accommodation_pool = search_processed_places(
                    query=f"{query} khach san luu tru",
                    source_kind="accommodations",
                    top_k=max(top_k, days * 2, 8),
                )
            if target_city_keys:
                # Strict city consistency when query asks for a concrete city set.
                restaurant_pool = [p for p in restaurant_pool if _city_match_any(p, target_city_keys)]
                accommodation_pool = [p for p in accommodation_pool if _city_match_any(p, target_city_keys)]
            if len(target_city_keys) > 1:
                restaurants = []
                accommodations = []
                for city_key, day_count in city_day_counts:
                    city_attractions = [p for p in tourism + entertainment if _city_match(p, city_key)]
                    city_restaurant_pool = [p for p in restaurant_pool if _city_match(p, city_key)]
                    city_accommodation_pool = [p for p in accommodation_pool if _city_match(p, city_key)]
                    restaurants.extend(
                        _pick_restaurants_near_attractions(
                            restaurants=city_restaurant_pool,
                            attractions=city_attractions,
                            limit=max(3, day_count * 3),
                        )
                    )
                    accommodations.extend(
                        _pick_accommodations_near_attractions(
                            accommodations=city_accommodation_pool,
                            attractions=city_attractions,
                            limit=max(2, day_count + 1),
                        )
                    )
                restaurants = _take_unique(restaurants, limit=max(days * 3, top_k))
                accommodations = _take_unique(
                    accommodations,
                    limit=max(4, min(len(accommodation_pool), days + 2)),
                )
            else:
                restaurants = _pick_restaurants_near_attractions(
                    restaurants=restaurant_pool,
                    attractions=tourism + entertainment,
                    limit=max(days * 3, top_k),
                )
                accommodations = _pick_accommodations_near_attractions(
                    accommodations=accommodation_pool,
                    attractions=tourism + entertainment,
                    limit=max(4, min(len(accommodation_pool), days + 2)),
                )
            # Keep enough records for planner: tourism + entertainment + 3 meals/day.
            required_total = len(tourism) + len(entertainment) + max(days * 3, 0) + len(accommodations)
            places = _take_unique(
                tourism + entertainment + restaurants + accommodations,
                limit=max(top_k, required_total),
            )

    transport = _transport_suggestions_from_weather(weather)
    map_selection = {"recommended_hotel": None, "mobility_plan": None}

    return RetrievalArtifacts(
        places=places,
        trace=trace,
        local_candidates_considered=len(local_ranked),
        weather=weather,
        guide=guide,
        transport=transport,
        recommended_hotel=map_selection.get("recommended_hotel"),
        mobility_plan=map_selection.get("mobility_plan"),
    )


def build_context_payload(
    query: str,
    places: List[dict],
    weather: dict | None = None,
    transport: List[str] | None = None,
    recommended_hotel: dict | None = None,
    mobility_plan: dict | None = None,
    guide: str = "",
) -> str:
    return _build_augmented_context(
        places=places,
        query=query,
        weather=weather,
        transport=transport,
        recommended_hotel=recommended_hotel,
        mobility_plan=mobility_plan,
        guide=guide,
    )


def build_research_output(query: str, places: List[dict], transport: List[str] | None) -> str:
    return _build_research_summary(query=query, places=places, transport=transport)


def build_itinerary_artifacts(query: str, places: List[dict], strict_mode: bool = False) -> dict[str, Any]:
    return build_trip_plan_payload(query=query, places=places, strict_mode=strict_mode)


def build_source_artifacts(places: List[dict], local_candidates_considered: int) -> SourceArtifacts:
    sources = [
        {
            "name": p.get("name"),
            "category": p.get("category"),
            "address": p.get("address"),
            "city": p.get("city"),
            "lat": p.get("lat"),
            "lon": p.get("lon"),
            "source": p.get("source"),
            "map_url": _map_url(p.get("lat"), p.get("lon"), p.get("name"), p.get("address"), p.get("city")),
            "customer_fit_score": p.get("customer_fit_score"),
            "intent_match_ratio": p.get("intent_match_ratio"),
            "retrieval_relevance_pct": p.get("retrieval_relevance_pct"),
            "retrieval_tier": p.get("retrieval_tier"),
            "detail_url": p.get("detail_url"),
            "item_id": p.get("item_id"),
            "source_category_code": p.get("source_category_code"),
            "map_provider": p.get("map_provider"),
            "map_place_id": p.get("map_place_id"),
            "map_place_uri": p.get("map_place_uri"),
            "map_business_status": p.get("map_business_status"),
            "google_place_id": p.get("google_place_id"),
            "google_maps_uri": p.get("google_maps_uri"),
            "google_business_status": p.get("google_business_status"),
            "coordinate_source": p.get("coordinate_source"),
            "coordinate_confidence": p.get("coordinate_confidence"),
            "intent_tags": p.get("intent_tags"),
            "planner_role": p.get("planner_role"),
            "primary_area_key": p.get("primary_area_key"),
            "city_key": p.get("city_key"),
            "density_bucket": p.get("density_bucket"),
            "verification_status": p.get("verification_status"),
            "rag_snippets": p.get("rag_snippets"),
        }
        for p in places
    ]
    verified_places = _build_verified_places(sources)
    route_plan = _build_route_plan(verified_places)
    grounding = {
        "strict_whitelist_enabled": True,
        "retrieved_place_count": len(verified_places),
        "verified_official_count": len(
            [item for item in verified_places if str(item.get("verification_status") or "") in {"official_source", "catalog_source"}]
        ),
        "policy": "answer chỉ được phép dùng địa điểm nằm trong sources/verified_places",
        "retrieval": {
            "db_only_mode": False,
            "vector_rag_enabled": True,
            "local_candidates_considered": local_candidates_considered,
            "min_local_fit_threshold": MIN_LOCAL_FIT_TO_SKIP_EXTERNAL,
            "customer_fit_score_scale": "0-100 (higher = better match to query intent)",
        },
    }
    return SourceArtifacts(
        sources=sources,
        verified_places=verified_places,
        route_plan=route_plan,
        grounding=grounding,
    )


def build_grounded_answer(query: str, context: str, verified_places: List[dict]) -> str:
    allowed = [str(p.get("name") or "").strip() for p in verified_places if str(p.get("name") or "").strip()]
    place_meta = {
        str(p.get("name") or "").strip(): {"source": p.get("source"), "map_url": p.get("map_url")}
        for p in verified_places
        if str(p.get("name") or "").strip()
    }
    answer = generate_answer(query=query, context=context, allowed_place_names=allowed, place_meta=place_meta)
    return _attach_map_links(answer, verified_places)


def build_coordinator_output(query: str, itinerary: str, transport: List[str] | None) -> str:
    return _build_coordinator_plan(
        query=query,
        itinerary=itinerary,
        transport=transport,
    )


def _transport_suggestions_from_weather(weather: dict | None) -> List[str] | None:
    if not weather:
        return None
    desc = str(weather.get("description") or "").lower()
    windy = float(weather.get("wind_kmh") or 0) >= 25
    rainy = "mưa" in desc or "giông" in desc

    if rainy:
        tips = [
            "Ưu tiên Grab/taxi hoặc ô tô (tránh đi bộ xa vì mưa).",
            "Nếu đi xe máy: mặc áo mưa tốt + bọc chống nước cho điện thoại.",
        ]
        if windy:
            tips.append("Gió lớn: hạn chế xe máy/đi biển, ưu tiên ô tô.")
        return tips

    if windy:
        return [
            "Gió khá mạnh: cân nhắc Grab/taxi khi đi xa; nếu xe máy thì chạy chậm và đội mũ bảo hiểm kín.",
        ]

    return ["Thời tiết ổn: ưu tiên đi bộ quãng ngắn + xe máy/Grab cho quãng xa."]


def _build_augmented_context(
    places: List[dict],
    query: str,
    weather: dict | None,
    transport: List[str] | None,
    recommended_hotel: dict | None,
    mobility_plan: dict | None,
    guide: str,
) -> str:
    base = build_context_from_places(places)
    parts: List[str] = [base] if base else []
    vector_chunk_context = format_chunk_context(retrieve_chunk_hits(query=query, top_k=6), limit=6)
    if vector_chunk_context:
        parts.append(vector_chunk_context)

    if weather:
        w_line = f"{weather.get('location','')}: {weather.get('description','')}, {weather.get('temp_c','?')}°C"
        parts.append("## Thời tiết (Open-Meteo)\n" + w_line.strip())
    if guide:
        parts.append("## Travel guide (Wikivoyage)\n" + guide)

    if transport:
        parts.append("## Gợi ý phương tiện di chuyển\n" + "\n".join([f"- {t}" for t in transport]))
    if recommended_hotel:
        parts.append(
            "## Khách sạn thuận tiện nhất\n"
            f"- Tên: {recommended_hotel.get('name','')}\n"
            f"- Địa chỉ: {recommended_hotel.get('address','')}\n"
            f"- Lý do: {recommended_hotel.get('reason','')}"
        )
    if mobility_plan:
        parts.append(
            "## Phân tích di chuyển MyTomTom\n"
            f"- Fastest mode: {mobility_plan.get('fastest_mode_label','')}\n"
            f"- ETA trung bình: ~{mobility_plan.get('avg_eta_min','?')} phút"
        )

    # Provide explicit instruction so LLM ranks by weather.
    parts.append(
        "## Yêu cầu suy luận\n"
        "Chỉ chọn địa điểm xuất hiện trong context retrieval, không thêm địa điểm mới."
    )

    return "\n\n".join([p for p in parts if p.strip()]).strip()


def _build_verified_places(sources: List[dict]) -> List[dict]:
    out: List[dict] = []
    for s in sources:
        lat = s.get("lat")
        lon = s.get("lon")
        verification_status = str(s.get("verification_status") or "")
        has_coords = isinstance(lat, (int, float)) and isinstance(lon, (int, float))
        if not has_coords and verification_status not in {"official_source", "catalog_source"}:
            continue
        item = {
            "name": s.get("name"),
            "category": s.get("category"),
            "address": s.get("address"),
            "city": s.get("city"),
            "source": s.get("source"),
            "map_url": s.get("map_url"),
            "is_verified_external": has_coords,
            "customer_fit_score": s.get("customer_fit_score"),
            "intent_match_ratio": s.get("intent_match_ratio"),
            "retrieval_relevance_pct": s.get("retrieval_relevance_pct"),
            "retrieval_tier": s.get("retrieval_tier"),
            "detail_url": s.get("detail_url"),
            "item_id": s.get("item_id"),
            "source_category_code": s.get("source_category_code"),
            "intent_tags": s.get("intent_tags"),
            "planner_role": s.get("planner_role"),
            "primary_area_key": s.get("primary_area_key"),
            "city_key": s.get("city_key"),
            "density_bucket": s.get("density_bucket"),
            "verification_status": verification_status,
        }
        if has_coords:
            item["lat"] = float(lat)
            item["lon"] = float(lon)
        out.append(item)
    return out


def _build_route_plan(verified_places: List[dict]) -> List[dict]:
    points: List[dict[str, Any]] = []
    for place in verified_places:
        if not resolve_point_for_map(place):
            continue
        points.append(dict(place))
        if len(points) >= 4:
            break
    if len(points) < 2:
        return []
    routes: List[dict] = []
    for i in range(len(points) - 1):
        a = points[i]
        b = points[i + 1]
        a_point, b_point = resolve_segment_points(a, b)
        if not a_point or not b_point:
            continue
        o = GeoPoint(lat=float(a_point[0]), lon=float(a_point[1]))
        d = GeoPoint(lat=float(b_point[0]), lon=float(b_point[1]))
        fastest = _fastest_mode(o, d)
        raw_distance_km = float(fastest["distance_m"] / 1000)
        same_place = (
            str(a.get("name") or "").strip().lower()
            == str(b.get("name") or "").strip().lower()
        )
        shown_distance_km = 0.0 if same_place and raw_distance_km <= 0.01 else raw_distance_km
        if not same_place and shown_distance_km < 0.2:
            shown_distance_km = 0.2
        routes.append(
            {
                "from": a["name"],
                "to": b["name"],
                "from_map_url": a.get("map_url") or place_map_url(a),
                "to_map_url": b.get("map_url") or place_map_url(b),
                "segment_map_url": segment_map_url(a, b),
                "distance_km": round(shown_distance_km, 2),
                "eta_min": fastest["eta_min"],
                "recommended_mode": fastest["mode"],
                "mode_label": fastest["mode_label"],
                "routing_source": fastest["routing_source"],
            }
        )
    return routes


def _fastest_mode(origin: GeoPoint, destination: GeoPoint) -> dict:
    mode_labels = {
        "car": "ô tô/Grab",
        "truck": "xe tải",
        "scooter": "xe máy",
        "pedestrian": "đi bộ",
    }
    best: dict | None = None
    for mode in configured_route_modes():
        est = estimate_route(origin, destination, travel_mode=mode)
        if not est:
            continue
        cand = {
            "mode": mode,
            "mode_label": mode_labels.get(mode, mode),
            "eta_min": max(1, int(round(est.travel_time_s / 60))),
            "distance_m": float(est.distance_m),
            "routing_source": "trackasia",
        }
        if best is None or cand["eta_min"] < best["eta_min"]:
            best = cand
    if best:
        return best

    # Fallback when TrackAsia routing is unavailable.
    dist_m = _haversine_m(origin.lat, origin.lon, destination.lat, destination.lon)
    # Assume city average speed 28 km/h for scooter
    eta_min = max(1, int(round((dist_m / 1000) / 28 * 60)))
    return {
        "mode": "scooter",
        "mode_label": "xe máy (ước tính)",
        "eta_min": eta_min,
        "distance_m": dist_m,
        "routing_source": "haversine_estimate",
    }


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = radians(lat1)
    p2 = radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _map_url(
    lat: object,
    lon: object,
    name: object = "",
    address: object = "",
    city: object = "",
) -> Optional[str]:
    url = place_map_url(
        {
            "name": str(name or "").strip(),
            "address": str(address or "").strip(),
            "city": str(city or "").strip(),
            "lat": lat,
            "lon": lon,
        }
    )
    return url or None


def _target_city_key_from_query(query: str) -> str:
    keys = _target_city_keys_from_query(query)
    if not keys:
        return "da_nang"
    return keys[0]


def _target_city_keys_from_query(query: str) -> list[str]:
    q = query.lower()
    mentions: list[tuple[int, str]] = []
    variants = {
        "da_nang": ("đà nẵng", "da nang", "danang"),
        "hoi_an": ("hội an", "hoi an"),
        "quang_nam": ("quảng nam", "quang nam"),
    }
    for city_key, names in variants.items():
        positions = [q.find(name) for name in names if name in q]
        if positions:
            mentions.append((min(positions), city_key))
    mentions.sort()
    keys = [city_key for _, city_key in mentions]
    if not keys:
        return ["da_nang"]
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _city_day_counts_from_query(query: str, days: int, target_city_keys: list[str]) -> list[tuple[str, int]]:
    if days <= 0:
        return []
    fallback_city = target_city_keys[0] if target_city_keys else "da_nang"
    planned = _planned_city_keys_per_day(query=query, total_days=days, fallback_city_key=fallback_city)
    counts: list[tuple[str, int]] = []
    for city_key in planned:
        if not city_key:
            continue
        if counts and counts[-1][0] == city_key:
            prev_city, prev_count = counts[-1]
            counts[-1] = (prev_city, prev_count + 1)
        else:
            counts.append((city_key, 1))
    if counts:
        return counts
    return [(city_key, 1) for city_key in target_city_keys] or [("da_nang", days)]


def _collect_multi_city_places(
    query: str,
    source_kind: str,
    city_day_counts: list[tuple[str, int]],
    suffix: str,
    per_day_need: int,
    extra_per_city: int,
    fallback_top_k: int,
) -> list[dict]:
    scoped: list[dict] = []
    min_fetch = {
        "destinations": 24,
        "entertainment": 18,
        "restaurants": 24,
        "accommodations": 16,
    }.get(source_kind, 16)
    for city_key, day_count in city_day_counts:
        city_label = _city_label_for_query(city_key)
        city_fetch = max(day_count * per_day_need + extra_per_city, extra_per_city + 2, min_fetch)
        city_rows = search_processed_places(
            query=_city_scoped_query(city_label=city_label, day_count=day_count, suffix=suffix, original_query=query),
            source_kind=source_kind,
            top_k=city_fetch,
        )
        city_rows = [item for item in city_rows if _city_match(item, city_key)]
        scoped.extend(_take_unique(city_rows, limit=city_fetch))

    fallback = search_processed_places(
        query=f"{query} {suffix}",
        source_kind=source_kind,
        top_k=fallback_top_k,
    )
    return _take_unique(scoped + fallback, limit=max(len(scoped) + len(fallback), fallback_top_k))


def _city_label_for_query(city_key: str) -> str:
    labels = {
        "da_nang": "Da Nang",
        "hoi_an": "Hoi An",
        "quang_nam": "Quang Nam",
    }
    return labels.get(city_key, city_key.replace("_", " ").title())


def _city_scoped_query(city_label: str, day_count: int, suffix: str, original_query: str) -> str:
    intake = evaluate_intake(original_query or "")
    interests = str(intake.collected.get("interests") or "").replace(",", " ").strip()
    parts = [city_label, f"{max(1, day_count)} ngay", interests, suffix]
    return " ".join(part for part in parts if part).strip()


def _city_match(p: dict, target_city: str) -> bool:
    explicit_city_key = str(p.get("city_key") or "").strip().lower()
    primary_area_key = str(p.get("primary_area_key") or "").strip().lower()
    if explicit_city_key:
        if target_city == "da_nang":
            return explicit_city_key == "da_nang"
        if target_city == "hoi_an":
            return explicit_city_key == "hoi_an"
        if target_city == "quang_nam":
            return explicit_city_key in {"quang_nam", "hoi_an"}
    if primary_area_key:
        if target_city == "da_nang" and primary_area_key in _DA_NANG_AREA_KEYS:
            return True
        if target_city == "hoi_an" and primary_area_key in {"hoi_an"}:
            return True
        if target_city == "quang_nam" and primary_area_key in _QUANG_NAM_AREA_KEYS:
            return True

    blob = " ".join(
        [
            str(p.get("city") or ""),
            str(p.get("address") or ""),
            str(p.get("district") or ""),
        ]
    ).lower()
    has_hoi_an = "hoi an" in blob
    has_quang_nam = "quang nam" in blob or "quảng nam" in blob
    has_tam_ky = "tam ky" in blob or "tam kỳ" in blob
    has_da_nang = "da nang" in blob or "đà nẵng" in blob
    da_nang_districts = (
        "hai chau",
        "hải châu",
        "son tra",
        "sơn trà",
        "thanh khe",
        "thanh khê",
        "ngu hanh son",
        "ngũ hành sơn",
        "cam le",
        "cẩm lệ",
        "lien chieu",
        "liên chiểu",
        "hoa vang",
        "hòa vang",
    )
    in_da_nang_area = has_da_nang or any(d in blob for d in da_nang_districts)
    if target_city == "hoi_an":
        return has_hoi_an
    if target_city == "da_nang":
        return in_da_nang_area and not (has_hoi_an or has_quang_nam or has_tam_ky)
    if target_city == "quang_nam":
        return has_quang_nam or has_hoi_an or has_tam_ky
    return True


def _city_match_any(p: dict, target_cities: list[str]) -> bool:
    if not target_cities:
        return True
    return any(_city_match(p, city) for city in target_cities)


def _take_unique(items: List[dict], limit: int) -> List[dict]:
    out: List[dict] = []
    seen: set[str] = set()
    for p in items:
        key = str(p.get("name") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= limit:
            break
    return out


def _take_segmented_city_candidates(
    items: List[dict],
    city_day_counts: list[tuple[str, int]],
    per_city_extra: int,
) -> List[dict]:
    selected: List[dict] = []
    for city_key, day_count in city_day_counts:
        city_items = [item for item in items if _city_match(item, city_key)]
        selected.extend(_take_unique(city_items, limit=max(1, day_count + per_city_extra)))
    return selected


def _pick_restaurants_near_attractions(restaurants: List[dict], attractions: List[dict], limit: int) -> List[dict]:
    if not attractions:
        return []
    attraction_areas = _collect_attraction_areas(attractions)
    scored: List[tuple[float, dict]] = []
    for r in restaurants:
        base = float(r.get("customer_fit_score") or 0)
        addr = str(r.get("address") or "").lower()
        near_bonus = 0.0
        if attraction_areas and any(t in addr for t in attraction_areas):
            near_bonus = 12.0
        scored.append((base + near_bonus, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return _take_unique([x[1] for x in scored], limit=limit)


def _pick_accommodations_near_attractions(accommodations: List[dict], attractions: List[dict], limit: int) -> List[dict]:
    if not attractions:
        return []
    attraction_areas = _collect_attraction_areas(attractions, include_district=True)
    scored: List[tuple[float, dict]] = []
    for hotel in accommodations:
        base = float(hotel.get("customer_fit_score") or 0)
        addr = str(hotel.get("address") or "").lower()
        district = str(hotel.get("district") or "").lower()
        star_bonus = _parse_star_rating(hotel.get("star_rating")) * 1.2
        near_bonus = 0.0
        if attraction_areas and any(token in addr or token in district for token in attraction_areas):
            near_bonus = 14.0
        scored.append((base + near_bonus + star_bonus, hotel))
    scored.sort(key=lambda x: x[0], reverse=True)
    return _take_unique([x[1] for x in scored], limit=limit)


def _parse_star_rating(value: object) -> float:
    text = str(value or "").lower()
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except Exception:
        return 0.0


def _attach_map_links(answer: str, verified_places: List[dict]) -> str:
    return answer


def _build_research_summary(query: str, places: List[dict], transport: List[str] | None) -> str:
    destination = _target_city_key_from_query(query).replace("_", " ").title()
    days = extract_trip_days(query) or 1
    attractions = [p for p in places if str(p.get("category") or "").lower() != "restaurant"]
    attractions = _take_unique(attractions, limit=6)
    stays = _pick_stay_areas(places, limit=3)
    destination_upper = destination.upper()
    lines = [
        f"TONG QUAN NGHIEN CUU {destination_upper}",
        "",
        f"{destination} la diem den phu hop cho hanh trinh {days} ngay voi trai nghiem can bang giua tham quan, am thuc va van hoa.",
        "",
        "CAC DIEM NOI BAT:",
    ]
    if attractions:
        for p in attractions:
            name = str(p.get("name") or "").strip()
            category = str(p.get("category") or "").strip()
            address = str(p.get("address") or "").strip()
            if name:
                tail = f" - {category}" if category else ""
                if address:
                    tail += f" ({address})"
                lines.append(f"• {name}{tail}")
    else:
        lines.append("• Dang thieu du lieu diem tham quan trong database.")

    lines.append("")
    lines.append("KHU VUC NEN O:")
    if stays:
        lines.extend([f"• {a}" for a in stays])
    else:
        lines.append("• Trung tam thanh pho (de di chuyen)")

    lines.append("")
    lines.append("TRAVEL TIPS:")
    if transport:
        lines.extend([f"• {t}" for t in transport[:6]])
    else:
        lines.extend(
            [
                "• Uu tien di som tai cac diem dong khach.",
                "• Kiem tra gio mo cua truoc khi di.",
                "• Du tru 10-20% thoi gian cho di chuyen.",
            ]
        )
    return "\n".join(lines).strip()


def _pick_stay_areas(places: List[dict], limit: int = 3) -> List[str]:
    scores: dict[str, int] = {}
    for p in places:
        addr = str(p.get("address") or "").lower()
        for token in _AREA_TOKENS:
            if token in addr:
                scores[token] = scores.get(token, 0) + 1
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [name.title() for name, _ in ranked]


def _collect_attraction_areas(
    attractions: List[dict],
    *,
    include_district: bool = False,
) -> set[str]:
    attraction_areas: set[str] = set()
    for attraction in attractions:
        fields = [str(attraction.get("address") or "").lower()]
        if include_district:
            fields.append(str(attraction.get("district") or "").lower())
        for token in _AREA_TOKENS:
            if any(token in field for field in fields):
                attraction_areas.add(token)
    return attraction_areas


def _build_coordinator_plan(
    query: str,
    itinerary: str,
    transport: List[str] | None,
) -> str:
    days = extract_trip_days(query) or 1
    destination = _target_city_key_from_query(query).replace("_", " ").title()
    lines = [
        f"KE HOACH DU LICH {destination.upper()} HOAN CHINH",
        "",
        "TOM TAT DIEU HANH:",
        f"Ke hoach {days} ngay tai {destination} duoc tong hop tu Research Agent va Itinerary Agent,",
        "can bang trai nghiem tham quan, am thuc, van hoa va di chuyen thuc te.",
        "",
        "CAC PHAN OUTPUT:",
        "1) Research Summary: Tong quan diem den, diem noi bat, khu vuc nen o, travel tips.",
        "2) Itinerary Chi Tiet: Ke hoach sang/trua/chieu/toi cho tung ngay.",
        "3) Coordinator Plan: Chien luoc trien khai, rui ro, checklist hanh dong.",
        "",
        "TOP 3 TRAI NGHIEM NEN UU TIEN:",
        "",
        *_top_3_priorities_from_itinerary(itinerary),
        "",
        "TOM TAT LICH THEO NGAY:",
        *_compact_day_overview(itinerary),
        "",
        "CHIEN LUOC DI CHUYEN & TRIEN KHAI:",
        "• Uu tien kham pha theo cum khu vuc de giam thoi gian di chuyen.",
        "• Ket hop lich co cau truc voi khoang thoi gian linh hoat.",
        "• Chi su dung dia diem da duoc truy xuat/kiem chung trong he thong.",
        "",
        "THACH THUC CO THE GAP & CACH XU LY:",
    ]
    if transport:
        lines.extend([f"• {t}" for t in transport[:3]])
    else:
        lines.extend(
            [
                "• Gio cao diem: di chuyen som hon 30-45 phut.",
                "• Thoi tiet xau: uu tien diem trong nha va phuong an taxi/Grab.",
            ]
        )
    lines.extend(
        [
            "",
            "CHECKLIST TRUOC CHUYEN DI:",
            "□ Xac nhan gio mo cua cac diem truoc ngay di.",
            "□ Chot thu tu diem uu tien cho tung ngay.",
            "□ Luu san map offline va du phong pin/sac du phong.",
            "",
            "KET QUA KY VONG: Chuyen di duoc to chuc mach lac, tiet kiem thoi gian len ke hoach va toi uu trai nghiem tai diem den.",
        ]
    )
    return "\n".join(lines).strip()


def _top_3_priorities_from_itinerary(itinerary: str) -> List[str]:
    picked: List[str] = []
    for line in itinerary.splitlines():
        s = line.strip()
        if "Hanh dong:" in s and "tai " in s:
            candidate = s.rsplit("tai ", 1)[-1].strip()
        elif "(tham quan):" in s or "(giai tri):" in s:
            candidate = s.split(":", 1)[1].strip() if ":" in s else s
        else:
            candidate = ""
        if candidate:
            candidate = re.sub(r"\s*—\s*Nguon:.*$", "", candidate).strip()
            if candidate and candidate not in picked:
                picked.append(candidate)
        if len(picked) >= 3:
            break
    if not picked:
        return [
            "1. Uu tien 1: Cac diem tham quan noi bat trong trung tam.",
            "2. Uu tien 2: Cum diem giai tri gan nhau de toi uu di chuyen.",
            "3. Uu tien 3: Khung gio toi cho an uong va trai nghiem dia phuong.",
        ]
    return [f"{i + 1}. {v}" for i, v in enumerate(picked[:3])]


def _compact_day_overview(itinerary: str) -> List[str]:
    out: List[str] = []
    for line in itinerary.splitlines():
        s = line.strip()
        if s.startswith("NGAY "):
            out.append(f"• {s}")
        if len(out) >= 7:
            break
    if not out:
        return ["• Lich trinh chi tiet da duoc tao theo tung ngay trong Itinerary Agent."]
    return out

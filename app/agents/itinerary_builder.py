from __future__ import annotations

import re
from math import cos, radians, sqrt
from random import SystemRandom
from typing import List

from app.services.route_utils import (
    haversine_km as _shared_haversine_km,
    place_map_url as _shared_place_map_url,
    resolve_point_for_map as _shared_resolve_point_for_map,
    resolve_segment_points as _shared_resolve_segment_points,
    segment_map_url as _shared_segment_map_url,
)
from app.services.external_place_store import cache_external_places
from app.services.place_metadata import fold_text as _fold
from app.services.place_metadata import is_user_facing_place_name
from app.services.query_utils import extract_trip_days
from app.tools.trackasia_tool import GeoPoint, configured_route_modes, estimate_route
from app.tools.nominatim_tool import search_places

_RNG = SystemRandom()
_CITY_LANDMARK_HINTS: dict[str, tuple[str, ...]] = {
    "Da Nang": (
        "ba na",
        "cau rong",
        "linh ung",
        "ngu hanh",
        "son tra",
        "bao tang cham",
    ),
    "Hoi An": (
        "pho co",
        "chua cau",
        "hoi quan",
        "an bang",
        "thanh ha",
        "hoi an",
    ),
    "Quang Nam": (
        "my son",
        "thanh dia",
        "cu lao cham",
        "tam ky",
        "hoi an",
        "cham island",
    ),
}

# District proximity graph (Da Nang focus) for soft distance penalties.
_DISTRICT_NEIGHBORS: dict[str, set[str]] = {
    "hai chau": {"son tra", "thanh khe", "ngu hanh son", "cam le"},
    "son tra": {"hai chau", "ngu hanh son"},
    "ngu hanh son": {"hai chau", "son tra", "cam le"},
    "thanh khe": {"hai chau", "lien chieu", "cam le"},
    "cam le": {"hai chau", "thanh khe", "ngu hanh son", "hoa vang"},
    "lien chieu": {"thanh khe", "hoa vang"},
    "hoa vang": {"cam le", "lien chieu"},
}

# Approximate map anchors when a place has no exact coordinates.
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
}

_AREA_TO_CITY_KEY: dict[str, str] = {
    "hai chau": "da_nang",
    "son tra": "da_nang",
    "ngu hanh son": "da_nang",
    "thanh khe": "da_nang",
    "cam le": "da_nang",
    "lien chieu": "da_nang",
    "hoa vang": "da_nang",
    "hoi an": "hoi_an",
    "tam ky": "quang_nam",
    "dien ban": "quang_nam",
    "duy xuyen": "quang_nam",
    "dai loc": "quang_nam",
    "thang binh": "quang_nam",
    "tien phuoc": "quang_nam",
    "nui thanh": "quang_nam",
}

_CITY_NAME_PATTERNS: dict[str, tuple[str, ...]] = {
    "da_nang": ("da nang", "danang"),
    "hoi_an": ("hoi an",),
    "quang_nam": ("quang nam",),
}
_HOTEL_RELOCATION_THRESHOLD_KM = 30.0
_MAX_EXTERNAL_HOTEL_QUERIES = 4
_MAX_EXTERNAL_EMERGENCY_FOOD_QUERIES = 5


def build_trip_plan_payload(query: str, places: List[dict], strict_mode: bool = False) -> dict:
    if not places:
        return {
            "plan": "Không đủ dữ liệu để gợi ý lịch trình.",
            "stay_plan": {"segments": [], "change_hotel": False},
            "recommended_hotel": None,
        }

    total_days = extract_trip_days(query) or 1
    destinations = _filter_by_categories(places, ["destination", "museum", "viewpoint", "entertainment"])
    food_places = _filter_by_categories(places, ["restaurant"])
    hotels = _filter_by_categories(places, ["accommodation"])
    city = _extract_city(query, places)
    target_city_keys = _target_city_keys(query, city)
    strict_target_city_key = target_city_keys[0] if len(target_city_keys) == 1 else ""
    day_city_plan = _planned_city_keys_per_day(
        query=query,
        total_days=total_days,
        fallback_city_key=strict_target_city_key or _city_key_from_text(city),
    )
    if strict_target_city_key:
        places_by_city = [p for p in places if _place_city_key(p) == strict_target_city_key]
        destinations_by_city = [p for p in destinations if _place_city_key(p) == strict_target_city_key]
        food_by_city = [p for p in food_places if _place_city_key(p) == strict_target_city_key]
        hotels_by_city = [p for p in hotels if _place_city_key(p) == strict_target_city_key]
        # Keep strict city filter when possible; fallback to original pools if too sparse.
        if destinations_by_city:
            places = places_by_city or places
            destinations = destinations_by_city
        if food_by_city:
            food_places = food_by_city
        if hotels_by_city:
            hotels = hotels_by_city

    if not destinations:
        destinations = [p for p in places if str(p.get("category") or "").lower() != "restaurant"]

    destination_label = _extract_city(query, places)
    lines: List[str] = [
        f"LỊCH TRÌNH {total_days} NGÀY TẠI {destination_label.upper()}",
        "",
    ]
    route_plan: List[dict] = []

    used_attraction_keys: set[str] = set()
    used_restaurant_keys: set[str] = set()
    attraction_pool = _rank_attractions(_unique_by_name(destinations), query=query)
    daily_frames: List[dict] = []
    for day in range(1, total_days + 1):
        day_target_city_key = day_city_plan[day - 1] if day - 1 < len(day_city_plan) else strict_target_city_key
        # Hard preference: morning tourism + afternoon entertainment in same district/huyen.
        morning, afternoon = _pick_daily_tourism_entertainment_pair(
            pool=attraction_pool,
            used=used_attraction_keys,
            query=query,
            target_city_key=day_target_city_key or strict_target_city_key,
            strict_mode=strict_mode,
        )
        if morning:
            used_attraction_keys.add(_place_key(morning))
        # If user prefers beach, force at least one beach/coastal attraction each day.
        if _is_beach_query(query) and not (_is_beach_place(morning) or _is_beach_place(afternoon)):
            beach_pick = _pick_next_beach_attraction(
                attraction_pool,
                used=used_attraction_keys,
                anchor=morning,
                strict_mode=strict_mode,
            )
            if beach_pick:
                afternoon = beach_pick
        if afternoon:
            used_attraction_keys.add(_place_key(afternoon))

        daily_frames.append(
            {
                "day": day,
                "morning": morning,
                "afternoon": afternoon,
                "city_key": _day_city_key(
                    morning,
                    afternoon,
                    day_target_city_key or strict_target_city_key or _city_key_from_text(city),
                ),
            }
        )

    stay_plan = _select_stay_plan(
        daily_frames=daily_frames,
        hotels=hotels,
        city=city,
        strict_mode=strict_mode,
    )
    daily_stays = {
        int(item.get("day")): item
        for item in (stay_plan.get("daily") or [])
        if isinstance(item, dict) and isinstance(item.get("day"), int)
    }
    if stay_plan.get("segments"):
        lines.extend(
            [
                "GỢI Ý NƠI NGHỈ:",
                *[
                    _stay_segment_line(segment)
                    for segment in stay_plan.get("segments", [])
                ],
                "",
            ]
        )

    for frame in daily_frames:
        day = int(frame["day"])
        morning = frame.get("morning")
        afternoon = frame.get("afternoon")
        daily_stay = daily_stays.get(day) or {}
        start_hotel = daily_stay.get("start_hotel")
        end_hotel = daily_stay.get("end_hotel")
        day_city_label = _city_label_from_key(str(frame.get("city_key") or ""), default_city=city)
        breakfast, lunch, dinner = _select_daily_restaurants(
            food_places=food_places,
            city=day_city_label,
            anchors=[morning, afternoon],
            start_hotel=start_hotel,
            end_hotel=end_hotel,
            used_restaurants=used_restaurant_keys,
            target_city_key=str(frame.get("city_key") or strict_target_city_key),
        )
        for meal in (breakfast, lunch, dinner):
            k = _place_key(meal)
            if k and "chua co" not in k:
                used_restaurant_keys.add(k)

        day_route_plan = _build_day_route_plan(
            day=day,
            start_hotel=start_hotel,
            end_hotel=end_hotel,
            breakfast=breakfast,
            morning=morning,
            lunch=lunch,
            afternoon=afternoon,
            dinner=dinner,
        )
        route_plan.extend(day_route_plan)
        day_theme = _day_theme_label(day=day, total_days=total_days, morning=morning, afternoon=afternoon)
        morning_action = _slot_action_text(slot="morning", place=morning, day=day)
        afternoon_action = _slot_action_text(slot="afternoon", place=afternoon, day=day)
        evening_action = _slot_action_text(slot="evening", place=dinner, day=day)

        lines.extend(
            [
                "",
                f"Ngày {day} — {day_theme}",
                _meal_and_activity_line(
                    slot="Sáng",
                    meal_kind="breakfast",
                    meal_place=breakfast,
                    activity_text=morning_action,
                    activity_place=morning,
                ),
                _meal_and_activity_line(
                    slot="Trưa",
                    meal_kind="lunch",
                    meal_place=lunch,
                ),
                _meal_and_activity_line(
                    slot="Chiều",
                    meal_kind="",
                    meal_place=None,
                    activity_text=afternoon_action,
                    activity_place=afternoon,
                ),
                _meal_and_activity_line(
                    slot="Tối",
                    meal_kind="dinner",
                    meal_place=dinner,
                    activity_text=evening_action,
                    activity_place=dinner,
                    fallback_place=end_hotel or afternoon,
                    include_evening_stroll=True,
                ),
            ]
        )

    lines.extend(
        [
            "",
            "Gợi ý tổng quan: Lịch trình cân bằng giữa tham quan, trải nghiệm địa phương và di chuyển hợp lý.",
            "Lưu ý: Có thể điều chỉnh khung giờ theo thời tiết và giờ mở cửa thực tế.",
        ]
    )
    return {
        "plan": "\n".join(lines).strip(),
        "stay_plan": stay_plan,
        "recommended_hotel": _recommended_hotel_from_stay_plan(stay_plan),
        "route_plan": route_plan,
    }


def _filter_by_categories(places: List[dict], categories: List[str]) -> List[dict]:
    wanted = {c.lower() for c in categories}
    return [p for p in places if str(p.get("category") or "").lower() in wanted]


def _place_key(p: dict | None) -> str:
    if not p:
        return ""
    name = str(p.get("name") or "").strip()
    if not is_user_facing_place_name(name):
        return ""
    return name.lower()


def _is_self_service_meal(place: dict | None) -> bool:
    if not place:
        return False
    if str(place.get("source") or "").strip() == "fallback-self-service":
        return True
    name = str(place.get("name") or "").strip().lower()
    return "tu tuc" in name or "tự túc" in name


def _meal_and_activity_line(
    *,
    slot: str,
    meal_kind: str,
    meal_place: dict | None,
    activity_text: str | None = None,
    activity_place: dict | None = None,
    fallback_place: dict | None = None,
    include_evening_stroll: bool = False,
) -> str:
    parts: list[str] = []
    if meal_kind:
        meal_sentence = _meal_sentence(meal_kind=meal_kind, meal_place=meal_place)
        if meal_sentence:
            parts.append(meal_sentence)
    activity_sentence = _activity_sentence(
        slot=slot,
        activity_text=activity_text,
        activity_place=activity_place,
        fallback_place=fallback_place,
        include_evening_stroll=include_evening_stroll,
    )
    if activity_sentence:
        parts.append(activity_sentence)
    body = " ".join(part.strip() for part in parts if part.strip()).strip()
    return f"• {slot}: {body}".strip()


def _meal_sentence(meal_kind: str, meal_place: dict | None) -> str:
    meal_map = {
        "breakfast": ("bữa sáng",),
        "lunch": ("bữa trưa",),
        "dinner": ("bữa tối",),
    }
    meal_label = meal_map.get(meal_kind, ("bữa ăn trong ngày",))[0]
    if _is_self_service_meal(meal_place):
        return f"Tự túc {meal_label} vì chưa tìm thấy địa điểm phù hợp gần hành trình."

    place_name = _fmt(meal_place) if meal_place else "điểm ăn uống gần tuyến"
    if meal_kind == "breakfast":
        return f"Bắt đầu ngày mới với bữa sáng tại {place_name}."
    if meal_kind == "lunch":
        return f"Dùng bữa trưa tại {place_name}."
    if meal_kind == "dinner":
        return f"Dùng bữa tối tại {place_name}."
    return f"Ghé {place_name} cho bữa ăn trong ngày."


def _activity_sentence(
    *,
    slot: str,
    activity_text: str | None,
    activity_place: dict | None,
    fallback_place: dict | None = None,
    include_evening_stroll: bool = False,
) -> str:
    action = str(activity_text or "").strip()
    target = _fmt(activity_place or fallback_place) if (activity_place or fallback_place) else ""
    if not action and not include_evening_stroll:
        return ""

    if slot == "Sáng" and action and target:
        return f"Sau đó, bạn có thể {action} tại {target}."
    if slot == "Chiều" and action and target:
        return f"Buổi chiều phù hợp để {action} tại {target}."
    if slot == "Tối":
        phrases: list[str] = []
        if action and target:
            phrases.append(f"Buổi tối, bạn có thể {action} tại {target}.")
        elif action:
            phrases.append(f"Buổi tối, bạn có thể {action}.")
        if include_evening_stroll:
            stroll_target = _fmt(fallback_place or activity_place) if (fallback_place or activity_place) else ""
            if stroll_target and not _is_self_service_meal(fallback_place or activity_place):
                phrases.append(
                    f"Sau bữa tối, dành ít thời gian đi dạo và thư giãn quanh khu vực {stroll_target}."
                )
            else:
                phrases.append(
                    "Sau bữa tối, bạn có thể đi dạo nhẹ và thư giãn quanh khu vực lưu trú."
                )
        return " ".join(phrases).strip()

    if action and target:
        return f"Bạn có thể {action} tại {target}."
    if action:
        return f"Bạn có thể {action}."
    return ""


def _unique_by_name(items: List[dict]) -> List[dict]:
    out: List[dict] = []
    seen: set[str] = set()
    for it in items:
        name = _place_key(it)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(it)
    return out


def _attraction_fame_score(p: dict, query: str = "") -> float:
    name_fold = _fold(str(p.get("name") or ""))
    city_fold = _fold(str(p.get("city") or ""))
    city_key = _city_key_from_fold(city_fold)
    cat = str(p.get("category") or "").lower()
    intent_tags = {
        str(tag).strip().lower()
        for tag in (p.get("intent_tags") or [])
        if str(tag).strip()
    }
    score = 0.0
    if cat == "museum":
        score += 5.0
    elif cat == "viewpoint":
        score += 4.0
    elif cat == "destination":
        score += 2.0
    keywords = (
        "bao tang",
        "cau rong",
        "dragon bridge",
        "ba na",
        "son tra",
        "linh ung",
        "chua",
        "marble",
        "ngu hanh",
        "my khe",
        "non nuoc",
        "pho co",
        "hoi an",
        "unesco",
        "bai bien",
        "beach",
        "dinh",
        "cap treo",
        "pagoda",
        "thanh dia",
        "di san",
    )
    for kw in keywords:
        if kw in name_fold:
            score += 2.5
    if city_key:
        for hint in _CITY_LANDMARK_HINTS.get(city_key, ()):
            if hint in name_fold:
                score += 3.0
    if name_fold.startswith("duong "):
        score -= 6.0
    # Preference-aware boost: if user asks for beach, prioritize beach/coastal spots.
    if _is_beach_query(query):
        if "bien" in intent_tags or any(k in name_fold for k in ("beach", "bai bien", "bien", "my khe", "non nuoc", "an bang", "cu lao")):
            score += 6.0
        if cat == "museum":
            score -= 2.5
    if _is_culture_query(query) and intent_tags.intersection({"bao_tang", "di_tich", "tam_linh"}):
        score += 5.0
    return score


def _rank_attractions(places: List[dict], query: str = "") -> List[dict]:
    return sorted(places, key=lambda p: (_attraction_fame_score(p, query=query), _place_key(p)), reverse=True)


def _pick_daily_tourism_entertainment_pair(
    pool: List[dict],
    used: set[str],
    query: str,
    target_city_key: str,
    strict_mode: bool = False,
) -> tuple[dict | None, dict | None]:
    tourism_candidates = [
        p
        for p in pool
        if _place_key(p)
        and _place_key(p) not in used
        and _is_quality_attraction_name(str(p.get("name") or ""))
        and _attraction_style(p) == "tourism"
    ]
    if target_city_key:
        tourism_same_city = [p for p in tourism_candidates if _place_city_key(p) == target_city_key]
        if tourism_same_city:
            tourism_candidates = tourism_same_city
    if not tourism_candidates:
        return None, None

    entertainment_candidates_all = [
        p
        for p in pool
        if _place_key(p)
        and _place_key(p) not in used
        and _is_quality_attraction_name(str(p.get("name") or ""))
        and _attraction_style(p) == "entertainment"
    ]
    if target_city_key:
        entertainment_same_city = [p for p in entertainment_candidates_all if _place_city_key(p) == target_city_key]
        if entertainment_same_city:
            entertainment_candidates_all = entertainment_same_city

    # Build strict pair list (same city + same district/huyen).
    strict_pairs: List[tuple[dict, dict]] = []
    for morning in tourism_candidates[: min(24, len(tourism_candidates))]:
        m_city = _place_city_key(morning)
        m_areas = _extract_admin_areas(morning)
        if not m_areas:
            continue
        for afternoon in entertainment_candidates_all:
            if _place_key(afternoon) == _place_key(morning):
                continue
            if m_city and _place_city_key(afternoon) != m_city:
                continue
            if not _extract_admin_areas(afternoon).intersection(m_areas):
                continue
            strict_pairs.append((morning, afternoon))
    if strict_pairs:
        if strict_mode:
            strict_pairs.sort(
                key=lambda pair: _area_alignment_score(pair[1], pair[0]),
                reverse=True,
            )
            return strict_pairs[0]
        idx = int(_RNG.random() * len(strict_pairs))
        return strict_pairs[idx]

    # For Hoi An, allow same-city pairing when district/huyen token is sparse.
    if target_city_key == "hoi_an":
        for morning in tourism_candidates[: min(24, len(tourism_candidates))]:
            m_city = _place_city_key(morning)
            entertainment_candidates = [p for p in entertainment_candidates_all if _place_key(p) != _place_key(morning) and (not m_city or _place_city_key(p) == m_city)]
            if entertainment_candidates:
                afternoon = (
                    _pick_best_area_aligned_attraction(entertainment_candidates, anchor=morning)
                    if strict_mode
                    else _random_pick_from_ranked(entertainment_candidates, window=8)
                )
                return morning, afternoon

    # Fallback 1: keep same city, relax district constraint.
    for morning in tourism_candidates[: min(24, len(tourism_candidates))]:
        m_city = _place_city_key(morning)
        same_city_ent = [
            p
            for p in entertainment_candidates_all
            if _place_key(p) != _place_key(morning)
            and (not m_city or _place_city_key(p) == m_city)
        ]
        if same_city_ent:
            if strict_mode:
                return morning, _pick_best_area_aligned_attraction(same_city_ent, anchor=morning)
            return morning, _random_pick_from_ranked(same_city_ent, window=8)

    # Fallback 2: always return tourism + entertainment to avoid missing slots in output.
    morning = _random_pick_from_ranked(tourism_candidates, window=8)
    if not morning:
        return None, None
    any_ent = [
        p for p in entertainment_candidates_all if _place_key(p) != _place_key(morning)
    ]
    if any_ent:
        if strict_mode:
            return morning, _pick_best_area_aligned_attraction(any_ent, anchor=morning)
        return morning, _random_pick_from_ranked(any_ent, window=8)
    return morning, None


def _pick_best_area_aligned_attraction(candidates: List[dict], anchor: dict) -> dict:
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            _area_alignment_score(candidate, anchor),
            _attraction_fame_score(candidate),
            _place_key(candidate),
        ),
        reverse=True,
    )
    return ranked[0]


def _area_alignment_score(candidate: dict, anchor: dict | None) -> float:
    if not anchor:
        return 0.0
    score = _district_distance_bonus(candidate, [anchor])
    a_pt = _resolve_point_for_map(anchor)
    c_pt = _resolve_point_for_map(candidate)
    if a_pt and c_pt:
        score -= _haversine_km(a_pt[0], a_pt[1], c_pt[0], c_pt[1]) * 0.45
    return score


def _attraction_style(p: dict) -> str:
    """Heuristic split for itinerary balancing: tourism vs entertainment."""
    explicit = str(p.get("planner_role") or "").strip().lower()
    if explicit in {"tourism", "entertainment"}:
        return explicit
    src = _fold(str(p.get("source") or ""))
    if "vcgt_danang.csv" in src:
        return "entertainment"
    if "dest_danang.json" in src:
        return "tourism"
    blob = _fold(
        " ".join(
            [
                str(p.get("name") or ""),
                str(p.get("description") or ""),
                str(p.get("address") or ""),
            ]
        )
    )
    tourism_markers = (
        "bao tang",
        "museum",
        "chua",
        "dinh",
        "thap",
        "di tich",
        "nha tho",
        "lang",
        "van hoa",
        "lich su",
        "thanh dia",
    )
    entertainment_markers = (
        "cong vien",
        "khu du lich",
        "sun world",
        "asia park",
        "vui choi",
        "giai tri",
        "beach",
        "bai bien",
        "bien",
        "resort",
        "vinwonders",
        "water park",
    )
    t_hits = sum(1 for k in tourism_markers if k in blob)
    e_hits = sum(1 for k in entertainment_markers if k in blob)
    return "entertainment" if e_hits > t_hits else "tourism"


def _is_beach_query(query: str) -> bool:
    q = _fold(query or "")
    return any(k in q for k in ("bien", "bai bien", "tam bien", "beach", "bo bien", "dao"))


def _is_culture_query(query: str) -> bool:
    q = _fold(query or "")
    return any(k in q for k in ("van hoa", "lich su", "di tich", "heritage", "historic", "museum", "bao tang", "tam linh"))


def _is_beach_place(p: dict | None) -> bool:
    if not p:
        return False
    name_fold = _fold(str(p.get("name") or ""))
    cat = str(p.get("category") or "").lower()
    if cat == "beach":
        return True
    return any(k in name_fold for k in ("beach", "bai bien", "bien", "my khe", "non nuoc", "an bang", "cu lao"))


def _pick_next_beach_attraction(
    pool: List[dict],
    used: set[str],
    anchor: dict | None = None,
    strict_mode: bool = False,
) -> dict | None:
    beach_candidates = [
        p
        for p in pool
        if _place_key(p)
        and _place_key(p) not in used
        and _is_quality_attraction_name(str(p.get("name") or ""))
        and _is_beach_place(p)
    ]
    if not beach_candidates:
        return None
    if anchor:
        strict = [
            p
            for p in beach_candidates
            if _place_city_key(p) == _place_city_key(anchor)
            and bool(_extract_admin_areas(p).intersection(_extract_admin_areas(anchor)))
        ]
        if strict:
            return _random_pick_from_ranked(strict, window=8)
        # Do not break same-district rule when strict candidate is absent.
        if strict_mode:
            same_city = [p for p in beach_candidates if _place_city_key(p) == _place_city_key(anchor)]
            if same_city:
                return _pick_best_area_aligned_attraction(same_city, anchor=anchor)
        return None
    return _random_pick_from_ranked(beach_candidates, window=8)


def _random_pick_from_ranked(items: List[dict], window: int = 10) -> dict:
    """
    Controlled randomness:
    - still prefers high-rank items
    - but avoids returning the exact same itinerary each run
    """
    top = items[: max(1, min(window, len(items)))]
    if len(top) == 1:
        return top[0]

    # Weighted roulette: rank-1 has highest chance, lower ranks still possible.
    weights = [1.0 / (i + 1) for i in range(len(top))]
    total = sum(weights)
    r = _RNG.random() * total
    acc = 0.0
    for item, w in zip(top, weights):
        acc += w
        if r <= acc:
            return item
    return top[-1]


def _restaurant_fame_score(p: dict, meal: str = "lunch") -> float:
    name_fold = _fold(str(p.get("name") or ""))
    detail_fold = _fold(
        " ".join(
            [
                str(p.get("description") or ""),
                str(p.get("detail_content") or ""),
                str(p.get("list_snippet") or ""),
            ]
        )
    )
    score = 0.0
    if "nha hang" in name_fold or "restaurant" in name_fold:
        score += 3.0
    if len(name_fold) >= 20:
        score += 1.0
    low_trust = ("lady selling", "20,000", "20k", "street")
    for t in low_trust:
        if t in name_fold:
            score -= 2.0
    score += _meal_suitability_score(name_fold=name_fold, detail_fold=detail_fold, meal=meal)
    return score


def _restaurant_composite_score(p: dict, anchor_pts: List[dict], meal: str = "lunch") -> float:
    fame = _restaurant_fame_score(p, meal=meal)
    geo_anchors = [
        a
        for a in anchor_pts
        if isinstance(a.get("lat"), (int, float)) and isinstance(a.get("lon"), (int, float))
    ]
    if not geo_anchors or not isinstance(p.get("lat"), (int, float)):
        return fame + _address_proximity_bonus(p, anchor_pts)
    near_km = min(
        _haversine_km(float(p["lat"]), float(p["lon"]), float(a["lat"]), float(a["lon"]))
        for a in geo_anchors
    )
    district_soft = _district_distance_bonus(p, anchor_pts)
    return fame - near_km * 0.2 + district_soft


def _meal_suitability_score(name_fold: str, detail_fold: str, meal: str) -> float:
    text = f"{name_fold} {detail_fold}"
    breakfast_good = (
        "bun ",
        "pho ",
        "mi quang",
        "my quang",
        "banh mi",
        "diem tam",
        "an sang",
        "com ga",
        "chao",
    )
    heavy_dinner_food = (
        "hai san",
        "seafood",
        "bbq",
        "buffet",
        "lau",
        "nuong",
        "beer",
        "bia",
        "grill",
    )
    score = 0.0
    if meal == "breakfast":
        if any(k in text for k in breakfast_good):
            score += 3.5
        if any(k in text for k in heavy_dinner_food):
            score -= 4.5
    elif meal == "lunch":
        if any(k in text for k in ("com ", "bun ", "pho ", "quan", "restaurant")):
            score += 1.5
        if any(k in text for k in ("bar", "beer", "bia")):
            score -= 1.5
    elif meal == "dinner":
        if any(k in text for k in heavy_dinner_food):
            score += 3.0
        if any(k in text for k in ("diem tam", "an sang")):
            score -= 2.0
    return score


def _address_proximity_bonus(p: dict, anchor_pts: List[dict]) -> float:
    """DB-only fallback when coordinates are missing: favor same district/huyen."""
    p_areas = _extract_admin_areas(p)
    if not p_areas:
        return 0.0
    bonus = 0.0
    for a in anchor_pts:
        a_areas = _extract_admin_areas(a)
        if not a_areas:
            continue
        # Require same city + same district/huyen.
        if _place_city_key(p) == _place_city_key(a) and p_areas.intersection(a_areas):
            bonus = max(bonus, 8.0)
        else:
            # Same city but other districts: apply softer proximity preference.
            bonus = max(bonus, _district_distance_bonus(p, [a]))
    return bonus


def _district_distance_bonus(p: dict, anchors: List[dict]) -> float:
    """
    Soft bonus by district proximity:
    - same district: +8
    - adjacent district: +4
    - 2-hop district: +2
    - same city but farther: +0.5
    - different/unknown city: 0
    """
    p_district = _primary_district_key(p)
    p_city = _place_city_key(p)
    if not p_district:
        return 0.0
    best = 0.0
    for a in anchors:
        if not a:
            continue
        if p_city and _place_city_key(a) and p_city != _place_city_key(a):
            continue
        a_district = _primary_district_key(a)
        if not a_district:
            continue
        if p_district == a_district:
            best = max(best, 8.0)
            continue
        if a_district in _DISTRICT_NEIGHBORS.get(p_district, set()) or p_district in _DISTRICT_NEIGHBORS.get(a_district, set()):
            best = max(best, 4.0)
            continue
        if _is_two_hop_neighbor(p_district, a_district):
            best = max(best, 2.0)
            continue
        best = max(best, 0.5)
    return best


def _is_two_hop_neighbor(a: str, b: str) -> bool:
    for mid in _DISTRICT_NEIGHBORS.get(a, set()):
        if b in _DISTRICT_NEIGHBORS.get(mid, set()):
            return True
    return False


def _primary_district_key(p: dict) -> str:
    areas = _extract_admin_areas(p)
    if not areas:
        return ""
    # deterministic preference order for stable scoring
    for k in (
        "hai chau",
        "son tra",
        "ngu hanh son",
        "thanh khe",
        "cam le",
        "lien chieu",
        "hoa vang",
        "hoi an",
        "tam ky",
        "dien ban",
        "duy xuyen",
        "dai loc",
        "thang binh",
        "tien phuoc",
        "nui thanh",
    ):
        if k in areas:
            return k
    return sorted(areas)[0]


def _extract_admin_areas(p: dict) -> set[str]:
    explicit = p.get("admin_area_keys")
    if isinstance(explicit, list):
        out = {str(item).strip().lower() for item in explicit if str(item).strip()}
        if out:
            return out
    text = " ".join(
        [
            str(p.get("district") or ""),
            str(p.get("address") or ""),
            str(p.get("city") or ""),
        ]
    )
    s = _fold(text)
    if not s:
        return set()
    known = (
        "hai chau",
        "son tra",
        "ngu hanh son",
        "thanh khe",
        "hoa vang",
        "cam le",
        "lien chieu",
        "hoi an",
        "tam ky",
        "dien ban",
        "tien phuoc",
        "duy xuyen",
        "dai loc",
        "thang binh",
    )
    return {k for k in known if k in s}


def _target_city_keys(query: str, fallback_city: str = "") -> List[str]:
    keys = _ordered_city_keys_from_query(query)
    fallback_key = _city_key_from_text(fallback_city)
    if not keys and fallback_key:
        keys.append(fallback_key)
    seen: set[str] = set()
    ordered: List[str] = []
    for key in keys:
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _ordered_city_keys_from_query(query: str) -> List[str]:
    q = _fold(query or "")
    mentions: List[tuple[int, str]] = []
    for city_key, variants in _CITY_NAME_PATTERNS.items():
        positions = [q.find(variant) for variant in variants if variant in q]
        if positions:
            mentions.append((min(positions), city_key))
    mentions.sort()
    ordered: List[str] = []
    seen: set[str] = set()
    for _, city_key in mentions:
        if city_key in seen:
            continue
        seen.add(city_key)
        ordered.append(city_key)
    return ordered


def _planned_city_keys_per_day(query: str, total_days: int, fallback_city_key: str = "") -> List[str]:
    if total_days <= 0:
        return []
    explicit_segments = _best_explicit_city_segments(query=query, total_days=total_days)
    if explicit_segments:
        plan: List[str] = []
        for _, city_key, count in explicit_segments:
            plan.extend([city_key] * max(0, count))
            if len(plan) >= total_days:
                return plan[:total_days]
        fill_city = plan[-1] if plan else fallback_city_key
        if fill_city:
            plan.extend([fill_city] * max(0, total_days - len(plan)))
        return plan[:total_days]

    ordered_cities = _ordered_city_keys_from_query(query)
    if not ordered_cities:
        ordered_cities = [fallback_city_key] if fallback_city_key else []
    if not ordered_cities:
        return []
    if len(ordered_cities) == 1:
        return [ordered_cities[0]] * total_days

    base = total_days // len(ordered_cities)
    remainder = total_days % len(ordered_cities)
    plan: List[str] = []
    for index, city_key in enumerate(ordered_cities):
        chunk_days = base + (1 if index < remainder else 0)
        plan.extend([city_key] * max(1, chunk_days))
    return plan[:total_days]


def _best_explicit_city_segments(query: str, total_days: int) -> List[tuple[int, str, int]]:
    raw_segments = _explicit_city_day_segments(query)
    if not raw_segments:
        return []
    best: List[tuple[int, str, int]] = []
    best_score: tuple[int, int, int, int] | None = None
    n = len(raw_segments)
    for mask in range(1, 1 << n):
        subset = [raw_segments[i] for i in range(n) if mask & (1 << i)]
        total = sum(count for _, _, count in subset)
        unique_cities = len({city_key for _, city_key, _ in subset})
        if total <= total_days:
            score = (2, total, unique_cities, len(subset))
        else:
            score = (1, -(total - total_days), unique_cities, len(subset))
        if best_score is None or score > best_score:
            best_score = score
            best = subset
    return best


def _explicit_city_day_segments(query: str) -> List[tuple[int, str, int]]:
    q = _fold(query or "")
    found: List[tuple[int, str, int]] = []
    seen: set[tuple[int, str, int]] = set()
    for city_key, variants in _CITY_NAME_PATTERNS.items():
        city_pattern = "(?:" + "|".join(re.escape(variant) for variant in variants) + ")"
        before_city = re.finditer(
            rf"(\d+)\s*ngay(?:\s+(?:o|tai|di|tham quan|du lich))?(?:\s+\w+){{0,2}}\s*{city_pattern}",
            q,
        )
        after_city = re.finditer(
            rf"{city_pattern}(?:\s+\w+){{0,2}}\s*(\d+)\s*ngay",
            q,
        )
        for match in list(before_city) + list(after_city):
            try:
                count = max(1, min(int(match.group(1)), 7))
            except Exception:
                continue
            item = (match.start(), city_key, count)
            if item in seen:
                continue
            seen.add(item)
            found.append(item)
    found.sort()
    return found


def _place_city_key(p: dict) -> str:
    explicit = str(p.get("city_key") or "").strip().lower()
    if explicit:
        return explicit
    primary_area = str(p.get("primary_area_key") or "").strip().lower()
    if primary_area in _AREA_TO_CITY_KEY:
        return _AREA_TO_CITY_KEY[primary_area]
    for area in _extract_admin_areas(p):
        if area in _AREA_TO_CITY_KEY:
            return _AREA_TO_CITY_KEY[area]
    city = str(p.get("city") or "")
    addr = str(p.get("address") or "")
    blob = _fold(f"{city} {addr}")
    return _city_key_from_text(blob) or _city_key_from_text(city)


def _city_key_from_text(text: str) -> str:
    t = _fold(text or "")
    for city_key, variants in _CITY_NAME_PATTERNS.items():
        if any(variant in t for variant in variants):
            return city_key
    for area_key, city_key in _AREA_TO_CITY_KEY.items():
        if area_key in t:
            return city_key
    return ""


def _day_city_key(morning: dict | None, afternoon: dict | None, fallback: str = "") -> str:
    morning_key = _place_city_key(morning) if morning else ""
    afternoon_key = _place_city_key(afternoon) if afternoon else ""
    if morning_key and afternoon_key and morning_key == afternoon_key:
        return morning_key
    if morning_key:
        return morning_key
    if afternoon_key:
        return afternoon_key
    return fallback


def _select_stay_plan(
    daily_frames: List[dict],
    hotels: List[dict],
    city: str,
    strict_mode: bool = False,
) -> dict:
    if not daily_frames:
        return {"segments": [], "daily": [], "change_hotel": False}

    full_segment = {
        "city_key": "",
        "days": [int(frame["day"]) for frame in daily_frames],
        "anchors": [
            anchor
            for frame in daily_frames
            for anchor in (frame.get("morning"), frame.get("afternoon"))
            if anchor
        ],
    }
    main_hotel = _select_segment_hotel(
        segment=full_segment,
        hotels=hotels,
        city=city,
        strict_mode=strict_mode,
    )

    daily: List[dict] = []
    current_end_hotel = main_hotel
    relocation_happened = False

    for frame in daily_frames:
        day = int(frame["day"])
        city_key = str(frame.get("city_key") or "")
        anchors = [frame.get("morning"), frame.get("afternoon")]
        start_hotel = current_end_hotel or main_hotel
        needs_relocation = _should_relocate_for_day(start_hotel=start_hotel, anchors=anchors)
        end_hotel = start_hotel
        reason = "main_base"

        if needs_relocation:
            candidate_hotel = _select_segment_hotel(
                segment={
                    "city_key": city_key,
                    "days": [day],
                    "anchors": [anchor for anchor in anchors if anchor],
                },
                hotels=hotels,
                city=city,
                strict_mode=strict_mode,
            )
            if candidate_hotel and not _same_hotel(candidate_hotel, start_hotel):
                end_hotel = candidate_hotel
                reason = "relocated_near_far_cluster"
                relocation_happened = True

        daily.append(
            {
                "day": day,
                "city_key": city_key,
                "start_hotel": start_hotel,
                "end_hotel": end_hotel,
                "reason": reason,
            }
        )
        current_end_hotel = end_hotel or current_end_hotel

    if relocation_happened and daily and main_hotel:
        daily[-1]["end_hotel"] = main_hotel
        daily[-1]["reason"] = "return_to_main_hotel"

    segments: List[dict] = []
    for item in daily:
        day = int(item["day"])
        hotel = item.get("end_hotel")
        city_key = str(item.get("city_key") or "")
        if not hotel:
            continue
        if not segments or not _same_hotel(segments[-1].get("hotel"), hotel):
            segments.append(
                {
                    "city_key": city_key,
                    "days": [day],
                    "hotel": hotel,
                    "reason": str(item.get("reason") or ""),
                }
            )
        else:
            segments[-1]["days"].append(day)

    for segment in segments:
        segment["city_label"] = _city_label_from_key(str(segment.get("city_key") or ""), default_city=city)
        segment["days_label"] = _days_label(segment.get("days", []))

    return {
        "segments": segments,
        "daily": daily,
        "main_hotel": main_hotel,
        "change_hotel": any(
            not _same_hotel(item.get("start_hotel"), item.get("end_hotel"))
            for item in daily
        ),
    }


def _should_relocate_for_day(
    *,
    start_hotel: dict | None,
    anchors: List[dict | None],
) -> bool:
    if not start_hotel:
        return False
    distances = _hotel_to_anchor_distances_km(start_hotel, anchors)
    if not distances:
        return False
    return min(distances) > _HOTEL_RELOCATION_THRESHOLD_KM


def _hotel_to_anchor_distances_km(hotel: dict | None, anchors: List[dict | None]) -> List[float]:
    hotel_pt = _resolve_point_for_map(hotel)
    if not hotel_pt:
        return []
    distances: List[float] = []
    for anchor in anchors:
        anchor_pt = _resolve_point_for_map(anchor)
        if not anchor_pt:
            continue
        distances.append(_haversine_km(hotel_pt[0], hotel_pt[1], anchor_pt[0], anchor_pt[1]))
    return distances


def _same_hotel(a: dict | None, b: dict | None) -> bool:
    a_name = _fold(str((a or {}).get("name") or ""))
    b_name = _fold(str((b or {}).get("name") or ""))
    return bool(a_name and b_name and a_name == b_name)


def _select_segment_hotel(
    segment: dict,
    hotels: List[dict],
    city: str,
    strict_mode: bool = False,
) -> dict | None:
    anchors = [anchor for anchor in segment.get("anchors", []) if anchor]
    city_key = str(segment.get("city_key") or "")
    local_candidates = [
        hotel for hotel in _unique_by_name(hotels)
        if _place_key(hotel)
        and (not city_key or _place_city_key(hotel) == city_key)
    ]

    best_local: dict | None = None
    best_local_score = float("-inf")
    if local_candidates:
        ranked = sorted(
            local_candidates,
            key=lambda hotel: (
                _hotel_segment_score(hotel, anchors),
                _hotel_star_bonus(hotel),
                _place_key(hotel),
            ),
            reverse=True,
        )
        best_local = ranked[0]
        best_local_score = _hotel_segment_score(best_local, anchors)

    should_try_external = best_local is None or best_local_score < 24.0
    external = _query_external_accommodations(
        city_label=_city_label_from_key(city_key, default_city=city),
        anchors=anchors,
        city_key=city_key,
        need=8 if strict_mode else 5,
    ) if should_try_external else []
    if external:
        ranked_external = sorted(
            external,
            key=lambda hotel: (
                _hotel_segment_score(hotel, anchors),
                _hotel_star_bonus(hotel),
                _place_key(hotel),
            ),
            reverse=True,
        )
        best_external = ranked_external[0]
        best_external_score = _hotel_segment_score(best_external, anchors)
        if best_local is None or best_external_score > best_local_score + 1.0:
            return _with_pick_reason(best_external, f"stay_external:{_days_label(segment.get('days', []))}")

    if best_local is not None:
        return _with_pick_reason(best_local, f"stay_db:{_days_label(segment.get('days', []))}")
    return None


def _hotel_segment_score(hotel: dict, anchors: List[dict]) -> float:
    district_soft = _district_distance_bonus(hotel, anchors)
    star_bonus = _hotel_star_bonus(hotel)
    type_bonus = _hotel_type_bonus(hotel)
    dominant_area = _dominant_area_key(anchors)
    dominant_area_bonus = 10.0 if dominant_area and _primary_district_key(hotel) == dominant_area else 0.0
    hotel_pt = _resolve_point_for_map(hotel)
    anchor_pts = [_resolve_point_for_map(anchor) for anchor in anchors if anchor]
    anchor_pts = [point for point in anchor_pts if point]
    if hotel_pt and anchor_pts:
        avg_km = sum(_haversine_km(hotel_pt[0], hotel_pt[1], pt[0], pt[1]) for pt in anchor_pts) / max(len(anchor_pts), 1)
        max_km = max(_haversine_km(hotel_pt[0], hotel_pt[1], pt[0], pt[1]) for pt in anchor_pts)
    else:
        avg_km = 6.0
        max_km = 8.0
    return 32.0 + dominant_area_bonus + district_soft + star_bonus + type_bonus - 2.2 * avg_km - 0.8 * max_km


def _hotel_star_bonus(hotel: dict) -> float:
    text = _fold(str(hotel.get("star_rating") or ""))
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return 0.0
    try:
        return float(match.group(1)) * 1.5
    except Exception:
        return 0.0


def _hotel_type_bonus(hotel: dict) -> float:
    text = _fold(
        " ".join(
            [
                str(hotel.get("accommodation_type") or ""),
                str(hotel.get("name") or ""),
            ]
        )
    )
    if any(token in text for token in ("resort", "villa", "hotel", "khach san")):
        return 2.5
    if any(token in text for token in ("hostel", "homestay", "guest house", "guesthouse")):
        return 1.0
    return 0.0


def _dominant_area_key(anchors: List[dict]) -> str:
    counts: dict[str, int] = {}
    for anchor in anchors:
        if not anchor:
            continue
        area = _primary_district_key(anchor)
        if not area:
            continue
        counts[area] = counts.get(area, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


def _query_external_accommodations(
    city_label: str,
    anchors: List[dict],
    city_key: str = "",
    need: int = 5,
) -> List[dict]:
    queries = _external_hotel_queries(city_label=city_label, anchors=anchors)
    out: List[dict] = []
    seen: set[str] = set()
    for query in queries[:_MAX_EXTERNAL_HOTEL_QUERIES]:
        raw = search_places(query, limit=max(12, need * 4))
        for item in raw:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            folded_name = _fold(name)
            if folded_name in seen or not _looks_like_accommodation(item):
                continue
            candidate = {
                "name": name,
                "category": "accommodation",
                "address": str(item.get("address") or ""),
                "city": city_label,
                "district": "",
                "lat": item.get("lat"),
                "lon": item.get("lon"),
                "source": "nominatim-fallback-accommodation",
                "osm_class": str(item.get("osm_class") or ""),
                "osm_type": str(item.get("osm_type") or ""),
                "query_used": query,
                "accommodation_type": "Khach san",
                "city_key": city_key or _city_key_from_text(city_label),
                "admin_area_keys": [],
            }
            if city_key and _place_city_key(candidate) and _place_city_key(candidate) != city_key:
                continue
            out.append(candidate)
            seen.add(folded_name)
            if len(out) >= need:
                cache_external_places(out)
                return out
    cache_external_places(out)
    return out


def _external_hotel_queries(city_label: str, anchors: List[dict]) -> List[str]:
    locations: List[str] = []
    for anchor in anchors:
        if not anchor:
            continue
        for area_key in sorted(_extract_admin_areas(anchor)):
            label = _AREA_SEARCH_LABELS.get(area_key)
            if label:
                locations.append(label)
        district = str(anchor.get("district") or "").strip()
        city = str(anchor.get("city") or "").strip()
        if district and city:
            locations.append(f"{district}, {city}")
    if city_label:
        locations.append(city_label)

    deduped: List[str] = []
    seen: set[str] = set()
    for location in locations:
        key = _fold(location)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(location)

    queries: List[str] = []
    for location in deduped:
        queries.extend(
            [
                f"hotel in {location}",
                f"khach san in {location}",
                f"resort in {location}",
                f"accommodation in {location}",
            ]
        )
    return queries


def _looks_like_accommodation(item: dict) -> bool:
    osm_class = _fold(str(item.get("osm_class") or ""))
    osm_type = _fold(str(item.get("osm_type") or ""))
    name = _fold(str(item.get("name") or ""))
    return (
        osm_class == "tourism"
        and osm_type in {"hotel", "hostel", "guest_house", "motel", "apartment", "resort"}
    ) or any(token in name for token in ("hotel", "resort", "hostel", "villa", "homestay", "khach san"))


def _city_label_from_key(city_key: str, default_city: str = "") -> str:
    labels = {
        "da_nang": "Đà Nẵng",
        "hoi_an": "Hội An, Quảng Nam",
        "quang_nam": "Quảng Nam",
        "tam_ky": "Tam Kỳ, Quảng Nam",
    }
    return labels.get(city_key, default_city or city_key.replace("_", " ").title())


def _days_label(days: List[int]) -> str:
    if not days:
        return ""
    if len(days) == 1:
        return f"Ngày {days[0]}"
    return f"Ngày {days[0]}–{days[-1]}"


def _stay_segment_line(segment: dict) -> str:
    hotel = segment.get("hotel")
    if hotel:
        return f"• {segment.get('days_label')}: nghỉ tại {_fmt(hotel)}"
    return f"• {segment.get('days_label')}: chưa tìm thấy khách sạn phù hợp trong dữ liệu"


def _recommended_hotel_from_stay_plan(stay_plan: dict) -> dict | None:
    segments = stay_plan.get("segments", [])
    if not segments:
        return None
    if len(segments) == 1:
        hotel = dict(segments[0].get("hotel") or {})
        if hotel:
            hotel["reason"] = str(segments[0].get("reason") or "")
        return hotel or None
    return {
        "type": "multi_city_stay",
        "reason": "Đổi khách sạn theo từng cụm ngày khi cự ly vượt ~30 km để tối ưu di chuyển, sau đó quay về khách sạn chính.",
        "segments": [
            {
                "days": segment.get("days", []),
                "city_key": segment.get("city_key"),
                "city_label": segment.get("city_label"),
                "hotel": segment.get("hotel"),
                "reason": segment.get("reason"),
            }
            for segment in segments
        ],
    }


def _select_daily_restaurants(
    food_places: List[dict],
    city: str,
    anchors: List[dict | None],
    start_hotel: dict | None,
    end_hotel: dict | None,
    used_restaurants: set[str],
    target_city_key: str = "",
) -> tuple[dict | None, dict | None, dict | None]:
    max_meal_distance_km = 5.0
    forbid = set(used_restaurants)
    anchor_pts = [a for a in anchors if a]
    morning_anchor = anchors[0] if len(anchors) > 0 else None
    afternoon_anchor = anchors[1] if len(anchors) > 1 else None
    meal_anchor_map = {"breakfast": morning_anchor, "lunch": morning_anchor, "dinner": afternoon_anchor or morning_anchor}
    meal_route_map: dict[str, tuple[dict | None, dict | None]] = {
        "breakfast": (start_hotel, morning_anchor),
        "lunch": (morning_anchor, afternoon_anchor),
        "dinner": (afternoon_anchor, end_hotel),
    }
    all_pool: List[dict] = _unique_by_name(food_places)
    local_pool: List[dict] = [p for p in all_pool if _place_key(p) not in forbid]
    picks: List[dict] = []
    anchor_areas: set[str] = set()
    anchor_city_keys: set[str] = set()
    for a in anchor_pts:
        anchor_areas.update(_extract_admin_areas(a))
        ck = _place_city_key(a)
        if ck:
            anchor_city_keys.add(ck)
    required_city_key = target_city_key or (next(iter(anchor_city_keys)) if anchor_city_keys else "")
    required_area_keys = set(anchor_areas)

    meal_order = ["breakfast", "lunch", "dinner"]
    for meal in meal_order:
        meal_anchor = meal_anchor_map.get(meal)
        route_start, route_end = meal_route_map.get(meal, (None, None))
        meal_max_distance_km = _max_meal_distance_km(meal_anchor)

        # 1) DB-first: prefer restaurants in the same district/area as the meal anchor.
        same_district_pick = _pick_same_district_restaurant(
            local_pool=local_pool,
            forbid=forbid,
            picks=picks,
            anchor_pts=anchor_pts,
            meal=meal,
            target_city_key=required_city_key,
            route_start=route_start,
            route_end=route_end,
            meal_anchor=meal_anchor,
        )
        if same_district_pick:
            picks.append(same_district_pick)
            forbid.add(_place_key(same_district_pick))
            continue

        # 2) Route-based in DB: prioritize restaurants closest to travel axis.
        on_route_pick = _pick_on_route_restaurant(
            local_pool=local_pool,
            forbid=forbid,
            picks=picks,
            anchor_pts=anchor_pts,
            meal=meal,
            target_city_key=required_city_key,
            route_start=route_start,
            route_end=route_end,
            meal_anchor=meal_anchor,
        )
        if on_route_pick:
            picks.append(on_route_pick)
            forbid.add(_place_key(on_route_pick))
            continue

        # 3) Near last POI <= 3km (soft-ranked, no hard city/district reset).
        near_anchor_pick = _pick_near_anchor_restaurant(
            local_pool=local_pool,
            forbid=forbid,
            picks=picks,
            anchor_pts=anchor_pts,
            meal=meal,
            target_city_key=required_city_key,
            route_start=route_start,
            route_end=route_end,
            meal_anchor=meal_anchor,
            max_anchor_km=3.0,
        )
        if near_anchor_pick:
            picks.append(near_anchor_pick)
            forbid.add(_place_key(near_anchor_pick))
            continue

        # 4) Wider fallback around last POI <= 5km.
        wider_anchor_pick = _pick_near_anchor_restaurant(
            local_pool=local_pool,
            forbid=forbid,
            picks=picks,
            anchor_pts=anchor_pts,
            meal=meal,
            target_city_key=required_city_key,
            route_start=route_start,
            route_end=route_end,
            meal_anchor=meal_anchor,
            max_anchor_km=meal_max_distance_km,
        )
        if wider_anchor_pick:
            picks.append(wider_anchor_pick)
            forbid.add(_place_key(wider_anchor_pick))
            continue

        ext = _query_external_with_radius_fallback(
            city=city,
            anchors=anchors,
            used_names={_place_key(x) for x in picks}.union(forbid),
            need=8,
            target_city_key=required_city_key,
            required_area_keys=required_area_keys,
            meal_anchor=meal_anchor,
            base_max_distance_km=meal_max_distance_km,
            route_start=route_start,
            route_end=route_end,
            meal=meal,
        )
        if not ext:
            # Last resort: allow cross-day reuse from external results,
            # but still avoid duplicate inside the same day.
            ext = _query_external_with_radius_fallback(
                city=city,
                anchors=anchors,
                used_names={_place_key(x) for x in picks},
                need=8,
                target_city_key=required_city_key,
                required_area_keys=set(),
                meal_anchor=meal_anchor,
                base_max_distance_km=meal_max_distance_km,
                route_start=route_start,
                route_end=route_end,
                meal=meal,
            )
        if ext:
            ext.sort(key=lambda p: _restaurant_composite_score(p, anchor_pts, meal=meal), reverse=True)
            best = _random_pick_from_ranked(ext, window=6)
            best = _with_pick_reason(best, f"external:{meal}")
            picks.append(best)
            forbid.add(_place_key(best))
        else:
            emergency = _query_external_food_emergency(
                city=city,
                used_names={_place_key(x) for x in picks}.union(forbid),
                need=6,
                anchors=anchors,
                target_city_key=required_city_key,
                required_area_keys=required_area_keys,
                meal_anchor=meal_anchor,
                route_start=route_start,
                route_end=route_end,
                meal=meal,
                max_distance_km=meal_max_distance_km,
            )
            if emergency:
                emergency.sort(key=lambda p: _restaurant_composite_score(p, anchor_pts, meal=meal), reverse=True)
                best = _random_pick_from_ranked(emergency, window=3)
                best = _with_pick_reason(best, f"external_emergency:{meal}")
                picks.append(best)
                forbid.add(_place_key(best))
            else:
                picks.append(_with_pick_reason(_restaurant_missing_placeholder(meal=meal), f"missing:{meal}"))

    return picks[0], picks[1], picks[2]


def _pick_same_district_restaurant(
    local_pool: List[dict],
    forbid: set[str],
    picks: List[dict],
    anchor_pts: List[dict],
    meal: str,
    target_city_key: str,
    route_start: dict | None,
    route_end: dict | None,
    meal_anchor: dict | None,
) -> dict | None:
    if not meal_anchor:
        return None
    anchor_areas = _extract_admin_areas(meal_anchor)
    if not anchor_areas:
        return None
    same_day_used = {_place_key(x) for x in picks}
    ranked: List[tuple[float, dict]] = []
    for p in local_pool:
        key = _place_key(p)
        if not key or key in forbid or key in same_day_used:
            continue
        if target_city_key and _place_city_key(p) and _place_city_key(p) != target_city_key:
            continue
        p_areas = _extract_admin_areas(p)
        if not p_areas or not p_areas.intersection(anchor_areas):
            continue
        score = _restaurant_route_score(
            p=p,
            anchor_pts=anchor_pts,
            meal=meal,
            route_start=route_start,
            route_end=route_end,
            meal_anchor=meal_anchor,
            selected_today=picks,
        )
        if score is None:
            continue
        ranked.append((score, p))
    if not ranked:
        return None
    ranked.sort(key=lambda x: x[0], reverse=True)
    best = _random_pick_from_ranked([x[1] for x in ranked], window=8)
    return _with_pick_reason(best, f"same_district_db:{meal}")


def _pick_on_route_restaurant(
    local_pool: List[dict],
    forbid: set[str],
    picks: List[dict],
    anchor_pts: List[dict],
    meal: str,
    target_city_key: str,
    route_start: dict | None,
    route_end: dict | None,
    meal_anchor: dict | None,
) -> dict | None:
    # Progressively widen route corridor; avoid hard reject at the beginning.
    corridor_tiers_km = [1.0, 2.0]
    same_day_used = {_place_key(x) for x in picks}

    for corridor_km in corridor_tiers_km:
        candidates: List[tuple[float, dict]] = []
        for p in local_pool:
            key = _place_key(p)
            if not key or key in forbid or key in same_day_used:
                continue
            if target_city_key and _place_city_key(p) and _place_city_key(p) != target_city_key:
                continue

            seg_km = _distance_to_route_segment_km(p, route_start, route_end)
            if seg_km is None:
                # no geometry route available -> still allow later via anchor fallback
                continue
            if seg_km > corridor_km:
                continue

            on_route_score = _restaurant_route_score(
                p=p,
                anchor_pts=anchor_pts,
                meal=meal,
                route_start=route_start,
                route_end=route_end,
                meal_anchor=meal_anchor,
                selected_today=picks,
            )
            if on_route_score is None:
                continue
            candidates.append((on_route_score, p))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            ranked = [x[1] for x in candidates]
            picked = _random_pick_from_ranked(ranked, window=8)
            seg_km = _distance_to_route_segment_km(picked, route_start, route_end)
            return _with_pick_reason(
                picked,
                f"on_route:{meal}:corridor<={corridor_km:.1f}km"
                + (f":dist_to_axis={seg_km:.2f}km" if isinstance(seg_km, (int, float)) else ""),
            )

    # No local candidate on route; caller will try local <=3km, then external.
    return None


def _pick_near_anchor_restaurant(
    local_pool: List[dict],
    forbid: set[str],
    picks: List[dict],
    anchor_pts: List[dict],
    meal: str,
    target_city_key: str,
    route_start: dict | None,
    route_end: dict | None,
    meal_anchor: dict | None,
    max_anchor_km: float,
) -> dict | None:
    same_day_used = {_place_key(x) for x in picks}
    ranked: List[tuple[float, dict]] = []
    for p in local_pool:
        key = _place_key(p)
        if not key or key in forbid or key in same_day_used:
            continue
        if target_city_key and _place_city_key(p) and _place_city_key(p) != target_city_key:
            continue
        anchor_km = _distance_to_anchor_km(p, meal_anchor)
        if anchor_km is not None and anchor_km > max_anchor_km:
            continue
        score = _restaurant_route_score(
            p=p,
            anchor_pts=anchor_pts,
            meal=meal,
            route_start=route_start,
            route_end=route_end,
            meal_anchor=meal_anchor,
            selected_today=picks,
        )
        if score is None:
            continue
        ranked.append((score, p))
    if not ranked:
        return None
    ranked.sort(key=lambda x: x[0], reverse=True)
    best = _random_pick_from_ranked([x[1] for x in ranked], window=8)
    dist_km = _distance_to_anchor_km(best, meal_anchor)
    reason = f"near_anchor:{meal}:radius<={max_anchor_km:.1f}km"
    if isinstance(dist_km, (int, float)):
        reason += f":dist_to_last_poi={dist_km:.2f}km"
    return _with_pick_reason(best, reason)


def _distance_to_anchor_km(p: dict, anchor: dict | None) -> float | None:
    if not anchor:
        return None
    p_pt = _resolve_point_for_map(p)
    a_pt = _resolve_point_for_map(anchor)
    if not p_pt or not a_pt:
        return None
    return _haversine_km(p_pt[0], p_pt[1], a_pt[0], a_pt[1])


def _max_meal_distance_km(anchor: dict | None) -> float:
    if not anchor:
        return 5.0
    rural_areas = {"hoa vang", "duy xuyen", "dai loc", "thang binh", "tien phuoc", "tam ky", "dien ban", "nui thanh"}
    if _extract_admin_areas(anchor).intersection(rural_areas):
        return 8.0
    return 5.0


def _estimate_detour_minutes(p: dict, route_start: dict | None, route_end: dict | None) -> float | None:
    s_pt = _resolve_point_for_map(route_start)
    e_pt = _resolve_point_for_map(route_end)
    p_pt = _resolve_point_for_map(p)
    if not s_pt or not e_pt or not p_pt:
        return None
    direct_km = _haversine_km(s_pt[0], s_pt[1], e_pt[0], e_pt[1])
    via_km = _haversine_km(s_pt[0], s_pt[1], p_pt[0], p_pt[1]) + _haversine_km(p_pt[0], p_pt[1], e_pt[0], e_pt[1])
    detour_km = max(0.0, via_km - direct_km)
    avg_city_speed_kmh = 25.0
    return detour_km / avg_city_speed_kmh * 60.0


def _restaurant_diversity_penalty(p: dict, selected_today: List[dict]) -> float:
    if not selected_today:
        return 0.0
    text = _fold(
        " ".join(
            [
                str(p.get("name") or ""),
                str(p.get("description") or ""),
                str(p.get("detail_content") or ""),
            ]
        )
    )
    penalty = 0.0
    for chosen in selected_today:
        chosen_text = _fold(
            " ".join(
                [
                    str(chosen.get("name") or ""),
                    str(chosen.get("description") or ""),
                    str(chosen.get("detail_content") or ""),
                ]
            )
        )
        if not chosen_text:
            continue
        if text and text == chosen_text:
            penalty += 10.0
        elif _primary_district_key(chosen) and _primary_district_key(chosen) == _primary_district_key(p):
            penalty += 1.2
    return penalty


def _restaurant_route_score(
    p: dict,
    anchor_pts: List[dict],
    meal: str,
    route_start: dict | None,
    route_end: dict | None,
    meal_anchor: dict | None,
    selected_today: List[dict],
) -> float | None:
    # Hard guardrail: avoid restaurants that require too much route detour.
    detour_min = _estimate_detour_minutes(p, route_start, route_end)
    if isinstance(detour_min, (int, float)) and detour_min > 20.0:
        return None

    route_km = _distance_to_route_segment_km(p, route_start, route_end)
    anchor_km = _distance_to_anchor_km(p, meal_anchor)
    district_soft = _district_distance_bonus(p, anchor_pts)
    fame = _restaurant_fame_score(p, meal=meal)
    diversity_penalty = _restaurant_diversity_penalty(p, selected_today=selected_today)

    route_term = 6.0 if route_km is None else route_km
    anchor_term = 6.0 if anchor_km is None else anchor_km
    detour_term = 8.0 if detour_min is None else detour_min

    # Route-aware ranking:
    # 1) nearest to last POI
    # 2) then on-route distance
    # 3) then detour cost and diversity
    return (
        24.0
        - 4.8 * anchor_term
        - 3.2 * route_term
        - 0.9 * detour_term
        - diversity_penalty
        + 1.1 * fame
        + district_soft
    )


def _query_external_restaurants(
    city: str,
    anchors: List[dict | None],
    used_names: set[str],
    need: int,
    target_city_key: str = "",
    required_area_keys: set[str] | None = None,
    meal_anchor: dict | None = None,
    max_distance_km: float = 3.0,
) -> List[dict]:
    if need <= 0:
        return []
    raw = search_places(f"restaurant in {city}", limit=max(20, need * 8))
    if not raw:
        return []
    anchor_pts = [a for a in anchors if a]
    anchor_city_keys = { _place_city_key(a) for a in anchor_pts if a and _place_city_key(a) }
    anchor_areas: set[str] = set()
    for a in anchor_pts:
        anchor_areas.update(_extract_admin_areas(a))

    out: List[dict] = []
    seen = set(used_names)
    for r in raw:
        name = str(r.get("name") or "").strip()
        if not name or not _is_usable_external_restaurant_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        lat, lon = r.get("lat"), r.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        p = {
            "name": name,
            "address": str(r.get("address") or ""),
            "category": "restaurant",
            "city": str(r.get("city") or city),
            "district": str(r.get("district") or ""),
            "lat": float(lat),
            "lon": float(lon),
            "source": "nominatim-fallback-restaurant",
            "osm_class": str(r.get("osm_class") or ""),
            "osm_type": str(r.get("osm_type") or ""),
            "query_used": f"restaurant in {city}",
        }
        # Keep city consistency when detectable; do not hard-drop unknown-city rows.
        p_city_key = _place_city_key(p)
        if target_city_key and p_city_key and p_city_key != target_city_key:
            continue
        if (not target_city_key) and anchor_city_keys and p_city_key and p_city_key not in anchor_city_keys:
            continue
        if required_area_keys and bool(required_area_keys):
            if not _extract_admin_areas(p).intersection(required_area_keys):
                # Do not enforce district token for Hoi An where external data is often sparse.
                if target_city_key != "hoi_an":
                    continue
        if target_city_key == "da_nang" and "hoi an" in _fold(str(p.get("address") or "")):
            continue
        if not _is_restaurant_within_anchor_radius(p=p, anchor=meal_anchor, max_km=max_distance_km):
            continue
        out.append(p)
        seen.add(key)
        if len(out) >= need:
            break
    out.sort(key=lambda x: _restaurant_composite_score(x, anchor_pts), reverse=True)
    cache_external_places(out[:need])
    return out[:need]


def _query_external_with_radius_fallback(
    city: str,
    anchors: List[dict | None],
    used_names: set[str],
    need: int,
    target_city_key: str,
    required_area_keys: set[str],
    meal_anchor: dict | None,
    base_max_distance_km: float,
    route_start: dict | None,
    route_end: dict | None,
    meal: str,
) -> List[dict]:
    """
    External restaurant fallback order:
    1) Prefer points near the travel axis of the current leg.
    2) Then enforce nearby radius <= 3km.
    3) Then fallback to radius <= 5km.
    3) If still empty, caller will show self-service meal.
    """
    # Stage 1: on-route priority (not hard-locked by meal radius).
    axis_pool = _query_external_restaurants(
        city=city,
        anchors=anchors,
        used_names=used_names,
        need=max(need * 2, 12),
        target_city_key=target_city_key,
        required_area_keys=required_area_keys,
        meal_anchor=meal_anchor,
        max_distance_km=50.0,
    )
    if axis_pool:
        for corridor_km in (1.0, 2.0):
            on_axis = []
            for p in axis_pool:
                seg_km = _distance_to_route_segment_km(p, route_start, route_end)
                if seg_km is None or seg_km > corridor_km:
                    continue
                score = _restaurant_route_score(
                    p=p,
                    anchor_pts=[a for a in anchors if a],
                    meal=meal,
                    route_start=route_start,
                    route_end=route_end,
                    meal_anchor=meal_anchor,
                    selected_today=[],
                )
                if score is None:
                    continue
                on_axis.append((score, p))
            if on_axis:
                on_axis.sort(key=lambda x: x[0], reverse=True)
                return [x[1] for x in on_axis[:need]]

    # Stage 2: strict area + strict radius <= 3km
    ext = _query_external_restaurants(
        city=city,
        anchors=anchors,
        used_names=used_names,
        need=need,
        target_city_key=target_city_key,
        required_area_keys=required_area_keys,
        meal_anchor=meal_anchor,
        max_distance_km=base_max_distance_km,
    )
    if ext:
        return ext

    # Stage 3: relax area, radius <= 5km
    ext = _query_external_restaurants(
        city=city,
        anchors=anchors,
        used_names=used_names,
        need=need,
        target_city_key=target_city_key,
        required_area_keys=set(),
        meal_anchor=meal_anchor,
        max_distance_km=max(5.0, base_max_distance_km),
    )
    if ext:
        return ext
    return []


def _query_external_food_emergency(
    city: str,
    used_names: set[str],
    need: int,
    anchors: List[dict | None],
    target_city_key: str = "",
    required_area_keys: set[str] | None = None,
    meal_anchor: dict | None = None,
    route_start: dict | None = None,
    route_end: dict | None = None,
    meal: str = "lunch",
    max_distance_km: float = 5.0,
) -> List[dict]:
    """Emergency fallback to avoid self-service meals, but still keep meals near the itinerary area."""
    if need <= 0:
        return []
    queries = _emergency_food_queries(city=city, meal_anchor=meal_anchor, required_area_keys=required_area_keys)
    out: List[tuple[float, dict]] = []
    seen = set(used_names)
    anchor_pts = [a for a in anchors if a]
    for q in queries[:_MAX_EXTERNAL_EMERGENCY_FOOD_QUERIES]:
        raw = search_places(q, limit=max(20, need * 6))
        if not raw:
            continue
        for r in raw:
            name = str(r.get("name") or "").strip()
            if not name or not _is_usable_external_restaurant_name(name):
                continue
            key = name.lower()
            if key in seen:
                continue
            lat, lon = r.get("lat"), r.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            p = {
                "name": name,
                "address": str(r.get("address") or ""),
                "category": "restaurant",
                "city": str(r.get("city") or city),
                "district": str(r.get("district") or ""),
                "lat": float(lat),
                "lon": float(lon),
                "source": "nominatim-emergency-food",
                "osm_class": str(r.get("osm_class") or ""),
                "osm_type": str(r.get("osm_type") or ""),
                "query_used": q,
            }
            p_city_key = _place_city_key(p)
            if target_city_key and p_city_key and p_city_key != target_city_key:
                continue
            if required_area_keys:
                p_areas = _extract_admin_areas(p)
                if p_areas and not p_areas.intersection(required_area_keys):
                    continue
            if meal_anchor and not _is_restaurant_within_anchor_radius(p=p, anchor=meal_anchor, max_km=max_distance_km):
                continue
            score = _restaurant_route_score(
                p=p,
                anchor_pts=anchor_pts,
                meal=meal,
                route_start=route_start,
                route_end=route_end,
                meal_anchor=meal_anchor,
                selected_today=[],
            )
            if score is None:
                continue
            out.append((score, p))
            seen.add(key)
    out.sort(key=lambda x: x[0], reverse=True)
    picked = [item for _, item in out[:need]]
    cache_external_places(picked)
    return picked


def _emergency_food_queries(
    city: str,
    meal_anchor: dict | None,
    required_area_keys: set[str] | None,
) -> List[str]:
    locations: List[str] = []
    if required_area_keys:
        for area_key in sorted(required_area_keys):
            label = _AREA_SEARCH_LABELS.get(area_key)
            if label:
                locations.append(label)
    if meal_anchor:
        anchor_city = str(meal_anchor.get("city") or "").strip()
        for area_key in sorted(_extract_admin_areas(meal_anchor)):
            label = _AREA_SEARCH_LABELS.get(area_key)
            if label:
                locations.append(label)
        district = str(meal_anchor.get("district") or "").strip()
        if district and anchor_city:
            locations.append(f"{district}, {anchor_city}")
        address = str(meal_anchor.get("address") or "").strip()
        if address:
            locations.append(address)
    if city:
        locations.append(city)

    deduped_locations: List[str] = []
    seen_locations: set[str] = set()
    for loc in locations:
        key = _fold(loc)
        if not key or key in seen_locations:
            continue
        seen_locations.add(key)
        deduped_locations.append(loc)

    queries: List[str] = []
    for loc in deduped_locations:
        queries.extend(
            [
                f"restaurant in {loc}",
                f"quan an in {loc}",
                f"nha hang in {loc}",
                f"food in {loc}",
                f"cafe in {loc}",
            ]
        )

    seen_queries: set[str] = set()
    ordered: List[str] = []
    for q in queries:
        key = _fold(q)
        if key in seen_queries:
            continue
        seen_queries.add(key)
        ordered.append(q)
    return ordered


def _is_restaurant_within_anchor_radius(p: dict, anchor: dict | None, max_km: float) -> bool:
    """
    Hard guardrail for meals: keep restaurant close to the nearby attraction.
    - If both points have coordinates: enforce haversine <= max_km.
    - If coordinates are missing: require same city + overlapping admin area.
    """
    if not anchor:
        return True
    p_pt = _resolve_point_for_map(p)
    a_pt = _resolve_point_for_map(anchor)
    if p_pt and a_pt:
        return _haversine_km(p_pt[0], p_pt[1], a_pt[0], a_pt[1]) <= max_km
    if _place_city_key(p) and _place_city_key(anchor) and _place_city_key(p) != _place_city_key(anchor):
        return False
    p_areas = _extract_admin_areas(p)
    a_areas = _extract_admin_areas(anchor)
    if p_areas and a_areas:
        return bool(p_areas.intersection(a_areas))
    return False


def _is_usable_external_restaurant_name(name: str) -> bool:
    if not is_user_facing_place_name(name):
        return False
    folded = _fold(name)
    if folded in {"restaurant", "restaurants", "food", "cafe", "quan an", "nha hang"}:
        return False
    return True


def _distance_to_route_segment_km(p: dict, a: dict | None, b: dict | None) -> float | None:
    """Distance from restaurant point to route segment a->b in km."""
    p_pt = _resolve_point_for_map(p)
    a_pt = _resolve_point_for_map(a)
    b_pt = _resolve_point_for_map(b)
    if not p_pt or not a_pt or not b_pt:
        return None
    if a_pt == b_pt:
        return _haversine_km(p_pt[0], p_pt[1], a_pt[0], a_pt[1])

    # Local planar approximation (accurate enough for city-scale routing).
    mean_lat = radians((a_pt[0] + b_pt[0] + p_pt[0]) / 3.0)
    km_per_deg_lat = 110.574
    km_per_deg_lon = 111.320 * cos(mean_lat)

    ax, ay = a_pt[1] * km_per_deg_lon, a_pt[0] * km_per_deg_lat
    bx, by = b_pt[1] * km_per_deg_lon, b_pt[0] * km_per_deg_lat
    px, py = p_pt[1] * km_per_deg_lon, p_pt[0] * km_per_deg_lat

    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    if ab2 <= 1e-9:
        dx, dy = px - ax, py - ay
        return sqrt(dx * dx + dy * dy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    cx, cy = ax + t * abx, ay + t * aby
    dx, dy = px - cx, py - cy
    return sqrt(dx * dx + dy * dy)


def _restaurant_missing_placeholder(meal: str = "") -> dict:
    return {
        "name": "Tự túc (chưa có nhà hàng gần tuyến trong dữ liệu)",
        "address": "",
        "category": "restaurant",
        "source": "fallback-self-service",
    }


def _extract_city(query: str, places: List[dict]) -> str:
    target_city_keys = _target_city_keys(query)
    if len(target_city_keys) > 1:
        return " / ".join(_city_label_from_key(city_key, default_city=city_key.replace("_", " ").title()) for city_key in target_city_keys)
    if len(target_city_keys) == 1:
        return _city_label_from_key(target_city_keys[0], default_city=target_city_keys[0].replace("_", " ").title())
    for p in places:
        city_key = _place_city_key(p)
        if city_key:
            return _city_label_from_key(city_key, default_city=city_key.replace("_", " ").title())
        city = str(p.get("city") or "").strip()
        if city:
            return city
    return "Đà Nẵng"


def _fmt(p: dict | None) -> str:
    if not p:
        return "—"
    name = str(p.get("name") or "").strip()
    address = str(p.get("address") or "").strip()
    map_url = (
        str(p.get("map_place_uri") or "").strip()
        or str(p.get("google_maps_uri") or "").strip()
        or _map_url(p.get("lat"), p.get("lon"))
    )
    map_bit = f" — Map: {map_url}" if map_url else ""
    if address:
        return f"{name} ({address}){map_bit}"
    return f"{name}{map_bit}".strip() or "—"


def _with_pick_reason(p: dict | None, reason: str) -> dict | None:
    if not p:
        return p
    out = dict(p)
    out["selection_reason"] = reason
    return out


def _day_theme_label(day: int, total_days: int, morning: dict | None, afternoon: dict | None) -> str:
    if day == 1:
        return "Khởi động & làm quen điểm đến"
    if day == total_days:
        return "Tổng kết & trải nghiệm cuối"
    blob = _fold(
        " ".join(
            [
                str((morning or {}).get("name") or ""),
                str((afternoon or {}).get("name") or ""),
                str((morning or {}).get("category") or ""),
                str((afternoon or {}).get("category") or ""),
            ]
        )
    )
    if any(k in blob for k in ("bao tang", "museum", "chua", "dinh", "thanh dia", "di tich", "van hoa")):
        return "Văn hóa & di sản"
    if any(k in blob for k in ("beach", "bai bien", "bien", "son tra", "ban dao", "doi", "nui")):
        return "Thiên nhiên & cảnh quan"
    if any(k in blob for k in ("market", "cho", "pho", "night", "giai tri", "cong vien")):
        return "Nhịp sống địa phương"
    return "Khám phá đa chủ đề"


def _slot_action_text(slot: str, place: dict | None, day: int) -> str:
    if not place:
        return "tự do khám phá"
    cat = str(place.get("category") or "").lower()
    name_blob = _fold(str(place.get("name") or ""))
    desc_blob = _fold(str(place.get("description") or ""))
    blob = f"{name_blob} {desc_blob}".strip()
    intent_tags = {
        str(tag).strip().lower()
        for tag in (place.get("intent_tags") or [])
        if str(tag).strip()
    }

    is_museum = cat == "museum" or "bao_tang" in intent_tags or "museum" in blob or "bao tang" in blob
    is_spiritual = (
        "tam_linh" in intent_tags
        or any(k in name_blob for k in ("chua", "pagoda", "den", "linh ung", "nha tho", "thanh dia"))
    )
    is_beach = "bien" in intent_tags or any(k in blob for k in ("beach", "bai bien", "bo bien", "my khe", "an bang"))
    is_nature = (
        "thien_nhien" in intent_tags
        or any(k in blob for k in ("khu bao ton", "thien nhien", "suoi", "thac", "rung", "ban dao", "doi", "nui"))
    )
    is_hot_spring = any(k in blob for k in ("suoi khoang", "khoang nong", "than tai", "hot spring"))
    is_shopping = "mua_sam" in intent_tags or any(k in blob for k in ("shopping", "cho dem", "market", "cho ", "pho di bo"))
    is_viewpoint = any(k in blob for k in ("viewpoint", "ban co", "tower", "skytree"))
    is_culture = (
        "di_tich" in intent_tags
        or any(k in blob for k in ("di tich", "heritage", "historic", "van hoa", "lang co", "nha trung bay"))
    )

    if slot == "morning":
        if is_museum:
            options = [
                "tham quan bộ sưu tập chính",
                "tìm hiểu lịch sử văn hoá",
                "check-in khu trưng bày nổi bật",
            ]
        elif is_spiritual and not is_nature:
            options = [
                "tham quan khu tâm linh",
                "tìm hiểu giá trị di sản",
                "đi bộ và chụp ảnh cảnh quan",
            ]
        elif is_beach:
            options = [
                "tản bộ bờ biển",
                "ngắm cảnh và chụp ảnh buổi sáng",
                "thư giãn nhẹ trước khi di chuyển",
            ]
        elif is_hot_spring:
            options = [
                "thư giãn với không gian suối khoáng",
                "tận hưởng không khí trong lành và nghỉ dưỡng",
                "khám phá khu sinh thái và chụp ảnh",
            ]
        elif is_nature:
            options = [
                "khám phá cảnh quan thiên nhiên",
                "đi bộ ngắm cảnh và chụp ảnh",
                "thư giãn giữa không gian xanh",
            ]
        elif is_culture:
            options = [
                "tìm hiểu giá trị di sản",
                "khám phá dấu ấn văn hoá địa phương",
                "tham quan điểm lịch sử nổi bật",
            ]
        else:
            options = [
                "khám phá điểm nổi bật",
                "tham quan khu vực trung tâm",
                "check-in điểm tham quan chính",
            ]
    elif slot == "afternoon":
        if is_shopping:
            options = [
                "tham quan khu mua sắm / phố đi bộ",
                "dạo quanh khu nhộn nhịp và mua sắm nhẹ",
                "khám phá không gian chợ đêm / phố đi bộ",
            ]
        elif is_hot_spring:
            options = [
                "thư giãn với dịch vụ suối khoáng và nghỉ dưỡng",
                "tận hưởng không gian sinh thái và thư giãn",
                "trải nghiệm hoạt động thư giãn tại khu suối khoáng",
            ]
        elif is_nature:
            options = [
                "khám phá thiên nhiên và ngắm cảnh",
                "đi bộ tham quan các góc cảnh quan nổi bật",
                "thư giãn và trải nghiệm không gian ngoài trời",
            ]
        elif cat == "entertainment" or any(k in blob for k in ("giai tri", "cong vien", "vui choi")):
            options = [
                "trải nghiệm hoạt động giải trí",
                "đi bộ và tận hưởng không gian địa phương",
                "thử các hoạt động địa phương",
            ]
        elif is_viewpoint:
            options = [
                "lên điểm nhìn toàn cảnh",
                "chụp ảnh khung giờ đẹp",
                "thư giãn và ngắm cảnh",
            ]
        elif is_culture or is_spiritual:
            options = [
                "tham quan thêm các góc văn hoá nổi bật",
                "tiếp tục khám phá giá trị lịch sử địa phương",
                "trải nghiệm không gian văn hoá đặc trưng",
            ]
        else:
            options = [
                "khám phá điểm vui chơi",
                "tham quan thêm các góc nổi bật",
                "trải nghiệm văn hoá bản địa",
            ]
    else:
        if any(k in blob for k in ("seafood", "hai san", "bbq", "grill", "lau", "nuong")):
            options = [
                "thử món đặc sản buổi tối",
                "thưởng thức bữa tối đậm chất địa phương",
                "kết hợp ăn tối và thư giãn",
            ]
        else:
            options = [
                "thưởng thức ẩm thực địa phương",
                "đi dạo khu vực nhộn nhịp",
                "kết thúc ngày với không gian thư giãn",
            ]
    return options[(day - 1) % len(options)]


def _map_url(lat: object, lon: object) -> str:
    return _shared_place_map_url({"lat": lat, "lon": lon})


def _segment_map_url(a: dict | None, b: dict | None, engine: str = "fossgis_osrm_car") -> str:
    return _shared_segment_map_url(a, b, engine=engine)


def _build_day_route_plan(
    *,
    day: int,
    start_hotel: dict | None,
    end_hotel: dict | None,
    breakfast: dict | None,
    morning: dict | None,
    lunch: dict | None,
    afternoon: dict | None,
    dinner: dict | None,
) -> List[dict]:
    route_plan: List[dict] = []
    legs = [
        ("Khởi hành", start_hotel, breakfast),
        ("Sáng", breakfast, morning),
        ("Trưa", morning, lunch),
        ("Chiều", lunch, afternoon),
        ("Tối", afternoon, dinner),
        ("Về khách sạn", dinner, end_hotel),
    ]
    for sequence, (leg_label, origin, destination) in enumerate(legs, start=1):
        leg = _build_route_leg(
            day=day,
            sequence=sequence,
            leg_label=leg_label,
            origin=origin,
            destination=destination,
        )
        if leg is not None:
            route_plan.append(leg)
    return route_plan


def _build_route_leg(
    *,
    day: int,
    sequence: int,
    leg_label: str,
    origin: dict | None,
    destination: dict | None,
) -> dict | None:
    if not origin or not destination:
        return None
    from_name = str(origin.get("name") or "").strip()
    to_name = str(destination.get("name") or "").strip()
    if not from_name or not to_name or from_name == to_name:
        return None

    raw_a_pt = _raw_point(origin)
    raw_b_pt = _raw_point(destination)
    payload: dict[str, object] = {
        "day": day,
        "day_label": f"Ngày {day}",
        "sequence": sequence,
        "leg_label": leg_label,
        "from": from_name,
        "to": to_name,
        "from_address": str(origin.get("address") or "").strip(),
        "to_address": str(destination.get("address") or "").strip(),
        "segment_map_url": _segment_map_url(origin, destination),
    }

    # Prefer direct DB coordinates first (fast path) so route planning stays responsive.
    a_pt, b_pt = raw_a_pt, raw_b_pt
    if not a_pt or not b_pt:
        a_pt, b_pt = _resolve_segment_points(origin, destination)
    if not a_pt or not b_pt:
        return payload

    a_lat, a_lon = a_pt
    b_lat, b_lon = b_pt
    origin_point = GeoPoint(lat=a_lat, lon=a_lon)
    destination_point = GeoPoint(lat=b_lat, lon=b_lon)
    fastest = _fastest_route_by_map(origin_point, destination_point)
    same_place = _fold(from_name) == _fold(to_name)
    exact_same_point = a_lat == b_lat and a_lon == b_lon

    if fastest:
        shown_km = float(fastest["distance_km"])
        if not same_place and shown_km < 0.2:
            shown_km = 0.2
        if exact_same_point and same_place:
            shown_km = 0.0
        payload.update(
            {
                "distance_km": round(shown_km, 2),
                "eta_min": max(3, int(fastest["eta_min"])),
                "recommended_mode": str(fastest.get("mode") or ""),
                "mode_label": str(fastest.get("mode_label") or ""),
                "routing_source": str(fastest.get("source") or "trackasia"),
            }
        )
        return payload

    km = _haversine_km(a_lat, a_lon, b_lat, b_lon)
    if not same_place and km < 0.2:
        km = 0.2
    eta_min = max(5, int(round(km / 28 * 60)))
    mode_label = "đi bộ" if km <= 1.0 else "Grab hoặc ô tô"
    payload.update(
        {
            "distance_km": round(km, 2),
            "eta_min": eta_min,
            "recommended_mode": "pedestrian" if km <= 1.0 else "car",
            "mode_label": mode_label,
            "routing_source": "haversine_estimate",
        }
    )
    return payload


def _resolve_point_for_map(p: dict | None) -> tuple[float, float] | None:
    return _shared_resolve_point_for_map(p)


def _resolve_segment_points(
    a: dict | None,
    b: dict | None,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    return _shared_resolve_segment_points(a, b)


def _fastest_route_by_map(origin: GeoPoint, destination: GeoPoint) -> dict | None:
    mode_labels = {
        "car": "ô tô / Grab",
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
            "distance_km": float(est.distance_m) / 1000,
            "source": "trackasia",
        }
        if best is None or cand["eta_min"] < best["eta_min"]:
            best = cand
    return best


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return _shared_haversine_km(lat1, lon1, lat2, lon2)


def _raw_point(place: dict | None) -> tuple[float, float] | None:
    if not place:
        return None
    lat = place.get("lat")
    lon = place.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return float(lat), float(lon)


def _is_quality_attraction_name(name: str) -> bool:
    n = _fold((name or "").strip())
    if not n:
        return False
    bad_prefixes = (
        "duong ",
        "pho ",
        "ngo ",
        "hem ",
        "street ",
        "road ",
        "avenue ",
        "nha ",
    )
    if n.startswith(bad_prefixes):
        return False
    if re.match(r"^\d+[a-zA-Z/-]*\s", n):
        return False
    # Remove address-like labels such as "nha 48 pho hang ngang"
    if (" pho " in n or " duong " in n) and re.search(r"\bnha\s*\d+", n):
        return False
    return True


def _city_key_from_fold(city_fold: str) -> str:
    if "quang nam" in city_fold:
        return "Quang Nam"
    if "hoi an" in city_fold:
        return "Hoi An"
    if "da nang" in city_fold:
        return "Da Nang"
    return ""

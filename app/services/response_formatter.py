from __future__ import annotations

from typing import Any

from app.services.query_utils import extract_trip_days

_RESEARCH_HIGHLIGHTS_HEADER = "CAC DIEM NOI BAT:"
_RESEARCH_STAY_HEADER = "KHU VUC NEN O:"
_RESEARCH_TIPS_HEADER = "TRAVEL TIPS:"
_PLAN_STAY_HEADER = "GOI Y NOI NGHI:"
_PLAN_SUMMARY_PREFIX = "Goi y tong quan:"
_PLAN_NOTE_PREFIX = "Luu y:"
_COORDINATOR_TOP_HEADER = "TOP 3 TRAI NGHIEM NEN UU TIEN:"
_COORDINATOR_CHALLENGE_HEADER = "THACH THUC CO THE GAP & CACH XU LY:"
_COORDINATOR_CHECKLIST_HEADER = "CHECKLIST TRUOC CHUYEN DI:"


def build_time_confirmation_question(destination: str) -> str:
    place_label = destination or "diem den nay"
    return (
        f"Ban du dinh di {place_label} vao ngay cu the nao hoac vao giai doan nao "
        "(vi du: cuoi thang 6, mua he, dip le 2/9)? "
        "Khi ban chot thoi gian, minh se doi chieu thoi tiet va nhac ban can chuan bi gi."
    )


def format_planning_answer(
    *,
    query: str,
    collected_info: dict[str, Any] | None,
    research: str | None,
    plan: str | None,
    coordinator_plan: str | None,
    weather: dict[str, Any] | None,
    transport: list[str] | None,
    recommended_hotel: dict[str, Any] | None,
    mobility_plan: dict[str, Any] | None,
    stay_plan: dict[str, Any] | None,
    route_plan: list[dict[str, Any]] | None,
    plan_validation: dict[str, Any] | None,
    verified_places: list[dict[str, Any]] | None,
) -> str:
    destination = _destination_label(query=query, collected_info=collected_info)
    days = _trip_days_label(query=query, collected_info=collected_info)
    research_sections = _parse_research_summary(research or "")
    itinerary_sections = _parse_itinerary_plan(plan or "")
    coordinator_sections = _parse_coordinator_plan(coordinator_plan or "")
    highlight_lines = _choose_highlights(research_sections, verified_places or [], coordinator_sections)
    stay_lines = _format_stay_lines(
        recommended_hotel=recommended_hotel,
        stay_plan=stay_plan,
        fallback_lines=itinerary_sections["stay_lines"],
    )
    route_lines = _format_route_lines(route_plan or [])
    transport_lines = _format_transport_lines(transport or [])
    weather_lines = _format_weather_lines(weather, mobility_plan)
    checklist_lines = coordinator_sections["checklist_lines"]
    challenge_lines = coordinator_sections["challenge_lines"]
    tips_lines = _merge_unique_lines(
        research_sections["tip_lines"] + itinerary_sections["note_lines"] + challenge_lines + checklist_lines
    )
    follow_up = build_time_confirmation_question(destination)

    intro = research_sections["overview"]
    if not intro:
        intro = (
            f"Minh da tong hop mot ke hoach {days} tai {destination} "
            "de ban co the tham khao va dieu chinh tiep theo nhu cau thuc te."
        )

    lines: list[str] = [
        f"KE HOACH DU LICH GOI Y - {destination.upper()}",
        "",
        "Tom tat nhanh:",
        intro,
    ]
    if weather_lines:
        lines.extend(["", "Tinh hinh thoi tiet/thoi diem hien co:", *weather_lines])

    if highlight_lines:
        lines.extend(["", "Diem nhan hanh trinh:", *highlight_lines])

    if stay_lines:
        lines.extend(["", "Luu tru de xuat:", *stay_lines])

    if itinerary_sections["day_blocks"]:
        lines.extend(["", "Lich trinh chi tiet tung ngay:", *itinerary_sections["day_blocks"]])

    if route_lines or transport_lines:
        lines.append("")
        lines.append("Di chuyen va ban do:")
        lines.extend(route_lines or ["- Chi tiet tung chang da duoc chen trong phan lich trinh moi ngay."])
        if transport_lines:
            lines.extend(["", "Goi y phuong tien:", *transport_lines])

    if tips_lines:
        lines.extend(["", "Tips va luu y truoc chuyen di:", *tips_lines[:10]])

    if plan_validation and not bool(plan_validation.get("passed", True)):
        issues = [str(item).strip() for item in plan_validation.get("issues", []) if str(item).strip()]
        if issues:
            lines.extend(
                [
                    "",
                    "Ghi chu he thong:",
                    "- Lich trinh da duoc kiem tra tu dong va van con mot so diem can xem lai: "
                    + ", ".join(issues) + ".",
                ]
            )

    lines.extend(
        [
            "",
            "De minh tinh chinh theo thoi gian thuc te:",
            follow_up,
        ]
    )
    return "\n".join(line for line in lines if line is not None).strip()


def _destination_label(query: str, collected_info: dict[str, Any] | None) -> str:
    destination = str((collected_info or {}).get("destination") or "").strip()
    if destination:
        return destination
    lowered = (query or "").lower()
    if "hoi an" in lowered:
        return "Hoi An"
    if "quang nam" in lowered:
        return "Quang Nam"
    if "da nang" in lowered or "danang" in lowered or "da nang" in lowered:
        return "Da Nang"
    return "Diem den da chon"


def _trip_days_label(query: str, collected_info: dict[str, Any] | None) -> str:
    raw_days = str((collected_info or {}).get("days") or "").strip()
    if raw_days.isdigit():
        return f"{raw_days} ngay"
    days = extract_trip_days(query)
    if days:
        return f"{days} ngay"
    return "vai ngay"


def _parse_research_summary(text: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in text.splitlines()]
    overview_parts: list[str] = []
    section = "overview"
    highlights: list[str] = []
    stay_lines: list[str] = []
    tip_lines: list[str] = []

    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue
        if line == _RESEARCH_HIGHLIGHTS_HEADER:
            section = "highlights"
            continue
        if line == _RESEARCH_STAY_HEADER:
            section = "stay"
            continue
        if line == _RESEARCH_TIPS_HEADER:
            section = "tips"
            continue
        if section == "overview":
            overview_parts.append(line)
        elif section == "highlights":
            highlights.append(_dashify(line))
        elif section == "stay":
            stay_lines.append(_dashify(line))
        elif section == "tips":
            tip_lines.append(_dashify(line))

    return {
        "overview": " ".join(overview_parts).strip(),
        "highlights": highlights,
        "stay_lines": stay_lines,
        "tip_lines": tip_lines,
    }


def _parse_itinerary_plan(text: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in text.splitlines()]
    stay_lines: list[str] = []
    day_blocks: list[str] = []
    note_lines: list[str] = []

    current_day_title = ""
    current_day_lines: list[str] = []
    in_stay = False

    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue
        if line == _PLAN_STAY_HEADER:
            in_stay = True
            continue
        if line.startswith("NGAY "):
            if current_day_title:
                day_blocks.append(_render_day_block(current_day_title, current_day_lines))
            current_day_title = line
            current_day_lines = []
            in_stay = False
            continue
        if line.startswith(_PLAN_SUMMARY_PREFIX):
            note_lines.append(_dashify(line.split(":", 1)[1].strip()))
            continue
        if line.startswith(_PLAN_NOTE_PREFIX):
            note_lines.append(_dashify(line.split(":", 1)[1].strip()))
            continue
        if in_stay:
            stay_lines.append(_dashify(line))
        elif current_day_title:
            current_day_lines.append(line)

    if current_day_title:
        day_blocks.append(_render_day_block(current_day_title, current_day_lines))

    return {
        "stay_lines": stay_lines,
        "day_blocks": day_blocks,
        "note_lines": note_lines,
    }


def _parse_coordinator_plan(text: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in text.splitlines()]
    top_lines: list[str] = []
    challenge_lines: list[str] = []
    checklist_lines: list[str] = []
    section = ""

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line == _COORDINATOR_TOP_HEADER:
            section = "top"
            continue
        if line == _COORDINATOR_CHALLENGE_HEADER:
            section = "challenge"
            continue
        if line == _COORDINATOR_CHECKLIST_HEADER:
            section = "checklist"
            continue
        if line.endswith(":") and line.isupper():
            section = ""
            continue
        if section == "top":
            top_lines.append(_dashify(line))
        elif section == "challenge":
            challenge_lines.append(_dashify(line))
        elif section == "checklist":
            checklist_lines.append(_dashify(line.replace("□", "").strip()))

    return {
        "top_lines": top_lines,
        "challenge_lines": challenge_lines,
        "checklist_lines": checklist_lines,
    }


def _choose_highlights(
    research_sections: dict[str, Any],
    verified_places: list[dict[str, Any]],
    coordinator_sections: dict[str, Any],
) -> list[str]:
    if research_sections["highlights"]:
        return research_sections["highlights"][:5]
    if coordinator_sections["top_lines"]:
        return coordinator_sections["top_lines"][:5]

    ranked = sorted(
        [
            item for item in verified_places
            if str(item.get("name") or "").strip()
        ],
        key=lambda item: float(item.get("customer_fit_score") or 0.0),
        reverse=True,
    )
    out: list[str] = []
    for item in ranked[:5]:
        name = str(item.get("name") or "").strip()
        category = str(item.get("category") or "").strip()
        address = str(item.get("address") or "").strip()
        detail = name
        if category:
            detail += f" - {category}"
        if address:
            detail += f" ({address})"
        out.append(f"- {detail}")
    return out


def _format_stay_lines(
    *,
    recommended_hotel: dict[str, Any] | None,
    stay_plan: dict[str, Any] | None,
    fallback_lines: list[str],
) -> list[str]:
    if isinstance(recommended_hotel, dict) and recommended_hotel:
        if recommended_hotel.get("type") == "multi_city_stay":
            out: list[str] = []
            for segment in recommended_hotel.get("segments", []) or []:
                hotel = segment.get("hotel") or {}
                hotel_name = str(hotel.get("name") or "").strip() or "chua co ten khach san"
                city_label = str(segment.get("city_label") or segment.get("city_key") or "").strip()
                days = segment.get("days") or []
                day_label = _days_compact_label(days)
                address = str(hotel.get("address") or "").strip()
                reason = str(segment.get("reason") or segment.get("reason") or "").strip()
                line = f"- {day_label}: o tai {hotel_name}"
                if city_label:
                    line += f" ({city_label})"
                if address:
                    line += f" - {address}"
                if reason:
                    line += f". Ly do: {reason}"
                out.append(line)
            if out:
                return out
        name = str(recommended_hotel.get("name") or "").strip()
        if name:
            out = [f"- Khach san goi y chinh: {name}"]
            address = str(recommended_hotel.get("address") or "").strip()
            if address:
                out.append(f"- Dia chi: {address}")
            reason = str(recommended_hotel.get("reason") or "").strip()
            if reason:
                out.append(f"- Ly do phu hop: {reason}")
            return out

    if stay_plan:
        out = []
        for segment in stay_plan.get("segments", []) or []:
            hotel = segment.get("hotel") or {}
            hotel_name = str(hotel.get("name") or "").strip()
            if not hotel_name:
                continue
            line = f"- {str(segment.get('days_label') or _days_compact_label(segment.get('days') or [])).strip()}: {hotel_name}"
            address = str(hotel.get("address") or "").strip()
            if address:
                line += f" - {address}"
            out.append(line)
        if out:
            return out

    return fallback_lines[:4]


def _format_route_lines(route_plan: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for item in route_plan[:6]:
        origin = str(item.get("from") or "").strip()
        destination = str(item.get("to") or "").strip()
        if not origin or not destination:
            continue
        distance = item.get("distance_km")
        eta = item.get("eta_min")
        mode = str(item.get("mode_label") or item.get("recommended_mode") or "").strip()
        line = f"- {origin} -> {destination}"
        details: list[str] = []
        if isinstance(distance, (int, float)):
            details.append(f"~{float(distance):.1f} km")
        if isinstance(eta, (int, float)):
            details.append(f"{int(eta)} phut")
        if mode:
            details.append(f"nen di {mode}")
        if details:
            line += ": " + ", ".join(details)
        from_map = str(item.get("from_map_url") or "").strip()
        to_map = str(item.get("to_map_url") or "").strip()
        if from_map or to_map:
            map_bits = [part for part in [from_map, to_map] if part]
            line += ". Map: " + " | ".join(map_bits)
        out.append(line)
    return out


def _format_transport_lines(transport: list[str]) -> list[str]:
    return [_dashify(line) for line in transport if str(line).strip()]


def _format_weather_lines(
    weather: dict[str, Any] | None,
    mobility_plan: dict[str, Any] | None,
) -> list[str]:
    if not weather:
        lines = [
            "- Hien ban chua chot ngay di cu the, vi vay minh moi de lich trinh o muc khung tham khao.",
            "- Khi ban gui ngay/thang hoac mua du lich, minh se doi chieu thoi tiet va nhac ban can mang theo gi.",
        ]
        if mobility_plan:
            fastest = str(mobility_plan.get("fastest_mode_label") or "").strip()
            eta = mobility_plan.get("avg_eta_min")
            if fastest:
                extra = f"- Tam thoi, phuong an di chuyen nhanh nhat dang uu tien la {fastest}"
                if isinstance(eta, (int, float)):
                    extra += f", ETA trung binh khoang {int(round(float(eta)))} phut."
                lines.append(extra)
        return lines

    location = str(weather.get("location") or "diem den").strip()
    description = str(weather.get("description") or "").strip()
    temp = weather.get("temp_c")
    wind = weather.get("wind_kmh")
    line = f"- Tham khao hien tai tai {location}:"
    detail_bits: list[str] = []
    if description:
        detail_bits.append(description)
    if isinstance(temp, (int, float)):
        detail_bits.append(f"{float(temp):.1f}°C")
    if isinstance(wind, (int, float)):
        detail_bits.append(f"gio ~{float(wind):.0f} km/h")
    if detail_bits:
        line += " " + ", ".join(detail_bits) + "."
    out = [line]
    if mobility_plan:
        fastest = str(mobility_plan.get("fastest_mode_label") or "").strip()
        eta = mobility_plan.get("avg_eta_min")
        if fastest:
            note = f"- Theo phan tich di chuyen, phuong an nhanh nhat hien tai la {fastest}"
            if isinstance(eta, (int, float)):
                note += f" voi ETA trung binh khoang {int(round(float(eta)))} phut."
            out.append(note)
    out.append("- Neu ban thay doi ngay di, minh nen cap nhat lai du bao thoi tiet de tinh chinh lich.")
    return out


def _render_day_block(title: str, lines: list[str]) -> str:
    block = [title]
    block.extend(lines)
    return "\n".join(block).strip()


def _merge_unique_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw_line in lines:
        line = _dashify(raw_line)
        normalized = line.lstrip("- ").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(line)
    return out


def _dashify(line: str) -> str:
    text = str(line or "").strip()
    if not text:
        return ""
    if text.startswith("- "):
        return text
    if text.startswith("• "):
        return "- " + text[2:].strip()
    if text.startswith("□ "):
        return "- " + text[2:].strip()
    return "- " + text


def _days_compact_label(days: Any) -> str:
    if not isinstance(days, list) or not days:
        return "lich chua xac dinh ngay"
    if len(days) == 1:
        return f"Ngay {days[0]}"
    return f"Ngay {days[0]}-{days[-1]}"

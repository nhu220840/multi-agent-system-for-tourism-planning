from __future__ import annotations

import re
from typing import Any

from app.services.place_metadata import normalize_address_text
from app.services.query_utils import extract_trip_days

# Headers produced by upstream agents (kept ASCII-fold because the source
# strings live in the other services). The final user-facing output below is
# written in proper diacritic Vietnamese.
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
    place_label = destination or "điểm đến này"
    return (
        f"Bạn dự định đi {place_label} vào ngày cụ thể nào hoặc vào giai đoạn nào "
        "(ví dụ: cuối tháng 6, mùa hè, dịp lễ 2/9)? "
        "Khi bạn chốt thời gian, mình sẽ đối chiếu thời tiết và nhắc bạn cần chuẩn bị gì."
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
    stay_recommendations: list[dict[str, Any]] | None,
    plan_validation: dict[str, Any] | None,
    verified_places: list[dict[str, Any]] | None,
    route_plan: list[dict[str, Any]] | None = None,
) -> str:
    destination = _destination_label(query=query, collected_info=collected_info)
    days_label = _trip_days_label(query=query, collected_info=collected_info)
    research_sections = _parse_research_summary(research or "")
    itinerary_sections = _parse_itinerary_plan(plan or "")
    coordinator_sections = _parse_coordinator_plan(coordinator_plan or "")
    highlight_lines = _choose_highlights(research_sections, verified_places or [], coordinator_sections)
    stay_lines = _format_stay_lines(
        recommended_hotel=recommended_hotel,
        stay_plan=stay_plan,
        stay_recommendations=stay_recommendations,
        fallback_lines=itinerary_sections["stay_lines"],
    )
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
            f"{destination} là điểm đến phù hợp cho hành trình {days_label} "
            "với trải nghiệm cân bằng giữa tham quan, ẩm thực và văn hoá. "
            "Mình đã tổng hợp gợi ý bên dưới để bạn tham khảo và tinh chỉnh theo nhu cầu thực tế."
        )

    lines: list[str] = []

    def add_section(title: str, body_lines: list[str], *, keep_blank: bool = False) -> None:
        if keep_blank:
            body = list(body_lines)
            while body and not str(body[0]).strip():
                body.pop(0)
            while body and not str(body[-1]).strip():
                body.pop()
            cleaned = body
        else:
            cleaned = [line for line in body_lines if str(line).strip()]
        if not cleaned:
            return
        if lines:
            lines.append("")
        lines.append(title)
        lines.append("")
        lines.extend(_prettify_vietnamese(line) for line in cleaned)

    lines.append(_prettify_vietnamese(f"KẾ HOẠCH DU LỊCH GỢI Ý — {destination.upper()}"))
    lines.append(f"Hành trình: {days_label}")

    add_section("TÓM TẮT NHANH", [intro])
    add_section("THỜI TIẾT & THỜI ĐIỂM", weather_lines)
    add_section("ĐIỂM NHẤN HÀNH TRÌNH", highlight_lines)
    add_section("NƠI LƯU TRÚ ĐỀ XUẤT", stay_lines)
    add_section("LỊCH TRÌNH CHI TIẾT", itinerary_sections["day_blocks"], keep_blank=True)
    add_section("DI CHUYỂN GỢI Ý", transport_lines)
    add_section("MẸO & LƯU Ý", tips_lines[:10])
    add_section("BƯỚC TIẾP THEO", [follow_up])

    return "\n".join(lines).strip()


def _destination_label(query: str, collected_info: dict[str, Any] | None) -> str:
    destination = str((collected_info or {}).get("destination") or "").strip()
    if destination:
        return destination
    lowered = (query or "").lower()
    if "hoi an" in lowered or "hội an" in lowered:
        return "Hội An"
    if "quang nam" in lowered or "quảng nam" in lowered:
        return "Quảng Nam"
    if "da nang" in lowered or "danang" in lowered or "đà nẵng" in lowered:
        return "Đà Nẵng"
    return "Điểm đến đã chọn"


def _trip_days_label(query: str, collected_info: dict[str, Any] | None) -> str:
    raw_days = str((collected_info or {}).get("days") or "").strip()
    if raw_days.isdigit():
        return f"{raw_days} ngày"
    days = extract_trip_days(query)
    if days:
        return f"{days} ngày"
    return "vài ngày"


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
            highlights.append(_bulletify(line))
        elif section == "stay":
            stay_lines.append(_bulletify(line))
        elif section == "tips":
            tip_lines.append(_bulletify(line))

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
            if current_day_title and (not current_day_lines or current_day_lines[-1] != ""):
                current_day_lines.append("")
            continue
        if line == _PLAN_STAY_HEADER:
            in_stay = True
            continue
        if _is_day_header(line):
            if current_day_title:
                day_blocks.append(_render_day_block(current_day_title, current_day_lines))
                day_blocks.append("")
            current_day_title = _format_day_header(line)
            current_day_lines = []
            in_stay = False
            continue
        if line.startswith(_PLAN_SUMMARY_PREFIX) or line.lower().startswith("gợi ý tổng quan:"):
            note_lines.append(_bulletify(line.split(":", 1)[1].strip()))
            continue
        if line.startswith(_PLAN_NOTE_PREFIX) or line.lower().startswith("lưu ý:"):
            note_lines.append(_bulletify(line.split(":", 1)[1].strip()))
            continue
        if in_stay:
            cleaned = _clean_user_facing_line(line)
            if cleaned:
                stay_lines.append(_bulletify(cleaned))
        elif current_day_title:
            cleaned = _clean_user_facing_line(line)
            if cleaned:
                current_day_lines.append(cleaned)

    if current_day_title:
        while current_day_lines and not current_day_lines[-1]:
            current_day_lines.pop()
        day_blocks.append(_render_day_block(current_day_title, current_day_lines))

    while day_blocks and not day_blocks[-1].strip():
        day_blocks.pop()

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
            top_lines.append(_bulletify(line))
        elif section == "challenge":
            challenge_lines.append(_bulletify(line))
        elif section == "checklist":
            checklist_lines.append(_bulletify(line.replace("□", "").strip()))

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
        address = normalize_address_text(str(item.get("address") or ""))
        detail = name
        if category and category.strip().lower() not in {"destination", "tourism"}:
            detail += f" — {category}"
        if address:
            detail += f" ({address})"
        out.append(f"• {detail}")
    return out


def _format_stay_lines(
    *,
    recommended_hotel: dict[str, Any] | None,
    stay_plan: dict[str, Any] | None,
    stay_recommendations: list[dict[str, Any]] | None,
    fallback_lines: list[str],
) -> list[str]:
    if stay_recommendations:
        out: list[str] = []
        for item in stay_recommendations[:2]:
            segment = str(item.get("segment") or "").strip()
            name = str(item.get("name") or "").strip()
            price_note = str(item.get("price_note") or "").strip()
            address = normalize_address_text(str(item.get("address") or ""))
            why_fit = str(item.get("why_fit") or "").strip()
            if not segment or not name:
                continue
            out.append(f"• {segment}: {name}")
            if price_note:
                out.append(f"   – Giá tham khảo: {price_note}")
            if address:
                out.append(f"   – Địa chỉ: {address}")
            if why_fit:
                out.append(f"   – Phù hợp vì: {why_fit}")
        return out

    if isinstance(recommended_hotel, dict) and recommended_hotel:
        if recommended_hotel.get("type") == "multi_city_stay":
            out: list[str] = []
            for segment in recommended_hotel.get("segments", []) or []:
                hotel = segment.get("hotel") or {}
                hotel_name = str(hotel.get("name") or "").strip() or "chưa có tên khách sạn"
                city_label = str(segment.get("city_label") or segment.get("city_key") or "").strip()
                days = segment.get("days") or []
                day_label = _days_compact_label(days)
                address = str(hotel.get("address") or "").strip()
                line = f"• {day_label}: nghỉ tại {hotel_name}"
                if city_label:
                    line += f" ({city_label})"
                out.append(line)
                if address:
                    out.append(f"   – Địa chỉ: {address}")
            if out:
                return out
        name = str(recommended_hotel.get("name") or "").strip()
        if name:
            out = [f"• Khách sạn gợi ý chính: {name}"]
            address = normalize_address_text(str(recommended_hotel.get("address") or ""))
            if address:
                out.append(f"   – Địa chỉ: {address}")
            return out

    if stay_plan:
        out = []
        for segment in stay_plan.get("segments", []) or []:
            hotel = segment.get("hotel") or {}
            hotel_name = str(hotel.get("name") or "").strip()
            if not hotel_name:
                continue
            day_label = str(segment.get("days_label") or _days_compact_label(segment.get("days") or [])).strip()
            out.append(f"• {day_label}: {hotel_name}")
            address = str(hotel.get("address") or "").strip()
            if address:
                out.append(f"   – Địa chỉ: {address}")
        if out:
            return out

    return fallback_lines[:4]


def _format_transport_lines(transport: list[str]) -> list[str]:
    return [_bulletify(line) for line in transport if str(line).strip()]


def _format_weather_lines(
    weather: dict[str, Any] | None,
    mobility_plan: dict[str, Any] | None,
) -> list[str]:
    if not weather:
        lines = [
            "• Hiện bạn chưa chốt ngày đi cụ thể, vì vậy lịch trình đang để ở khung tham khảo.",
            "• Khi bạn gửi ngày/tháng hoặc mùa du lịch, mình sẽ đối chiếu thời tiết và nhắc bạn cần mang theo gì.",
        ]
        if mobility_plan:
            fastest = str(mobility_plan.get("fastest_mode_label") or "").strip()
            eta = mobility_plan.get("avg_eta_min")
            if fastest:
                extra = f"• Tạm thời, phương án di chuyển nhanh nhất đang ưu tiên là {fastest}"
                if isinstance(eta, (int, float)):
                    extra += f", ETA trung bình khoảng {int(round(float(eta)))} phút."
                else:
                    extra += "."
                lines.append(extra)
        return lines

    location = str(weather.get("location") or "điểm đến").strip()
    description = str(weather.get("description") or "").strip()
    temp = weather.get("temp_c")
    wind = weather.get("wind_kmh")
    line = f"• Tham khảo hiện tại tại {location}:"
    detail_bits: list[str] = []
    if description:
        detail_bits.append(description)
    if isinstance(temp, (int, float)):
        detail_bits.append(f"{float(temp):.1f}°C")
    if isinstance(wind, (int, float)):
        detail_bits.append(f"gió ~{float(wind):.0f} km/h")
    if detail_bits:
        line += " " + ", ".join(detail_bits) + "."
    out = [line]
    if mobility_plan:
        fastest = str(mobility_plan.get("fastest_mode_label") or "").strip()
        eta = mobility_plan.get("avg_eta_min")
        if fastest:
            note = f"• Theo phân tích di chuyển, phương án nhanh nhất hiện tại là {fastest}"
            if isinstance(eta, (int, float)):
                note += f" với ETA trung bình khoảng {int(round(float(eta)))} phút."
            else:
                note += "."
            out.append(note)
    out.append("• Nếu bạn thay đổi ngày đi, mình sẽ cập nhật lại dự báo thời tiết để tinh chỉnh lịch.")
    return out


def _is_day_header(line: str) -> bool:
    return bool(re.match(r"^(NGAY|Ngày)\s+\d+", line, flags=re.IGNORECASE))


def _format_day_header(line: str) -> str:
    """Normalise legacy 'NGAY 1 - THEME' and new 'Ngày 1 — Theme' headers."""
    match = re.match(r"^(?:NGAY|Ngày)\s+(\d+)\s*[-—:]?\s*(.*)$", line, flags=re.IGNORECASE)
    if not match:
        return line
    day = match.group(1)
    theme = (match.group(2) or "").strip()
    if theme:
        return f"▸ Ngày {day} — {theme}"
    return f"▸ Ngày {day}"


def _render_day_block(title: str, lines: list[str]) -> str:
    block = [title]
    block.extend(lines)
    return "\n".join(block).strip()


def _clean_user_facing_line(line: str) -> str:
    text = str(line or "").strip()
    if not text:
        return ""
    if re.match(r"^[-•]?\s*(ly do phu hop|lý do phù hợp):", text, flags=re.IGNORECASE):
        return ""
    if re.match(r"^(?:tom tat|thong tin|tóm tắt|thông tin)\s+di\s*chuy[eê]n:\s*$", text, flags=re.IGNORECASE):
        return ""
    if re.match(r"^[-•]?\s*chang\s+\d+\s*:", text, flags=re.IGNORECASE):
        return ""
    if re.match(r"^[-•]?\s*chặng\s+\d+\s*:", text, flags=re.IGNORECASE):
        return ""
    if re.match(r"^[-•]?\s*Link\s*chặng:", text, flags=re.IGNORECASE):
        return ""
    if re.match(r"^[-•]?\s*Link chặng:", text, flags=re.IGNORECASE):
        return ""
    if re.match(r"^[-•]?\s*(Nghi dem|Nghỉ đêm)\s*:", text, flags=re.IGNORECASE):
        return ""

    if text.startswith("• Ban do tuyen ngay:"):
        return ""
    if text.startswith("• Bản đồ tuyến ngày:"):
        return ""
    if text.startswith("• Thu tu diem:"):
        return ""
    if text.startswith("• Thứ tự điểm:"):
        return ""
    if re.match(r"^(?:[-•]+\s*)?Link chặng:\s*https?://\S+$", text, flags=re.IGNORECASE):
        return ""
    if re.match(r"^(?:[-•]+\s*)?Link\s*chặng:\s*https?://\S+$", text, flags=re.IGNORECASE):
        return ""
    if "->" in text and "http" in text and not re.search(r"\b(?:km|phut)\b", text, flags=re.IGNORECASE):
        return ""
    if (
        "->" in text
        and "http" in text
        and not re.search(r"\b(?:km|phut|phút)\b", text, flags=re.IGNORECASE)
    ):
        return ""
    if text == "Map tung chang:" or text == "Bản đồ từng chặng:":
        return ""

    text = re.sub(
        r"\s*—\s*(?:Nguon|Nguồn):\s*.*?(?=(?:\s*—\s*(?:Ly do chon|Lý do chọn|Map|Bản đồ):)|(?:\.\s+(?:Hanh dong|Hoạt động):)|(?:\.\s+Link\s*chặng:)|(?:\.\s*$)|$)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*—\s*(?:Ly do chon|Lý do chọn):\s*.*?(?=(?:\s*—\s*(?:Map|Bản đồ):)|(?:\.\s+(?:Hanh dong|Hoạt động):)|(?:\.\s+Link\s*chặng:)|(?:\.\s*$)|$)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*\((?:nguon map|nguồn map):\s*[^)]+\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*-\s*destination\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(\s*,\s*", "(", text)
    text = re.sub(r",\s*,+", ", ", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s*—\s*(?:Map|Bản đồ):\s*(https?://\S+)", r" — Bản đồ: \1", text)
    text = text.replace(" . ", ". ")
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = _rewrite_itinerary_prose(text)
    return _prettify_vietnamese(text)


def _merge_unique_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw_line in lines:
        line = _bulletify(raw_line)
        normalized = line.lstrip("•- ").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(line)
    return out


def _bulletify(line: str) -> str:
    text = str(line or "").strip()
    if not text:
        return ""
    if text.startswith("• "):
        return text
    if text.startswith("- "):
        return "• " + text[2:].strip()
    if text.startswith("□ "):
        return "• " + text[2:].strip()
    return "• " + text


def _days_compact_label(days: Any) -> str:
    if not isinstance(days, list) or not days:
        return "Ngày chưa xác định"
    if len(days) == 1:
        return f"Ngày {days[0]}"
    return f"Ngày {days[0]}–{days[-1]}"


def _prettify_vietnamese(text: str) -> str:
    out = str(text or "")
    replacements: list[tuple[str, str]] = [
        (r"\bDa Nang\b", "Đà Nẵng"),
        (r"\bHoi An\b", "Hội An"),
        (r"\bQuang Nam\b", "Quảng Nam"),
        (r"\bNgay\b", "Ngày"),
        (r"\bSang:", "Sáng:"),
        (r"\bTrua:", "Trưa:"),
        (r"\bChieu:", "Chiều:"),
        (r"\bToi:", "Tối:"),
        (r"\bHanh dong\b", "Hoạt động"),
        (r"\bChi duong\b", "Chỉ đường"),
        (r"\bBan do\b", "Bản đồ"),
        (r"\bdia diem\b", "địa điểm"),
        (r"\bdiem den\b", "điểm đến"),
        (r"\bhanh trinh\b", "hành trình"),
        (r"\btham khao\b", "tham khảo"),
        (r"\bthoi tiet\b", "thời tiết"),
        (r"\bdoi chieu\b", "đối chiếu"),
        (r"\bam thuc\b", "ẩm thực"),
        (r"\bvan hoa\b", "văn hoá"),
        (r"\btrai nghiem\b", "trải nghiệm"),
        (r"\bgoi y\b", "gợi ý"),
        (r"\bluu tru\b", "lưu trú"),
        (r"\bdi chuyen\b", "di chuyển"),
        (r"\bkhach san\b", "khách sạn"),
        (r"\bphu hop\b", "phù hợp"),
        (r"\bmeo\b", "mẹo"),
        (r"\bluu y\b", "lưu ý"),
        (r"\bco the\b", "có thể"),
        (r"\bdieu chinh\b", "điều chỉnh"),
        (r"\bthuc te\b", "thực tế"),
        (r"\bchua\b", "chưa"),
        (r"\bgan\b", "gần"),
        (r"\bbua sang\b", "bữa sáng"),
        (r"\bbua trua\b", "bữa trưa"),
        (r"\bbua toi\b", "bữa tối"),
        (r"\btu tuc\b", "tự túc"),
        (r"\bnghi\b", "nghỉ"),
    ]
    for pattern, replacement in replacements:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out


def _rewrite_itinerary_prose(text: str) -> str:
    out = str(text or "").strip()
    if not out:
        return out

    self_service_replacements = [
        (
            r"^(?:[•-]\s*)?(?:Sang|Sáng):\s*An sang tai An sang tu tuc \(khong tim thay dia diem phu hop gan hanh trinh\)\.?\s*",
            "Sáng: Tự túc bữa sáng vì chưa tìm thấy địa điểm phù hợp gần hành trình. ",
        ),
        (
            r"^(?:[•-]\s*)?(?:Trua|Trưa):\s*An trua tai An trua tu tuc \(khong tim thay dia diem phu hop gan hanh trinh\)\.?\s*",
            "Trưa: Tự túc bữa trưa vì chưa tìm thấy địa điểm phù hợp gần hành trình. ",
        ),
        (
            r"^(?:[•-]\s*)?(?:Toi|Tối):\s*An toi tai An toi tu tuc \(khong tim thay dia diem phu hop gan hanh trinh\)\.?\s*",
            "Tối: Tự túc bữa tối vì chưa tìm thấy địa điểm phù hợp gần hành trình. ",
        ),
    ]
    for pattern, replacement in self_service_replacements:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)

    if re.match(r"^(?:[•-]\s*)?(?:Sang|Sáng):", out, flags=re.IGNORECASE):
        out = re.sub(r"\.\s*(?:Hanh dong|Hoạt động):\s*", ". Sau đó, bạn có thể ", out, flags=re.IGNORECASE)
    elif re.match(r"^(?:[•-]\s*)?(?:Chieu|Chiều):", out, flags=re.IGNORECASE):
        out = re.sub(
            r"^(?:([•-]\s*)?(?:Chieu|Chiều):)\s*(?:Hanh dong|Hoạt động):\s*",
            r"\1 Buổi chiều phù hợp để ",
            out,
            flags=re.IGNORECASE,
        )
    elif re.match(r"^(?:[•-]\s*)?(?:Toi|Tối):", out, flags=re.IGNORECASE):
        out = re.sub(r"\.\s*(?:Hanh dong|Hoạt động):\s*", ". Buổi tối, bạn có thể ", out, flags=re.IGNORECASE)

    out = re.sub(r"\bdi dao/chill\b", "dạo chơi và thư giãn", out, flags=re.IGNORECASE)
    out = re.sub(r"\bquanh khu vuc\b", "quanh khu vực", out, flags=re.IGNORECASE)
    out = re.sub(r"\ban sang tai\b", "ăn sáng tại", out, flags=re.IGNORECASE)
    out = re.sub(r"\ban trua tai\b", "ăn trưa tại", out, flags=re.IGNORECASE)
    out = re.sub(r"\ban toi tai\b", "ăn tối tại", out, flags=re.IGNORECASE)
    return out.strip()

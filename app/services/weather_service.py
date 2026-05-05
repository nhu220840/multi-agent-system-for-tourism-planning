from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
import re
from typing import Any
from urllib.parse import urlencode
import urllib.request

from app.services.place_metadata import fold_text as _fold

_OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_FORECAST_LOOKAHEAD_DAYS = 16

_DESTINATION_COORDS: dict[str, dict[str, float | str]] = {
    "da_nang": {
        "label": "Đà Nẵng",
        "latitude": 16.0544,
        "longitude": 108.2022,
    },
    "hoi_an": {
        "label": "Hội An",
        "latitude": 15.8801,
        "longitude": 108.3380,
    },
    "quang_nam": {
        "label": "Quảng Nam",
        "latitude": 15.5394,
        "longitude": 108.0191,
    },
}

_SEASONAL_NOTES: dict[int, list[str]] = {
    1: [
        "Thời điểm này ở miền Trung thường mát, đôi lúc có mưa nhẹ về chiều.",
        "Nên mang thêm áo khoác mỏng nếu đi biển hoặc lên khu cao gió.",
    ],
    2: [
        "Thời tiết thường dễ chịu, phù hợp cho lịch trình ngoài trời kéo dài.",
        "Vẫn nên kiểm tra lại dự báo sát ngày đi để tránh mưa trái mùa.",
    ],
    3: [
        "Đây là giai đoạn khá đẹp để đi Đà Nẵng, nắng nhiều hơn và ít mưa.",
        "Nếu đi ngoài trời lâu, nên mang nón và kem chống nắng.",
    ],
    4: [
        "Thời tiết thường khô ráo, thích hợp cho biển và các điểm tham quan ngoài trời.",
        "Buổi trưa có thể nắng gắt, nên ưu tiên hoạt động ngoài trời vào sáng sớm hoặc chiều.",
    ],
    5: [
        "Bắt đầu vào mùa nắng rõ hơn, khá hợp cho biển và các điểm ngắm cảnh.",
        "Nên chuẩn bị nước, kính mát và tránh xếp lịch leo bộ quá dày vào giữa trưa.",
    ],
    6: [
        "Tháng này thường nắng đẹp, rất hợp để đi biển hoặc bán đảo Sơn Trà.",
        "Khung 11h-15h có thể nóng, nên ưu tiên hoạt động trong nhà hoặc nghỉ trưa.",
    ],
    7: [
        "Mùa hè ở Đà Nẵng thường nắng mạnh, rất hợp cho lịch trình biển nếu đi sớm hoặc chiều muộn.",
        "Cần chống nắng kỹ và hạn chế xếp các chặng đi bộ dài vào đầu giờ chiều.",
    ],
    8: [
        "Thời tiết vẫn thiên về nắng nóng, phù hợp cho các trải nghiệm ngoài trời có kiểm soát thời gian.",
        "Nên giữ lịch linh hoạt để tránh nắng gắt hoặc mưa dông cục bộ cuối ngày.",
    ],
    9: [
        "Thời điểm giao mùa, có thể xuất hiện mưa hoặc dông rải rác hơn.",
        "Nên kiểm tra forecast sát ngày để quyết định có cần đổi ưu tiên sang điểm trong nhà hay không.",
    ],
    10: [
        "Đây thường là giai đoạn mưa nhiều hơn ở miền Trung.",
        "Nên chuẩn bị phương án thay thế trong nhà và hạn chế phụ thuộc quá nhiều vào biển.",
    ],
    11: [
        "Khả năng mưa và gió tăng lên, đặc biệt với lịch trình ngoài trời.",
        "Phù hợp hơn nếu giữ kế hoạch linh hoạt và có sẵn điểm tham quan trong nhà.",
    ],
    12: [
        "Cuối năm thường mát hơn nhưng vẫn có thể còn mưa ở miền Trung.",
        "Nên mang áo khoác mỏng, ô hoặc áo mưa gọn nhẹ.",
    ],
}


@dataclass(frozen=True)
class ParsedTravelWindow:
    status: str
    start_date: date | None = None
    end_date: date | None = None
    label: str = ""


def get_weather_for_trip(
    *,
    query: str,
    destination_hint: str | None = None,
    trip_days: int | None = None,
) -> dict[str, Any] | None:
    text = str(query or "").strip()
    if not text:
        return None

    destination_key = _resolve_destination_key(destination_hint or text)
    destination_meta = _DESTINATION_COORDS.get(destination_key, _DESTINATION_COORDS["da_nang"])
    parsed_window = _parse_travel_window(text, trip_days=trip_days)
    if parsed_window.status == "missing":
        return None

    if parsed_window.status != "specific" or not parsed_window.start_date:
        return {
            "forecast_status": "need_specific_date",
            "location": str(destination_meta["label"]),
            "travel_window_label": parsed_window.label or "thời gian bạn vừa nêu",
            "advice_lines": [
                "Mình đã ghi nhận thời gian bạn muốn đi, nhưng mốc này chưa đủ cụ thể để check forecast chi tiết.",
                "Bạn có thể gửi ngày rõ hơn, ví dụ 12/06/2026 hoặc 12-14/06/2026.",
            ],
            "plan_adjustment_suggestions": [],
            "should_offer_replan": False,
        }

    today = date.today()
    forecast_deadline = today + timedelta(days=_FORECAST_LOOKAHEAD_DAYS - 1)
    if parsed_window.start_date > forecast_deadline:
        return {
            "forecast_status": "out_of_range",
            "location": str(destination_meta["label"]),
            "travel_window_label": parsed_window.label,
            "month": parsed_window.start_date.month,
            "advice_lines": _SEASONAL_NOTES.get(
                parsed_window.start_date.month,
                [
                    "Thời điểm này còn quá xa để lấy forecast ngày cụ thể.",
                    "Bạn có thể quay lại sát ngày đi hơn để mình check thời tiết chính xác.",
                ],
            ),
            "plan_adjustment_suggestions": [],
            "should_offer_replan": False,
        }

    end_date = parsed_window.end_date or parsed_window.start_date
    if end_date > forecast_deadline:
        end_date = forecast_deadline

    weather = _fetch_open_meteo_forecast(
        latitude=float(destination_meta["latitude"]),
        longitude=float(destination_meta["longitude"]),
        start_date=parsed_window.start_date,
        end_date=end_date,
    )
    if not weather:
        return {
            "forecast_status": "api_unavailable",
            "location": str(destination_meta["label"]),
            "travel_window_label": parsed_window.label,
            "advice_lines": [
                "Hiện mình chưa lấy được forecast chi tiết cho thời gian này.",
                "Bạn vẫn nên kiểm tra lại thời tiết sát ngày đi để chốt đồ dùng và khung giờ ngoài trời.",
            ],
            "plan_adjustment_suggestions": [],
            "should_offer_replan": False,
        }

    weather.update(
        {
            "forecast_status": "forecast_available",
            "location": str(destination_meta["label"]),
            "travel_window_label": parsed_window.label,
        }
    )
    return weather


def _parse_travel_window(text: str, *, trip_days: int | None) -> ParsedTravelWindow:
    folded = _fold(text)
    today = date.today()

    if "hom nay" in folded:
        return ParsedTravelWindow(status="specific", start_date=today, end_date=today, label=_format_date_label(today))
    if "ngay mai" in folded:
        tomorrow = today + timedelta(days=1)
        return ParsedTravelWindow(status="specific", start_date=tomorrow, end_date=tomorrow, label=_format_date_label(tomorrow))

    range_match = re.search(
        r"(\d{1,2})\s*[-~]\s*(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(\d{2,4}))?",
        folded,
    )
    if range_match:
        year = _normalize_year(range_match.group(4), default_year=today.year)
        month = int(range_match.group(3))
        start = _safe_date(year, month, int(range_match.group(1)))
        end = _safe_date(year, month, int(range_match.group(2)))
        if start and end and start <= end:
            start, end = _shift_forward_if_past(start, end, text_has_year=bool(range_match.group(4)))
            return ParsedTravelWindow(status="specific", start_date=start, end_date=end, label=_format_range_label(start, end))

    explicit_range = re.search(
        r"(?:tu|từ)\s*(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\s*(?:den|đến)\s*(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?",
        folded,
    )
    if explicit_range:
        start_year = _normalize_year(explicit_range.group(3), default_year=today.year)
        end_year = _normalize_year(explicit_range.group(6), default_year=start_year)
        start = _safe_date(start_year, int(explicit_range.group(2)), int(explicit_range.group(1)))
        end = _safe_date(end_year, int(explicit_range.group(5)), int(explicit_range.group(4)))
        if start and end and start <= end:
            start, end = _shift_forward_if_past(start, end, text_has_year=bool(explicit_range.group(3) or explicit_range.group(6)))
            return ParsedTravelWindow(status="specific", start_date=start, end_date=end, label=_format_range_label(start, end))

    date_matches = list(
        re.finditer(r"(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", folded)
    )
    if date_matches:
        picked = date_matches[-1]
        if picked.group(1):
            parsed = _safe_date(int(picked.group(1)), int(picked.group(2)), int(picked.group(3)))
            text_has_year = True
        else:
            parsed = _safe_date(
                _normalize_year(picked.group(6), default_year=today.year),
                int(picked.group(5)),
                int(picked.group(4)),
            )
            text_has_year = bool(picked.group(6))
        if parsed:
            start = _shift_single_forward_if_past(parsed, text_has_year=text_has_year)
            span_days = max(1, int(trip_days or 1))
            end = start + timedelta(days=span_days - 1)
            label = _format_date_label(start) if span_days == 1 else _format_range_label(start, end)
            return ParsedTravelWindow(status="specific", start_date=start, end_date=end, label=label)

    if any(phrase in folded for phrase in ("cuoi thang", "giua thang", "dau thang", "mua he", "mua thu", "mua dong", "mua xuan")):
        return ParsedTravelWindow(status="ambiguous", label=_extract_time_phrase(text))

    return ParsedTravelWindow(status="missing")


def _resolve_destination_key(text: str) -> str:
    folded = _fold(text)
    if "hoi an" in folded:
        return "hoi_an"
    if "quang nam" in folded:
        return "quang_nam"
    return "da_nang"


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except Exception:
        return None


def _normalize_year(raw_year: str | None, *, default_year: int) -> int:
    if not raw_year:
        return default_year
    year = int(raw_year)
    if year < 100:
        return 2000 + year
    return year


def _shift_single_forward_if_past(value: date, *, text_has_year: bool) -> date:
    if text_has_year:
        return value
    today = date.today()
    if value >= today:
        return value
    try:
        return value.replace(year=value.year + 1)
    except Exception:
        return value


def _shift_forward_if_past(start: date, end: date, *, text_has_year: bool) -> tuple[date, date]:
    if text_has_year:
        return start, end
    today = date.today()
    if start >= today:
        return start, end
    try:
        return start.replace(year=start.year + 1), end.replace(year=end.year + 1)
    except Exception:
        return start, end


def _fetch_open_meteo_forecast(
    *,
    latitude: float,
    longitude: float,
    start_date: date,
    end_date: date,
) -> dict[str, Any] | None:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "Asia/Ho_Chi_Minh",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": ",".join(
            [
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "precipitation_sum",
                "wind_speed_10m_max",
            ]
        ),
    }
    try:
        request = urllib.request.Request(
            f"{_OPEN_METEO_FORECAST_URL}?{urlencode(params)}",
            headers={"User-Agent": "multi-agent-travel/0.1"},
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    if not dates:
        return None

    descriptions: list[str] = []
    max_temps: list[float] = []
    min_temps: list[float] = []
    rain_probs: list[float] = []
    rain_amounts: list[float] = []
    wind_speeds: list[float] = []
    tones: list[str] = []

    for index, _ in enumerate(dates):
        weather_code = _pick_series_value(daily.get("weather_code"), index)
        description, tone = _describe_weather_code(weather_code)
        descriptions.append(description)
        tones.append(tone)
        max_temp = _pick_series_value(daily.get("temperature_2m_max"), index, cast=float)
        min_temp = _pick_series_value(daily.get("temperature_2m_min"), index, cast=float)
        rain_prob = _pick_series_value(daily.get("precipitation_probability_max"), index, cast=float)
        rain_amount = _pick_series_value(daily.get("precipitation_sum"), index, cast=float)
        wind_speed = _pick_series_value(daily.get("wind_speed_10m_max"), index, cast=float)
        if max_temp is not None:
            max_temps.append(max_temp)
        if min_temp is not None:
            min_temps.append(min_temp)
        if rain_prob is not None:
            rain_probs.append(rain_prob)
        if rain_amount is not None:
            rain_amounts.append(rain_amount)
        if wind_speed is not None:
            wind_speeds.append(wind_speed)

    avg_max = round(sum(max_temps) / len(max_temps), 1) if max_temps else None
    avg_min = round(sum(min_temps) / len(min_temps), 1) if min_temps else None
    avg_temp = round(((avg_max or 0) + (avg_min or 0)) / 2, 1) if avg_max is not None and avg_min is not None else avg_max or avg_min
    max_rain_prob = round(max(rain_probs), 0) if rain_probs else None
    total_rain = round(sum(rain_amounts), 1) if rain_amounts else None
    max_wind = round(max(wind_speeds), 0) if wind_speeds else None
    dominant_description = _dominant_description(descriptions)
    dominant_tone = _dominant_tone(tones, max_rain_prob=max_rain_prob, avg_max=avg_max, max_wind=max_wind)
    advice_lines, adjustment_lines, should_offer_replan = _build_weather_advice(
        tone=dominant_tone,
        avg_max=avg_max,
        max_rain_prob=max_rain_prob,
        total_rain=total_rain,
        max_wind=max_wind,
    )

    return {
        "description": dominant_description,
        "condition_tone": dominant_tone,
        "temp_c": avg_temp,
        "temp_min_c": avg_min,
        "temp_max_c": avg_max,
        "wind_kmh": max_wind,
        "precipitation_probability_pct": max_rain_prob,
        "precipitation_mm": total_rain,
        "advice_lines": advice_lines,
        "plan_adjustment_suggestions": adjustment_lines,
        "should_offer_replan": should_offer_replan,
    }


def _pick_series_value(values: Any, index: int, cast: type | None = None) -> Any:
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    if cast is None:
        return value
    try:
        return cast(value)
    except Exception:
        return None


def _describe_weather_code(code: Any) -> tuple[str, str]:
    try:
        parsed = int(code)
    except Exception:
        return "Thời tiết biến động nhẹ", "mixed"
    if parsed == 0:
        return "Trời quang", "pleasant"
    if parsed in {1, 2}:
        return "Nắng nhẹ, có mây", "pleasant"
    if parsed == 3:
        return "Nhiều mây", "mixed"
    if parsed in {45, 48}:
        return "Sương mù", "mixed"
    if parsed in {51, 53, 55, 56, 57}:
        return "Mưa phùn", "rainy"
    if parsed in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "Có mưa", "rainy"
    if parsed in {71, 73, 75, 77}:
        return "Thời tiết lạnh", "mixed"
    if parsed in {85, 86}:
        return "Mưa tuyết", "mixed"
    if parsed in {95, 96, 99}:
        return "Dông mưa", "rainy"
    return "Thời tiết thay đổi", "mixed"


def _dominant_description(descriptions: list[str]) -> str:
    counts: dict[str, int] = {}
    for item in descriptions:
        counts[item] = counts.get(item, 0) + 1
    if not counts:
        return "Thời tiết thay đổi"
    return sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[0][0]


def _dominant_tone(
    tones: list[str],
    *,
    max_rain_prob: float | None,
    avg_max: float | None,
    max_wind: float | None,
) -> str:
    if (max_rain_prob or 0) >= 65:
        return "rainy"
    if (max_wind or 0) >= 30:
        return "windy"
    if (avg_max or 0) >= 33:
        return "hot"
    if "rainy" in tones:
        return "rainy"
    if "pleasant" in tones:
        return "pleasant"
    return "mixed"


def _build_weather_advice(
    *,
    tone: str,
    avg_max: float | None,
    max_rain_prob: float | None,
    total_rain: float | None,
    max_wind: float | None,
) -> tuple[list[str], list[str], bool]:
    if tone == "rainy":
        return (
            [
                f"Khả năng mưa cao khoảng {int(max_rain_prob or 0)}% và lượng mưa dự kiến khoảng {float(total_rain or 0):.1f} mm.",
                "Nên chuẩn bị ô hoặc áo mưa gọn, túi chống nước cho điện thoại và ưu tiên di chuyển bằng ô tô/Grab khi cần.",
            ],
            [
                "Nếu muốn, mình có thể đổi bớt các khung biển hoặc đi bộ ngoài trời sang bảo tàng, cafe, mua sắm hoặc điểm trong nhà.",
                "Các hoạt động ngoài trời vẫn có thể giữ lại, nhưng nên đẩy về sáng sớm hoặc khung giờ ít mưa hơn.",
            ],
            True,
        )
    if tone == "hot":
        return (
            [
                f"Nhiệt độ ban ngày dự kiến khá cao, khoảng {float(avg_max or 0):.1f}°C.",
                "Nên ưu tiên biển, ngắm cảnh và các điểm ngoài trời vào sáng sớm hoặc chiều muộn; giữa trưa nên nghỉ hoặc chuyển sang hoạt động trong nhà.",
            ],
            [
                "Nếu cần, mình có thể đảo lại thứ tự lịch trình để các điểm ngoài trời nằm ở sáng/chiều, còn giữa trưa chuyển sang ăn uống hoặc nghỉ ngơi.",
            ],
            True,
        )
    if tone == "windy":
        return (
            [
                f"Gió có thể khá mạnh, khoảng {int(max_wind or 0)} km/h ở thời điểm cao nhất.",
                "Nếu đi biển hoặc di chuyển xa bằng xe máy, bạn nên theo dõi lại thời tiết sát giờ đi.",
            ],
            [
                "Nếu muốn, mình có thể giảm bớt các chặng ngoài trời nhiều gió và ưu tiên điểm ít phụ thuộc thời tiết hơn.",
            ],
            True,
        )
    if tone == "pleasant":
        return (
            [
                "Thời tiết nhìn chung khá đẹp và thuận lợi cho các hoạt động ngoài trời.",
                "Bạn vẫn nên mang theo nón, kem chống nắng và nước nếu đi biển hoặc đi bộ nhiều.",
            ],
            [],
            False,
        )
    return (
        [
            "Thời tiết có dao động nhẹ nên lịch trình hiện tại vẫn có thể giữ làm khung chính.",
            "Bạn nên giữ lịch linh hoạt một chút để đổi thứ tự điểm tham quan khi cần.",
        ],
        [],
        False,
    )


def _extract_time_phrase(text: str) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact[-80:] if len(compact) > 80 else compact


def _format_date_label(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _format_range_label(start: date, end: date) -> str:
    if start == end:
        return _format_date_label(start)
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%d')}-{end.strftime('%d/%m/%Y')}"
    return f"{start.strftime('%d/%m/%Y')} - {end.strftime('%d/%m/%Y')}"

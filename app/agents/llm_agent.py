from __future__ import annotations

import json
import time

from app.config.settings import get_settings


def _fallback_answer(query: str, context: str) -> str:
    if not context.strip():
        return "không tìm thấy"
    # Simple non-LLM fallback: return the top items as suggestions.
    lines = [line for line in context.splitlines() if line.strip()]
    names = [l.replace("Tên:", "").strip() for l in lines if l.startswith("Tên:")][:5]
    if not names:
        return "không tìm thấy"
    bullets = "\n".join([f"- {n}" for n in names])
    return f"Bạn có thể tham khảo:\n{bullets}"


def generate_answer(
    query: str,
    context: str,
    allowed_place_names: list[str] | None = None,
    place_meta: dict[str, dict] | None = None,
) -> str:
    """
    LLM Agent (core RAG).
    - If `OPENROUTER_API_KEY` is set: call OpenRouter via the OpenAI-compatible SDK.
    - Otherwise: return a deterministic fallback answer.
    """
    settings = get_settings()
    openrouter_key = (settings.openrouter_api_key or "").strip()
    if not openrouter_key:
        return _fallback_answer(query, context)

    try:
        from openai import (  # type: ignore
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            OpenAI,
            RateLimitError,
        )
    except Exception:
        return _fallback_answer(query, context)

    client = OpenAI(
        base_url=settings.openrouter_base_url,
        api_key=openrouter_key,
    )
    model = settings.openrouter_model
    extra_body = {"reasoning": {"enabled": bool(settings.openrouter_reasoning_enabled)}}

    whitelist = "\n".join([f"- {n}" for n in (allowed_place_names or [])[:30]])
    prompt = f"""Bạn là trợ lý du lịch.

Dựa vào thông tin sau:
{context}

Hãy trả lời câu hỏi:
{query}

Bạn CHỈ được phép đề xuất địa điểm trong danh sách ALLOWED_PLACES sau:
{whitelist}

Trả về JSON hợp lệ với schema:
{{
  "intro": "string",
  "recommended_places": [{{"name": "exact name from ALLOWED_PLACES", "reason": "string"}}],
  "tips": ["string"]
}}
Nếu không có thông tin thì trả recommended_places rỗng.
"""
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Bạn là trợ lý du lịch."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if extra_body is not None:
        kwargs["extra_body"] = extra_body

    transient = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
    max_attempts = 3
    resp = None
    for attempt in range(max_attempts):
        try:
            resp = client.chat.completions.create(**kwargs)
            break
        except transient:
            if attempt >= max_attempts - 1:
                return _fallback_answer(query, context)
            time.sleep(2**attempt)

    if resp is None:
        return _fallback_answer(query, context)

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return "không tìm thấy"
    rendered = _render_grounded_output(raw, allowed_place_names or [], place_meta=place_meta or {})
    return rendered or _fallback_answer(query, context)


def _render_grounded_output(raw: str, allowed: list[str], place_meta: dict[str, dict]) -> str:
    if not allowed:
        return ""
    allowed_set = {a.strip() for a in allowed if a.strip()}
    try:
        payload = json.loads(raw)
    except Exception:
        # Try to extract json block
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return ""
        try:
            payload = json.loads(raw[start : end + 1])
        except Exception:
            return ""
    intro = str(payload.get("intro") or "Bạn có thể tham khảo các địa điểm sau:")
    recs = payload.get("recommended_places") or []
    tips = payload.get("tips") or []
    lines: list[str] = [intro, "", "Địa điểm gợi ý (đã kiểm chứng từ nguồn):"]
    kept = 0
    for item in recs:
        name = str((item or {}).get("name") or "").strip()
        if name not in allowed_set:
            continue
        reason = str((item or {}).get("reason") or "").strip()
        meta = place_meta.get(name) or {}
        src = str(meta.get("source") or "").strip()
        url = str(meta.get("map_url") or "").strip()
        lines.append(f"- {name}" + (f": {reason}" if reason else ""))
        if src:
            lines.append(f"  - Nguồn: {src}")
        if url:
            lines.append(f"  - Bản đồ: {url}")
        kept += 1
    if kept == 0:
        return ""
    if isinstance(tips, list) and tips:
        lines.append("")
        lines.append("Mẹo di chuyển:")
        for tip in tips[:4]:
            t = str(tip).strip()
            if t:
                lines.append(f"- {t}")
    return "\n".join(lines).strip()

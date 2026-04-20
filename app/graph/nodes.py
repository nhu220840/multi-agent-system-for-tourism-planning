from __future__ import annotations

from typing import Any

from app.agents.intake_agent import evaluate_intake
from app.graph.state import TravelGraphState
from app.services.itinerary_validation import (
    build_retry_query,
    should_retry_itinerary,
    validate_itinerary_plan,
)
from app.services.response_formatter import (
    build_time_confirmation_question,
    format_planning_answer,
)
from app.services.rag_service import (
    build_context_payload,
    build_coordinator_output,
    build_grounded_answer,
    build_itinerary_artifacts,
    build_research_output,
    build_source_artifacts,
    retrieve_trip_artifacts,
)

_INTAKE_FOLLOW_UP_ANSWER = (
    "Minh chua du thong tin de truy xuat va len lich trinh. "
    "Vui long tra loi cac cau hoi bo sung ben duoi."
)


def _append_trace(state: TravelGraphState, *items: str) -> list[str]:
    trace = list(state.get("trace", []))
    trace.extend(items)
    return trace


def intake_node(state: TravelGraphState) -> dict[str, Any]:
    intake = evaluate_intake(state.get("message", ""))
    return {
        "intake_complete": intake.is_complete,
        "collected_info": intake.collected,
        "missing_fields": intake.missing_fields,
        "follow_up_questions": intake.follow_up_questions,
        "trace": _append_trace(state, "intake_agent"),
    }


def prepare_query_node(state: TravelGraphState) -> dict[str, Any]:
    collected = state.get("collected_info", {}) or {}
    q_extra = " ".join(
        str(collected.get(k) or "")
        for k in ("destination", "days", "interests")
    ).strip()
    base_message = state.get("message", "").strip()
    rag_query = f"{q_extra} {base_message}".strip() if q_extra else base_message
    return {"rag_query": rag_query}


def retrieve_candidates_node(state: TravelGraphState) -> dict[str, Any]:
    retrieval = retrieve_trip_artifacts(
        query=state.get("rag_query", state.get("message", "")),
        category=state.get("category"),
        top_k=int(state.get("top_k", 5) or 5),
        with_plan=bool(state.get("with_plan", False)),
    )
    return {
        "places": retrieval.places,
        "local_candidates_considered": retrieval.local_candidates_considered,
        "weather": retrieval.weather,
        "guide": retrieval.guide,
        "transport": retrieval.transport,
        "recommended_hotel": retrieval.recommended_hotel,
        "mobility_plan": retrieval.mobility_plan,
        "trace": _append_trace(state, *retrieval.trace),
    }


def context_builder_node(state: TravelGraphState) -> dict[str, Any]:
    context = build_context_payload(
        query=state.get("rag_query", state.get("message", "")),
        places=state.get("places", []),
        weather=state.get("weather"),
        transport=state.get("transport"),
        recommended_hotel=state.get("recommended_hotel"),
        mobility_plan=state.get("mobility_plan"),
        guide=state.get("guide", ""),
    )
    return {
        "context": context,
        "trace": _append_trace(state, "context_builder_agent"),
    }


def research_node(state: TravelGraphState) -> dict[str, Any]:
    research = build_research_output(
        query=state.get("rag_query", state.get("message", "")),
        places=state.get("places", []),
        transport=state.get("transport"),
    )
    return {
        "research": research,
        "trace": _append_trace(state, "research_agent"),
    }


def itinerary_node(state: TravelGraphState) -> dict[str, Any]:
    itinerary = build_itinerary_artifacts(
        query=state.get("rag_query", state.get("message", "")),
        places=state.get("places", []),
    )
    return {
        "plan": itinerary.get("plan"),
        "stay_plan": itinerary.get("stay_plan"),
        "recommended_hotel": itinerary.get("recommended_hotel") or state.get("recommended_hotel"),
        "trace": _append_trace(state, "itinerary_builder"),
    }


def itinerary_validation_node(state: TravelGraphState) -> dict[str, Any]:
    query = state.get("rag_query", state.get("message", ""))
    current_plan = state.get("plan", "") or ""
    validation = validate_itinerary_plan(
        query=query,
        plan=current_plan,
        places=state.get("places", []),
    )
    retry_attempted = bool(state.get("itinerary_retry_attempted", False))
    if should_retry_itinerary(validation, retry_attempted=retry_attempted):
        retry_query = build_retry_query(query, validation)
        retry_itinerary = build_itinerary_artifacts(
            query=retry_query,
            places=state.get("places", []),
            strict_mode=True,
        )
        retry_validation = validate_itinerary_plan(
            query=query,
            plan=str(retry_itinerary.get("plan") or ""),
            places=state.get("places", []),
        )
        retry_validation["retried"] = True
        retry_validation["retry_query"] = retry_query
        return {
            "plan": retry_itinerary.get("plan"),
            "stay_plan": retry_itinerary.get("stay_plan"),
            "recommended_hotel": retry_itinerary.get("recommended_hotel") or state.get("recommended_hotel"),
            "plan_validation": retry_validation,
            "itinerary_retry_attempted": True,
            "trace": _append_trace(state, "itinerary_validator", "itinerary_retry_strict"),
        }
    validation["retried"] = retry_attempted
    return {
        "plan_validation": validation,
        "itinerary_retry_attempted": retry_attempted,
        "trace": _append_trace(state, "itinerary_validator"),
    }


def source_payload_node(state: TravelGraphState) -> dict[str, Any]:
    source_artifacts = build_source_artifacts(
        places=state.get("places", []),
        local_candidates_considered=int(state.get("local_candidates_considered", 0) or 0),
    )
    return {
        "sources": source_artifacts.sources,
        "verified_places": source_artifacts.verified_places,
        "route_plan": source_artifacts.route_plan,
        "grounding": source_artifacts.grounding,
    }


def answer_node(state: TravelGraphState) -> dict[str, Any]:
    answer = build_grounded_answer(
        query=state.get("rag_query", state.get("message", "")),
        context=state.get("context", ""),
        verified_places=state.get("verified_places", []),
    )
    return {
        "answer": answer,
        "trace": _append_trace(state, "llm_agent"),
    }


def coordinator_node(state: TravelGraphState) -> dict[str, Any]:
    coordinator_plan = build_coordinator_output(
        query=state.get("rag_query", state.get("message", "")),
        itinerary=state.get("plan", "") or "",
        transport=state.get("transport"),
    )
    return {
        "coordinator_plan": coordinator_plan,
        "answer": coordinator_plan,
        "trace": _append_trace(state, "coordinator_agent"),
    }


def clarify_response_node(state: TravelGraphState) -> dict[str, Any]:
    response_payload = {
        "answer": _INTAKE_FOLLOW_UP_ANSWER,
        "conversation_stage": "intake",
        "collected_info": state.get("collected_info"),
        "missing_fields": state.get("missing_fields", []),
        "follow_up_questions": state.get("follow_up_questions", []),
        "trace": state.get("trace", []),
        "sources": [],
        "plan": None,
        "stay_plan": None,
        "plan_validation": None,
        "research": None,
        "coordinator_plan": None,
        "weather": None,
        "transport": None,
        "recommended_hotel": None,
        "mobility_plan": None,
        "verified_places": None,
        "route_plan": None,
        "grounding": None,
    }
    return {
        "answer": _INTAKE_FOLLOW_UP_ANSWER,
        "conversation_stage": "intake",
        "response_payload": response_payload,
    }


def planning_response_node(state: TravelGraphState) -> dict[str, Any]:
    follow_up_questions = [
        build_time_confirmation_question(
            str((state.get("collected_info") or {}).get("destination") or "")
        )
    ]
    formatted_answer = format_planning_answer(
        query=state.get("rag_query", state.get("message", "")),
        collected_info=state.get("collected_info"),
        research=state.get("research"),
        plan=state.get("plan"),
        coordinator_plan=state.get("coordinator_plan"),
        weather=state.get("weather"),
        transport=state.get("transport"),
        recommended_hotel=state.get("recommended_hotel"),
        mobility_plan=state.get("mobility_plan"),
        stay_plan=state.get("stay_plan"),
        route_plan=state.get("route_plan"),
        plan_validation=state.get("plan_validation"),
        verified_places=state.get("verified_places"),
    )
    response_payload = {
        "answer": formatted_answer,
        "conversation_stage": "planning",
        "collected_info": state.get("collected_info"),
        "missing_fields": [],
        "follow_up_questions": follow_up_questions,
        "trace": state.get("trace", []),
        "sources": state.get("sources", []),
        "plan": state.get("plan"),
        "stay_plan": state.get("stay_plan"),
        "plan_validation": state.get("plan_validation"),
        "research": state.get("research"),
        "coordinator_plan": state.get("coordinator_plan"),
        "weather": state.get("weather"),
        "transport": state.get("transport"),
        "recommended_hotel": state.get("recommended_hotel"),
        "mobility_plan": state.get("mobility_plan"),
        "verified_places": state.get("verified_places"),
        "route_plan": state.get("route_plan"),
        "grounding": _merge_grounding_with_validation(
            state.get("grounding"),
            state.get("plan_validation"),
        ),
    }
    return {
        "answer": formatted_answer,
        "conversation_stage": "planning",
        "response_payload": response_payload,
    }


def _merge_grounding_with_validation(
    grounding: dict[str, Any] | None,
    plan_validation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not grounding and not plan_validation:
        return grounding
    merged = dict(grounding or {})
    if plan_validation is not None:
        merged["plan_validation"] = plan_validation
    return merged


def route_after_intake(state: TravelGraphState) -> str:
    return "clarify" if not state.get("intake_complete") else "prepare_query"


def route_after_context(state: TravelGraphState) -> str:
    if state.get("with_plan") and not state.get("category"):
        return "research"
    return "source_payload"


def route_after_answer(state: TravelGraphState) -> str:
    if state.get("with_plan") and not state.get("category"):
        return "coordinator"
    return "planning_response"

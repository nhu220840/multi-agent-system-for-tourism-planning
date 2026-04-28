from __future__ import annotations

from time import perf_counter
from typing import Any

from app.agents.intake_agent import evaluate_intake
from app.graph.state import TravelGraphState
from app.services.itinerary_validation import (
    build_retry_query,
    should_retry_itinerary,
    validate_itinerary_plan,
)
from app.services.planning_tools import (
    build_itinerary_tool,
    build_sources_tool,
    coordinator_tool,
    grounded_answer_tool,
    prepare_query_tool,
    research_tool,
    retrieve_places_tool,
    score_places_tool,
)
from app.services.response_formatter import (
    build_time_confirmation_question,
    format_planning_answer,
)
from app.services.stay_recommendation_service import build_stay_recommendations

_INTAKE_FOLLOW_UP_ANSWER = (
    "Minh chua du thong tin de truy xuat va len lich trinh. "
    "Vui long tra loi cac cau hoi bo sung ben duoi."
)

_TRACE_TITLE_MAP = {
    "intake_agent": "Intake Agent",
    "planning_agent": "Planning Agent",
    "validator_agent": "Validator Agent",
    "response_service": "Response Service",
    "elasticsearch_index": "Elasticsearch Index",
    "hybrid_vector_rag": "Hybrid Vector RAG",
    "db_only_local_sufficient": "Local Catalog Sufficient",
    "db_only_local_limited_results": "Local Catalog Limited",
    "db_only_not_enough_attractions": "Missing Attraction Coverage",
    "db_only_attractions_ready": "Attractions Ready",
    "planning_retry_strict": "Strict Replan",
    "context_builder_agent": "Context Builder Agent",
    "research_agent": "Research Agent",
    "itinerary_builder": "Itinerary Builder",
    "itinerary_validator": "Itinerary Validator",
    "llm_agent": "LLM Answer Agent",
    "coordinator_agent": "Coordinator Agent",
}


def _append_trace(state: TravelGraphState, *items: str) -> list[str]:
    trace = list(state.get("trace", []))
    trace.extend(items)
    return trace


def _copy_timings(state: TravelGraphState) -> dict[str, Any]:
    return dict(state.get("timings") or {})


def _should_generate_plan(state: TravelGraphState) -> bool:
    return bool(state.get("with_plan")) and not state.get("category")


def intake_node(state: TravelGraphState) -> dict[str, Any]:
    started = perf_counter()
    intake = evaluate_intake(state.get("message", ""))
    timings = _copy_timings(state)
    timings["intake_ms"] = round((perf_counter() - started) * 1000, 1)
    return {
        "intake_complete": intake.is_complete,
        "collected_info": intake.collected,
        "missing_fields": intake.missing_fields,
        "follow_up_questions": intake.follow_up_questions,
        "trace": _append_trace(state, "intake_agent"),
        "timings": timings,
    }


def planning_node(state: TravelGraphState) -> dict[str, Any]:
    total_started = perf_counter()
    timings = _copy_timings(state)

    step_started = perf_counter()
    rag_query = prepare_query_tool(
        state.get("message", ""),
        state.get("collected_info"),
    )
    timings["planning_prepare_query_ms"] = round((perf_counter() - step_started) * 1000, 1)
    retry_query = str(state.get("retry_query") or "").strip()
    use_cached_retrieval = bool(retry_query and state.get("places"))

    places = list(state.get("places", []) or [])
    local_candidates_considered = int(state.get("local_candidates_considered", 0) or 0)
    weather = state.get("weather")
    guide = str(state.get("guide") or "")
    transport = state.get("transport")
    recommended_hotel = state.get("recommended_hotel")
    mobility_plan = state.get("mobility_plan")
    planning_trace = ["planning_agent"]

    # Collapse the legacy retrieval/context/research/itinerary stages behind tool calls.
    if use_cached_retrieval:
        planning_trace.append("planning_retry_strict")
        timings["planning_retrieval_ms"] = 0.0
    else:
        step_started = perf_counter()
        retrieval = retrieve_places_tool(
            query=rag_query,
            category=state.get("category"),
            top_k=int(state.get("top_k", 5) or 5),
            with_plan=bool(state.get("with_plan", False)),
        )
        places = retrieval.places
        local_candidates_considered = retrieval.local_candidates_considered
        weather = retrieval.weather
        guide = retrieval.guide
        transport = retrieval.transport
        recommended_hotel = retrieval.recommended_hotel
        mobility_plan = retrieval.mobility_plan
        planning_trace.extend(retrieval.trace)
        timings["planning_retrieval_ms"] = round((perf_counter() - step_started) * 1000, 1)

    step_started = perf_counter()
    context = score_places_tool(
        query=rag_query,
        places=places,
        weather=weather,
        transport=transport,
        recommended_hotel=recommended_hotel,
        mobility_plan=mobility_plan,
        guide=guide,
    )
    timings["planning_context_ms"] = round((perf_counter() - step_started) * 1000, 1)

    research = None
    plan = None
    stay_plan = None
    coordinator_plan = None
    route_plan = None
    itinerary_query = rag_query

    if _should_generate_plan(state):
        step_started = perf_counter()
        research = research_tool(
            query=rag_query,
            places=places,
            transport=transport,
        )
        timings["planning_research_ms"] = round((perf_counter() - step_started) * 1000, 1)
        itinerary_query = retry_query or rag_query
        step_started = perf_counter()
        itinerary = build_itinerary_tool(
            query=itinerary_query,
            places=places,
            strict_mode=bool(retry_query),
        )
        timings["planning_itinerary_ms"] = round((perf_counter() - step_started) * 1000, 1)
        plan = itinerary.get("plan")
        stay_plan = itinerary.get("stay_plan")
        route_plan = itinerary.get("route_plan")
        recommended_hotel = itinerary.get("recommended_hotel") or recommended_hotel
        step_started = perf_counter()
        coordinator_plan = coordinator_tool(
            query=rag_query,
            itinerary=str(plan or ""),
            transport=transport,
        )
        timings["planning_coordinator_ms"] = round((perf_counter() - step_started) * 1000, 1)
    else:
        timings["planning_research_ms"] = 0.0
        timings["planning_itinerary_ms"] = 0.0
        timings["planning_coordinator_ms"] = 0.0

    step_started = perf_counter()
    source_artifacts = build_sources_tool(
        places=places,
        local_candidates_considered=local_candidates_considered,
    )
    timings["planning_sources_ms"] = round((perf_counter() - step_started) * 1000, 1)
    answer = ""
    if not _should_generate_plan(state):
        step_started = perf_counter()
        answer = grounded_answer_tool(
            query=rag_query,
            context=context,
            verified_places=source_artifacts.verified_places,
        )
        timings["planning_answer_ms"] = round((perf_counter() - step_started) * 1000, 1)
    else:
        timings["planning_answer_ms"] = 0.0
    timings["planning_total_ms"] = round((perf_counter() - total_started) * 1000, 1)

    return {
        "rag_query": rag_query,
        "planning_query": itinerary_query,
        "places": places,
        "local_candidates_considered": local_candidates_considered,
        "weather": weather,
        "guide": guide,
        "transport": transport,
        "recommended_hotel": recommended_hotel,
        "mobility_plan": mobility_plan,
        "context": context,
        "research": research,
        "plan": plan,
        "stay_plan": stay_plan,
        "answer": answer,
        "coordinator_plan": coordinator_plan,
        "sources": source_artifacts.sources,
        "verified_places": source_artifacts.verified_places,
        "route_plan": route_plan or source_artifacts.route_plan,
        "grounding": source_artifacts.grounding,
        "retry_query": None,
        "needs_replan": False,
        "trace": _append_trace(state, *planning_trace),
        "timings": timings,
    }


def validator_node(state: TravelGraphState) -> dict[str, Any]:
    started = perf_counter()
    timings = _copy_timings(state)
    if not _should_generate_plan(state):
        timings["validator_ms"] = round((perf_counter() - started) * 1000, 1)
        return {
            "plan_validation": None,
            "needs_replan": False,
            "retry_query": None,
            "trace": _append_trace(state, "validator_agent"),
            "timings": timings,
        }

    query = state.get("rag_query", state.get("message", ""))
    retry_attempted = bool(state.get("itinerary_retry_attempted", False))
    validation = validate_itinerary_plan(
        query=query,
        plan=str(state.get("plan", "") or ""),
        places=state.get("places", []),
    )
    needs_replan = should_retry_itinerary(
        validation,
        retry_attempted=retry_attempted,
    )
    validation["retried"] = retry_attempted

    if needs_replan:
        retry_query = build_retry_query(query, validation)
        validation["retry_query"] = retry_query
        timings["validator_ms"] = round((perf_counter() - started) * 1000, 1)
        return {
            "plan_validation": validation,
            "needs_replan": True,
            "retry_query": retry_query,
            "itinerary_retry_attempted": True,
            "trace": _append_trace(state, "validator_agent"),
            "timings": timings,
        }

    timings["validator_ms"] = round((perf_counter() - started) * 1000, 1)
    return {
        "plan_validation": validation,
        "needs_replan": False,
        "retry_query": None,
        "itinerary_retry_attempted": retry_attempted,
        "trace": _append_trace(state, "validator_agent"),
        "timings": timings,
    }


def clarify_response_node(state: TravelGraphState) -> dict[str, Any]:
    timings = _copy_timings(state)
    trace = _append_trace(state, "response_service")
    response_payload = {
        "answer": _INTAKE_FOLLOW_UP_ANSWER,
        "conversation_stage": "intake",
        "collected_info": state.get("collected_info"),
        "missing_fields": state.get("missing_fields", []),
        "follow_up_questions": state.get("follow_up_questions", []),
        "trace": trace,
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
        "debug_steps": _build_debug_steps(state, stage="intake"),
    }
    return {
        "answer": _INTAKE_FOLLOW_UP_ANSWER,
        "conversation_stage": "intake",
        "trace": trace,
        "timings": timings,
        "response_payload": response_payload,
    }


def response_node(state: TravelGraphState) -> dict[str, Any]:
    started = perf_counter()
    timings = _copy_timings(state)
    if _should_generate_plan(state):
        follow_up_questions = [
            build_time_confirmation_question(
                str((state.get("collected_info") or {}).get("destination") or "")
            )
        ]
        stay_recommendations = build_stay_recommendations(
            query=state.get("rag_query", state.get("message", "")),
            places=state.get("places", []),
            recommended_hotel=state.get("recommended_hotel"),
        )
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
            stay_recommendations=stay_recommendations,
            plan_validation=state.get("plan_validation"),
            verified_places=state.get("verified_places"),
        )
    else:
        follow_up_questions = []
        stay_recommendations = None
        formatted_answer = str(state.get("answer") or "").strip()
    trace = _append_trace(state, "response_service")
    timings["response_ms"] = round((perf_counter() - started) * 1000, 1)
    response_payload = {
        "answer": formatted_answer,
        "conversation_stage": "planning",
        "collected_info": state.get("collected_info"),
        "missing_fields": [],
        "follow_up_questions": follow_up_questions,
        "trace": trace,
        "sources": state.get("sources", []),
        "plan": state.get("plan"),
        "stay_plan": state.get("stay_plan"),
        "stay_recommendations": stay_recommendations,
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
        "debug_steps": _build_debug_steps(
            {
                **state,
                "trace": trace,
                "timings": timings,
            },
            stage="planning",
        ),
    }
    return {
        "answer": formatted_answer,
        "conversation_stage": "planning",
        "trace": trace,
        "timings": timings,
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
    return "clarify" if not state.get("intake_complete") else "planning"


def route_after_validation(state: TravelGraphState) -> str:
    return "planning" if state.get("needs_replan") else "response"


def _build_debug_steps(state: TravelGraphState, *, stage: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    collected = dict(state.get("collected_info") or {})
    missing = list(state.get("missing_fields") or [])
    trace = [str(item).strip() for item in state.get("trace", []) if str(item).strip()]
    rag_query = str(state.get("rag_query") or state.get("message") or "").strip()
    planning_query = str(state.get("planning_query") or rag_query).strip()
    places = state.get("places", []) or []
    verification = state.get("plan_validation") or {}
    route_plan = state.get("route_plan") or []
    transport = state.get("transport") or []
    sources = state.get("sources") or []
    timings = dict(state.get("timings") or {})

    steps.append(
        {
            "key": "intake",
            "title": "1. Intake Agent",
            "status": "done" if not missing else "needs_input",
            "summary": (
                "Da du thong tin dau vao de chuyen sang planning."
                if not missing
                else "Van thieu mot so thong tin dau vao."
            ),
            "details": {
                "collected_info": collected,
                "missing_fields": missing,
                "follow_up_questions": state.get("follow_up_questions") or [],
                "effective_request": str(state.get("message") or "").strip(),
                "timings_ms": timings,
            },
        }
    )

    if stage == "intake":
        steps.append(
            {
                "key": "planning_blocked",
                "title": "2. Planning Agent",
                "status": "waiting",
                "summary": "Dang cho user bo sung destination, days, interests.",
                "details": {
                    "required_fields": ["destination", "days", "interests"],
                    "missing_fields": missing,
                },
            }
        )
        return steps

    steps.append(
        {
            "key": "planning",
            "title": "2. Planning Agent",
            "status": "done",
            "summary": "Gom retrieval, scoring, research, itinerary va grounding trong mot agent.",
            "details": {
                "rag_query": rag_query,
                "planning_query": planning_query,
                "places_found": len(places),
                "sources_ready": len(sources),
                "local_candidates_considered": int(state.get("local_candidates_considered", 0) or 0),
                "context_ready": bool(str(state.get("context") or "").strip()),
                "research_ready": bool(str(state.get("research") or "").strip()),
                "plan_ready": bool(state.get("plan")),
                "stay_plan_ready": bool(state.get("stay_plan")),
                "recommended_hotel_ready": bool(state.get("recommended_hotel")),
                "route_items": len(route_plan),
                "transport_options": list(transport[:5]),
                "retry_mode": bool(state.get("itinerary_retry_attempted")),
                "trace": [_TRACE_TITLE_MAP.get(item, item) for item in trace],
                "timings_ms": {
                    key: value
                    for key, value in timings.items()
                    if str(key).startswith("planning_")
                },
            },
        }
    )
    steps.append(
        {
            "key": "validation",
            "title": "3. Validator Agent",
            "status": (
                "waiting"
                if state.get("needs_replan")
                else ("done" if verification else "skipped")
            ),
            "summary": (
                "Validator yeu cau planning agent sinh lai lich trinh."
                if state.get("needs_replan")
                else (
                    "Lich trinh da qua validator."
                    if verification and bool(verification.get("passed", True))
                    else "Validation da chay xong va giu lai cac canh bao hien co."
                )
            ),
            "details": {
                "validation": verification,
                "retry_query": state.get("retry_query"),
                "retried": bool(verification.get("retried", False)),
                "timings_ms": {
                    "validator_ms": timings.get("validator_ms"),
                },
            },
        }
    )
    steps.append(
        {
            "key": "response",
            "title": "4. Response Service",
            "status": "done",
            "summary": "Dinh dang output cuoi cung cho UI va nguoi dung.",
            "details": {
                "answer_ready": bool(str(state.get("answer") or "").strip()),
                "coordinator_ready": bool(str(state.get("coordinator_plan") or "").strip()),
                "final_trace": [_TRACE_TITLE_MAP.get(item, item) for item in trace],
                "timings_ms": {
                    "response_ms": timings.get("response_ms"),
                    "intake_ms": timings.get("intake_ms"),
                },
            },
        }
    )
    return steps

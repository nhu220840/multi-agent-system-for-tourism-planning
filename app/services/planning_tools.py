from __future__ import annotations

from typing import Any

from app.services.rag_service import (
    RetrievalArtifacts,
    SourceArtifacts,
    build_context_payload,
    build_coordinator_output,
    build_grounded_answer,
    build_itinerary_artifacts,
    build_research_output,
    build_source_artifacts,
    retrieve_trip_artifacts,
)


def prepare_query_tool(message: str, collected_info: dict[str, Any] | None) -> str:
    collected = collected_info or {}
    q_extra = " ".join(
        str(collected.get(key) or "")
        for key in ("destination", "days", "interests")
    ).strip()
    base_message = (message or "").strip()
    return f"{q_extra} {base_message}".strip() if q_extra else base_message


def retrieve_places_tool(
    *,
    query: str,
    category: str | None,
    top_k: int,
    with_plan: bool,
) -> RetrievalArtifacts:
    return retrieve_trip_artifacts(
        query=query,
        category=category,
        top_k=top_k,
        with_plan=with_plan,
    )


def score_places_tool(
    *,
    query: str,
    places: list[dict[str, Any]],
    weather: dict[str, Any] | None,
    transport: list[str] | None,
    recommended_hotel: dict[str, Any] | None,
    mobility_plan: dict[str, Any] | None,
    guide: str,
) -> str:
    return build_context_payload(
        query=query,
        places=places,
        weather=weather,
        transport=transport,
        recommended_hotel=recommended_hotel,
        mobility_plan=mobility_plan,
        guide=guide,
    )


def research_tool(
    *,
    query: str,
    places: list[dict[str, Any]],
    transport: list[str] | None,
) -> str:
    return build_research_output(
        query=query,
        places=places,
        transport=transport,
    )


def build_itinerary_tool(
    *,
    query: str,
    places: list[dict[str, Any]],
    strict_mode: bool = False,
) -> dict[str, Any]:
    return build_itinerary_artifacts(
        query=query,
        places=places,
        strict_mode=strict_mode,
    )


def build_sources_tool(
    *,
    places: list[dict[str, Any]],
    local_candidates_considered: int,
) -> SourceArtifacts:
    return build_source_artifacts(
        places=places,
        local_candidates_considered=local_candidates_considered,
    )


def grounded_answer_tool(
    *,
    query: str,
    context: str,
    verified_places: list[dict[str, Any]],
) -> str:
    return build_grounded_answer(
        query=query,
        context=context,
        verified_places=verified_places,
    )


def coordinator_tool(
    *,
    query: str,
    itinerary: str,
    transport: list[str] | None,
) -> str:
    return build_coordinator_output(
        query=query,
        itinerary=itinerary,
        transport=transport,
    )

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    answer_node,
    clarify_response_node,
    context_builder_node,
    coordinator_node,
    itinerary_node,
    intake_node,
    planning_response_node,
    prepare_query_node,
    research_node,
    retrieve_candidates_node,
    itinerary_validation_node,
    route_after_answer,
    route_after_context,
    route_after_intake,
    source_payload_node,
)
from app.graph.state import TravelGraphState
from app.models.response import ChatResponse


def build_travel_graph():
    workflow = StateGraph(TravelGraphState)

    workflow.add_node("intake", intake_node)
    workflow.add_node("clarify_response", clarify_response_node)
    workflow.add_node("prepare_query", prepare_query_node)
    workflow.add_node("retrieve_candidates", retrieve_candidates_node)
    workflow.add_node("context_builder", context_builder_node)
    workflow.add_node("research", research_node)
    workflow.add_node("itinerary", itinerary_node)
    workflow.add_node("itinerary_validation", itinerary_validation_node)
    workflow.add_node("source_payload", source_payload_node)
    workflow.add_node("answer", answer_node)
    workflow.add_node("coordinator", coordinator_node)
    workflow.add_node("planning_response", planning_response_node)

    workflow.add_edge(START, "intake")
    workflow.add_conditional_edges(
        "intake",
        route_after_intake,
        {
            "clarify": "clarify_response",
            "prepare_query": "prepare_query",
        },
    )
    workflow.add_edge("clarify_response", END)

    workflow.add_edge("prepare_query", "retrieve_candidates")
    workflow.add_edge("retrieve_candidates", "context_builder")
    workflow.add_conditional_edges(
        "context_builder",
        route_after_context,
        {
            "research": "research",
            "source_payload": "source_payload",
        },
    )
    workflow.add_edge("research", "itinerary")
    workflow.add_edge("itinerary", "itinerary_validation")
    workflow.add_edge("itinerary_validation", "source_payload")
    workflow.add_edge("source_payload", "answer")
    workflow.add_conditional_edges(
        "answer",
        route_after_answer,
        {
            "coordinator": "coordinator",
            "planning_response": "planning_response",
        },
    )
    workflow.add_edge("coordinator", "planning_response")
    workflow.add_edge("planning_response", END)

    return workflow.compile()


@lru_cache
def get_travel_graph():
    return build_travel_graph()


def run_travel_graph(
    message: str,
    *,
    top_k: int = 5,
    with_plan: bool = True,
    category: str | None = None,
) -> ChatResponse:
    state = get_travel_graph().invoke(
        {
            "message": message,
            "top_k": top_k,
            "with_plan": with_plan,
            "category": category,
            "trace": [],
        }
    )
    return ChatResponse(**state["response_payload"])

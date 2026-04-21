from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    clarify_response_node,
    intake_node,
    planning_node,
    response_node,
    route_after_intake,
    route_after_validation,
    validator_node,
)
from app.graph.state import TravelGraphState
from app.models.response import ChatResponse


def build_travel_graph():
    workflow = StateGraph(TravelGraphState)

    workflow.add_node("intake", intake_node)
    workflow.add_node("clarify_response", clarify_response_node)
    workflow.add_node("planning", planning_node)
    workflow.add_node("validator", validator_node)
    workflow.add_node("response", response_node)

    workflow.add_edge(START, "intake")
    workflow.add_conditional_edges(
        "intake",
        route_after_intake,
        {
            "clarify": "clarify_response",
            "planning": "planning",
        },
    )
    workflow.add_edge("clarify_response", END)
    workflow.add_edge("planning", "validator")
    workflow.add_conditional_edges(
        "validator",
        route_after_validation,
        {
            "planning": "planning",
            "response": "response",
        },
    )
    workflow.add_edge("response", END)

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

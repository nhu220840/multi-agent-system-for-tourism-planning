from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

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


def iter_travel_graph_events(
    message: str,
    *,
    top_k: int = 5,
    with_plan: bool = True,
    category: str | None = None,
) -> Iterator[dict[str, Any]]:
    state: TravelGraphState = {
        "message": message,
        "top_k": top_k,
        "with_plan": with_plan,
        "category": category,
        "trace": [],
    }

    yield {
        "type": "stage",
        "stage": "intake",
        "status": "started",
        "trace": [],
    }
    state.update(intake_node(state))
    intake_route = route_after_intake(state)
    yield {
        "type": "stage",
        "stage": "intake",
        "status": "completed",
        "trace": list(state.get("trace", [])),
        "conversation_stage": "intake" if intake_route == "clarify" else "planning",
        "missing_fields": list(state.get("missing_fields", []) or []),
        "follow_up_questions": list(state.get("follow_up_questions", []) or []),
    }

    if intake_route == "clarify":
        yield {
            "type": "stage",
            "stage": "response",
            "status": "started",
            "trace": list(state.get("trace", [])),
            "conversation_stage": "intake",
        }
        state.update(clarify_response_node(state))
        yield {
            "type": "stage",
            "stage": "response",
            "status": "completed",
            "trace": list(state.get("trace", [])),
            "conversation_stage": "intake",
        }
        yield {
            "type": "response",
            "response": ChatResponse(**state["response_payload"]).model_dump(),
        }
        return

    while True:
        yield {
            "type": "stage",
            "stage": "planning",
            "status": "started",
            "trace": list(state.get("trace", [])),
            "conversation_stage": "planning",
            "retrying": bool(state.get("retry_query")),
        }
        state.update(planning_node(state))
        yield {
            "type": "stage",
            "stage": "planning",
            "status": "completed",
            "trace": list(state.get("trace", [])),
            "conversation_stage": "planning",
        }

        yield {
            "type": "stage",
            "stage": "validator",
            "status": "started",
            "trace": list(state.get("trace", [])),
            "conversation_stage": "planning",
        }
        state.update(validator_node(state))
        validator_route = route_after_validation(state)
        yield {
            "type": "stage",
            "stage": "validator",
            "status": "completed",
            "trace": list(state.get("trace", [])),
            "conversation_stage": "planning",
            "needs_replan": validator_route == "planning",
        }

        if validator_route == "planning":
            continue
        break

    yield {
        "type": "stage",
        "stage": "response",
        "status": "started",
        "trace": list(state.get("trace", [])),
        "conversation_stage": "planning",
    }
    state.update(response_node(state))
    yield {
        "type": "stage",
        "stage": "response",
        "status": "completed",
        "trace": list(state.get("trace", [])),
        "conversation_stage": "planning",
    }
    yield {
        "type": "response",
        "response": ChatResponse(**state["response_payload"]).model_dump(),
    }

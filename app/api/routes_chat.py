from __future__ import annotations

import json
from collections.abc import Iterator
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.dependencies import get_current_principal
from app.graph.build_graph import iter_travel_graph_events, run_travel_graph
from app.models.request import ChatSendRequest
from app.models.response import ChatResponse
from app.services.conversation_service import ConversationService
from app.services.session_service import PrincipalContext

router = APIRouter()


def get_conversation_service() -> ConversationService:
    return ConversationService()


def _execute_chat(
    request: ChatSendRequest,
    principal: PrincipalContext,
    conversation_service: ConversationService,
) -> ChatResponse:
    request_started = perf_counter()

    step_started = perf_counter()
    conversation = conversation_service.get_or_create_conversation(
        principal_id=principal.principal_id,
        conversation_id=request.conversation_id,
        initial_message=request.message,
    )
    conversation_lookup_ms = round((perf_counter() - step_started) * 1000, 1)

    step_started = perf_counter()
    effective_message = conversation_service.build_effective_user_message(
        principal_id=principal.principal_id,
        conversation_id=conversation.id,
        current_message=request.message,
    )
    effective_message_ms = round((perf_counter() - step_started) * 1000, 1)

    step_started = perf_counter()
    response = run_travel_graph(
        effective_message,
        top_k=request.top_k,
        with_plan=request.with_plan,
        category=request.category,
    )
    graph_ms = round((perf_counter() - step_started) * 1000, 1)

    step_started = perf_counter()
    conversation_service.append_message(
        conversation_id=conversation.id,
        role="user",
        content=request.message,
        metadata={
            "top_k": request.top_k,
            "with_plan": request.with_plan,
            "category": request.category,
            "effective_message": effective_message,
        },
    )
    save_user_message_ms = round((perf_counter() - step_started) * 1000, 1)
    response.conversation_id = conversation.id
    step_started = perf_counter()
    conversation_service.append_message(
        conversation_id=conversation.id,
        role="assistant",
        content=response.answer,
        metadata={
            **response.model_dump(),
            "effective_message": effective_message,
        },
    )
    save_assistant_message_ms = round((perf_counter() - step_started) * 1000, 1)
    total_request_ms = round((perf_counter() - request_started) * 1000, 1)
    response.grounding = {
        **(response.grounding or {}),
        "request_timing_ms": {
            "conversation_lookup_ms": conversation_lookup_ms,
            "effective_message_ms": effective_message_ms,
            "graph_ms": graph_ms,
            "save_user_message_ms": save_user_message_ms,
            "save_assistant_message_ms": save_assistant_message_ms,
            "total_request_ms": total_request_ms,
        },
    }
    return response


def _encode_sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _iter_answer_chunks(answer: str, target_size: int = 36) -> Iterator[str]:
    buffer = ""
    for char in answer:
        buffer += char
        if char == "\n":
            yield buffer
            buffer = ""
            continue
        if len(buffer) >= target_size and char.isspace():
            yield buffer
            buffer = ""
    if buffer:
        yield buffer


def _execute_chat_stream(
    request: ChatSendRequest,
    principal: PrincipalContext,
    conversation_service: ConversationService,
) -> Iterator[str]:
    request_started = perf_counter()

    step_started = perf_counter()
    conversation = conversation_service.get_or_create_conversation(
        principal_id=principal.principal_id,
        conversation_id=request.conversation_id,
        initial_message=request.message,
    )
    conversation_lookup_ms = round((perf_counter() - step_started) * 1000, 1)
    yield _encode_sse(
        "conversation",
        {
            "conversation_id": conversation.id,
        },
    )

    step_started = perf_counter()
    effective_message = conversation_service.build_effective_user_message(
        principal_id=principal.principal_id,
        conversation_id=conversation.id,
        current_message=request.message,
    )
    effective_message_ms = round((perf_counter() - step_started) * 1000, 1)

    response: ChatResponse | None = None
    step_started = perf_counter()
    for event in iter_travel_graph_events(
        effective_message,
        top_k=request.top_k,
        with_plan=request.with_plan,
        category=request.category,
    ):
        if event.get("type") == "stage":
            yield _encode_sse(
                "stage",
                {
                    "stage": event.get("stage"),
                    "status": event.get("status"),
                    "trace": event.get("trace", []),
                    "conversation_stage": event.get("conversation_stage"),
                    "missing_fields": event.get("missing_fields", []),
                    "follow_up_questions": event.get("follow_up_questions", []),
                    "needs_replan": bool(event.get("needs_replan")),
                    "retrying": bool(event.get("retrying")),
                },
            )
            continue
        if event.get("type") == "response":
            response = ChatResponse(**dict(event.get("response") or {}))
            break
    graph_ms = round((perf_counter() - step_started) * 1000, 1)

    if response is None:
        raise RuntimeError("Chat stream finished without a response payload.")

    step_started = perf_counter()
    conversation_service.append_message(
        conversation_id=conversation.id,
        role="user",
        content=request.message,
        metadata={
            "top_k": request.top_k,
            "with_plan": request.with_plan,
            "category": request.category,
            "effective_message": effective_message,
        },
    )
    save_user_message_ms = round((perf_counter() - step_started) * 1000, 1)
    response.conversation_id = conversation.id
    step_started = perf_counter()
    conversation_service.append_message(
        conversation_id=conversation.id,
        role="assistant",
        content=response.answer,
        metadata={
            **response.model_dump(),
            "effective_message": effective_message,
        },
    )
    save_assistant_message_ms = round((perf_counter() - step_started) * 1000, 1)
    total_request_ms = round((perf_counter() - request_started) * 1000, 1)
    response.grounding = {
        **(response.grounding or {}),
        "request_timing_ms": {
            "conversation_lookup_ms": conversation_lookup_ms,
            "effective_message_ms": effective_message_ms,
            "graph_ms": graph_ms,
            "save_user_message_ms": save_user_message_ms,
            "save_assistant_message_ms": save_assistant_message_ms,
            "total_request_ms": total_request_ms,
        },
    }

    yield _encode_sse("answer_start", {"conversation_id": conversation.id})
    for delta in _iter_answer_chunks(response.answer):
        yield _encode_sse("answer_delta", {"delta": delta})
    yield _encode_sse("complete", response.model_dump())


@router.post("/send", response_model=ChatResponse)
def chat_send(
    request: ChatSendRequest,
    principal: PrincipalContext = Depends(get_current_principal),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ChatResponse:
    return _execute_chat(request, principal, conversation_service)


@router.post("/stream")
def chat_send_stream(
    request: ChatSendRequest,
    principal: PrincipalContext = Depends(get_current_principal),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> StreamingResponse:
    def event_stream() -> Iterator[str]:
        try:
            yield from _execute_chat_stream(request, principal, conversation_service)
        except Exception as exc:
            yield _encode_sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

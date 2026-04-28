from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.core.dependencies import get_current_principal
from app.models.principal import ConversationDetail, ConversationSummary
from app.services.conversation_service import ConversationService
from app.services.session_service import PrincipalContext

router = APIRouter()


def get_conversation_service() -> ConversationService:
    return ConversationService()


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    principal: PrincipalContext = Depends(get_current_principal),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> list[ConversationSummary]:
    return conversation_service.list_conversations(principal.principal_id)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def read_conversation(
    conversation_id: str,
    principal: PrincipalContext = Depends(get_current_principal),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> ConversationDetail:
    return conversation_service.get_conversation_detail(principal.principal_id, conversation_id)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    principal: PrincipalContext = Depends(get_current_principal),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Response:
    conversation_service.delete_conversation(principal.principal_id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", response_model=dict[str, int])
def delete_all_conversations(
    principal: PrincipalContext = Depends(get_current_principal),
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> dict[str, int]:
    deleted_count = conversation_service.delete_all_conversations(principal.principal_id)
    return {"deleted_conversations": deleted_count}

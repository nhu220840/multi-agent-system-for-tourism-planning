from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_principal
from app.models.principal import PrincipalPayload, SessionInfoResponse
from app.services.session_service import PrincipalContext

router = APIRouter()


def _to_session_response(principal: PrincipalContext) -> SessionInfoResponse:
    return SessionInfoResponse(
        principal=PrincipalPayload(
            id=principal.principal_id,
            type=principal.principal_type,
        ),
        session_started_at=datetime.fromisoformat(principal.first_seen_at),
        last_seen_at=datetime.fromisoformat(principal.last_seen_at),
    )


@router.post("/init", response_model=SessionInfoResponse)
def init_session(
    principal: PrincipalContext = Depends(get_current_principal),
) -> SessionInfoResponse:
    return _to_session_response(principal)


@router.get("/me", response_model=SessionInfoResponse)
def read_session(
    principal: PrincipalContext = Depends(get_current_principal),
) -> SessionInfoResponse:
    return _to_session_response(principal)

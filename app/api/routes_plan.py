from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_principal
from app.models.principal import PlanPayload, PlanSaveRequest
from app.services.plan_service import PlanService
from app.services.session_service import PrincipalContext

router = APIRouter()


def get_plan_service() -> PlanService:
    return PlanService()


@router.post("/save", response_model=PlanPayload)
def save_plan(
    request: PlanSaveRequest,
    principal: PrincipalContext = Depends(get_current_principal),
    plan_service: PlanService = Depends(get_plan_service),
) -> PlanPayload:
    return plan_service.save_plan(
        principal_id=principal.principal_id,
        conversation_id=request.conversation_id,
        city=request.city,
        days=request.days,
        structured_json=request.structured_json,
    )


@router.get("/{plan_id}", response_model=PlanPayload)
def read_plan(
    plan_id: str,
    principal: PrincipalContext = Depends(get_current_principal),
    plan_service: PlanService = Depends(get_plan_service),
) -> PlanPayload:
    return plan_service.get_plan(principal_id=principal.principal_id, plan_id=plan_id)

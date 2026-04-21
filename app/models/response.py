from typing import Any

from pydantic import BaseModel, Field


class DebugStep(BaseModel):
    key: str
    title: str
    status: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    answer: str
    conversation_id: str | None = None
    plan_id: str | None = None
    conversation_stage: str = "planning"
    collected_info: dict[str, Any] | None = None
    missing_fields: list[str] | None = None
    follow_up_questions: list[str] | None = None
    trace: list[str] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    plan: str | None = None
    stay_plan: dict[str, Any] | None = None
    stay_recommendations: list[dict[str, Any]] | None = None
    plan_validation: dict[str, Any] | None = None
    research: str | None = None
    coordinator_plan: str | None = None
    weather: dict[str, Any] | None = None
    transport: list[str] | None = None
    recommended_hotel: dict[str, Any] | None = None
    mobility_plan: dict[str, Any] | None = None
    verified_places: list[dict[str, Any]] | None = None
    route_plan: list[dict[str, Any]] | None = None
    grounding: dict[str, Any] | None = None
    debug_steps: list[DebugStep] = Field(default_factory=list)

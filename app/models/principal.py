from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PrincipalPayload(BaseModel):
    id: str
    type: Literal["anonymous", "user"]
    display_name: str | None = None


class SessionInfoResponse(BaseModel):
    principal: PrincipalPayload
    session_started_at: datetime
    last_seen_at: datetime


class MessagePayload(BaseModel):
    id: str
    role: str
    content: str
    metadata: dict[str, Any] | None = None
    created_at: datetime


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    latest_message_preview: str | None = None
    message_count: int = 0


class ConversationDetail(ConversationSummary):
    principal_id: str
    messages: list[MessagePayload] = Field(default_factory=list)

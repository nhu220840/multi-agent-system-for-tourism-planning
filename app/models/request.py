from pydantic import BaseModel, Field


class ChatSendRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message")
    conversation_id: str | None = Field(
        default=None,
        description="Existing conversation id for follow-up messages.",
    )
    top_k: int = Field(default=5, ge=1, le=50)
    with_plan: bool = True
    category: str | None = None

from fastapi import APIRouter

from app.api.routes_chat import router as chat_router
from app.api.routes_conversations import router as conversations_router
from app.api.routes_plan import router as plan_router
from app.api.routes_session import router as session_router

router = APIRouter()
router.include_router(session_router, prefix="/session", tags=["session"])
router.include_router(chat_router, prefix="/chat", tags=["chat"])
router.include_router(conversations_router, prefix="/conversations", tags=["conversations"])
router.include_router(plan_router, prefix="/plans", tags=["plans"])

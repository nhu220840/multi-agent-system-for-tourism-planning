from fastapi import APIRouter

from app.graph.build_graph import run_travel_graph
from app.models.request import ChatRequest
from app.models.response import ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    return run_travel_graph(request.message, top_k=5, with_plan=True)

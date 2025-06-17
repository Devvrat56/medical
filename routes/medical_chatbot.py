import logging
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from source.medical_chatbot.MedicalChatBot import MedicalChatBot
from typing import Optional

medical_chat_router = APIRouter()
logger = logging.getLogger(__name__)


class MedicalChat(BaseModel):
    query: Optional[str] = Field(
        " ",
        description="Query to be asked to the chatbot",
    )
    session: str
    image_base64: Optional[str] = Field(
        None,
        description="Optional base64 encoded image string",
    )


@medical_chat_router.post("/medical_chat", response_class=JSONResponse)
async def chat(request: MedicalChat):
    try:
        analyzer = MedicalChatBot(
            request.session, request.query, image_base64=request.image_base64
        )
        report = analyzer.chatbot_flow()
        return report
    except Exception as e:
        logger.error(f"Unhandled error:-{e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Chatbot API error: {e}"},
        )

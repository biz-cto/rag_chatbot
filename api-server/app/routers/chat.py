import logging
from typing import List, Dict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.services.rag_service import RagService, get_rag_service
from app.utils.logger_config import setup_logger

# 채팅 라우터용 로거 설정
logger = setup_logger("app.routers.chat", "logs/chat.log", logging.INFO)

router = APIRouter(prefix="/chat", tags=["chat"])

class ChatRequest(BaseModel):
    question: str

class SourceContent(BaseModel):
    source: str
    contents: List[str]

class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceContent] = []

@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest, rag_service: RagService = Depends(get_rag_service)):
    """사용자 질문에 대한 응답을 생성합니다."""
    logger.info(f"질문 받음: {request.question}")
    try:
        response = rag_service.answer_question(request.question)
        logger.debug(f"응답 생성 완료: {response['answer'][:50]}...")
        return response
    
    except Exception as e:
        logger.error(f"질문 처리 중 오류 발생: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"질문 처리 중 오류 발생: {str(e)}")

@router.post("/reset")
async def reset_chat(rag_service: RagService = Depends(get_rag_service)):
    """대화 기록을 초기화합니다."""
    logger.info("대화 기록 초기화 요청")
    rag_service.reset_conversation()
    return {"message": "대화 기록이 초기화되었습니다."}

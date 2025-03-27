import json
import logging
import boto3
import os
import traceback
from typing import Dict, List, Any, Optional
from .embeddings import EmbeddingService
from .document_store import DocumentStore
from .retriever import Retriever
from .bedrock_client import BedrockClient

# 로깅 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class ChatService:
    """
    RAG 챗봇 서비스 클래스
    """
    
    def __init__(self, s3_bucket_name: str, aws_region: str):
        """
        ChatService 초기화
        
        Parameters:
        - s3_bucket_name: PDF 문서가 저장된 S3 버킷 이름
        - aws_region: AWS 리전
        """
        self.s3_bucket_name = s3_bucket_name
        self.aws_region = aws_region
        self.conversations: Dict[str, List[Dict[str, str]]] = {}
        
        # 서비스 컴포넌트 초기화
        self.embedding_service = EmbeddingService(aws_region)
        self.document_store = DocumentStore(s3_bucket_name, aws_region)
        self.retriever = Retriever(self.document_store, self.embedding_service)
        self.llm = BedrockClient(aws_region)
        
        logger.info(f"ChatService 초기화 완료 - 버킷: {s3_bucket_name}, 리전: {aws_region}")
    
    def process_message(self, user_message: str, session_id: str) -> Dict[str, Any]:
        """
        사용자 메시지 처리 및 응답 생성
        
        Parameters:
        - user_message: 사용자 메시지
        - session_id: 세션 ID
        
        Returns:
        - 응답 내용
        """
        logger.info(f"사용자 메시지 처리 - 세션: {session_id}")
        
        # 메시지 유효성 검사
        if not user_message or not user_message.strip():
            logger.warning(f"세션 {session_id}에서 빈 메시지 수신")
            return {
                "response": "메시지가 비어 있습니다. 질문을 입력해 주세요.",
                "sources": []
            }
        
        # 대화 기록 초기화 (필요 시)
        if session_id not in self.conversations:
            self.conversations[session_id] = []
        
        # 사용자 메시지 추가
        self.conversations[session_id].append({
            "role": "user",
            "content": user_message
        })
        
        try:
            # 관련 문서 검색
            relevant_docs = self.retriever.retrieve(user_message)
            
            # 검색 결과 확인
            if not relevant_docs:
                logger.warning(f"쿼리 '{user_message[:30]}...'에 대한 관련 문서를 찾지 못했습니다.")
            
            # 컨텍스트 구성
            context = "\n\n".join([doc['content'] for doc in relevant_docs])
            sources = [doc['source'] for doc in relevant_docs]
            
            # 프롬프트 구성 및 응답 생성
            response = self._generate_response(user_message, context, session_id)
            
            # 어시스턴트 응답 추가
            self.conversations[session_id].append({
                "role": "assistant",
                "content": response
            })
            
            return {
                "response": response,
                "sources": list(set(sources))
            }
        except Exception as e:
            error_msg = f"메시지 처리 중 오류: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            
            # 오류 발생 시 기본 응답
            fallback_response = "죄송합니다. 요청을 처리하는 중에 문제가 발생했습니다."
            
            # 어시스턴트 응답 추가 (오류 상황도 기록)
            self.conversations[session_id].append({
                "role": "assistant",
                "content": fallback_response
            })
            
            return {
                "response": fallback_response,
                "sources": [],
                "error": str(e)
            }
    
    def _generate_response(self, user_message: str, context: str, session_id: str) -> str:
        """
        LLM을 사용하여 응답 생성
        
        Parameters:
        - user_message: 사용자 메시지
        - context: 검색된 문서 컨텍스트
        - session_id: 세션 ID
        
        Returns:
        - LLM 응답
        """
        # 사용자 대화 기록 (최근 5개 메시지만 사용)
        conversation_history = self.conversations[session_id][-10:]
        
        # 프롬프트 구성
        system_prompt = f"""당신은 도움이 되는 AI 어시스턴트입니다. 
아래 제공된 컨텍스트를 기반으로 사용자 질문에 정확하게 답변하세요.
컨텍스트에 관련 정보가 없는 경우, '이 정보는 제공된 문서에 포함되어 있지 않습니다.'라고 답변하세요.

컨텍스트:
{context}
"""
        
        # LLM에 요청 보내기
        response = self.llm.generate_response(
            system_prompt=system_prompt,
            conversation_history=conversation_history
        )
        
        return response
    
    def reset_conversation(self, session_id: str) -> None:
        """
        특정 세션의 대화 기록 초기화
        
        Parameters:
        - session_id: 초기화할 세션 ID
        """
        logger.info(f"대화 기록 초기화 - 세션: {session_id}")
        if session_id in self.conversations:
            self.conversations[session_id] = [] 
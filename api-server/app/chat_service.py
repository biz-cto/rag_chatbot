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
        # Bedrock은 무조건 us-east-1 리전 사용
        self.aws_region = "us-east-1"
        self.conversations: Dict[str, List[Dict[str, str]]] = {}
        
        # 단계적으로 서비스 컴포넌트 초기화
        logger.info(f"ChatService 초기화 시작 - 버킷: {s3_bucket_name}, 리전: {self.aws_region}")
        
        try:
            # 임베딩 서비스 초기화
            logger.info("EmbeddingService 초기화 중...")
            self.embedding_service = EmbeddingService(self.aws_region)
            
            # 문서 저장소 초기화 (S3는 원래 리전 사용)
            logger.info("DocumentStore 초기화 중...")
            self.document_store = DocumentStore(s3_bucket_name, aws_region)
            
            # 검색기 초기화
            logger.info("Retriever 초기화 중...")
            self.retriever = Retriever(self.document_store, self.embedding_service)
            
            # LLM 클라이언트 초기화
            logger.info("BedrockClient 초기화 중...")
            self.llm = BedrockClient(self.aws_region)
            
            # 모든 컴포넌트 초기화 확인
            self._check_components()
            
            logger.info(f"ChatService 초기화 완료 - 버킷: {s3_bucket_name}, 리전: {self.aws_region}")
        except Exception as e:
            logger.error(f"ChatService 초기화 중 오류 발생: {str(e)}")
            logger.error(traceback.format_exc())
            raise
    
    def _check_components(self):
        """서비스 컴포넌트 유효성 검사"""
        if not hasattr(self, 'embedding_service') or self.embedding_service.bedrock_runtime is None:
            logger.warning("EmbeddingService가 정상적으로 초기화되지 않았습니다. 임베딩 기능이 제한됩니다.")
        
        if not hasattr(self, 'document_store') or not self.document_store.documents:
            logger.warning("DocumentStore가 정상적으로 초기화되지 않았거나 문서가 로드되지 않았습니다.")
        
        if not hasattr(self, 'llm') or self.llm.bedrock_runtime is None:
            logger.warning("BedrockClient가 정상적으로 초기화되지 않았습니다. LLM 응답 생성 기능이 제한됩니다.")
    
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
            # 관련 문서 검색 시도
            relevant_docs = []
            try:
                if hasattr(self, 'retriever'):
                    relevant_docs = self.retriever.retrieve(user_message)
            except Exception as retriever_error:
                logger.error(f"문서 검색 중 오류: {str(retriever_error)}")
            
            # 검색 결과 확인
            if not relevant_docs:
                logger.warning(f"쿼리 '{user_message[:30]}...'에 대한 관련 문서를 찾지 못했습니다.")
            
            # 컨텍스트 구성
            context = "\n\n".join([doc['content'] for doc in relevant_docs]) if relevant_docs else ""
            sources = [doc['source'] for doc in relevant_docs] if relevant_docs else []
            
            # 프롬프트 구성 및 응답 생성
            response = self._generate_response(user_message, context, session_id)
            
            # JSON 응답 처리
            if response.strip().startswith("{"):
                try:
                    # 중첩된 JSON 문제 처리
                    current_response = response
                    if "\"answer\": \"{" in response:
                        try:
                            # 외부 JSON 파싱
                            outer_json = json.loads(response)
                            if "answer" in outer_json and isinstance(outer_json["answer"], str):
                                inner_str = outer_json["answer"]
                                # 내부 JSON이 유효한지 확인
                                if inner_str.strip().startswith("{") and inner_str.strip().endswith("}"):
                                    try:
                                        inner_json = json.loads(inner_str)
                                        if "answer" in inner_json:
                                            # 내부 JSON 사용
                                            current_response = inner_str
                                    except:
                                        # 내부 JSON 파싱 실패시 원본 유지
                                        pass
                        except:
                            # 외부 JSON 파싱 실패시 원본 유지
                            pass
                    
                    # 최종 JSON 파싱
                    json_response = json.loads(current_response)
                    
                    # 응답 형식 확인
                    if "answer" in json_response:
                        # 대화 기록에 추가
                        self.conversations[session_id].append({
                            "role": "assistant",
                            "content": json_response["answer"]
                        })
                        
                        # 원본 JSON 응답 반환
                        return json_response
                except json.JSONDecodeError:
                    logger.warning("JSON 파싱 실패, 일반 텍스트로 처리")
                except Exception as json_error:
                    logger.error(f"JSON 응답 처리 오류: {str(json_error)}")
            else:
                logger.info("일반 텍스트 응답 처리")
            
            # 일반 텍스트 응답 처리 (JSON 파싱 실패 시)
            self.conversations[session_id].append({
                "role": "assistant",
                "content": response
            })
            
            # 기존 형식으로 응답 변환
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
        # LLM 클라이언트가 초기화되지 않은 경우
        if not hasattr(self, 'llm') or self.llm.bedrock_runtime is None:
            logger.warning("LLM 클라이언트가 초기화되지 않아 기본 응답 반환")
            if context:
                return "이 질문에 관련된 정보를 찾았으나, 현재 AI 응답 생성에 문제가 있습니다. 잠시 후 다시 시도해 주세요."
            else:
                return "죄송합니다. 현재 AI 응답 생성에 문제가 있습니다. 잠시 후 다시 시도해 주세요."
                
        # 빠른 응답을 위해 대화 기록 제한 (최근 5개만 사용)
        conversation_history = self.conversations[session_id][-5:]
        
        # 원본 문서 정보 및 출처 가져오기
        doc_sources = []
        if context:
            try:
                # 검색된 문서의 원본 정보 추출
                for doc in self.retriever.retrieve(user_message):
                    if 'source' in doc and 'content' in doc:
                        doc_sources.append({
                            'source': doc['source'],
                            'contents': [doc['content']]
                        })
            except Exception as e:
                logger.error(f"문서 원본 정보 추출 중 오류: {str(e)}")
        
        # JSON 응답 형식 지시사항 추가 - 간소화하여 모델이 쉽게 따르도록 함
        json_format_instruction = """
다음 형식으로 JSON 응답을 제공하세요:
{
  "answer": "답변 내용",
  "sources": [{"source": "출처명", "contents": ["내용"]}]
}
"""
        
        # 프롬프트 간소화하여 처리 속도 향상
        system_prompt = f"""당신은 문서 기반 질의응답 AI입니다.
주어진 컨텍스트 정보만 사용하여 사용자 질문에 답변하세요.
컨텍스트에 없는 내용은 '이 정보는 제공된 문서에 포함되어 있지 않습니다'라고 답하세요.

컨텍스트:
{context}

{json_format_instruction}
"""
        
        # 문서 소스 정보 로그 추가
        if doc_sources:
            logger.info(f"응답 생성에 사용 가능한 문서 소스: {len(doc_sources)}개")
        
        # LLM에 요청 보내기
        try:
            response = self.llm.generate_response(
                system_prompt=system_prompt,
                conversation_history=conversation_history
            )
            
            # JSON 응답인지 확인
            if response.strip().startswith("{") and response.strip().endswith("}"):
                try:
                    # 유효한 JSON 확인
                    json_response = json.loads(response)
                    
                    # JSON 응답에 문서 소스 정보가 없으면 추가
                    if "sources" not in json_response and doc_sources:
                        logger.info(f"JSON 응답에 문서 소스 정보 추가: {len(doc_sources)}개")
                        json_response["sources"] = doc_sources
                        response = json.dumps(json_response, ensure_ascii=False)
                    
                    logger.info(f"최종 LLM 응답 구조: {', '.join(json_response.keys())}")
                except json.JSONDecodeError:
                    logger.warning("LLM의 응답이 유효한 JSON 형식이 아닙니다")
                except Exception as json_error:
                    logger.error(f"JSON 응답 처리 중 오류: {str(json_error)}")
            else:
                logger.info("응답이 JSON 형식이 아닙니다.")
            
            return response
        except Exception as e:
            logger.error(f"LLM 응답 생성 중 오류: {str(e)}")
            if context:
                return "관련 정보를 찾았으나 응답 생성 중 오류가 발생했습니다. 질문을 다시 작성해 주세요."
            else:
                return "죄송합니다. 응답 생성 중 오류가 발생했습니다. 다시 시도해 주세요."
    
    def reset_conversation(self, session_id: str) -> None:
        """
        특정 세션의 대화 기록 초기화
        
        Parameters:
        - session_id: 초기화할 세션 ID
        """
        logger.info(f"대화 기록 초기화 - 세션: {session_id}")
        if session_id in self.conversations:
            self.conversations[session_id] = [] 
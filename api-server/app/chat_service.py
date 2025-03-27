import json
import logging
import boto3
import os
import traceback
from typing import Dict, List, Any, Optional, Tuple
from .embeddings import EmbeddingService
from .document_store import DocumentStore
from .retriever import Retriever
from .bedrock_client import BedrockClient
from .utils.cost_tracker import CostTracker

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
        self.cost_tracker = CostTracker()
        
        # Lambda 메모리 가져오기
        try:
            self.lambda_memory_mb = int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "1024"))
        except (ValueError, TypeError):
            self.lambda_memory_mb = 1024
        
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
        # 비용 추적 시작
        self.cost_tracker.start(self.lambda_memory_mb)
        
        logger.info(f"사용자 메시지 처리 - 세션: {session_id}")
        
        # 메시지 유효성 검사
        if not user_message or not user_message.strip():
            logger.warning(f"세션 {session_id}에서 빈 메시지 수신")
            # 비용 추적 완료 및 로깅
            self.cost_tracker.stop()
            self.cost_tracker.log_costs(request_id=session_id, request_type="chat_empty")
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
            embedding_token_usage = {"input_tokens": 0, "model_id": ""}
            try:
                if hasattr(self, 'retriever'):
                    relevant_docs, retrieval_token_usage = self.retriever.retrieve_with_usage(user_message)
                    # 임베딩 토큰 사용량 추적
                    embedding_token_usage = retrieval_token_usage
                    if embedding_token_usage["model_id"]:
                        self.cost_tracker.add_bedrock_cost(
                            embedding_token_usage["model_id"],
                            embedding_token_usage["input_tokens"],
                            0  # 임베딩은 출력 토큰 없음
                        )
            except Exception as retriever_error:
                logger.error(f"문서 검색 중 오류: {str(retriever_error)}")
            
            # 검색 결과 확인
            if not relevant_docs:
                logger.warning(f"쿼리 '{user_message[:30]}...'에 대한 관련 문서를 찾지 못했습니다.")
            else:
                # S3 비용 추적 (PDF 접근)
                s3_requests = 1 if relevant_docs else 0
                s3_data_size = sum(len(doc.get('content', '')) for doc in relevant_docs) / 1024  # KB 단위
                self.cost_tracker.add_s3_cost(get_requests=s3_requests, data_size_kb=s3_data_size)
            
            # 컨텍스트 구성
            context = "\n\n".join([doc['content'] for doc in relevant_docs]) if relevant_docs else ""
            sources = [doc['source'] for doc in relevant_docs] if relevant_docs else []
            
            # 프롬프트 구성 및 응답 생성
            response, llm_token_usage = self._generate_response(user_message, context, session_id)
            
            # LLM 토큰 사용량 추적
            if llm_token_usage["model_id"]:
                self.cost_tracker.add_bedrock_cost(
                    llm_token_usage["model_id"],
                    llm_token_usage["input_tokens"],
                    llm_token_usage["output_tokens"]
                )
            
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
                        response_data = json_response
                        
                        # 비용 추적 완료 및 로깅
                        self.cost_tracker.stop()
                        cost_info = self.cost_tracker.log_costs(request_id=session_id, request_type="chat_json")
                        
                        # 응답에 비용 정보 추가 (개발용)
                        if os.environ.get("COST_DEBUG", "").lower() == "true":
                            response_data["_debug_cost"] = cost_info
                        
                        return response_data
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
            
            # 기본 출처 정보 생성
            default_sources = []
            if sources:
                for source in sources:
                    if isinstance(source, str):
                        # 출처 문자열을 파싱하여 파일명과 페이지 정보 추출
                        source_display = source
                        if " (페이지 " in source:
                            parts = source.split(" (페이지 ")
                            file_path = parts[0]
                            page = parts[1].replace(")", "")
                            # 파일명만 추출 (경로 제거)
                            file_name = file_path.split("/")[-1] if "/" in file_path else file_path
                            source_display = f"PDF 파일: {file_name} (페이지: {page})"
                        
                        default_sources.append({
                            "source": source_display,
                            "contents": []
                        })
                    elif isinstance(source, dict) and "source" in source:
                        default_sources.append(source)
            
            # 소스 정보가 없지만 컨텍스트가 있는 경우 기본 정보 추가
            if not default_sources and context:
                # 관련 문서에서 일부 정보라도 추출
                sample_content = []
                if relevant_docs and len(relevant_docs) > 0:
                    first_doc = relevant_docs[0]
                    if 'source' in first_doc:
                        source_name = first_doc['source']
                        source_display = source_name
                        # 파일 정보 추출
                        if 'file' in first_doc:
                            file_name = first_doc['file'].split("/")[-1] if "/" in first_doc['file'] else first_doc['file']
                            source_display = f"PDF 파일: {file_name}"
                            if 'page' in first_doc:
                                source_display += f" (페이지: {first_doc['page']})"
                        
                        default_sources.append({
                            "source": source_display,
                            "contents": sample_content or ["관련 문서 내용"]
                        })
            
            response_data = {
                "answer": response,
                "sources": default_sources
            }
            
            # 비용 추적 완료 및 로깅
            self.cost_tracker.stop()
            cost_info = self.cost_tracker.log_costs(request_id=session_id, request_type="chat_text")
            
            # 응답에 비용 정보 추가 (개발용)
            if os.environ.get("COST_DEBUG", "").lower() == "true":
                response_data["_debug_cost"] = cost_info
            
            return response_data
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
            
            # 기본 출처 정보 생성
            default_sources = []
            if sources:
                for source in sources:
                    if isinstance(source, str):
                        # 출처 문자열을 파싱하여 파일명과 페이지 정보 추출
                        source_display = source
                        if " (페이지 " in source:
                            parts = source.split(" (페이지 ")
                            file_path = parts[0]
                            page = parts[1].replace(")", "")
                            # 파일명만 추출 (경로 제거)
                            file_name = file_path.split("/")[-1] if "/" in file_path else file_path
                            source_display = f"PDF 파일: {file_name} (페이지: {page})"
                        
                        default_sources.append({
                            "source": source_display,
                            "contents": []
                        })
                    elif isinstance(source, dict) and "source" in source:
                        default_sources.append(source)
            
            # 소스 정보가 없지만 컨텍스트가 있는 경우 기본 정보 추가
            if not default_sources and context:
                # 관련 문서에서 일부 정보라도 추출
                sample_content = []
                if relevant_docs and len(relevant_docs) > 0:
                    first_doc = relevant_docs[0]
                    if 'source' in first_doc:
                        source_name = first_doc['source']
                        source_display = source_name
                        # 파일 정보 추출
                        if 'file' in first_doc:
                            file_name = first_doc['file'].split("/")[-1] if "/" in first_doc['file'] else first_doc['file']
                            source_display = f"PDF 파일: {file_name}"
                            if 'page' in first_doc:
                                source_display += f" (페이지: {first_doc['page']})"
                        
                        default_sources.append({
                            "source": source_display,
                            "contents": sample_content or ["관련 문서 내용"]
                        })
            
            # 비용 추적 완료 및 로깅
            self.cost_tracker.stop()
            self.cost_tracker.log_costs(request_id=session_id, request_type="chat_error")
            
            return {
                "answer": fallback_response,
                "sources": default_sources,
                "error": str(e)
            }
    
    def _generate_response(self, user_message: str, context: str, session_id: str) -> Tuple[str, Dict[str, Any]]:
        """
        LLM을 사용하여 응답 생성
        
        Parameters:
        - user_message: 사용자 메시지
        - context: 검색된 문서 컨텍스트
        - session_id: 세션 ID
        
        Returns:
        - LLM 응답, 토큰 사용량 {input_tokens, output_tokens, model_id}
        """
        # LLM 클라이언트가 초기화되지 않은 경우
        if not hasattr(self, 'llm') or self.llm.bedrock_runtime is None:
            logger.warning("LLM 클라이언트가 초기화되지 않아 기본 응답 반환")
            if context:
                return "이 질문에 관련된 정보를 찾았으나, 현재 AI 응답 생성에 문제가 있습니다. 잠시 후 다시 시도해 주세요.", {"input_tokens": 0, "output_tokens": 0, "model_id": ""}
            else:
                return "죄송합니다. 현재 AI 응답 생성에 문제가 있습니다. 잠시 후 다시 시도해 주세요.", {"input_tokens": 0, "output_tokens": 0, "model_id": ""}
                
        # 빠른 응답을 위해 대화 기록 제한 (최근 5개만 사용)
        conversation_history = self.conversations[session_id][-5:]
        
        # 원본 문서 정보 및 출처 가져오기
        doc_sources = []
        if context:
            try:
                # 검색된 문서의 원본 정보 추출
                for doc in self.retriever.retrieve(user_message):
                    if 'source' in doc:
                        # PDF 파일명과 페이지 정보 추출
                        source_display = doc['source']
                        # PDF 파일명만 깔끔하게 추출 (경로 제거)
                        if "file" in doc:
                            file_name = doc["file"].split("/")[-1] if "/" in doc["file"] else doc["file"]
                            source_display = f"PDF 파일: {file_name}"
                            if "page" in doc:
                                source_display += f" (페이지: {doc['page']})"
                        
                        source_info = {
                            'source': source_display,
                            'contents': []
                        }
                        if 'content' in doc:
                            # 내용을 더 명확하게 표시 (너무 길지 않게, 핵심 내용 중심으로)
                            content = doc['content']
                            
                            # 긴 내용은 중요 문장 위주로 추출 (마침표 기준으로 분리)
                            sentences = content.split('. ')
                            if len(sentences) > 3:
                                # 앞부분 2문장과 뒷부분 1문장 포함
                                content_preview = '. '.join(sentences[:2]) + '. ... ' + sentences[-1]
                            else:
                                content_preview = content[:300] + "..." if len(content) > 300 else content
                                
                            source_info['contents'].append(content_preview)
                        doc_sources.append(source_info)
            except Exception as e:
                logger.error(f"문서 원본 정보 추출 중 오류: {str(e)}")
                # 기본 소스 정보라도 추가
                if context:
                    doc_sources.append({
                        'source': "문서",
                        'contents': ["관련 문서 내용"]
                    })
        
        # 빈 문서 소스 배열이라도 생성
        if not doc_sources and context:
            doc_sources = [{
                'source': "문서",
                'contents': ["관련 문서 내용"]
            }]
        
        # JSON 응답 형식 지시사항 추가 - 중첩 JSON 문제 해결을 위한 명확한 지시
        json_format_instruction = """
반드시 다음 형식으로만 JSON 응답을 제공하세요. 중첩된 JSON이나 이스케이프된 따옴표를 사용하지 마세요:
{
  "answer": "답변 내용을 여기에 작성",
  "sources": [
    {
      "source": "출처명 (파일명, 페이지 등)",
      "contents": ["참고한 내용/문장"]
    }
  ]
}
JSON 문법을 정확히 준수하고, 답변은 "answer" 필드에 직접 작성하세요. 절대로 JSON 안에 또 다른 JSON을 포함시키지 마세요.
"""
        
        # 프롬프트 간소화하여 처리 속도 향상
        system_prompt = f"""당신은 문서 기반 질의응답 AI입니다.
주어진 컨텍스트 정보만 사용하여 사용자 질문에 답변하세요.
컨텍스트에 없는 내용은 '이 정보는 제공된 문서에 포함되어 있지 않습니다'라고 답하세요.
반드시 관련 문서 출처 정보를 포함해 주세요.
답변에 사용한 구체적인 문장이나 내용을 출처와 함께 명확히 표시하세요.

컨텍스트:
{context}

{json_format_instruction}
"""
        
        # 문서 소스 정보 로그 추가
        if doc_sources:
            logger.info(f"응답 생성에 사용 가능한 문서 소스: {len(doc_sources)}개")
        
        # LLM에 요청 보내기
        try:
            response, token_usage = self.llm.generate_response(
                system_prompt=system_prompt,
                conversation_history=conversation_history
            )
            
            # JSON 응답인지 확인
            if response.strip().startswith("{") and response.strip().endswith("}"):
                try:
                    # 유효한 JSON 확인
                    json_response = json.loads(response)
                    
                    # JSON 응답에 문서 소스 정보가 없거나 비어있으면 추가
                    if "sources" not in json_response or not json_response["sources"]:
                        logger.info(f"JSON 응답에 문서 소스 정보 추가: {len(doc_sources)}개")
                        json_response["sources"] = doc_sources
                    
                    # 최종 JSON 응답 생성
                    response = json.dumps(json_response, ensure_ascii=False)
                    logger.info(f"최종 LLM 응답 구조: {', '.join(json_response.keys())} (소스 수: {len(json_response.get('sources', []))})")
                except json.JSONDecodeError:
                    logger.warning("LLM의 응답이 유효한 JSON 형식이 아닙니다. 수동으로 JSON 형식 생성")
                    # JSON이 아닌 경우 수동으로 JSON 형식 생성
                    formatted_response = {
                        "answer": response,
                        "sources": doc_sources
                    }
                    response = json.dumps(formatted_response, ensure_ascii=False)
                except Exception as json_error:
                    logger.error(f"JSON 응답 처리 중 오류: {str(json_error)}")
                    # 오류 발생 시 수동으로 JSON 형식 생성
                    formatted_response = {
                        "answer": response,
                        "sources": doc_sources
                    }
                    response = json.dumps(formatted_response, ensure_ascii=False)
            else:
                logger.info("응답이 JSON 형식이 아닙니다. 수동으로 JSON 형식 생성")
                # JSON이 아닌 경우 수동으로 JSON 형식 생성
                formatted_response = {
                    "answer": response,
                    "sources": doc_sources
                }
                response = json.dumps(formatted_response, ensure_ascii=False)
            
            return response, token_usage
        except Exception as e:
            logger.error(f"LLM 응답 생성 중 오류: {str(e)}")
            # 오류 시에도 JSON 응답 형식 유지
            error_response = {
                "answer": "죄송합니다. 응답 생성 중 오류가 발생했습니다. 다시 시도해 주세요.",
                "sources": doc_sources,
                "error": str(e)
            }
            return json.dumps(error_response, ensure_ascii=False), {"input_tokens": 0, "output_tokens": 0, "model_id": ""}
    
    def reset_conversation(self, session_id: str) -> None:
        """
        특정 세션의 대화 기록 초기화
        
        Parameters:
        - session_id: 초기화할 세션 ID
        """
        # 비용 추적 시작
        self.cost_tracker.start(self.lambda_memory_mb)
        
        logger.info(f"대화 기록 초기화 - 세션: {session_id}")
        if session_id in self.conversations:
            self.conversations[session_id] = []
        
        # 비용 추적 완료 및 로깅
        self.cost_tracker.stop()
        self.cost_tracker.log_costs(request_id=session_id, request_type="reset") 
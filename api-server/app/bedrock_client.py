import json
import logging
import boto3
import botocore.config
import time
import random
import os
from typing import List, Dict, Any, Optional
from botocore.exceptions import ClientError, ConnectionError

# 로깅 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class BedrockClientError(Exception):
    """Bedrock 클라이언트 오류"""
    pass

class BedrockClient:
    """
    Amazon Bedrock LLM 클라이언트 클래스
    """
    
    def __init__(self, aws_region: str):
        """
        BedrockClient 초기화
        
        Parameters:
        - aws_region: AWS 리전
        """
        # Bedrock은 무조건 us-east-1 리전 사용
        self.aws_region = "us-east-1"
        self.bedrock_runtime = self._create_bedrock_client(self.aws_region)
        
        # 환경 변수로부터 모드 확인
        smart_mode = os.environ.get("SMART_MODE", "").lower() == "true"
        fast_mode = os.environ.get("FAST_MODE", "").lower() == "true"
        
        # 모드에 따라 모델 선택
        if smart_mode:
            # 스마트 모드 - Claude 3 Haiku로 변경 (Sonnet보다 빠름)
            self.model_id = "anthropic.claude-3-haiku-20240307-v1:0"
            self.max_tokens = 2048
            logger.info("스마트 모드 활성화: Claude 3 Haiku 모델 사용 (빠른 응답)")
        elif fast_mode:
            # 빠른 응답 모드 - Claude Instant 사용
            self.model_id = "anthropic.claude-instant-v1"
            self.max_tokens = 1024
            logger.info("빠른 응답 모드 활성화: Claude Instant 모델 사용")
        else:
            # 기본 모드 - Claude 3 Haiku
            self.model_id = "anthropic.claude-3-haiku-20240307-v1:0"
            self.max_tokens = 2048
            logger.info("기본 모드 활성화: Claude 3 Haiku 모델 사용")
        
        # 폴백 모델 - 항상 가장 가벼운 모델로 설정
        self.fallback_model_id = "anthropic.claude-instant-v1"
        
        # 재시도 설정
        self.max_retries = 2
        self.retry_base_delay = 0.5
        
        logger.info(f"BedrockClient, 리전: {self.aws_region}, 초기화 완료 - 모델: {self.model_id}")
    
    def _create_bedrock_client(self, aws_region: str):
        """
        Bedrock 클라이언트 생성
        
        Parameters:
        - aws_region: AWS 리전
        
        Returns:
        - Bedrock 클라이언트
        """
        try:
            # 단순한 클라이언트 생성
            logger.info(f"bedrock-runtime 서비스 클라이언트 생성 시도: 리전={aws_region}")
            bedrock_client = boto3.client('bedrock-runtime', region_name=aws_region)
            logger.info("bedrock-runtime 클라이언트 생성 성공")
            return bedrock_client
        except Exception as e:
            logger.error(f"Bedrock 클라이언트 생성 실패: {str(e)}")
            return None
    
    def _is_bedrock_available(self, model_id: str) -> bool:
        """
        Bedrock 모델 사용 가능 여부 확인
        
        Parameters:
        - model_id: 확인할 모델 ID
        
        Returns:
        - 사용 가능 여부
        """
        if self.bedrock_runtime is None:
            logger.warning("Bedrock 클라이언트가 없어 모델 가용성 확인 불가")
            return False
            
        try:
            # 클라이언트 및 모델 사용 가능성 테스트 - 간단한 요청으로 확인
            self.bedrock_runtime.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]})
            )
            return True
        except Exception as e:
            logger.warning(f"Bedrock 모델 {model_id} 사용 불가: {str(e)}")
            return False
    
    def _exponential_backoff(self, retry_attempt: int) -> float:
        """
        지수 백오프 지연 시간 계산
        
        Parameters:
        - retry_attempt: 현재 재시도 횟수
        
        Returns:
        - 지연 시간(초)
        """
        # 지수 백오프와 약간의 무작위성 추가 (지터)
        return self.retry_base_delay * (2 ** retry_attempt) + random.uniform(0, 0.5)
    
    def generate_response(self, system_prompt: str, 
                         conversation_history: List[Dict[str, str]],
                         max_tokens: int = None) -> str:
        """
        LLM을 사용하여 응답 생성
        
        Parameters:
        - system_prompt: 시스템 프롬프트
        - conversation_history: 대화 기록
        - max_tokens: 최대 토큰 수 (기본값 사용 시 None)
        
        Returns:
        - LLM 응답
        """
        max_tokens = max_tokens or self.max_tokens
        
        # Bedrock 클라이언트가 없으면 기본 응답 반환
        if self.bedrock_runtime is None:
            logger.error("Bedrock 클라이언트가 초기화되지 않았습니다.")
            return "죄송합니다. 현재 서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."
        
        # 요청 내용 로그
        logger.info(f"응답 생성 요청 - 대화 길이: {len(conversation_history)}, 시스템 프롬프트 길이: {len(system_prompt)}")
        
        # 시스템 프롬프트에서 JSON 응답 포맷 요청이 있는지 확인
        is_json_response = "json" in system_prompt.lower() or "응답 json 포맷" in system_prompt.lower()
        
        # 기본 모델로 시도
        use_model_id = self.model_id
        retry_attempt = 0
        tried_fallback = False
        
        while retry_attempt <= self.max_retries:
            try:
                # Anthropic Claude 메시지 형식
                request_body = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max_tokens,
                    "system": system_prompt,
                    "messages": conversation_history,
                    "temperature": 0.5,  # 더 결정적인 응답을 위해 온도 낮춤
                    "top_p": 0.9,        # 상위 확률 단어만 선택
                    "top_k": 50          # 상위 50개 토큰으로 제한
                }
                
                # Bedrock 호출
                response = self.bedrock_runtime.invoke_model(
                    modelId=use_model_id,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(request_body)
                )
                
                # 응답 처리
                response_body = json.loads(response['body'].read().decode('utf-8'))
                llm_response = response_body.get('content', [{'text': '응답을 생성할 수 없습니다.'}])[0]['text']
                
                # JSON 응답 형식 처리 (필요시)
                if is_json_response and "\"sources\":" in llm_response:
                    try:
                        # 로깅 최소화 (응답 시간 단축)
                        logger.info("JSON 응답 포맷 처리")
                        
                        # JSON 응답 파싱 시도 - 이중 JSON 문제 해결
                        if llm_response.strip().startswith("{") and "\"answer\": \"{" in llm_response:
                            # 중첩된 JSON 감지 (JSON 안에 이스케이프된 JSON이 있는 경우)
                            try:
                                outer_json = json.loads(llm_response)
                                if "answer" in outer_json and isinstance(outer_json["answer"], str):
                                    # 내부 JSON 추출 시도
                                    inner_str = outer_json["answer"]
                                    if inner_str.strip().startswith("{") and inner_str.strip().endswith("}"):
                                        try:
                                            inner_json = json.loads(inner_str)
                                            if "answer" in inner_json and "sources" in inner_json:
                                                # 내부 JSON이 올바른 형식을 가지고 있으면 이를 사용
                                                llm_response = inner_str
                                                logger.info("중첩된 JSON 구조 감지 및 정상화 완료")
                                        except:
                                            # 내부 JSON 파싱 실패시 원본 사용
                                            pass
                            except:
                                # 외부 JSON 파싱 실패시 계속 진행
                                pass

                        # 정상적인 JSON 처리 진행
                        response_json = json.loads(llm_response)
                        
                        # sources 키 형식 변경
                        if "sources" in response_json and isinstance(response_json["sources"], list):
                            # 최소한의 로깅만 유지
                            for source in response_json["sources"]:
                                if "page" in source:
                                    # page 키를 contents로 변경
                                    contents = source.pop("page", None)
                                    if contents:
                                        source["contents"] = [contents]
                            
                            # 개선된 JSON 응답으로 변환
                            llm_response = json.dumps(response_json, ensure_ascii=False)
                        
                    except json.JSONDecodeError:
                        logger.warning("JSON 응답 포맷 처리 실패: 유효하지 않은 JSON 형식")
                    except Exception as json_error:
                        logger.warning(f"JSON 응답 포맷 처리 중 오류: {str(json_error)}")
                
                logger.info(f"LLM 응답 생성 완료 - 모델: {use_model_id}, 응답 길이: {len(llm_response)} 글자")
                return llm_response
                
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                error_msg = e.response.get('Error', {}).get('Message', str(e))
                
                logger.error(f"Bedrock 호출 오류 (시도 {retry_attempt+1}/{self.max_retries+1}) - 코드: {error_code}, 메시지: {error_msg}")
                
                # 사용량 제한이나 모델 사용 불가 오류일 경우
                if error_code in ('ThrottlingException', 'ServiceUnavailableException', 'ModelNotReadyException'):
                    # 폴백 모델로 전환 (아직 시도하지 않았을 경우)
                    if not tried_fallback and use_model_id != self.fallback_model_id:
                        use_model_id = self.fallback_model_id
                        logger.info(f"폴백 모델로 전환: {use_model_id}")
                        tried_fallback = True
                        continue
                    
                    # 지수 백오프로 재시도
                    wait_time = self._exponential_backoff(retry_attempt)
                    logger.info(f"{wait_time:.2f}초 후 재시도")
                    time.sleep(wait_time)
                    retry_attempt += 1
                    continue
                else:
                    # 다른 오류이지만 폴백 모델로 시도하지 않았다면 전환
                    if not tried_fallback and use_model_id != self.fallback_model_id:
                        use_model_id = self.fallback_model_id
                        logger.info(f"오류 발생으로 폴백 모델로 전환: {use_model_id}")
                        tried_fallback = True
                        continue
                    # 폴백모델도 실패했다면 종료
                    raise BedrockClientError(f"Bedrock API 오류: {error_msg}")
                    
            except ConnectionError as e:
                logger.error(f"Bedrock 연결 오류 (시도 {retry_attempt+1}/{self.max_retries+1}): {str(e)}")
                
                # 폴백 모델로 전환 (아직 시도하지 않았을 경우)
                if not tried_fallback and use_model_id != self.fallback_model_id:
                    use_model_id = self.fallback_model_id
                    logger.info(f"연결 오류로 폴백 모델로 전환: {use_model_id}")
                    tried_fallback = True
                    continue
                
                wait_time = self._exponential_backoff(retry_attempt)
                logger.info(f"{wait_time:.2f}초 후 재시도")
                time.sleep(wait_time)
                retry_attempt += 1
                
            except Exception as e:
                logger.error(f"LLM 응답 생성 중 오류 발생: {str(e)}")
                
                # 폴백 모델로 전환 (아직 시도하지 않았을 경우)
                if not tried_fallback and use_model_id != self.fallback_model_id:
                    use_model_id = self.fallback_model_id
                    logger.info(f"일반 오류로 폴백 모델로 전환: {use_model_id}")
                    tried_fallback = True
                    continue
                
                # 재시도
                if retry_attempt < self.max_retries:
                    wait_time = self._exponential_backoff(retry_attempt)
                    logger.info(f"{wait_time:.2f}초 후 재시도")
                    time.sleep(wait_time)
                    retry_attempt += 1
                else:
                    break        
                

        # 모든 재시도 실패 시 폴백 응답
        logger.error("최대 재시도 횟수를 초과하여 기본 응답 반환")
        if len(conversation_history) > 0 and 'content' in conversation_history[-1]:
            last_message = conversation_history[-1]['content']
            return f"죄송합니다. 현재 응답을 생성할 수 없습니다. 질문을 다시 작성해 주시거나 나중에 다시 시도해 주세요."
        else:
            return "죄송합니다. 서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요." 
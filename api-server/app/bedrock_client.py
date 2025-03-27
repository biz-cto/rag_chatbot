import json
import logging
import boto3
import botocore.config
import time
import random
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
        # 기본 모델 - 더 빠른 응답을 위해 Claude Instant 사용
        self.model_id = "anthropic.claude-instant-v1"
        # 폴백 모델 없음 (이미 가장 빠른 모델 사용)
        self.fallback_model_id = self.model_id
        # 빠른 응답을 위해 토큰 수 축소
        self.max_tokens = 1024
        
        # 재시도 설정
        self.max_retries = 2
        self.retry_base_delay = 0.5
        
        logger.info(f"BedrockClient, 리전: {self.aws_region}, 초기화 완료 - 기본 모델: {self.model_id} (빠른 응답 모드)")
    
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
        
        # 기본 모델로 시도
        use_model_id = self.model_id
        retry_attempt = 0
        
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
                
                logger.info(f"LLM 응답 생성 완료 - 모델: {use_model_id}, 응답 길이: {len(llm_response)} 글자")
                return llm_response
                
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                error_msg = e.response.get('Error', {}).get('Message', str(e))
                
                logger.error(f"Bedrock 호출 오류 (시도 {retry_attempt+1}/{self.max_retries+1}) - 코드: {error_code}, 메시지: {error_msg}")
                
                # 사용량 제한이나 모델 사용 불가 오류일 경우
                if error_code in ('ThrottlingException', 'ServiceUnavailableException', 'ModelNotReadyException'):
                    # 지수 백오프로 재시도
                    wait_time = self._exponential_backoff(retry_attempt)
                    logger.info(f"{wait_time:.2f}초 후 재시도")
                    time.sleep(wait_time)
                    retry_attempt += 1
                    continue
                else:
                    # 다른 오류는 바로 실패 처리
                    raise BedrockClientError(f"Bedrock API 오류: {error_msg}")
                    
            except ConnectionError as e:
                logger.error(f"Bedrock 연결 오류 (시도 {retry_attempt+1}/{self.max_retries+1}): {str(e)}")
                wait_time = self._exponential_backoff(retry_attempt)
                logger.info(f"{wait_time:.2f}초 후 재시도")
                time.sleep(wait_time)
                retry_attempt += 1
                
            except Exception as e:
                logger.error(f"LLM 응답 생성 중 오류 발생: {str(e)}")
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
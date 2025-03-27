import json
import logging
import boto3
import botocore.config
import time
import random
import os
from typing import List, Dict, Any, Optional, Tuple
from botocore.exceptions import ClientError, ConnectionError

# 로깅 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class EmbeddingServiceError(Exception):
    """임베딩 서비스 오류"""
    pass

class EmbeddingService:
    """
    텍스트 임베딩 서비스 클래스 - Amazon Bedrock을 사용
    """
    
    def __init__(self, aws_region: str):
        """
        EmbeddingService 초기화
        
        Parameters:
        - aws_region: AWS 리전
        """
        # Bedrock은 무조건 us-east-1 리전 사용
        self.aws_region = "us-east-1"
        self.bedrock_runtime = self._create_bedrock_client(self.aws_region)
        # 기본 임베딩 모델
        self.model_id = "amazon.titan-embed-text-v1"
        # 재시도 설정 - 빠른 응답 위해 줄임
        self.max_retries = 3
        self.retry_base_delay = 0.2
        
        # 환경 변수에서 배치 크기 설정 가져오기 - 더 큰 배치로 빠른 처리
        self.batch_size = int(os.environ.get("BATCH_SIZE", "20"))
        
        logger.info(f"EmbeddingService, 리전: {self.aws_region}, 초기화 완료 - 모델: {self.model_id}, 배치 크기: {self.batch_size} (빠른 응답 모드)")
        
        # 임베딩 디폴트 차원
        self.default_dimension = 1536
    
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
            
            # 폴백: 서비스 이름에 버전 포함해서 시도
            try:
                logger.info("대체 방법으로 bedrock 클라이언트 생성 시도")
                return boto3.client('bedrock', region_name=aws_region)
            except Exception as e2:
                logger.error(f"대체 bedrock 클라이언트 생성도 실패: {str(e2)}")
                return None
    
    def _exponential_backoff(self, retry_attempt: int) -> float:
        """
        지수 백오프 지연 시간 계산
        
        Parameters:
        - retry_attempt: 현재 재시도 횟수
        
        Returns:
        - 지연 시간(초)
        """
        # 지수 백오프와 약간의 무작위성 추가 (지터)
        return self.retry_base_delay * (2 ** retry_attempt) + random.uniform(0, 0.1)
    
    def embed_query(self, text: str) -> List[float]:
        """
        쿼리 텍스트의 임베딩 벡터 생성
        
        Parameters:
        - text: 임베딩할 텍스트
        
        Returns:
        - 임베딩 벡터
        """
        # 단일 텍스트 임베딩 생성
        if not text or not text.strip():
            logger.warning("임베딩하려는 쿼리 텍스트가 비어 있습니다.")
            return [0.0] * self.default_dimension
            
        # Bedrock 클라이언트가 없으면 기본 임베딩 반환
        if self.bedrock_runtime is None:
            logger.warning("Bedrock 클라이언트가 초기화되지 않아 기본 임베딩 반환")
            return [0.0] * self.default_dimension
            
        return self._get_embedding(text)
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        문서 텍스트 목록의 임베딩 벡터 생성
        
        Parameters:
        - texts: 임베딩할 텍스트 목록
        
        Returns:
        - 임베딩 벡터 목록
        """
        if not texts:
            logger.warning("임베딩할 문서 텍스트가 비어 있습니다.")
            return []
            
        # Bedrock 클라이언트가 없으면 기본 임베딩 반환
        if self.bedrock_runtime is None:
            logger.warning("Bedrock 클라이언트가 초기화되지 않아 기본 임베딩 반환")
            return [[0.0] * self.default_dimension for _ in range(len(texts))]
            
        embeddings = []
        # 배치 크기 환경 변수에서 가져오기
        batch_size = self.batch_size
        
        # 배치 처리
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            logger.info(f"문서 임베딩 배치 처리 중: {i+1}-{i+len(batch)}/{len(texts)}")
            
            # 배치의 각 항목에 대해 임베딩 생성
            batch_embeddings = []
            for text in batch:
                try:
                    embedding = self._get_embedding(text)
                    batch_embeddings.append(embedding)
                except Exception as e:
                    logger.error(f"배치 임베딩 중 오류 발생: {str(e)}")
                    # 오류 발생 시 기본 임베딩 사용
                    batch_embeddings.append([0.0] * self.default_dimension)
            
            embeddings.extend(batch_embeddings)
            
            # 배치 간 짧은 지연 (API 제한 방지)
            if i + batch_size < len(texts):
                time.sleep(0.5)
        
        return embeddings
    
    def _get_embedding(self, text: str) -> List[float]:
        """
        Amazon Bedrock API를 사용하여 텍스트 임베딩 생성
        
        Parameters:
        - text: 임베딩할 텍스트
        
        Returns:
        - 임베딩 벡터
        """
        # 텍스트 정리 및 준비
        cleaned_text = text.replace('\n', ' ').strip()
        if not cleaned_text:
            logger.warning("임베딩을 위한 빈 텍스트가 제공되었습니다.")
            return [0.0] * self.default_dimension
        
        # 텍스트 길이 제한 (8K tokens 제한 고려)
        if len(cleaned_text) > 8000:
            logger.warning(f"텍스트가 너무 깁니다. 길이 제한으로 자릅니다: {len(cleaned_text)} -> 8000")
            cleaned_text = cleaned_text[:8000]
        
        # 재시도 로직
        retry_attempt = 0
        
        while retry_attempt <= self.max_retries:
            try:
                # Bedrock 요청 바디 생성
                request_body = json.dumps({
                    "inputText": cleaned_text
                })
                
                # Bedrock 클라이언트가 없으면 기본 임베딩 반환 (중간 검사)
                if self.bedrock_runtime is None:
                    logger.warning("Bedrock 클라이언트가 없어 임베딩 생성 불가")
                    return [0.0] * self.default_dimension
                
                # Bedrock 호출
                response = self.bedrock_runtime.invoke_model(
                    modelId=self.model_id,
                    contentType="application/json",
                    accept="application/json",
                    body=request_body
                )
                
                # 응답 처리
                response_body = json.loads(response['body'].read())
                embedding = response_body.get('embedding')
                
                if not embedding:
                    raise EmbeddingServiceError("임베딩이 응답에 없습니다.")
                
                return embedding
                
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                error_msg = e.response.get('Error', {}).get('Message', str(e))
                
                logger.error(f"Bedrock 임베딩 호출 오류 (시도 {retry_attempt+1}/{self.max_retries+1}): {error_code} - {error_msg}")
                
                # 사용량 제한이나 서비스 불가 오류
                if error_code in ('ThrottlingException', 'ServiceUnavailableException', 'ModelNotReadyException'):
                    wait_time = self._exponential_backoff(retry_attempt)
                    logger.info(f"{wait_time:.2f}초 후 재시도")
                    time.sleep(wait_time)
                    retry_attempt += 1
                    continue
                else:
                    # 다른 오류는 바로 실패 처리
                    logger.error(f"치명적인 Bedrock API 오류: {error_msg}")
                    break
                    
            except ConnectionError as e:
                logger.error(f"Bedrock 임베딩 연결 오류 (시도 {retry_attempt+1}/{self.max_retries+1}): {str(e)}")
                wait_time = self._exponential_backoff(retry_attempt)
                logger.info(f"{wait_time:.2f}초 후 재시도")
                time.sleep(wait_time)
                retry_attempt += 1
                
            except Exception as e:
                logger.error(f"임베딩 생성 중 예상치 못한 오류: {str(e)}")
                if retry_attempt < self.max_retries:
                    wait_time = self._exponential_backoff(retry_attempt)
                    logger.info(f"{wait_time:.2f}초 후 재시도")
                    time.sleep(wait_time)
                    retry_attempt += 1
                else:
                    break
        
        # 모든 재시도 실패 시 기본 임베딩 반환
        logger.error(f"최대 재시도 횟수를 초과하여 기본 임베딩 반환")
        return [0.0] * self.default_dimension 

    def get_embeddings(self, texts: List[str]) -> Tuple[List[List[float]], Dict[str, int]]:
        """
        텍스트 목록에 대한 임베딩 계산
        
        Parameters:
        - texts: 임베딩할 텍스트 목록
        
        Returns:
        - 임베딩 벡터 목록, 토큰 사용량
        """
        if not texts:
            logger.warning("임베딩할 텍스트가 제공되지 않았습니다.")
            return [], {"input_tokens": 0, "model_id": ""}
        
        embeddings = []
        token_usage = {"input_tokens": 0, "model_id": self.model_id}
        
        # 배치 처리 크기 조정
        try:
            batch_size = int(os.environ.get("BATCH_SIZE", "10"))
        except ValueError:
            batch_size = 10
            
        # 토큰 추정
        total_tokens = sum(len(text.split()) for text in texts) * 1.3  # 단어당 약 1.3개 토큰으로 추정
        token_usage["input_tokens"] = int(total_tokens)
        
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i+batch_size]
            batch_embeddings = self._get_embeddings_batch(batch_texts)
            
            if batch_embeddings:
                embeddings.extend(batch_embeddings)
            else:
                # 실패한 텍스트에 대해 0으로 채워진 임베딩 생성 (차원 동일하게 유지 필요)
                dimension = 1536  # Titan 임베딩 기본 차원
                for _ in range(len(batch_texts)):
                    embeddings.append([0.0] * dimension)
        
        logger.info(f"{len(texts)}개 텍스트에 대한 임베딩 계산 완료, 토큰: {token_usage}")
        return embeddings, token_usage

    def _get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        텍스트 목록에 대한 임베딩 계산
        
        Parameters:
        - texts: 임베딩할 텍스트 목록
        
        Returns:
        - 임베딩 벡터 목록
        """
        if not texts:
            logger.warning("임베딩할 텍스트가 제공되지 않았습니다.")
            return []
        
        embeddings = []
        
        # 배치 처리
        for i in range(0, len(texts), self.batch_size):
            batch_texts = texts[i:i+self.batch_size]
            logger.info(f"문서 임베딩 배치 처리 중: {i+1}-{i+len(batch_texts)}/{len(texts)}")
            
            # 배치의 각 항목에 대해 임베딩 생성
            batch_embeddings = []
            for text in batch_texts:
                try:
                    embedding = self._get_embedding(text)
                    batch_embeddings.append(embedding)
                except Exception as e:
                    logger.error(f"배치 임베딩 중 오류 발생: {str(e)}")
                    # 오류 발생 시 기본 임베딩 사용
                    batch_embeddings.append([0.0] * self.default_dimension)
            
            embeddings.extend(batch_embeddings)
            
            # 배치 간 짧은 지연 (API 제한 방지)
            if i + self.batch_size < len(texts):
                time.sleep(0.5)
        
        return embeddings 
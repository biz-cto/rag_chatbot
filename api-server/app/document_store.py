import os
import json
import logging
import boto3
import botocore.config
import tempfile
from typing import List, Dict, Any, Optional
import PyPDF2
from pathlib import Path
import time
import threading
from botocore.exceptions import ClientError

# 로깅 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class DocumentStore:
    """
    문서 저장소 클래스 - S3 버킷에서 PDF 문서를 로드하고 관리
    """
    
    def __init__(self, s3_bucket_name: str, aws_region: str):
        """
        DocumentStore 초기화
        
        Parameters:
        - s3_bucket_name: PDF 문서가 저장된 S3 버킷 이름
        - aws_region: AWS 리전
        """
        self.s3_bucket_name = s3_bucket_name
        self.aws_region = aws_region
        self.s3_client = self._create_s3_client(aws_region)
        self.documents: List[Dict[str, Any]] = []
        self.embeddings: List[List[float]] = []
        
        # 스레드 안전성을 위한 락
        self._lock = threading.RLock()
        
        # 캐시 디렉토리
        self.cache_dir = Path("/tmp/document_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 초기 로딩
        self._load_documents()
        
        logger.info(f"DocumentStore 초기화 완료 - 문서 {len(self.documents)}개 로드됨")
    
    def _create_s3_client(self, aws_region: str):
        """
        S3 클라이언트 생성
        
        Parameters:
        - aws_region: AWS 리전
        
        Returns:
        - boto3 S3 클라이언트
        """
        try:
            # 재시도 구성을 통한 S3 클라이언트 생성
            config = botocore.config.Config(
                retries={
                    'max_attempts': 10,
                    'mode': 'adaptive'
                },
                connect_timeout=5,
                read_timeout=60
            )
            return boto3.client('s3', region_name=aws_region, config=config)
        except Exception as e:
            logger.error(f"S3 클라이언트 생성 실패: {str(e)}")
            # 기본 클라이언트로 폴백
            return boto3.client('s3', region_name=aws_region)
    
    def _check_bucket_exists(self) -> bool:
        """
        S3 버킷 존재 여부 확인
        
        Returns:
        - 버킷 존재 여부
        """
        try:
            self.s3_client.head_bucket(Bucket=self.s3_bucket_name)
            return True
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == '404':
                logger.error(f"S3 버킷 {self.s3_bucket_name}이 존재하지 않습니다.")
            elif error_code == '403':
                logger.error(f"S3 버킷 {self.s3_bucket_name}에 접근 권한이 없습니다.")
            else:
                logger.error(f"S3 버킷 접근 오류: {str(e)}")
            return False
    
    def _load_documents(self) -> None:
        """S3 버킷에서 PDF 문서 로드"""
        try:
            # 버킷 존재 여부 확인
            if not self._check_bucket_exists():
                logger.warning(f"S3 버킷 {self.s3_bucket_name}에 접근할 수 없어 문서를 로드하지 못했습니다.")
                return
                
            # S3 버킷의 객체 목록 가져오기
            response = self.s3_client.list_objects_v2(Bucket=self.s3_bucket_name)
            
            if 'Contents' not in response:
                logger.warning(f"S3 버킷 {self.s3_bucket_name}에 문서가 없습니다.")
                return
            
            # PDF 문서만 필터링
            pdf_objects = [obj for obj in response['Contents'] 
                          if obj['Key'].lower().endswith('.pdf')]
            
            logger.info(f"S3 버킷에서 {len(pdf_objects)}개의 PDF 문서 발견")
            
            # 각 PDF 처리
            for obj in pdf_objects:
                pdf_key = obj['Key']
                try:
                    self._process_pdf(pdf_key)
                except Exception as e:
                    logger.error(f"PDF 처리 실패 ({pdf_key}): {str(e)}")
                
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchBucket':
                logger.error(f"S3 버킷 {self.s3_bucket_name}이 존재하지 않습니다.")
            else:
                logger.error(f"S3 버킷 접근 중 오류 발생: {str(e)}")
        except Exception as e:
            logger.error(f"문서 로드 중 오류 발생: {str(e)}")
    
    def _process_pdf(self, pdf_key: str) -> None:
        """
        S3에서 PDF 다운로드하고 처리하기
        
        Parameters:
        - pdf_key: S3 버킷 내 PDF 객체 키
        """
        try:
            # 캐시된 파일 경로
            cache_path = self.cache_dir / pdf_key.replace('/', '_')
            
            # 로컬에 PDF 다운로드
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                try:
                    self.s3_client.download_file(self.s3_bucket_name, pdf_key, temp_file.name)
                except ClientError as e:
                    logger.error(f"PDF 다운로드 실패 ({pdf_key}): {str(e)}")
                    return
                
                # PDF 파일 열기
                try:
                    with open(temp_file.name, 'rb') as pdf_file:
                        # PDF 읽기
                        try:
                            pdf_reader = PyPDF2.PdfReader(pdf_file)
                            num_pages = len(pdf_reader.pages)
                            
                            # 각 페이지를 청크로 처리
                            with self._lock:
                                for page_num in range(num_pages):
                                    try:
                                        page = pdf_reader.pages[page_num]
                                        text = page.extract_text()
                                        
                                        if text and text.strip():
                                            # 청크 추가
                                            self.documents.append({
                                                'content': text,
                                                'source': f"{pdf_key} (페이지 {page_num + 1})",
                                                'page': page_num + 1,
                                                'file': pdf_key
                                            })
                                    except Exception as e:
                                        logger.error(f"페이지 추출 실패 ({pdf_key}, 페이지 {page_num+1}): {str(e)}")
                        except Exception as e:
                            logger.error(f"PDF 파싱 실패 ({pdf_key}): {str(e)}")
                except Exception as e:
                    logger.error(f"PDF 파일 읽기 실패 ({pdf_key}): {str(e)}")
            
            # 임시 파일 삭제
            try:
                os.unlink(temp_file.name)
            except Exception as e:
                logger.warning(f"임시 파일 삭제 실패 ({temp_file.name}): {str(e)}")
            
        except Exception as e:
            logger.error(f"PDF 처리 중 오류 발생 {pdf_key}: {str(e)}")
    
    def get_documents(self) -> List[Dict[str, Any]]:
        """저장된 모든 문서 반환"""
        with self._lock:
            return self.documents.copy()
    
    def store_embeddings(self, embeddings: List[List[float]]) -> None:
        """
        문서 임베딩 저장
        
        Parameters:
        - embeddings: 임베딩 벡터 목록
        """
        with self._lock:
            if len(embeddings) != len(self.documents):
                logger.warning(f"임베딩 개수가 문서 개수와 일치하지 않습니다: {len(embeddings)} vs {len(self.documents)}")
                # 작은 크기에 맞춰 자름
                min_len = min(len(embeddings), len(self.documents))
                embeddings = embeddings[:min_len]
                self.documents = self.documents[:min_len]
                
            self.embeddings = embeddings
            logger.info(f"{len(embeddings)}개의 문서 임베딩이 저장되었습니다.")
        
    def search_similar(self, query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        쿼리 임베딩과 유사한 문서 검색 (라이브러리 의존성 없이 구현)
        
        Parameters:
        - query_embedding: 쿼리 임베딩
        - top_k: 반환할 최대 문서 수
        
        Returns:
        - 유사한 문서 목록
        """
        with self._lock:
            if not self.embeddings or not self.documents:
                logger.warning("문서나 임베딩이 없어 검색할 수 없습니다.")
                return []
                
            # 검색 유효성 검사
            if not query_embedding:
                logger.warning("쿼리 임베딩이 비어 있습니다.")
                return []
                
            # 코사인 유사도 계산
            try:
                similarities = []
                for doc_embedding in self.embeddings:
                    similarity = self._cosine_similarity(query_embedding, doc_embedding)
                    similarities.append(similarity)
                    
                # 상위 K개 가져오기
                if not similarities:
                    return []
                    
                # 최대 문서 수 조정
                top_k = min(top_k, len(similarities))
                # 유사도 기준으로 정렬된 인덱스 가져오기
                top_indices = sorted(range(len(similarities)), key=lambda i: similarities[i], reverse=True)[:top_k]
                
                return [self.documents[i] for i in top_indices]
            except Exception as e:
                logger.error(f"유사 문서 검색 중 오류 발생: {str(e)}")
                return []
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """
        두 벡터 간의 코사인 유사도 계산
        
        Parameters:
        - vec1: 첫 번째 벡터
        - vec2: 두 번째 벡터
        
        Returns:
        - 코사인 유사도 (0~1)
        """
        try:
            # 벡터 길이 검사
            if len(vec1) != len(vec2):
                logger.warning(f"벡터 길이가 일치하지 않습니다: {len(vec1)} vs {len(vec2)}")
                # 짧은 쪽 길이에 맞추기
                min_len = min(len(vec1), len(vec2))
                vec1 = vec1[:min_len]
                vec2 = vec2[:min_len]
                
            dot_product = sum(a * b for a, b in zip(vec1, vec2))
            magnitude1 = sum(a * a for a in vec1) ** 0.5
            magnitude2 = sum(b * b for b in vec2) ** 0.5
            
            if magnitude1 * magnitude2 == 0:
                return 0
                
            return dot_product / (magnitude1 * magnitude2)
        except Exception as e:
            logger.error(f"코사인 유사도 계산 중 오류 발생: {str(e)}")
            return 0.0 
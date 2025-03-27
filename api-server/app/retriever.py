import logging
import time
from typing import List, Dict, Any, Optional
from .document_store import DocumentStore
from .embeddings import EmbeddingService

# 로깅 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class RetrieverError(Exception):
    """문서 검색기 오류"""
    pass

class Retriever:
    """
    문서 검색기 클래스 - 쿼리와 관련된 문서를 검색
    """
    
    def __init__(self, document_store: DocumentStore, embedding_service: EmbeddingService):
        """
        Retriever 초기화
        
        Parameters:
        - document_store: 문서 저장소 인스턴스
        - embedding_service: 임베딩 서비스 인스턴스
        """
        self.document_store = document_store
        self.embedding_service = embedding_service
        self.is_embedding_initialized = False
        
        # 초기 문서 로드
        self.documents = document_store.get_documents()
        
        # 유효성 확인
        if not self.documents:
            logger.warning("문서가 로드되지 않았습니다. 검색 기능이 제한됩니다.")
        else:
            # 임베딩 초기화 - 지연 초기화 사용
            try:
                self._initialize_embeddings()
            except Exception as e:
                logger.error(f"초기 임베딩 초기화 실패: {str(e)}")
        
        logger.info(f"Retriever 초기화 완료 - 임베딩 초기화 상태: {self.is_embedding_initialized}")
    
    def _initialize_embeddings(self) -> None:
        """문서 임베딩 초기화"""
        if not self.documents:
            logger.warning("임베딩할 문서가 없습니다.")
            return
            
        try:
            # 문서 내용 추출
            contents = [doc['content'] for doc in self.documents]
            
            # 크기 경고
            if len(contents) > 100:
                logger.warning(f"많은 수의 문서({len(contents)}개)에 대한 임베딩을 초기화합니다. 시간이 오래 걸릴 수 있습니다.")
            
            # 임베딩 생성
            logger.info(f"{len(contents)}개 문서의 임베딩 생성 중...")
            start_time = time.time()
            
            # 임베딩 생성
            embeddings = self.embedding_service.embed_documents(contents)
            
            # 임베딩 크기 확인
            if not embeddings:
                logger.error("임베딩 생성 실패: 빈 임베딩 리스트 반환됨")
                return
                
            if len(embeddings) != len(contents):
                logger.warning(f"생성된 임베딩 수가 문서 수와 일치하지 않습니다: {len(embeddings)} vs {len(contents)}")
            
            # 문서 저장소에 임베딩 저장
            self.document_store.store_embeddings(embeddings)
            
            elapsed_time = time.time() - start_time
            logger.info(f"임베딩 초기화 완료: {len(embeddings)}개 문서, {elapsed_time:.2f}초 소요")
            
            self.is_embedding_initialized = True
            
        except Exception as e:
            logger.error(f"문서 임베딩 초기화 중 오류 발생: {str(e)}")
            self.is_embedding_initialized = False
            raise RetrieverError(f"임베딩 초기화 실패: {str(e)}")
    
    def retrieve(self, query: str, top_k: int = 3, retry_init: bool = True) -> List[Dict[str, Any]]:
        """
        쿼리와 관련된 문서 검색
        
        Parameters:
        - query: 검색 쿼리
        - top_k: 반환할 최대 문서 수 (빠른 응답을 위해 3개로 줄임)
        - retry_init: 실패 시 임베딩 재시도 여부
        
        Returns:
        - 관련 문서 목록
        """
        # 검색 요청 로그
        logger.info(f"쿼리 검색 요청: '{query[:30]}...' (길이: {len(query)})")
        
        # 빠른 응답을 위해 비어있는 검색 쿼리는 바로 빈 목록 반환
        if not query or not query.strip():
            logger.warning("빈 쿼리로 검색 요청이 들어왔습니다.")
            return []
            
        # 임베딩 초기화 체크
        if not self.is_embedding_initialized:
            if retry_init:
                logger.info("임베딩이 초기화되지 않아 초기화를 시도합니다.")
                try:
                    self._initialize_embeddings()
                except Exception as e:
                    logger.error(f"임베딩 재초기화 실패: {str(e)}")
            
            # 여전히 임베딩이 초기화되지 않은 경우
            if not self.is_embedding_initialized:
                logger.warning("임베딩이 초기화되지 않아 랜덤 문서를 반환합니다.")
                # 랜덤 문서 반환 (폴백)
                import random
                docs = self.document_store.get_documents()
                if docs:
                    sample_size = min(top_k, len(docs))
                    return random.sample(docs, sample_size)
                return []
        
        try:
            start_time = time.time()
            
            # 쿼리 임베딩 생성
            query_embedding = self.embedding_service.embed_query(query)
            
            # 유사한 문서 검색
            similar_docs = self.document_store.search_similar(
                query_embedding=query_embedding,
                top_k=top_k
            )
            
            elapsed_time = time.time() - start_time
            logger.info(f"{len(similar_docs)}개의 관련 문서 검색 완료 ({elapsed_time:.2f}초)")
            
            return similar_docs
            
        except Exception as e:
            logger.error(f"문서 검색 중 오류 발생: {str(e)}")
            
            # 문서 저장소에서 직접 문서 반환 (폴백)
            try:
                docs = self.document_store.get_documents()
                if docs:
                    import random
                    sample_size = min(top_k, len(docs))
                    logger.info(f"오류로 인해 랜덤 문서 {sample_size}개 반환")
                    return random.sample(docs, sample_size)
            except Exception as fallback_error:
                logger.error(f"폴백 검색 중 오류 발생: {str(fallback_error)}")
                
            return [] 
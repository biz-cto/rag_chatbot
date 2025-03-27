import os
import logging
from typing import Optional

from langchain.chains import ConversationalRetrievalChain
from langchain.memory import ConversationBufferMemory
from langchain_aws import BedrockEmbeddings
from langchain_aws import ChatBedrock
from langchain_community.vectorstores import FAISS

from app.utils.s3_utils import download_and_process_all_pdfs
from app.utils.logger_config import setup_logger

# RAG 서비스용 로거 설정
logger = setup_logger("app.services.rag", "logs/rag.log", logging.INFO)

# 싱글톤 인스턴스
_rag_service_instance = None

class RagService:
    def __init__(self):
        self.qa_chain = None
        # 경고는 있지만 현재 버전에서는 여전히 작동함
        self.conversation_memory = ConversationBufferMemory(
            memory_key="chat_history", 
            return_messages=True,
            output_key="answer"
        )
        self.initialize_rag_system()
    
    def initialize_rag_system(self):
        """S3 버킷 내 모든 PDF를 다운로드하고 RAG 시스템을 초기화합니다."""
        try:
            logger.info("RAG 시스템 초기화 시작")
            
            # AWS 자격 증명 확인
            aws_access_key = os.environ.get("AWS_ACCESS_KEY")
            aws_secret_key = os.environ.get("AWS_SECRET_KEY")
            region = os.environ.get("AWS_REGION", "ap-northeast-2")
            
            if not aws_access_key or not aws_secret_key:
                logger.error("AWS 자격 증명이 설정되지 않았습니다.")
                raise ValueError("AWS 자격 증명이 필요합니다. AWS_ACCESS_KEY와 AWS_SECRET_KEY 환경 변수를 설정하세요.")
            
            # S3 버킷 이름 가져오기
            bucket_name = os.environ.get("S3_BUCKET_NAME")
            if not bucket_name:
                logger.error("S3 버킷 이름이 설정되지 않았습니다.")
                raise ValueError("S3_BUCKET_NAME 환경 변수를 설정하세요.")
                
            logger.debug(f"사용할 S3 버킷: {bucket_name}, 리전: {region}")
            
            # S3 버킷 내 모든 PDF 처리
            logger.info("S3 버킷에서 PDF 파일 다운로드 및 처리 시작")
            chunks = download_and_process_all_pdfs(bucket_name)
            
            if not chunks:
                error_msg = f"버킷 '{bucket_name}'에서 처리할 PDF 파일이 없습니다."
                logger.error(error_msg)
                raise Exception(error_msg)
            
            logger.info(f"총 {len(chunks)}개의 청크를 생성했습니다.")
            
            # 임베딩 및 벡터 저장소 생성
            logger.info("임베딩 모델 초기화 중")
            try:
                embeddings = BedrockEmbeddings(
                    model_id="amazon.titan-embed-text-v1",
                    region_name=region,
                    aws_access_key_id=aws_access_key,
                    aws_secret_access_key=aws_secret_key
                )
                logger.info("임베딩 모델 초기화 완료")
            except Exception as e:
                logger.error(f"임베딩 모델 초기화 중 오류 발생: {str(e)}", exc_info=True)
                raise
            
            logger.info("벡터 저장소 생성 중")
            vector_store = FAISS.from_documents(chunks, embeddings)
            logger.info("벡터 저장소 생성 완료")
            
            # 대화형 검색 체인 생성
            logger.info("LLM 모델 초기화 중")
            try:
                llm = ChatBedrock(
                    model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
                    model_kwargs={
                        "temperature": 0,
                        "max_tokens": 4096
                    },
                    region_name=region,
                    aws_access_key_id=aws_access_key,
                    aws_secret_access_key=aws_secret_key
                )
                logger.info("LLM 모델 초기화 완료")
            except Exception as e:
                logger.error(f"LLM 모델 초기화 중 오류 발생: {str(e)}", exc_info=True)
                raise
            
            logger.info("대화형 검색 체인 생성 중")
            self.qa_chain = ConversationalRetrievalChain.from_llm(
                llm=llm,
                retriever=vector_store.as_retriever(search_kwargs={"k": 5}),
                memory=self.conversation_memory,
                return_source_documents=True
            )
            
            logger.info("RAG 시스템이 성공적으로 초기화되었습니다.")
            
        except Exception as e:
            logger.critical(f"RAG 시스템 초기화 중 오류 발생: {str(e)}", exc_info=True)
            raise

    
    def answer_question(self, question: str) -> str:
        """사용자 질문에 대한 응답을 생성합니다."""
        if self.qa_chain is None:
            error_msg = "RAG 시스템이 초기화되지 않았습니다."
            logger.error(error_msg)
            raise Exception(error_msg)
        
        logger.info(f"질문 처리 중: {question}")
        try:
            result = self.qa_chain.invoke({"question": question})
            
            # 소스 문서 정보 추가
            answer = result["answer"]
            source_documents = result.get("source_documents", [])
            
            if source_documents:
                answer += "\n\n참고 문서:"
                unique_sources = set()
                for doc in source_documents:
                    source = doc.metadata.get("source", "알 수 없는 소스")
                    if source not in unique_sources:
                        unique_sources.add(source)
                        answer += f"\n- {source}"
                        logger.debug(f"참고 문서: {source}")
            
            logger.info("응답 생성 완료")
            return answer
            
        except Exception as e:
            logger.error(f"질문 처리 중 오류 발생: {str(e)}", exc_info=True)
            raise Exception(f"질문 처리 중 오류 발생: {str(e)}")

    
    def reset_conversation(self):
        """대화 기록을 초기화합니다."""
        logger.info("대화 기록 초기화")
        self.conversation_memory.clear()

def get_rag_service() -> RagService:
    """RagService의 싱글톤 인스턴스를 반환합니다."""
    global _rag_service_instance
    if _rag_service_instance is None:
        logger.info("새로운 RAG 서비스 인스턴스 생성")
        _rag_service_instance = RagService()
    return _rag_service_instance

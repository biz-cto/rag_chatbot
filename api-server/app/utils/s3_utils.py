import os
import tempfile
import logging
import boto3
from typing import List, Dict

from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter

from app.utils.logger_config import setup_logger

# S3 유틸리티용 로거 설정
logger = setup_logger("app.utils.s3", "logs/s3.log", logging.DEBUG)

def get_s3_client():
    """S3 클라이언트를 생성합니다."""
    logger.debug("S3 클라이언트 생성 중")
    return boto3.client(
        's3',
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "your-aws-access-key"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "your-aws-secret-key"),
        region_name=os.environ.get("AWS_REGION", "ap-northeast-2")
    )

def list_all_pdfs_in_bucket(bucket_name: str) -> List[str]:
    """S3 버킷 내 모든 PDF 파일 목록을 반환합니다."""
    logger.info(f"버킷 '{bucket_name}'에서 PDF 파일 목록 검색 중")
    s3_client = get_s3_client()
    pdf_files = []
    
    # 페이지네이션을 위한 초기 설정
    paginator = s3_client.get_paginator('list_objects_v2')
    operation_parameters = {'Bucket': bucket_name}
    
    # 페이지별로 처리
    for page in paginator.paginate(**operation_parameters):
        if 'Contents' in page:
            for obj in page['Contents']:
                if obj['Key'].lower().endswith('.pdf'):
                    pdf_files.append(obj['Key'])
                    logger.debug(f"PDF 파일 발견: {obj['Key']}")
    
    logger.info(f"총 {len(pdf_files)}개의 PDF 파일을 찾았습니다.")
    return pdf_files

def download_and_process_all_pdfs(bucket_name: str) -> List[Dict]:
    """S3 버킷 내 모든 PDF 파일을 다운로드하고 처리합니다."""
    logger.info(f"버킷 '{bucket_name}'의 모든 PDF 처리 시작")
    s3_client = get_s3_client()
    all_chunks = []
    
    # 버킷 내 모든 PDF 파일 목록 가져오기
    pdf_files = list_all_pdfs_in_bucket(bucket_name)
    
    if not pdf_files:
        logger.warning(f"버킷 '{bucket_name}'에서 PDF 파일을 찾을 수 없습니다.")
        return all_chunks
    
    # 각 PDF 파일 처리
    for pdf_key in pdf_files:
        try:
            logger.info(f"처리 중: {pdf_key}")
            
            # S3에서 PDF 파일 가져오기
            response = s3_client.get_object(Bucket=bucket_name, Key=pdf_key)
            pdf_content = response['Body'].read()
            
            # 임시 파일로 저장
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
                temp_path = temp_file.name
                temp_file.write(pdf_content)
            
            # PDF 처리
            try:
                # PDF 로드
                loader = PyPDFLoader(temp_path)
                documents = loader.load()
                
                # 메타데이터에 파일 이름 추가
                for doc in documents:
                    doc.metadata['source'] = pdf_key
                
                # 텍스트 분할
                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=1000,
                    chunk_overlap=200,
                    separators=["\n\n", "\n", ".", " ", ""],
                    length_function=len
                )
                chunks = text_splitter.split_documents(documents)
                all_chunks.extend(chunks)
                
                logger.info(f"'{pdf_key}' 파일에서 {len(chunks)}개의 청크를 생성했습니다.")
            
            except Exception as e:
                logger.error(f"'{pdf_key}' 파일 처리 중 오류 발생: {str(e)}", exc_info=True)
            
            finally:
                # 임시 파일 삭제
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                    logger.debug(f"임시 파일 삭제: {temp_path}")
        
        except Exception as e:
            logger.error(f"'{pdf_key}' 파일 다운로드 중 오류 발생: {str(e)}", exc_info=True)
    
    logger.info(f"총 {len(all_chunks)}개의 청크를 생성했습니다.")
    return all_chunks

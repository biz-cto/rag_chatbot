import json
import logging
import os
from typing import Dict, Any

from app.services.rag_service import get_rag_service
from app.models.chat_models import ChatRequest

# 로깅 설정
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# RAG 서비스 인스턴스
rag_service = None

def init_service():
    """Lambda 함수 초기화 시 RAG 서비스를 초기화합니다."""
    global rag_service
    if rag_service is None:
        logger.info("RAG 서비스 초기화 중...")
        rag_service = get_rag_service()
        logger.info("RAG 서비스 초기화 완료")

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda 함수 핸들러
    
    Parameters:
        event: API Gateway로부터의 이벤트 데이터
        context: Lambda 컨텍스트 객체
        
    Returns:
        Lambda 응답 객체
    """
    # 서비스 초기화
    init_service()
    
    try:
        # API Gateway로부터 요청 본문 파싱
        if 'body' in event:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event
        
        # 경로 판별
        path = event.get('path', '')
        http_method = event.get('httpMethod', 'POST')
        
        # 채팅 요청 처리
        if path.endswith('/chat') and http_method == 'POST':
            question = body.get('question', '')
            if not question:
                return {
                    'statusCode': 400,
                    'body': json.dumps({
                        'error': '질문이 필요합니다.'
                    })
                }
            
            # 질문 처리
            response = rag_service.answer_question(question)
            
            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps(response)
            }
        
        # 채팅 기록 초기화 요청 처리
        elif path.endswith('/chat/reset') and http_method == 'POST':
            rag_service.reset_conversation()
            
            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'message': '대화 기록이 초기화되었습니다.'
                })
            }
        
        # 알 수 없는 경로
        else:
            return {
                'statusCode': 404,
                'body': json.dumps({
                    'error': '요청한 엔드포인트가 존재하지 않습니다.'
                })
            }
            
    except Exception as e:
        logger.error(f"요청 처리 중 오류 발생: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'요청 처리 중 오류 발생: {str(e)}'
            })
        } 
import json
import logging
import os
import traceback
from typing import Dict, Any

from app.services.rag_service import get_rag_service
from app.models.chat_models import ChatRequest

# 로깅 설정
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 환경 변수 검증
required_env_vars = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "S3_BUCKET_NAME"]
missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
if missing_vars:
    logger.error(f"필수 환경 변수가 설정되지 않았습니다: {', '.join(missing_vars)}")

# RAG 서비스 인스턴스
rag_service = None

def init_service():
    """Lambda 함수 초기화 시 RAG 서비스를 초기화합니다."""
    global rag_service
    try:
        if rag_service is None:
            logger.info("RAG 서비스 초기화 중...")
            rag_service = get_rag_service()
            logger.info("RAG 서비스 초기화 완료")
    except Exception as e:
        error_msg = f"RAG 서비스 초기화 실패: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        raise RuntimeError(error_msg)

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda 함수 핸들러
    
    Parameters:
        event: API Gateway로부터의 이벤트 데이터
        context: Lambda 컨텍스트 객체
        
    Returns:
        Lambda 응답 객체
    """
    # 요청 로깅
    request_id = context.aws_request_id if context else "unknown"
    logger.info(f"요청 ID: {request_id} - 이벤트: {json.dumps(event, ensure_ascii=False)[:1000]}")
    
    # 서비스 초기화
    try:
        init_service()
    except Exception as e:
        logger.error(f"서비스 초기화 실패: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': f'서비스 초기화 실패: {str(e)}'
            })
        }
    
    try:
        # API Gateway로부터 요청 본문 파싱
        if 'body' in event:
            try:
                body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
            except json.JSONDecodeError as e:
                logger.error(f"JSON 파싱 오류: {str(e)}")
                return {
                    'statusCode': 400,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    },
                    'body': json.dumps({
                        'error': '잘못된 JSON 형식입니다.'
                    })
                }
        else:
            body = event
        
        # 경로 판별
        path = event.get('path', '')
        http_method = event.get('httpMethod', 'POST')
        
        logger.info(f"처리 중: {http_method} {path}")
        
        # 채팅 요청 처리
        if path.endswith('/chat') and http_method == 'POST':
            question = body.get('question', '')
            if not question:
                return {
                    'statusCode': 400,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    },
                    'body': json.dumps({
                        'error': '질문이 필요합니다.'
                    })
                }
            
            # 질문 처리
            logger.info(f"질문 처리 중: {question[:100]}...")
            response = rag_service.answer_question(question)
            logger.info(f"응답 생성 완료: {len(json.dumps(response))} 바이트")
            
            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps(response, ensure_ascii=False)
            }
        
        # 채팅 기록 초기화 요청 처리
        elif path.endswith('/chat/reset') and http_method == 'POST':
            logger.info("대화 기록 초기화 요청")
            rag_service.reset_conversation()
            logger.info("대화 기록 초기화 완료")
            
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
            logger.warning(f"알 수 없는 엔드포인트: {http_method} {path}")
            return {
                'statusCode': 404,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': '요청한 엔드포인트가 존재하지 않습니다.'
                })
            }
            
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"요청 처리 중 오류 발생: {str(e)}")
        logger.error(error_trace)
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': f'요청 처리 중 오류 발생: {str(e)}'
            }, ensure_ascii=False)
        } 
import json
import logging
import os
import traceback
import time
from typing import Dict, Any, Optional

from app.chat_service import ChatService
from app.utils.cost_tracker import CostTracker

# 로깅 설정
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 비용 추적 인스턴스 - 간소화됨
_cost_tracker = CostTracker()

# 환경 변수 검증 및 설정
def validate_environment():
    """환경 변수를 검증하고 기본값을 설정합니다."""
    # 필수 환경 변수
    env_vars = {
        "AWS_REGION": os.environ.get("CUSTOM_AWS_REGION", os.environ.get("AWS_REGION", "ap-northeast-2")),
        "S3_BUCKET_NAME": os.environ.get("S3_BUCKET_NAME", "garden-rag-01")
    }
    
    missing_vars = [key for key, value in env_vars.items() if not value]
    if missing_vars:
        warning_msg = f"일부 환경 변수가 설정되지 않았습니다: {', '.join(missing_vars)}. 기본값을 사용합니다."
        logger.warning(warning_msg)
    
    # 환경 변수 설정
    for key, value in env_vars.items():
        if value:
            os.environ[key] = value
    
    return env_vars

# 초기 환경 변수 검증
ENV = validate_environment()

# 챗봇 서비스 인스턴스 - 지연 초기화 패턴 적용
_chat_service = None

def get_chat_service():
    """ChatService 인스턴스를 가져옵니다. 없으면 생성합니다."""
    global _chat_service
    if _chat_service is None:
        try:
            logger.info("ChatService 초기화 중...")
            logger.info(f"사용 중인 리전: {ENV['AWS_REGION']}, S3 버킷: {ENV['S3_BUCKET_NAME']}")
            _chat_service = ChatService(
                s3_bucket_name=ENV["S3_BUCKET_NAME"],
                aws_region=ENV["AWS_REGION"]
            )
            logger.info("ChatService 초기화 완료")
        except Exception as e:
            error_msg = f"ChatService 초기화 실패: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            
            # 폴백 서비스 - 제한된 기능으로 응답
            logger.warning("제한된 기능의 폴백 서비스로 초기화합니다.")
            try:
                _chat_service = _create_fallback_service()
            except Exception as fallback_error:
                logger.error(f"폴백 서비스 초기화도 실패: {str(fallback_error)}")
                raise RuntimeError(error_msg)
    
    return _chat_service

def _create_fallback_service():
    """초기화 실패 시 사용할 제한된 기능의 서비스를 생성합니다."""
    # 간단한 폴백 서비스 구현
    class FallbackService:
        def __init__(self):
            self.conversations = {}
            logger.info("FallbackService 초기화 완료")
            
        def process_message(self, user_message, session_id):
            logger.info(f"FallbackService 메시지 처리 - 세션: {session_id}")
            # 대화 기록 관리
            if session_id not in self.conversations:
                self.conversations[session_id] = []
                
            self.conversations[session_id].append({
                "role": "user", 
                "content": user_message
            })
            
            # 시스템 응답
            response = "죄송합니다. 현재 서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."
            
            self.conversations[session_id].append({
                "role": "assistant",
                "content": response
            })
            
            return {
                "response": response,
                "sources": [],
                "error": "service_initialization_failed"
            }
            
        def reset_conversation(self, session_id):
            logger.info(f"FallbackService 대화 초기화 - 세션: {session_id}")
            if session_id in self.conversations:
                self.conversations[session_id] = []
                return True
            return False
    
    return FallbackService()

def error_response(message, headers, status_code=400):
    """에러 응답 생성"""
    return {
        'statusCode': status_code,
        'headers': headers,
        'body': json.dumps({
            'error': message
        })
    }

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda 핸들러 함수 (간소화됨)
    
    Parameters:
    - event: Lambda 이벤트 객체
    - context: Lambda 컨텍스트 객체
    
    Returns:
    - API Gateway 응답 객체
    """
    request_id = context.aws_request_id if context else "unknown"
    logger.info(f"요청 ID: {request_id}")
    
    # 간소화된 비용 추적 시작
    lambda_memory = int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "1024"))
    _cost_tracker.start(lambda_memory_mb=lambda_memory)
    
    # API Gateway 프록시 통합에서의 HTTP 메서드와 경로 추출
    http_method = event.get('httpMethod', '')
    path = event.get('path', '')
    
    # CORS 헤더 설정
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token,X-Requested-With'
    }
    
    # OPTIONS 요청 처리 (CORS 프리플라이트)
    if http_method == 'OPTIONS':
        _cost_tracker.stop()
        _cost_tracker.log_costs(request_id=request_id, request_type="options")
        
        return {
            'statusCode': 200,
            'headers': cors_headers,
            'body': json.dumps({'message': 'CORS enabled'})
        }
    
    try:
        # 서비스 초기화
        chat_service = get_chat_service()
        
        # POST 요청 처리
        if http_method == 'POST':
            # 요청 본문 파싱
            try:
                body = json.loads(event.get('body', '{}')) if event.get('body') else {}
            except json.JSONDecodeError as e:
                logger.error(f"JSON 파싱 오류: {str(e)}")
                _cost_tracker.stop()
                _cost_tracker.log_costs(request_id=request_id, request_type="json_error")
                return error_response('잘못된 JSON 형식입니다.', cors_headers)
            
            # 엔드포인트별 처리
            if path.endswith('/chat'):
                # 채팅 엔드포인트
                user_message = body.get('message', '')
                session_id = body.get('session_id', '')
                
                if not user_message:
                    _cost_tracker.stop()
                    _cost_tracker.log_costs(request_id=request_id, request_type="empty_message")
                    return error_response('메시지가 제공되지 않았습니다.', cors_headers)
                
                if not session_id:
                    # 세션 ID가 없는 경우 랜덤 생성
                    import uuid
                    session_id = str(uuid.uuid4())
                    logger.info(f"새 세션 ID 생성: {session_id}")
                
                # 채팅 응답 생성
                response = chat_service.process_message(user_message, session_id)
                
                # 응답에 session_id 추가
                if isinstance(response, dict):
                    if 'session_id' not in response:
                        response['session_id'] = session_id
                    
                # 비용 추적 종료
                _cost_tracker.stop()
                _cost_tracker.log_costs(request_id=request_id, request_type="chat")
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps(response)
                }
                
            elif path.endswith('/chat/reset'):
                # 대화 기록 초기화 엔드포인트
                session_id = body.get('session_id', '')
                
                if not session_id:
                    _cost_tracker.stop()
                    _cost_tracker.log_costs(request_id=request_id, request_type="reset_error")
                    return error_response('세션 ID가 제공되지 않았습니다.', cors_headers)
                
                # 대화 기록 초기화
                chat_service.reset_conversation(session_id)
                
                # 비용 추적 종료
                _cost_tracker.stop()
                _cost_tracker.log_costs(request_id=request_id, request_type="reset")
                
                return {
                    'statusCode': 200,
                    'headers': cors_headers,
                    'body': json.dumps({
                        'message': '대화 기록이 초기화되었습니다.',
                        'session_id': session_id
                    })
                }
            else:
                # 알 수 없는 엔드포인트
                _cost_tracker.stop()
                _cost_tracker.log_costs(request_id=request_id, request_type="unknown_path")
                return error_response(f'지원되지 않는 경로: {path}', cors_headers, 404)
        else:
            # 지원되지 않는 HTTP 메서드
            _cost_tracker.stop()
            _cost_tracker.log_costs(request_id=request_id, request_type="unknown_method")
            return error_response(f'지원되지 않는 HTTP 메서드: {http_method}', cors_headers, 405)
            
    except Exception as e:
        # 예외 처리
        logger.error(f"처리 중 오류 발생: {str(e)}")
        logger.error(traceback.format_exc())
        
        # 비용 추적 종료
        _cost_tracker.stop()
        _cost_tracker.log_costs(request_id=request_id, request_type="error")
        
        return {
            'statusCode': 500,
            'headers': cors_headers,
            'body': json.dumps({
                'error': '서버 내부 오류가 발생했습니다.',
                'message': str(e)
            })
        } 
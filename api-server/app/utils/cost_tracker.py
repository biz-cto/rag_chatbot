import time
import logging
import json
from typing import Dict, Any, Optional

# 로깅 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class CostTracker:
    """
    AWS 서비스 비용을 간소화하여 추적하는 클래스
    """
    
    def __init__(self):
        """CostTracker 초기화"""
        self.reset()
    
    def reset(self):
        """비용 추적 초기화"""
        self.start_time = None
        self.end_time = None
        self.lambda_memory_mb = 0
    
    def start(self, lambda_memory_mb: int = 1024):
        """
        비용 추적 시작
        
        Parameters:
        - lambda_memory_mb: Lambda 함수 메모리 크기 (MB)
        """
        self.reset()
        self.start_time = time.time()
        self.lambda_memory_mb = lambda_memory_mb
    
    def stop(self):
        """비용 추적 중단 및 실행 시간 계산"""
        if not self.start_time:
            return
        
        self.end_time = time.time()
    
    def add_bedrock_cost(self, model_id: str, input_tokens: int, output_tokens: int):
        """
        Bedrock 모델 사용 기록 (간소화됨)
        
        Parameters:
        - model_id: 사용한 Bedrock 모델 ID
        - input_tokens: 입력 토큰 수
        - output_tokens: 출력 토큰 수
        """
        logger.info(f"Bedrock 사용: 모델={model_id}, 입력 토큰={input_tokens}, 출력 토큰={output_tokens}")
    
    def add_s3_cost(self, get_requests: int = 0, data_size_kb: int = 0):
        """
        S3 사용 기록 (간소화됨)
        
        Parameters:
        - get_requests: S3 GET 요청 횟수
        - data_size_kb: 처리한 데이터 크기 (KB)
        """
        logger.info(f"S3 사용: GET 요청={get_requests}, 데이터 크기={data_size_kb}KB")
    
    def get_cost_summary(self) -> Dict[str, Any]:
        """
        사용량 요약 정보 반환 (간소화됨)
        
        Returns:
        - 간소화된 사용량 요약 정보
        """
        if self.start_time and not self.end_time:
            self.stop()
            
        duration_ms = 0
        if self.start_time and self.end_time:
            duration_ms = (self.end_time - self.start_time) * 1000
            
        return {
            "duration_ms": duration_ms,
            "memory_mb": self.lambda_memory_mb,
            "timestamp": time.time()
        }
    
    def log_costs(self, request_id: Optional[str] = None, request_type: str = "chat"):
        """
        간소화된 사용 정보를 CloudWatch에 로깅
        
        Parameters:
        - request_id: 요청 ID (없으면 생략)
        - request_type: 요청 유형 (chat, reset 등)
        """
        if self.start_time and not self.end_time:
            self.stop()
            
        summary = self.get_cost_summary()
        
        # 간소화된 로그 포맷팅
        log_message = {
            "request_id": request_id or "unknown",
            "request_type": request_type,
            "duration_ms": summary["duration_ms"],
            "memory_mb": summary["memory_mb"]
        }
        
        logger.info(f"실행 정보: {json.dumps(log_message)}")
        return log_message 
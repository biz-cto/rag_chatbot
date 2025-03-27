import time
import logging
import json
from typing import Dict, Any, Optional

# 로깅 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class CostTracker:
    """
    AWS 서비스 비용을 추적하고 계산하는 클래스
    """
    
    # AWS 서비스별 가격 정보 (미국 동부 리전 기준 - 2024년 3월 기준)
    # 가격은 변동될 수 있으므로 최신 AWS 요금 정보를 확인하세요
    PRICING = {
        # Lambda 기본 요금: 메모리 및 실행 시간 기준 (USD per GB-second)
        "lambda": {
            "gb_second": 0.0000166667,  # $0.0000166667 per GB-second
            "request": 0.0000002,       # $0.20 per 1M requests
        },
        # API Gateway 기본 요금: 요청당 (USD per request)
        "api_gateway": {
            "request": 0.0000035,       # $3.50 per 1M requests for REST API
        },
        # Bedrock 모델별 요금 (USD per 1K tokens)
        "bedrock": {
            # Claude 모델 입력/출력 토큰 가격
            "anthropic.claude-instant-v1": {
                "input": 0.00163,       # $1.63 per 1M input tokens
                "output": 0.00551,      # $5.51 per 1M output tokens
            },
            "anthropic.claude-3-haiku-20240307-v1:0": {
                "input": 0.00025,       # $0.25 per 1M input tokens
                "output": 0.00125,      # $1.25 per 1M output tokens
            },
            "anthropic.claude-3-sonnet-20240229-v1:0": {
                "input": 0.003,         # $3.00 per 1M input tokens
                "output": 0.015,        # $15.00 per 1M output tokens
            },
            # 임베딩 모델 가격
            "amazon.titan-embed-text-v1": {
                "input": 0.0002,        # $0.20 per 1M tokens
                "output": 0.0,          # 출력 토큰 없음
            },
        },
        # CloudWatch 로그 기본 요금: 저장 및 수집 (USD per GB)
        "cloudwatch": {
            "log_storage": 0.03,        # $0.03 per GB per month
            "log_ingestion": 0.50,      # $0.50 per GB
        },
        # S3 기본 요금: 저장용량 (USD per GB per month) 및 요청
        "s3": {
            "storage": 0.023,           # $0.023 per GB per month for Standard
            "get_request": 0.0000004,   # $0.0004 per 1000 GET requests
        },
    }
    
    def __init__(self):
        """CostTracker 초기화"""
        self.reset()
    
    def reset(self):
        """비용 추적 초기화"""
        self.start_time = None
        self.end_time = None
        self.lambda_memory_mb = 0
        self.costs = {
            "lambda": 0.0,
            "api_gateway": 0.0,
            "bedrock": 0.0, 
            "s3": 0.0,
            "cloudwatch": 0.0,
            "total": 0.0
        }
        self.usage = {
            "lambda_duration_ms": 0,
            "lambda_gb_seconds": 0.0,
            "bedrock_input_tokens": 0,
            "bedrock_output_tokens": 0,
            "bedrock_model": "",
            "s3_get_requests": 0
        }
    
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
        """비용 추적 중단 및 Lambda 실행 시간 계산"""
        if not self.start_time:
            return
        
        self.end_time = time.time()
        duration_ms = (self.end_time - self.start_time) * 1000  # 밀리초로 변환
        self.usage["lambda_duration_ms"] = duration_ms
        
        # Lambda GB-seconds 계산
        memory_gb = self.lambda_memory_mb / 1024  # GB로 변환
        duration_seconds = duration_ms / 1000     # 초로 변환
        gb_seconds = memory_gb * duration_seconds
        self.usage["lambda_gb_seconds"] = gb_seconds
        
        # Lambda 비용 계산
        lambda_compute_cost = gb_seconds * self.PRICING["lambda"]["gb_second"]
        lambda_request_cost = self.PRICING["lambda"]["request"]
        self.costs["lambda"] = lambda_compute_cost + lambda_request_cost
        
        # API Gateway 비용 계산
        self.costs["api_gateway"] = self.PRICING["api_gateway"]["request"]
        
        # CloudWatch 로그 비용 (추정)
        approx_log_size_kb = 2  # 평균 로그 크기 추정 (KB)
        log_gb = approx_log_size_kb / 1024 / 1024  # KB -> GB 변환
        self.costs["cloudwatch"] = (
            log_gb * self.PRICING["cloudwatch"]["log_ingestion"] +
            (log_gb * self.PRICING["cloudwatch"]["log_storage"] / 30)  # 일별 비용으로 변환
        )
        
        # 총 비용 계산
        self.costs["total"] = sum(self.costs.values())
    
    def add_bedrock_cost(self, model_id: str, input_tokens: int, output_tokens: int):
        """
        Bedrock 모델 사용 비용 추가
        
        Parameters:
        - model_id: 사용한 Bedrock 모델 ID
        - input_tokens: 입력 토큰 수
        - output_tokens: 출력 토큰 수
        """
        self.usage["bedrock_model"] = model_id
        self.usage["bedrock_input_tokens"] += input_tokens
        self.usage["bedrock_output_tokens"] += output_tokens
        
        if model_id in self.PRICING["bedrock"]:
            input_cost = (input_tokens / 1000) * self.PRICING["bedrock"][model_id]["input"]
            output_cost = (output_tokens / 1000) * self.PRICING["bedrock"][model_id]["output"]
            self.costs["bedrock"] += input_cost + output_cost
            self.costs["total"] = sum(self.costs.values())
        else:
            logger.warning(f"알 수 없는 Bedrock 모델: {model_id}, 비용 계산 생략")
    
    def add_s3_cost(self, get_requests: int = 0, data_size_kb: int = 0):
        """
        S3 사용 비용 추가
        
        Parameters:
        - get_requests: S3 GET 요청 횟수
        - data_size_kb: 처리한 데이터 크기 (KB)
        """
        self.usage["s3_get_requests"] += get_requests
        
        # S3 GET 요청 비용
        request_cost = get_requests * self.PRICING["s3"]["get_request"]
        
        # S3 데이터 전송 비용 (일일 사용량으로 추정)
        data_gb = data_size_kb / 1024 / 1024  # KB -> GB 변환
        storage_cost = (data_gb * self.PRICING["s3"]["storage"]) / 30  # 일별 비용으로 변환
        
        self.costs["s3"] += request_cost + storage_cost
        self.costs["total"] = sum(self.costs.values())
    
    def get_cost_summary(self) -> Dict[str, Any]:
        """
        비용 및 사용량 요약 정보 반환
        
        Returns:
        - 비용 및 사용량 요약 정보 딕셔너리
        """
        if self.start_time and not self.end_time:
            self.stop()  # 자동으로 종료 처리
            
        return {
            "costs": self.costs,
            "usage": self.usage,
            "duration_ms": self.usage["lambda_duration_ms"],
            "timestamp": time.time(),
        }
    
    def log_costs(self, request_id: Optional[str] = None, request_type: str = "chat"):
        """
        비용 정보를 CloudWatch에 로깅
        
        Parameters:
        - request_id: 요청 ID (없으면 생략)
        - request_type: 요청 유형 (chat, reset 등)
        """
        if self.start_time and not self.end_time:
            self.stop()  # 자동으로 종료 처리
            
        cost_info = self.get_cost_summary()
        
        # 로그 포맷팅
        log_message = {
            "cost_tracking": True,
            "request_id": request_id or "unknown",
            "request_type": request_type,
            "duration_ms": cost_info["duration_ms"],
            "costs_usd": {k: f"${v:.8f}" for k, v in cost_info["costs"].items()},
            "usage": cost_info["usage"]
        }
        
        logger.info(f"AWS 비용 정보: {json.dumps(log_message)}")
        return log_message 
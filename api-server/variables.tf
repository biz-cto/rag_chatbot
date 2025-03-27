variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "us-east-1"
}

variable "s3_bucket_name" {
  description = "PDF 파일이 저장된 S3 버킷 이름"
  type        = string
  default     = "garden-rag-01"
}

variable "lambda_memory_size" {
  description = "Lambda 함수에 할당할 메모리 크기(MB)"
  type        = number
  default     = 2048
}

variable "lambda_timeout" {
  description = "Lambda 함수 타임아웃(초)"
  type        = number
  default     = 600  # 10분으로 증가 (최대 15분까지 가능)
}

variable "environment" {
  description = "배포 환경 (prod, dev, stage 등)"
  type        = string
  default     = "prod"
}

variable "lambda_runtime" {
  description = "Lambda 함수 런타임 버전"
  type        = string
  default     = "python3.10"  # 보다 안정적인 Python 3.10 사용
}

variable "log_retention_days" {
  description = "CloudWatch 로그 그룹 보존 기간(일)"
  type        = number
  default     = 30  # 기본 30일 보존
}

variable "create_s3_bucket" {
  description = "필요한 경우 S3 버킷을 자동으로 생성할지 여부"
  type        = bool
  default     = false
}

variable "batch_size" {
  description = "임베딩 처리 배치 크기"
  type        = number
  default     = 10
} 
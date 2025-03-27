variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "ap-northeast-2"
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
  default     = 300
}

variable "environment" {
  description = "배포 환경 (prod, dev, stage 등)"
  type        = string
  default     = "prod"
} 
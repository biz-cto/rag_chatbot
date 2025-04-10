# 기본 provider는 Bedrock 서비스를 위한 us-east-1 리전 사용
provider "aws" {
  region = var.aws_region
  alias  = "bedrock_region"
}

# S3 버킷을 위한 ap-northeast-2 provider
provider "aws" {
  region = "ap-northeast-2"
  alias  = "s3_region"
}

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
  required_version = ">= 1.0"
}

# 로컬 변수 정의
locals {
  package_dir   = "${path.module}/.lambda_package"
  zip_file_path = "${path.module}/lambda_function.zip"
  src_dir       = "${path.module}"
}

# S3 버킷 참조 또는 생성
resource "aws_s3_bucket" "pdfs_bucket" {
  count    = var.create_s3_bucket ? 1 : 0
  provider = aws.s3_region
  bucket   = var.s3_bucket_name
  
  tags = {
    Name        = var.s3_bucket_name
    Environment = var.environment
    Managed     = "terraform"
  }
}

data "aws_s3_bucket" "existing_bucket" {
  count    = var.create_s3_bucket ? 0 : 1
  provider = aws.s3_region
  bucket   = var.s3_bucket_name
}

locals {
  bucket_id = var.create_s3_bucket ? aws_s3_bucket.pdfs_bucket[0].id : data.aws_s3_bucket.existing_bucket[0].id
}

# Lambda 함수를 위한 IAM 역할
resource "aws_iam_role" "lambda_role" {
  provider = aws.s3_region
  name = "rag-chatbot-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "rag-chatbot-lambda-role"
    Environment = var.environment
  }
}

# Lambda에 필요한 정책 첨부
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  provider   = aws.s3_region
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# S3 접근 정책 추가
resource "aws_iam_policy" "s3_policy" {
  provider    = aws.s3_region
  name        = "rag-chatbot-s3-policy"
  description = "RAG Chatbot S3 액세스 정책"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Effect   = "Allow"
        Resource = [
          "arn:aws:s3:::${var.s3_bucket_name}",
          "arn:aws:s3:::${var.s3_bucket_name}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "s3_attachment" {
  provider   = aws.s3_region
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.s3_policy.arn
}

# Bedrock 액세스 정책 추가
resource "aws_iam_policy" "bedrock_policy" {
  provider    = aws.s3_region
  name        = "rag-chatbot-bedrock-policy"
  description = "RAG Chatbot Bedrock 액세스 정책"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action   = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Effect   = "Allow"
        Resource = [
          "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v1",
          "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-instant-v1"
        ]
      },
      {
        Action   = [
          "bedrock:GetFoundationModel",
          "bedrock:ListFoundationModels"
        ]
        Effect   = "Allow"
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "bedrock_attachment" {
  provider   = aws.s3_region
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.bedrock_policy.arn
}

# Lambda 패키지 생성
resource "null_resource" "install_dependencies" {
  triggers = {
    requirements_hash = filemd5("${local.src_dir}/requirements-lambda.txt")
    src_hash = filemd5("${local.src_dir}/lambda_function.py")
    app_hash = filemd5("${local.src_dir}/app/chat_service.py")
    embeddings_hash = filemd5("${local.src_dir}/app/embeddings.py")
    document_store_hash = filemd5("${local.src_dir}/app/document_store.py")
    retriever_hash = filemd5("${local.src_dir}/app/retriever.py")
    bedrock_client_hash = filemd5("${local.src_dir}/app/bedrock_client.py")
    cost_tracker_hash = filemd5("${local.src_dir}/app/utils/cost_tracker.py")
  }

  provisioner "local-exec" {
    command = <<-EOT
      echo "Lambda 패키지 준비 중..."
      rm -rf ${local.package_dir}
      mkdir -p ${local.package_dir}
      
      # 소스 파일 복사
      cp -R ${local.src_dir}/app ${local.package_dir}/
      cp ${local.src_dir}/lambda_function.py ${local.package_dir}/
      cp ${local.src_dir}/.env ${local.package_dir}/ 2>/dev/null || true
      
      # 의존성 설치
      pip install --target ${local.package_dir} -r ${local.src_dir}/requirements-lambda.txt
      
      # 불필요한 파일 제거
      find ${local.package_dir} -name "*.pyc" -delete
      find ${local.package_dir} -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
      find ${local.package_dir} -name "tests" -type d -exec rm -rf {} + 2>/dev/null || true
      rm -rf ${local.package_dir}/bin
    EOT
  }
}

# Lambda 배포 패키지 생성
data "archive_file" "lambda_package" {
  type        = "zip"
  source_dir  = local.package_dir
  output_path = local.zip_file_path
  
  depends_on = [null_resource.install_dependencies]
}

# Lambda 함수 정의
resource "aws_lambda_function" "rag_chatbot" {
  provider         = aws.s3_region
  function_name    = "rag-chatbot"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = var.lambda_runtime
  filename         = data.archive_file.lambda_package.output_path
  source_code_hash = data.archive_file.lambda_package.output_base64sha256
  timeout          = 30
  memory_size      = 1024

  environment {
    variables = {
      CUSTOM_AWS_REGION = var.aws_region
      S3_BUCKET_NAME    = var.s3_bucket_name
      BATCH_SIZE        = "20"
      FAST_MODE         = "true"
    }
  }

  tags = {
    Name        = "rag-chatbot"
    Environment = var.environment
  }

  ephemeral_storage {
    size = 10240 # MB
  }
}

# CloudWatch 로그 그룹
resource "aws_cloudwatch_log_group" "lambda_logs" {
  provider          = aws.s3_region
  name              = "/aws/lambda/${aws_lambda_function.rag_chatbot.function_name}"
  retention_in_days = var.log_retention_days
  
  tags = {
    Name        = "rag-chatbot-logs"
    Environment = var.environment
  }
}

# API Gateway REST API
resource "aws_api_gateway_rest_api" "chatbot_api" {
  provider    = aws.s3_region
  name        = "rag-chatbot-api"
  description = "API for RAG Chatbot"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = {
    Name        = "rag-chatbot-api"
    Environment = var.environment
  }
}

# /chat 리소스
resource "aws_api_gateway_resource" "chat_resource" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  parent_id   = aws_api_gateway_rest_api.chatbot_api.root_resource_id
  path_part   = "chat"
}

# CORS 설정을 위한 OPTIONS 메서드
resource "aws_api_gateway_method" "chat_options" {
  provider      = aws.s3_region
  rest_api_id   = aws_api_gateway_rest_api.chatbot_api.id
  resource_id   = aws_api_gateway_resource.chat_resource.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "chat_options_integration" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  resource_id = aws_api_gateway_resource.chat_resource.id
  http_method = aws_api_gateway_method.chat_options.http_method
  type        = "MOCK"
  
  request_templates = {
    "application/json" = jsonencode({
      statusCode = 200
    })
  }
}

resource "aws_api_gateway_method_response" "chat_options_response" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  resource_id = aws_api_gateway_resource.chat_resource.id
  http_method = aws_api_gateway_method.chat_options.http_method
  status_code = "200"
  
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Headers" = true
  }
}

resource "aws_api_gateway_integration_response" "chat_options_integration_response" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  resource_id = aws_api_gateway_resource.chat_resource.id
  http_method = aws_api_gateway_method.chat_options.http_method
  status_code = aws_api_gateway_method_response.chat_options_response.status_code
  
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,POST,OPTIONS'"
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token,X-Requested-With'"
  }
}

# POST 메서드 - 채팅 질문 처리
resource "aws_api_gateway_method" "chat_post" {
  provider      = aws.s3_region
  rest_api_id   = aws_api_gateway_rest_api.chatbot_api.id
  resource_id   = aws_api_gateway_resource.chat_resource.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "chat_post_integration" {
  provider               = aws.s3_region
  rest_api_id             = aws_api_gateway_rest_api.chatbot_api.id
  resource_id             = aws_api_gateway_resource.chat_resource.id
  http_method             = aws_api_gateway_method.chat_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.rag_chatbot.invoke_arn
}

resource "aws_api_gateway_method_response" "chat_post_response" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  resource_id = aws_api_gateway_resource.chat_resource.id
  http_method = aws_api_gateway_method.chat_post.http_method
  status_code = "200"
  
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin" = true
  }
}

# /chat/reset 리소스 - 대화 기록 초기화
resource "aws_api_gateway_resource" "reset_resource" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  parent_id   = aws_api_gateway_resource.chat_resource.id
  path_part   = "reset"
}

# CORS 설정을 위한 OPTIONS 메서드
resource "aws_api_gateway_method" "reset_options" {
  provider      = aws.s3_region
  rest_api_id   = aws_api_gateway_rest_api.chatbot_api.id
  resource_id   = aws_api_gateway_resource.reset_resource.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "reset_options_integration" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  resource_id = aws_api_gateway_resource.reset_resource.id
  http_method = aws_api_gateway_method.reset_options.http_method
  type        = "MOCK"
  
  request_templates = {
    "application/json" = jsonencode({
      statusCode = 200
    })
  }
}

resource "aws_api_gateway_method_response" "reset_options_response" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  resource_id = aws_api_gateway_resource.reset_resource.id
  http_method = aws_api_gateway_method.reset_options.http_method
  status_code = "200"
  
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Headers" = true
  }
}

resource "aws_api_gateway_integration_response" "reset_options_integration_response" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  resource_id = aws_api_gateway_resource.reset_resource.id
  http_method = aws_api_gateway_method.reset_options.http_method
  status_code = aws_api_gateway_method_response.reset_options_response.status_code
  
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin"  = "'*'"
    "method.response.header.Access-Control-Allow-Methods" = "'GET,POST,OPTIONS'"
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token,X-Requested-With'"
  }
}

# POST 메서드 - 대화 기록 초기화
resource "aws_api_gateway_method" "reset_post" {
  provider      = aws.s3_region
  rest_api_id   = aws_api_gateway_rest_api.chatbot_api.id
  resource_id   = aws_api_gateway_resource.reset_resource.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "reset_post_integration" {
  provider               = aws.s3_region
  rest_api_id             = aws_api_gateway_rest_api.chatbot_api.id
  resource_id             = aws_api_gateway_resource.reset_resource.id
  http_method             = aws_api_gateway_method.reset_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.rag_chatbot.invoke_arn
}

resource "aws_api_gateway_method_response" "reset_post_response" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  resource_id = aws_api_gateway_resource.reset_resource.id
  http_method = aws_api_gateway_method.reset_post.http_method
  status_code = "200"
  
  response_parameters = {
    "method.response.header.Access-Control-Allow-Origin" = true
  }
}

# Lambda 함수 호출 권한 설정
resource "aws_lambda_permission" "api_gateway_chat" {
  provider      = aws.s3_region
  statement_id  = "AllowAPIGatewayInvokeChat"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rag_chatbot.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.chatbot_api.execution_arn}/*/*/chat"
}

resource "aws_lambda_permission" "api_gateway_reset" {
  provider      = aws.s3_region
  statement_id  = "AllowAPIGatewayInvokeReset"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rag_chatbot.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.chatbot_api.execution_arn}/*/*/chat/reset"
}

# API Gateway 배포
resource "aws_api_gateway_deployment" "chatbot_deployment" {
  provider    = aws.s3_region
  rest_api_id = aws_api_gateway_rest_api.chatbot_api.id
  
  depends_on = [
    aws_api_gateway_integration.chat_post_integration,
    aws_api_gateway_integration.reset_post_integration,
    aws_api_gateway_integration.chat_options_integration,
    aws_api_gateway_integration.reset_options_integration
  ]
  
  lifecycle {
    create_before_destroy = true
  }
  
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.chat_resource.id,
      aws_api_gateway_resource.reset_resource.id,
      aws_api_gateway_method.chat_post.id,
      aws_api_gateway_method.reset_post.id,
      aws_api_gateway_integration.chat_post_integration.id,
      aws_api_gateway_integration.reset_post_integration.id
    ]))
  }
}

# API 스테이지 정의
resource "aws_api_gateway_stage" "prod" {
  provider      = aws.s3_region
  deployment_id = aws_api_gateway_deployment.chatbot_deployment.id
  rest_api_id   = aws_api_gateway_rest_api.chatbot_api.id
  stage_name    = "prod"
  
  tags = {
    Name        = "rag-chatbot-api-stage"
    Environment = var.environment
  }
}

# 출력값
output "api_endpoint_chat" {
  description = "챗봇 API 엔드포인트 URL (채팅)"
  value       = "${aws_api_gateway_deployment.chatbot_deployment.invoke_url}${aws_api_gateway_stage.prod.stage_name}/chat"
}

output "api_endpoint_reset" {
  description = "챗봇 API 엔드포인트 URL (초기화)"
  value       = "${aws_api_gateway_deployment.chatbot_deployment.invoke_url}${aws_api_gateway_stage.prod.stage_name}/chat/reset"
}

output "lambda_function_name" {
  description = "Lambda 함수 이름"
  value       = aws_lambda_function.rag_chatbot.function_name
}

output "cloudwatch_log_group" {
  description = "CloudWatch 로그 그룹"
  value       = aws_cloudwatch_log_group.lambda_logs.name
} 
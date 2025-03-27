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
}

# S3 버킷 참조 또는 생성 (ap-northeast-2 리전 사용)
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
        Action   = "bedrock:*"
        Effect   = "Allow"
        Resource = "*"
      },
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

# Lambda 패키지 생성을 위한 Python 의존성 모듈 설치 및 압축
resource "null_resource" "install_dependencies" {
  triggers = {
    # 종속성이 변경되거나 Lambda 코드가 변경될 때만 재실행
    dependencies_hash = fileexists("${path.module}/requirements-lambda.txt") ? filemd5("${path.module}/requirements-lambda.txt") : filemd5("${path.module}/requirements.txt")
    lambda_hash      = filemd5("${path.module}/lambda_function.py")
    app_dir_hash     = md5(join("", [for f in fileset("${path.module}/app", "**") : filemd5("${path.module}/app/${f}")]))
  }

  provisioner "local-exec" {
    # 패키지 디렉토리 생성
    command = <<EOT
      rm -rf ${local.package_dir} && mkdir -p ${local.package_dir}
      
      # requirements.txt 파일 선택
      REQUIREMENTS_FILE="${path.module}/requirements-lambda.txt"
      if [ ! -f "$REQUIREMENTS_FILE" ]; then
        REQUIREMENTS_FILE="${path.module}/requirements.txt"
      fi
      
      echo "패키지 디렉토리: ${local.package_dir}"
      echo "의존성 파일: $REQUIREMENTS_FILE"
      
      # Python 의존성 설치
      PYTHONPATH="${local.package_dir}" pip install --upgrade pip
      PYTHONPATH="${local.package_dir}" pip install -r $REQUIREMENTS_FILE -t ${local.package_dir} --no-cache-dir
      
      # 의존성 설치 확인
      if [ $? -ne 0 ]; then
        echo "의존성 설치 실패"
        exit 1
      fi
      
      # Lambda 함수 코드 복사
      echo "Lambda 함수 코드 복사 중..."
      cp ${path.module}/lambda_function.py ${local.package_dir}/
      cp -r ${path.module}/app ${local.package_dir}/
      
      # .env 파일이 존재하면 복사
      if [ -f "${path.module}/.env" ]; then
        echo ".env 파일을 패키지에 포함시킵니다."
        cp ${path.module}/.env ${local.package_dir}/
      fi
      
      # 불필요한 파일 제거
      echo "불필요한 파일 제거 중..."
      find ${local.package_dir} -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
      find ${local.package_dir} -name "*.pyc" -delete
      find ${local.package_dir} -name "*.dist-info" -type d -exec rm -rf {} + 2>/dev/null || true
      find ${local.package_dir} -name "*.egg-info" -type d -exec rm -rf {} + 2>/dev/null || true
      find ${local.package_dir} -name "*.so" -type f -exec strip {} \; 2>/dev/null || true
      find ${local.package_dir} -name "tests" -type d -exec rm -rf {} + 2>/dev/null || true
      
      # Lambda에 불필요한 대용량 패키지 제거
      echo "불필요한 패키지 제거 중..."
      rm -rf ${local.package_dir}/numpy/tests
      rm -rf ${local.package_dir}/bin
      
      # 패키지 크기 확인
      du -sh ${local.package_dir}
      
      echo "Lambda 패키지 준비 완료"
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
  timeout          = 30  # 시간 초과 값 축소 (불필요한 대기 시간 감소)
  memory_size      = 1024  # 메모리 최적화 (충분한 성능 + 비용 효율성)

  environment {
    variables = {
      CUSTOM_AWS_REGION = var.aws_region
      S3_BUCKET_NAME    = var.s3_bucket_name
      BATCH_SIZE        = "20"  # 임베딩 처리 속도 향상
      FAST_MODE         = "true"  # 빠른 모드 활성화
      SMART_MODE        = "false"  # 스마트 모드 비활성화 (응답속도 향상)
      COST_DEBUG        = var.environment == "dev" ? "true" : "false"  # 개발 환경에서만 비용 디버깅 활성화
    }
  }

  tags = {
    Name        = "rag-chatbot"
    Environment = var.environment
  }

  # 복잡한 Python 패키지를 처리하기 위한 임시 디렉토리의 크기 제한 증가
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
  
  # 배포마다 유니크한 값을 생성하여 새로운 배포가 강제되도록 함
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
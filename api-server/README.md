# RAG 챗봇 API 서버

이 프로젝트는 AWS Lambda와 API Gateway를 사용하여 배포되는 RAG(Retrieval-Augmented Generation) 챗봇 API 서버입니다.

## 개요

이 서비스는 다음과 같은 기능을 제공합니다:

- S3 버킷에 저장된 PDF 문서를 기반으로 질문에 답변
- 쿼리와 관련된 문서를 검색하여 컨텍스트 생성
- Amazon Bedrock을 사용한 임베딩 생성 및 LLM 답변 생성
- 대화 기록 관리
- API Gateway를 통한 RESTful API 인터페이스 제공

## 시스템 요구사항

- AWS 계정
- AWS CLI 설정 (액세스 키 및 시크릿 키)
- Terraform (버전 1.0 이상)
- Python 3.11 이상
- 필요한 권한:
  - IAM 역할 및 정책 생성 권한
  - Lambda 함수 생성 및 관리 권한
  - API Gateway 생성 및 관리 권한
  - Amazon Bedrock 모델 호출 권한
  - S3 버킷 접근 권한

## 배포 방법

### 1. 환경 준비

```bash
# 저장소 클론
git clone <repository-url>
cd rag_chatbot_test/api-server

# (선택사항) 환경 변수 파일 생성
cat > .env << EOL
AWS_REGION=ap-northeast-2
S3_BUCKET_NAME=garden-rag-01
EOL
```

### 2. Terraform으로 배포

```bash
# Terraform 초기화
terraform init

# 배포 계획 확인
terraform plan

# AWS에 배포
terraform apply
```

### 3. 배포 확인

배포가 완료되면 API Gateway 엔드포인트 URL이 출력됩니다:

```
Outputs:
api_endpoint_chat = "https://abcdef123.execute-api.ap-northeast-2.amazonaws.com/prod/chat"
api_endpoint_reset = "https://abcdef123.execute-api.ap-northeast-2.amazonaws.com/prod/chat/reset"
```

## 사용 방법

### 대화 요청

```bash
curl -X POST https://abcdef123.execute-api.ap-northeast-2.amazonaws.com/prod/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "안녕하세요, 질문이 있습니다.",
    "session_id": "user-123"
  }'
```

### 모델 유형 선택 (스마트/빠른 응답)

```bash
curl -X POST https://abcdef123.execute-api.ap-northeast-2.amazonaws.com/prod/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "안녕하세요, 질문이 있습니다.",
    "session_id": "user-123",
    "model_type": "smart"  // "smart" 또는 "speed" 선택 가능
  }'
```

### 대화 초기화

```bash
curl -X POST https://abcdef123.execute-api.ap-northeast-2.amazonaws.com/prod/chat/reset \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "user-123"
  }'
```

## 비용 추적 기능

이 API는 AWS 리소스 사용량을 추적하고 CloudWatch 로그에 비용 정보를 기록하는 기능을 포함하고 있습니다.

### 추적되는 비용 항목
- Lambda 실행 시간 및 메모리 사용량
- API Gateway 요청 수
- Bedrock 모델 토큰 사용량 (입력/출력)
- S3 읽기 요청 및 데이터 전송량
- CloudWatch 로그 저장 및 수집

### 비용 추적 로그 확인

비용 추적 로그는 CloudWatch에서 다음 필터를 사용하여 확인할 수 있습니다:

```bash
aws logs filter-log-events --log-group-name "/aws/lambda/rag-chatbot" --filter-pattern "AWS 비용 정보"
```

### 개발 모드에서 비용 디버깅

개발 환경에서는 비용 정보를 API 응답에 포함시킬 수 있습니다. Terraform 변수 `environment`를 `dev`로 설정하면 `COST_DEBUG` 환경 변수가 활성화됩니다:

```bash
terraform apply -var="environment=dev"
```

이 모드에서는 API 응답에 다음과 같은 추가 필드가 포함됩니다:

```json
{
  "answer": "...",
  "sources": [...],
  "_debug_cost": {
    "costs_usd": {
      "lambda": "$0.00000123",
      "api_gateway": "$0.00000350",
      "bedrock": "$0.00015600",
      "s3": "$0.00000040",
      "cloudwatch": "$0.00000010",
      "total": "$0.00016123"
    },
    "usage": {
      "lambda_duration_ms": 243,
      "lambda_gb_seconds": 0.0002430,
      "bedrock_input_tokens": 512,
      "bedrock_output_tokens": 78,
      "bedrock_model": "anthropic.claude-3-haiku-20240307-v1:0",
      "s3_get_requests": 1
    }
  }
}
```

## 에러 처리 및 문제 해결

### 일반적인 문제

#### 1. S3 버킷 접근 오류

문제: Lambda가 S3 버킷에 접근할 수 없습니다.
해결방법:
- S3 버킷이 존재하는지 확인
- Lambda IAM 역할에 S3 읽기 권한이 있는지 확인
- 버킷 이름이 `variables.tf`의 `s3_bucket_name` 변수와 일치하는지 확인

#### 2. Bedrock 모델 호출 오류

문제: Bedrock 모델을 호출할 수 없습니다.
해결방법:
- AWS 계정에서 Bedrock 서비스가 활성화되어 있는지 확인
- Lambda IAM 역할에 `bedrock:InvokeModel` 권한이 있는지 확인
- 사용하려는 모델(`anthropic.claude-3-sonnet-20240229-v1:0` 등)에 대한 접근 권한이 있는지 확인

#### 3. Lambda 패키지 크기 초과

문제: Lambda 배포 패키지가 크기 제한(250MB 압축 / 10GB 압축 해제)을 초과합니다.
해결방법:
- `requirements-lambda.txt`에서 불필요한 의존성 제거
- 큰 라이브러리를 더 작은 대안으로 대체
- 코드를 여러 Lambda 함수로 분할

#### 4. API Gateway 응답 오류

문제: API Gateway에서 500 오류를 반환합니다.
해결방법:
- CloudWatch 로그에서 Lambda 오류 확인
- API Gateway와 Lambda 통합이 올바르게 설정되었는지 확인
- Lambda 타임아웃 및 메모리 설정 조정

### 로그 확인

Lambda 함수의 문제를 해결하려면 CloudWatch 로그를 확인하세요:

```bash
aws logs filter-log-events --log-group-name "/aws/lambda/rag-chatbot"
```

## 인프라 삭제

AWS 리소스를 삭제하려면:

```bash
terraform destroy
```

## 보안 고려사항

- 프로덕션 환경에서는 API 인증 메커니즘 추가 (API 키, Cognito 등)
- 민감한 환경 변수는 AWS Parameter Store 또는 Secrets Manager 사용
- S3 버킷과 Lambda 함수에 적절한 IAM 정책 적용
- CloudWatch 로그 보존 기간 설정 검토 
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

### 대화 초기화

```bash
curl -X POST https://abcdef123.execute-api.ap-northeast-2.amazonaws.com/prod/chat/reset \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "user-123"
  }'
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
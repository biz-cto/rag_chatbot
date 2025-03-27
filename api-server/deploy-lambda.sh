#!/bin/bash
set -e  # 오류 발생 시 스크립트 종료

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 필수 명령어 확인
for cmd in npm node; do
    if ! command -v $cmd &> /dev/null; then
        log_error "$cmd 명령어를 찾을 수 없습니다. 설치 후 다시 시도하세요."
        exit 1
    fi
done

# 필요한 디렉토리 생성
log_info "필요한 디렉토리 구조 확인 중..."
mkdir -p app/models

# 파일 존재 확인
for file in lambda_function.py app/services/rag_service.py requirements-lambda.txt serverless.yml; do
    if [ ! -f "$file" ]; then
        log_error "$file 파일을 찾을 수 없습니다."
        exit 1
    fi
done

# Serverless Framework 설치 (필요한 경우)
if ! command -v serverless &> /dev/null; then
    log_info "Serverless Framework 설치 중..."
    npm install -g serverless
    
    if ! command -v serverless &> /dev/null; then
        log_error "Serverless Framework 설치 실패"
        exit 1
    fi
fi

# serverless-python-requirements 플러그인 설치
if [ ! -d "node_modules/serverless-python-requirements" ]; then
    log_info "serverless-python-requirements 플러그인 설치 중..."
    npm init -y
    npm install --save serverless-python-requirements
    
    if [ ! -d "node_modules/serverless-python-requirements" ]; then
        log_error "serverless-python-requirements 플러그인 설치 실패"
        exit 1
    fi
fi

# 환경 변수 확인
if [ ! -f .env ]; then
    log_error ".env 파일이 없습니다."
    exit 1
fi

# 필수 환경 변수 확인
required_vars=("AWS_ACCESS_KEY_ID" "AWS_SECRET_ACCESS_KEY" "AWS_REGION" "S3_BUCKET_NAME")
missing_vars=()

for var in "${required_vars[@]}"; do
    if ! grep -q "^$var=" .env; then
        missing_vars+=("$var")
    fi
done

if [ ${#missing_vars[@]} -ne 0 ]; then
    log_error "다음 환경 변수가 .env 파일에 없습니다: ${missing_vars[*]}"
    exit 1
fi

# 환경 변수 로드
log_info "환경 변수 로드 중..."
export $(grep -v '^#' .env | xargs)

# Serverless 배포
log_info "Lambda 함수 배포 중..."
serverless deploy

if [ $? -eq 0 ]; then
    log_info "배포 완료!"
    serverless info
else
    log_error "배포 실패!"
    exit 1
fi 
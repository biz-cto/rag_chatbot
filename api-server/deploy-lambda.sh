#!/bin/bash
set -e  # 오류 발생 시 스크립트 종료

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 전역 변수
DEPLOY_METHOD=""
TIMEOUT_SECONDS=300  # npm 설치 등 작업의 타임아웃 (5분)
PACKAGE_DIR="./package_lambda"
ZIP_FILE="deployment.zip"

# 함수 정의
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "\n${BLUE}[STEP]${NC} $1"
    echo -e "${BLUE}=======================${NC}"
}

show_help() {
    echo "사용법: $0 [OPTIONS]"
    echo ""
    echo "옵션:"
    echo "  --help          이 도움말 메시지 출력"
    echo "  --aws-cli       AWS CLI를 사용하여 직접 배포 (serverless 프레임워크 없이)"
    echo "  --serverless    Serverless 프레임워크를 사용하여 배포 (기본값)"
    echo "  --package-only  패키지만 생성하고 배포는 하지 않음"
    echo ""
    exit 0
}

# 타임아웃 설정 함수
run_with_timeout() {
    local cmd="$1"
    local timeout=$2
    local msg="$3"
    
    log_info "$msg (타임아웃: ${timeout}초)"
    
    # 백그라운드에서 명령 실행
    eval "$cmd" &
    local pid=$!
    
    # 타이머 시작
    local count=0
    while kill -0 $pid 2>/dev/null; do
        if [ $count -ge $timeout ]; then
            log_error "명령이 ${timeout}초 내에 완료되지 않아 종료합니다: $cmd"
            kill -9 $pid 2>/dev/null || true
            return 1
        fi
        sleep 1
        count=$((count + 1))
        if [ $((count % 10)) -eq 0 ]; then
            echo -n "."
        fi
    done
    
    # 프로세스가 성공적으로 종료되었는지 확인
    wait $pid
    local status=$?
    if [ $status -eq 0 ]; then
        log_info "명령이 성공적으로 완료되었습니다."
        return 0
    else
        log_error "명령이 실패했습니다 (종료 코드: $status)"
        return $status
    fi
}

# 인수 파싱
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --help) show_help ;;
        --aws-cli) DEPLOY_METHOD="aws-cli"; shift ;;
        --serverless) DEPLOY_METHOD="serverless"; shift ;;
        --package-only) DEPLOY_METHOD="package-only"; shift ;;
        *) log_error "알 수 없는 옵션: $1"; show_help ;;
    esac
done

# 기본 배포 방법 설정
if [ -z "$DEPLOY_METHOD" ]; then
    DEPLOY_METHOD="serverless"
fi

# 필요한 디렉토리 생성
log_step "필요한 디렉토리 구조 확인 중..."
mkdir -p app/models

# 파일 존재 확인
log_info "필수 파일 확인 중..."
for file in lambda_function.py app/services/rag_service.py requirements-lambda.txt; do
    if [ ! -f "$file" ]; then
        log_error "$file 파일을 찾을 수 없습니다."
        exit 1
    fi
done

# 환경 변수 확인
if [ ! -f .env ]; then
    log_error ".env 파일이 없습니다."
    exit 1
fi

# 필수 환경 변수 확인
log_info "환경 변수 확인 중..."
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

# 패키지 디렉토리 정리
log_step "패키지 생성 준비 중..."
rm -rf "$PACKAGE_DIR" "$ZIP_FILE"
mkdir -p "$PACKAGE_DIR"

# 패키지 생성 함수
create_package() {
    log_step "Lambda 패키지 생성 중..."
    
    # 종속성 설치
    log_info "Python 종속성 설치 중..."
    if ! pip install -r requirements-lambda.txt -t "$PACKAGE_DIR"; then
        log_error "Python 종속성 설치 실패"
        exit 1
    fi
    
    # 코드 복사
    log_info "코드 파일 복사 중..."
    cp lambda_function.py "$PACKAGE_DIR/"
    cp -r app "$PACKAGE_DIR/"
    
    # 불필요한 파일 제거
    log_info "불필요한 파일 제거 중..."
    find "$PACKAGE_DIR" -name "__pycache__" -type d -exec rm -rf {} +
    find "$PACKAGE_DIR" -name "*.pyc" -delete
    
    # ZIP 파일 생성
    log_info "ZIP 파일 생성 중..."
    cd "$PACKAGE_DIR" && zip -r "../$ZIP_FILE" . && cd ..
    
    log_info "패키지 생성 완료: $ZIP_FILE ($(du -h "$ZIP_FILE" | cut -f1))"
}

# Serverless Framework로 배포
deploy_with_serverless() {
    log_step "Serverless Framework로 배포 중..."
    
    if ! command -v serverless &> /dev/null; then
        log_warn "Serverless Framework가 설치되어 있지 않습니다."
        
        if ! command -v npm &> /dev/null; then
            log_error "npm이 설치되어 있지 않습니다. npm을 설치하거나 다른 배포 방법을 선택하세요."
            exit 1
        fi
        
        log_info "Serverless Framework 설치 시도 중 (타임아웃: ${TIMEOUT_SECONDS}초)..."
        if ! run_with_timeout "npm install -g serverless" $TIMEOUT_SECONDS "Serverless Framework 설치 중"; then
            log_error "Serverless Framework 설치 실패 또는 타임아웃. 다른 배포 방법을 시도합니다."
            deploy_with_aws_cli
            return
        fi
        
        if ! command -v serverless &> /dev/null; then
            log_error "Serverless Framework 설치 실패. 다른 배포 방법을 시도합니다."
            deploy_with_aws_cli
            return
        fi
    fi
    
    # serverless-python-requirements 플러그인 설치
    if [ ! -d "node_modules/serverless-python-requirements" ]; then
        log_info "serverless-python-requirements 플러그인 설치 중..."
        if ! run_with_timeout "npm init -y && npm install --save serverless-python-requirements" $TIMEOUT_SECONDS "플러그인 설치 중"; then
            log_warn "플러그인 설치 실패 또는 타임아웃. 수동 패키징으로 전환합니다."
            
            # 패키지 생성
            create_package
            
            # serverless.yml 수정하여 개별 패키징 사용하지 않도록 설정
            if [ -f "serverless.yml" ]; then
                log_info "serverless.yml 파일 수정 중..."
                # custom 섹션 제거 또는 주석 처리
                sed -i.bak '/^custom:/,/^[a-z]/s/^/#/' serverless.yml
                # 플러그인 섹션 제거 또는 주석 처리
                sed -i.bak '/^plugins:/,/^[a-z]/s/^/#/' serverless.yml
                # Artifact 패스 추가
                echo "package:" >> serverless.yml
                echo "  artifact: $ZIP_FILE" >> serverless.yml
            fi
        fi
    fi
    
    # Serverless 배포
    log_info "Lambda 함수 배포 중..."
    if ! serverless deploy; then
        log_error "Serverless 배포 실패. AWS CLI로 배포를 시도합니다."
        deploy_with_aws_cli
        return
    fi
    
    log_info "Serverless 배포 완료!"
    serverless info
}

# AWS CLI로 배포
deploy_with_aws_cli() {
    log_step "AWS CLI로 배포 중..."
    
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI가 설치되어 있지 않습니다. AWS CLI를 설치하세요."
        exit 1
    fi
    
    # 패키지 생성
    create_package
    
    # Lambda 함수 존재 여부 확인
    FUNCTION_NAME="rag-chatbot"
    log_info "Lambda 함수 '$FUNCTION_NAME' 확인 중..."
    
    if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$AWS_REGION" &> /dev/null; then
        # 함수 업데이트
        log_info "Lambda 함수 '$FUNCTION_NAME' 업데이트 중..."
        aws lambda update-function-code \
            --function-name "$FUNCTION_NAME" \
            --zip-file "fileb://$ZIP_FILE" \
            --region "$AWS_REGION"
    else
        # 함수 생성
        log_info "Lambda 함수 '$FUNCTION_NAME' 생성 중..."
        
        # IAM 역할 찾기 또는 생성
        ROLE_NAME="rag-chatbot-lambda-role"
        ROLE_ARN=""
        
        if aws iam get-role --role-name "$ROLE_NAME" &> /dev/null; then
            ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
            log_info "기존 IAM 역할 사용: $ROLE_ARN"
        else
            log_info "새 IAM 역할 생성 중: $ROLE_NAME..."
            
            # 신뢰 정책 생성
            echo '{
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }' > trust-policy.json
            
            # 역할 생성
            aws iam create-role \
                --role-name "$ROLE_NAME" \
                --assume-role-policy-document file://trust-policy.json
                
            # 필요한 정책 연결
            aws iam attach-role-policy \
                --role-name "$ROLE_NAME" \
                --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
                
            aws iam attach-role-policy \
                --role-name "$ROLE_NAME" \
                --policy-arn "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
                
            # Bedrock 권한을 위한 인라인 정책 생성
            echo '{
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": "bedrock:InvokeModel",
                    "Resource": "*"
                }]
            }' > bedrock-policy.json
            
            aws iam put-role-policy \
                --role-name "$ROLE_NAME" \
                --policy-name "BedrockInvokeModel" \
                --policy-document file://bedrock-policy.json
                
            # 역할 ARN 가져오기
            ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
            
            # 임시 파일 제거
            rm -f trust-policy.json bedrock-policy.json
            
            log_info "IAM 역할 생성 완료: $ROLE_ARN"
            
            # IAM 역할이 전파될 시간 대기
            log_info "IAM 역할 전파 대기 중 (10초)..."
            sleep 10
        fi
        
        # Lambda 함수 생성
        aws lambda create-function \
            --function-name "$FUNCTION_NAME" \
            --zip-file "fileb://$ZIP_FILE" \
            --handler "lambda_function.lambda_handler" \
            --runtime "python3.11" \
            --role "$ROLE_ARN" \
            --timeout 300 \
            --memory-size 2048 \
            --environment "Variables={AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID,AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY,AWS_REGION=$AWS_REGION,S3_BUCKET_NAME=$S3_BUCKET_NAME,LAMBDA_ENVIRONMENT=true}" \
            --region "$AWS_REGION"
        
        # API Gateway 생성
        log_info "API Gateway 생성 중..."
        API_ID=$(aws apigateway create-rest-api \
            --name "RAG Chatbot API" \
            --region "$AWS_REGION" \
            --query 'id' --output text)
            
        ROOT_ID=$(aws apigateway get-resources \
            --rest-api-id "$API_ID" \
            --region "$AWS_REGION" \
            --query 'items[0].id' --output text)
            
        # /chat 리소스 생성
        CHAT_ID=$(aws apigateway create-resource \
            --rest-api-id "$API_ID" \
            --parent-id "$ROOT_ID" \
            --path-part "chat" \
            --region "$AWS_REGION" \
            --query 'id' --output text)
            
        # POST 메소드 생성
        aws apigateway put-method \
            --rest-api-id "$API_ID" \
            --resource-id "$CHAT_ID" \
            --http-method "POST" \
            --authorization-type "NONE" \
            --region "$AWS_REGION"
            
        # Lambda 함수와 통합
        LAMBDA_ARN="arn:aws:lambda:$AWS_REGION:$(aws sts get-caller-identity --query 'Account' --output text):function:$FUNCTION_NAME"
            
        aws apigateway put-integration \
            --rest-api-id "$API_ID" \
            --resource-id "$CHAT_ID" \
            --http-method "POST" \
            --type "AWS_PROXY" \
            --integration-http-method "POST" \
            --uri "arn:aws:apigateway:$AWS_REGION:lambda:path/2015-03-31/functions/$LAMBDA_ARN/invocations" \
            --region "$AWS_REGION"
            
        # Lambda 권한 추가
        aws lambda add-permission \
            --function-name "$FUNCTION_NAME" \
            --statement-id "apigateway-post" \
            --action "lambda:InvokeFunction" \
            --principal "apigateway.amazonaws.com" \
            --source-arn "arn:aws:execute-api:$AWS_REGION:$(aws sts get-caller-identity --query 'Account' --output text):$API_ID/*/POST/chat" \
            --region "$AWS_REGION"
            
        # API 배포
        aws apigateway create-deployment \
            --rest-api-id "$API_ID" \
            --stage-name "prod" \
            --region "$AWS_REGION"
            
        # API URL 표시
        API_URL="https://$API_ID.execute-api.$AWS_REGION.amazonaws.com/prod/chat"
        log_info "API 배포 완료! 엔드포인트: $API_URL"
    fi
    
    log_info "AWS CLI 배포 완료!"
}

# 선택한 배포 방법 실행
case "$DEPLOY_METHOD" in
    "aws-cli")
        deploy_with_aws_cli
        ;;
    "serverless")
        deploy_with_serverless
        ;;
    "package-only")
        create_package
        log_info "패키지만 생성했습니다. 배포는 수행하지 않았습니다."
        ;;
    *)
        log_error "알 수 없는 배포 방법: $DEPLOY_METHOD"
        exit 1
        ;;
esac

log_step "배포 작업 완료!" 
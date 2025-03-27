#!/bin/bash

# Serverless Framework 설치 (필요한 경우)
if ! command -v serverless &> /dev/null
then
    echo "Serverless Framework 설치 중..."
    npm install -g serverless
fi

# serverless-python-requirements 플러그인 설치
if [ ! -d "node_modules" ]; then
    echo "serverless-python-requirements 플러그인 설치 중..."
    npm init -y
    npm install --save serverless-python-requirements
fi

# 필요한 디렉토리 생성
mkdir -p app/models

# 환경 변수 확인
if [ ! -f .env ]; then
    echo "오류: .env 파일이 없습니다."
    exit 1
fi

# 환경 변수 로드
export $(grep -v '^#' .env | xargs)

# Serverless 배포
echo "Lambda 함수 배포 중..."
serverless deploy

echo "배포 완료!" 
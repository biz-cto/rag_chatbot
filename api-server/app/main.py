from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
import os
import logging
from app.routers import chat
from app.utils.logger_config import setup_logger
import time



# 메인 로거 설정
logger = setup_logger("app.main", "logs/app.log", logging.INFO)

app = FastAPI(title="경비지침규정 챗봇 API")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(chat.router)

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.error(f"HTTP 오류: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail}
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"처리되지 않은 예외: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"message": "서버 내부 오류가 발생했습니다. 나중에 다시 시도해주세요."}
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    
    # 요청 로깅
    logger.info(f"요청: {request.method} {request.url}")
    
    try:
        response = await call_next(request)
        
        # 응답 로깅
        process_time = time.time() - start_time
        logger.info(f"응답: {response.status_code} - {process_time:.4f}초 소요")
        
        return response
    except Exception as e:
        logger.error(f"요청 처리 중 오류 발생: {str(e)}", exc_info=True)
        raise

@app.on_event("startup")
async def startup_event():
    """앱 시작 시 실행되는 이벤트 핸들러"""
    logger.info("애플리케이션이 시작되었습니다.")
    # 로그 디렉토리 생성
    os.makedirs("logs", exist_ok=True)

@app.get("/health")
async def health_check():
    """서비스 상태 확인 엔드포인트"""
    logger.debug("상태 확인 요청을 받았습니다.")
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    logger.info("서버를 시작합니다.")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

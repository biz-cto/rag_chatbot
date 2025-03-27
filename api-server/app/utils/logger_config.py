import os
import logging
from logging.handlers import RotatingFileHandler
from rich.logging import RichHandler

def setup_logger(name, log_file, level=logging.INFO):
    """로거를 설정하고 반환합니다."""
    
    # 로그 디렉토리가 없으면 생성
    log_dir = os.path.dirname(log_file)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    # 로거 설정
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 이미 핸들러가 설정되어 있다면 추가하지 않음
    if logger.hasHandlers():
        return logger
    
    # 콘솔 핸들러 (Rich 라이브러리 사용)
    console_handler = RichHandler(rich_tracebacks=True)
    console_handler.setLevel(level)
    console_formatter = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_formatter)
    
    # 파일 핸들러 (로테이션 적용)
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=10485760,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(level)
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    
    # 핸들러 추가
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

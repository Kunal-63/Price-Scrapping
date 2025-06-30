import logging
import os
from datetime import datetime

def get_logger(name: str = "app", log_dir: str = "logs") -> logging.Logger:
    # Ensure log directory exists
    os.makedirs(log_dir, exist_ok=True)

    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Prevent duplicate handlers if logger is reused
    if logger.handlers:
        return logger

    # Format for log messages
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (daily log files)
    log_file_name = datetime.now().strftime("%Y-%m-%d") + ".log"
    file_handler = logging.FileHandler(os.path.join(log_dir, log_file_name), encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

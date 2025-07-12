import logging
import os
from datetime import datetime

import logging
import os
from datetime import datetime

def get_logger(name: str = "app", log_dir: str = "logs") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler with per-scraper name and daily rotation
    log_file_name = f"{name}_{datetime.now().strftime('%Y-%m-%d')}.log"
    file_handler = logging.FileHandler(os.path.join(log_dir, log_file_name), encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

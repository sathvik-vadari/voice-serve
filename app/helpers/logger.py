"""Logging helper for setting up application logging."""
import os
import logging
from datetime import datetime
from pathlib import Path
from app.helpers.config import Config


def setup_logger(name: str = __name__) -> logging.Logger:
    """Set up and return a configured logger."""
    # Create logs directory if it doesn't exist
    log_dir = Path(Config.LOG_DIR)
    log_dir.mkdir(exist_ok=True)
    
    # Create timestamped log file
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = log_dir / f"{timestamp}_voicelive.log"
    
    # Configure logging
    logging.basicConfig(
        filename=str(log_file),
        filemode="w",
        format='%(asctime)s:%(name)s:%(levelname)s:%(message)s',
        level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)
    )
    
    logger = logging.getLogger(name)
    
    # Also add console handler for development
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    return logger


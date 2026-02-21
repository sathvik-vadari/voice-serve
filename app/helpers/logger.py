"""Logging helper for setting up application logging."""
import logging
from datetime import datetime
from pathlib import Path
from app.helpers.config import Config

_initialized = False


def _init_root_logging() -> None:
    """Configure root logger once with both file and console handlers."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO))

    log_dir = Path(Config.LOG_DIR)
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = log_dir / f"{timestamp}_voicelive.log"

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    fh = logging.FileHandler(str(log_file), mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)


def setup_logger(name: str = __name__) -> logging.Logger:
    """Return a named logger, ensuring root handlers are configured."""
    _init_root_logging()
    return logging.getLogger(name)


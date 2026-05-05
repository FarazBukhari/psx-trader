"""
Logging configuration for PSX Signal System.
Call setup_logging() once at startup.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"


def setup_logging(level: str = "INFO", log_to_file: bool = True):
    LOG_DIR.mkdir(exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # Rotating file handler — all levels
    if log_to_file:
        fh = logging.handlers.RotatingFileHandler(
            LOG_DIR / "psx_signals.log",
            maxBytes=5 * 1024 * 1024,   # 5 MB
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

        # Error-only handler — logs/errors.txt (2 MB rotating, 3 backups)
        eh = logging.handlers.RotatingFileHandler(
            LOG_DIR / "errors.txt",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        eh.setLevel(logging.ERROR)
        eh.setFormatter(fmt)
        root.addHandler(eh)

    # Silence noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logging.getLogger("psx.startup").info(
        "Logging initialized — level=%s file=%s", level, log_to_file
    )

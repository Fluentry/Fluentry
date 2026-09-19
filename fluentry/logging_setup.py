"""The log file `--check` has always promised.

Text insertion is the one step that can fail silently: the transcript is
correct, the history row is written, and nothing reaches the document. The
backend returns a bool nobody reads and the tool's stderr is discarded, so
the user sees a working app that types nothing. This gives that path a
voice.

Off by default at DEBUG; `FLUENTRY_DEBUG=1` turns on the detail.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from .persistence.defaults import state_home

LOGGER_NAME = "fluentry"
_configured = False


def log_path():
    return state_home() / "fluentry.log"


def configure_logging() -> logging.Logger:
    """Idempotent: the entry point and the tests may both ask for it."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    level = logging.DEBUG if os.environ.get("FLUENTRY_DEBUG") else logging.INFO
    logger.setLevel(level)
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # A dictation log grows with use and is not worth unbounded disk.
        handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s")
        )
        logger.addHandler(handler)
    except OSError:
        # A missing state directory must never stop the app from dictating.
        pass
    logger.propagate = False
    _configured = True
    return logger


def get_logger(suffix: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}")

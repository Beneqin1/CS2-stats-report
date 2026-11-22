"""Logging setup shared by the GUI and CLI."""

from __future__ import annotations

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_DIR_NAME = "CS2StatsReport"


def log_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME / "logs"
    return Path(tempfile.gettempdir()) / APP_DIR_NAME / "logs"


def log_file() -> Path:
    return log_directory() / "app.log"


def configure_logging(level: int = logging.INFO) -> Path:
    path = log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        handler = RotatingFileHandler(
            path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    return path

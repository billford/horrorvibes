"""Central logging setup: console + rotating file handler.

Replaces the print()-based debug spam in the original script. Call
configure_logging() once, at process startup, before any module logs.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from horrorvibes.config import LoggingConfig

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(config: LoggingConfig) -> None:
    """Configure the root logger for console + rotating file output."""
    log_file = Path(config.file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(config.level)
    root.handlers.clear()

    formatter = logging.Formatter(_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=config.max_bytes, backupCount=config.backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

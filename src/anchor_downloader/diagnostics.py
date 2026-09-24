"""Small, process-safe diagnostic log used by dashboards and the broker."""

from __future__ import annotations

import contextlib
import logging
import fcntl
import os
from pathlib import Path


LOGGER_NAME = "anchor_downloader"
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3


class LockedRotatingFileHandler(logging.FileHandler):
    """A tiny cross-process rotating handler for the local dashboards."""

    def __init__(self, filename: Path):
        self.lock_path = filename.with_suffix(".lock")
        super().__init__(filename, mode="a", encoding="utf-8", delay=True)

    def _rotate_if_needed(self) -> None:
        path = Path(self.baseFilename)
        try:
            if path.stat().st_size < MAX_LOG_BYTES:
                return
        except FileNotFoundError:
            return
        if self.stream:
            self.stream.close()
            self.stream = None
        Path(f"{path}.{BACKUP_COUNT}").unlink(missing_ok=True)
        for number in range(BACKUP_COUNT - 1, 0, -1):
            source = Path(f"{path}.{number}")
            if source.exists():
                os.replace(source, Path(f"{path}.{number + 1}"))
        os.replace(path, Path(f"{path}.1"))

    def emit(self, record: logging.LogRecord) -> None:
        self.lock_path.touch(mode=0o600, exist_ok=True)
        with self.lock_path.open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                self._rotate_if_needed()
                super().emit(record)
                if self.stream:
                    self.stream.flush()
                with contextlib.suppress(OSError):
                    Path(self.baseFilename).chmod(0o600)
                # Other dashboard processes may rotate the path between writes.
                # Reopen on every record so no process keeps writing to a renamed
                # inode after rotation.
                if self.stream:
                    self.stream.close()
                    self.stream = None
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def config_dir() -> Path:
    return Path(
        os.environ.get(
            "ANCHOR_DOWNLOADER_HOME",
            Path.home() / ".config" / "anchor-downloader",
        )
    ).expanduser().resolve()


def configure_logging() -> logging.Logger:
    """Return the shared logger, installing one bounded file handler once."""
    logger = logging.getLogger(LOGGER_NAME)
    if any(getattr(handler, "_anchor_downloader", False) for handler in logger.handlers):
        return logger

    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    handler = LockedRotatingFileHandler(directory / "diagnostic.log")
    handler._anchor_downloader = True
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


logger = configure_logging()

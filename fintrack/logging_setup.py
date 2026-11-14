"""Logging that never writes secrets.

A filter scrubs anything that looks like a Plaid access/public/link token
from every log record, plus the configured client secret values. This is a
safety net: code should also avoid logging tokens in the first place.
"""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Plaid tokens look like "access-sandbox-<uuid>", "public-production-<uuid>", etc.
_TOKEN_RE = re.compile(r"\b(access|public|link)-(sandbox|development|production)-[0-9a-fA-F-]+")
# API keys passed as a `token=` query parameter (a safety net; Alpaca keys travel in headers,
# and the configured keys are also redacted by value).
# Values another rule already redacted (e.g. "access-production-[REDACTED]") are left as is.
_QUERY_TOKEN_RE = re.compile(r"(token=)(?![^&\s\"']*\[REDACTED\])[^&\s\"']+")


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str] | None = None):
        super().__init__()
        # Ignore empty/short strings so we don't redact every "a" in a message.
        self._secrets = [s for s in (secrets or []) if len(s) >= 8]

    def redact(self, text: str) -> str:
        text = _TOKEN_RE.sub(r"\1-\2-[REDACTED]", text)
        text = _QUERY_TOKEN_RE.sub(r"\1[REDACTED]", text)
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        # Format once with args, then replace the message so handlers see the clean text.
        record.msg = self.redact(record.getMessage())
        record.args = None
        return True


class PrivateRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler whose files are mode 600, like everything in FINTRACK_HOME.

    The standard handler opens the log with the process's default permissions (644 here), on first use and again after
    every rotation; this opens it with 600 instead. Rotated copies are renamed, so they keep the mode.
    """

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(self.baseFilename, 0o600)   # an older file made before this handler keeps its mode otherwise
        return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)


def _tighten(logs_dir: Path) -> None:
    """Files already in the logs folder (older logs, rotated copies, launchd logs) to mode 600."""
    for path in logs_dir.iterdir():
        if path.is_file() and not path.is_symlink() and path.stat().st_mode & 0o077:
            path.chmod(0o600)


def configure_logging(logs_dir: Path, secrets: list[str] | None = None) -> None:
    logs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _tighten(logs_dir)
    redactor = RedactingFilter(secrets)

    file_handler = PrivateRotatingFileHandler(logs_dir / "fintrack.log", maxBytes=1_000_000, backupCount=3)
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        handler.addFilter(redactor)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Filters sit on the handlers, so every record that reaches them is
    # scrubbed. The server starts uvicorn with log_config=None so uvicorn's
    # loggers propagate here instead of installing their own handlers.
    root.handlers = [file_handler, console_handler]

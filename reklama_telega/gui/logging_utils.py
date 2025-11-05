"""Utilities for routing standard logging output into Qt widgets."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6 import QtCore


class LogEmitter(QtCore.QObject):
    """Qt bridge that emits log messages to the GUI thread."""

    message = QtCore.Signal(str)


class QtLogHandler(logging.Handler):
    """Logging handler that forwards records to a Qt signal."""

    def __init__(self, *, level: int = logging.INFO, formatter: Optional[logging.Formatter] = None) -> None:
        super().__init__(level=level)
        self._emitter = LogEmitter()
        if formatter:
            self.setFormatter(formatter)

    def connect(self, slot) -> None:
        """Connect the internal signal to a widget slot."""
        self._emitter.message.connect(slot)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # pragma: no cover - formatting errors should not break app
            self.handleError(record)
            return
        self._emitter.message.emit(msg)

from __future__ import annotations

import contextlib
import contextvars
import logging
import uuid
from typing import Iterator

_CORRELATION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")


def get_correlation_id() -> str:
    return _CORRELATION_ID.get()


def set_correlation_id(value: str) -> None:
    _CORRELATION_ID.set(value.strip() or "-")


def new_correlation_id(prefix: str = "marco") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@contextlib.contextmanager
def correlation_scope(*, value: str | None = None, prefix: str = "marco") -> Iterator[str]:
    scoped_value = value or new_correlation_id(prefix=prefix)
    token = _CORRELATION_ID.set(scoped_value)
    try:
        yield scoped_value
    finally:
        _CORRELATION_ID.reset(token)


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        return True

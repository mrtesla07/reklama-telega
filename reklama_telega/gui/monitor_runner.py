"""Async helpers for running monitor routines from the GUI."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Callable, Optional

from reklama_telega.config import AppConfig
from reklama_telega.monitor import (
    MatchCallback,
    MatchResult,
    StatusCallback,
    send_manual_reply,
    scan_history,
    watch_stream,
)
from reklama_telega.storage import MatchStorage, StoredMatch

log = logging.getLogger(__name__)

CodeCallback = Callable[[str], str]
PasswordCallback = Callable[[str], str]
ErrorCallback = Callable[[str], None]


class MonitorRunner:
    """Manage long-running monitoring tasks for the GUI."""

    def __init__(self, storage: Optional[MatchStorage] = None) -> None:
        self._watch_task: Optional[asyncio.Task[None]] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._storage = storage

    @property
    def watching(self) -> bool:
        return self._watch_task is not None

    def update_storage(self, storage: Optional[MatchStorage]) -> None:
        self._storage = storage

    async def scan(
        self,
        config: AppConfig,
        limit: Optional[int],
        *,
        on_match: MatchCallback,
        on_status: StatusCallback,
        on_error: Optional[ErrorCallback],
        code_callback: Optional[CodeCallback],
        password_callback: Optional[PasswordCallback],
        storage: Optional[MatchStorage] = None,
    ) -> None:
        await scan_history(
            config,
            limit,
            on_match=on_match,
            on_status=on_status,
            error_callback=_wrap_error(on_error),
            code_callback=code_callback,
            password_callback=password_callback,
            storage=storage or self._storage,
        )

    async def start_watch(
        self,
        config: AppConfig,
        *,
        on_match: MatchCallback,
        on_status: StatusCallback,
        on_error: Optional[ErrorCallback],
        code_callback: Optional[CodeCallback],
        password_callback: Optional[PasswordCallback],
        auto_join: bool = False,
        runtime_auto_reply: Optional[bool] = None,
        storage: Optional[MatchStorage] = None,
    ) -> None:
        if self._watch_task:
            raise RuntimeError("Мониторинг уже запущен.")

        self._stop_event = asyncio.Event()
        storage = storage or self._storage
        error_cb = _wrap_error(on_error)

        async def _run() -> None:
            try:
                await watch_stream(
                    config,
                    on_match=on_match,
                    on_status=on_status,
                    error_callback=error_cb,
                    stop_event=self._stop_event,
                    code_callback=code_callback,
                    password_callback=password_callback,
                    auto_join=auto_join,
                    runtime_auto_reply=runtime_auto_reply,
                    storage=storage,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка во время мониторинга.")
                await _safe_status(on_status, "Ошибка во время мониторинга, см. логи.")
                if error_cb:
                    await error_cb("См. журнал: мониторинг завершился ошибкой.")
            finally:
                self._watch_task = None
                self._stop_event = None

        self._watch_task = asyncio.create_task(_run())

    async def stop_watch(self) -> None:
        if not self._watch_task:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        try:
            await self._watch_task
        except asyncio.CancelledError:
            pass
        finally:
            self._watch_task = None
            self._stop_event = None

    async def send_manual_reply(
        self,
        config: AppConfig,
        match: StoredMatch,
        *,
        on_status: StatusCallback,
        on_error: Optional[ErrorCallback],
        code_callback: Optional[CodeCallback],
        password_callback: Optional[PasswordCallback],
    ) -> None:
        error_cb = _wrap_error(on_error)
        await send_manual_reply(
            config,
            match,
            on_status=on_status,
            error_callback=error_cb,
            code_callback=code_callback,
            password_callback=password_callback,
        )


def _wrap_error(callback: Optional[ErrorCallback]):
    if callback is None:
        return None

    async def _async(message: str) -> None:
        try:
            result = callback(message)
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("Ошибка обработчика журнала ошибок: %s", message)

    return _async


async def _safe_status(callback: StatusCallback, message: str) -> None:
    if not callback:
        return
    try:
        result = callback(message)
        if inspect.isawaitable(result):
            await result
    except Exception:
        log.exception("Ошибка обработчика статуса: %s", message)

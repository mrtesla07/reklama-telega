"""Telegram client helpers."""

from __future__ import annotations

import inspect
import logging
from getpass import getpass
from typing import Awaitable, Callable, Optional

from telethon import TelegramClient, errors

from .config import AppConfig, ConfigError

log = logging.getLogger(__name__)

CodeCallback = Callable[[str], str | Awaitable[str]]
PasswordCallback = Callable[[str], str | Awaitable[str]]
StatusCallback = Callable[[str], None | Awaitable[None]]


def create_client(config: AppConfig) -> TelegramClient:
    """Create Telethon client based on configuration."""
    return TelegramClient(
        session=config.telegram.session_name,
        api_id=config.telegram.api_id,
        api_hash=config.telegram.api_hash,
        system_version="reklama-telega",
    )


async def ensure_authorized(
    client: TelegramClient,
    config: AppConfig,
    *,
    code_callback: CodeCallback | None = None,
    password_callback: PasswordCallback | None = None,
    status_callback: StatusCallback | None = None,
) -> None:
    """Ensure the Telethon client is authorized and ready."""

    async def _call_callback(
        cb: Callable[[str], str | Awaitable[str]] | None,
        prompt: str,
        *,
        default_getpass: bool = False,
    ) -> str:
        if cb is None:
            if default_getpass:
                return getpass(prompt).strip()
            return input(prompt).strip()
        value = cb(prompt)
        if inspect.isawaitable(value):
            value = await value
        return value.strip()

    async def _notify(message: str) -> None:
        if status_callback is None:
            return
        try:
            result = status_callback(message)
            if inspect.isawaitable(result):
                await result
        except Exception:  # pragma: no cover
            log.exception("Ошибка обработчика статуса: %s", message)

    await client.connect()
    try:
        if await client.is_user_authorized():
            return

        phone = config.telegram.phone
        if not phone:
            raise ConfigError(
                "Требуется telegram.phone в config.toml для первичной авторизации."
            )

        await _notify(f"Запрашиваем код подтверждения для {phone}")
        try:
            await client.send_code_request(phone, force_sms=config.telegram.force_sms)
        except errors.PhoneNumberFloodError as exc:
            raise RuntimeError(
                "Telegram временно заблокировал отправку кода. Попробуйте позже."
            ) from exc

        try:
            code = await _call_callback(
                code_callback,
                "Введите код из Telegram (пример 12345): ",
            )
            await client.sign_in(phone=phone, code=code)
        except errors.SessionPasswordNeededError:
            password = await _call_callback(
                password_callback,
                "Введите пароль двухфакторной аутентификации: ",
                default_getpass=True,
            )
            await client.sign_in(password=password)
        except errors.PhoneCodeInvalidError as exc:
            raise RuntimeError("Введён неверный код подтверждения.") from exc
    finally:
        await client.disconnect()

"""Core logic for scanning and watching Telegram comments."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from getpass import getpass
from typing import Awaitable, Callable, Dict, List, Optional, Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text
from telethon import TelegramClient, errors, events, functions
from telethon.tl.custom.message import Message
from telethon.tl.types import Channel, Chat, MessageService, PeerChannel

from .client import create_client, ensure_authorized
from .config import AppConfig, load_config
from .storage import MatchStorage, StoredMatch

log = logging.getLogger(__name__)
console = Console()


MatchCallback = Optional[Callable[["MatchResult"], Awaitable[None] | None]]
StatusCallback = Optional[Callable[[str], Awaitable[None] | None]]
ErrorCallback = Optional[Callable[[str], Awaitable[None] | None]]
CodeCallback = Optional[Callable[[str], str | Awaitable[str]]]
PasswordCallback = Optional[Callable[[str], str | Awaitable[str]]]


@dataclass(slots=True)
class TargetDialog:
    """Resolved target chat/channel entity."""

    id: int
    title: str
    entity: Channel | Chat
    origin: str


@dataclass(slots=True)
class MatchResult:
    """Information about a detected keyword match."""

    target_title: str
    chat_id: int
    message_id: int
    timestamp: Optional[datetime]
    author: str
    text: str
    matched_keywords: List[str]
    link: Optional[str]
    is_new: bool = True


def _format_timestamp(ts: Optional[datetime]) -> str:
    if not ts:
        return ""
    aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    dt = aware.astimezone()
    return dt.strftime("%d.%m %H:%M")


def _ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _highlight(text: str, keywords: Sequence[str], *, case_sensitive: bool) -> Text:
    base = Text(text)
    haystack = text if case_sensitive else text.lower()
    for keyword in keywords:
        needle = keyword if case_sensitive else keyword.lower()
        start = 0
        while True:
            idx = haystack.find(needle, start)
            if idx == -1:
                break
            base.stylize("bold red", idx, idx + len(keyword))
            start = idx + len(needle)
    return base


def _match_keywords(text: str, keywords: Sequence[str], case_sensitive: bool) -> List[str]:
    if not text:
        return []
    haystack = text if case_sensitive else text.lower()
    matches: List[str] = []
    for keyword in keywords:
        needle = keyword if case_sensitive else keyword.lower()
        if needle in haystack:
            matches.append(keyword)
    return matches



async def _handle_incoming_message(
    client: TelegramClient,
    target: TargetDialog,
    message: Message,
    *,
    chat_title: str,
    keywords: Sequence[str],
    case_sensitive: bool,
    storage: Optional[MatchStorage],
    should_reply_default: bool,
    templates: Sequence[str],
    template_random: bool,
    default_reply: str,
    guard_mapping: Dict[str, str],
    guard_delay: float,
    runtime_auto_reply: Optional[bool],
    on_status: StatusCallback,
    on_match: MatchCallback,
    error_callback: ErrorCallback,
) -> bool:
    if isinstance(message, MessageService):
        return False

    text = message.message or ""
    log.info("[watch] incoming chat=%s id=%s date=%s text=%r", chat_title, message.id, message.date, text[:120])

    matched = _match_keywords(text, keywords, case_sensitive)
    if not matched:
        log.info("[watch] skipped chat=%s id=%s (нет ключевых слов)", chat_title, message.id)
        return False

    if getattr(message, "sender", None) is None:
        with contextlib.suppress(Exception):
            await message.get_sender()

    result = MatchResult(
        target_title=target.title,
        chat_id=target.id,
        message_id=message.id,
        timestamp=_ensure_aware(message.date),
        author=_author_from_message(message),
        text=text,
        matched_keywords=matched,
        link=_build_message_link(target, message),
    )

    inserted = True
    if storage:
        try:
            inserted = await storage.save_match(result)
            result.is_new = inserted
        except Exception as exc:
            log.exception("Не удалось сохранить совпадение в БД.")
            await _dispatch_error(
                error_callback,
                f"Не удалось сохранить совпадение ({target.title} #{message.id}): {exc}",
            )
            inserted = False

    if result.is_new or not storage:
        await _dispatch_callback(on_match, result)

    should_reply = runtime_auto_reply if runtime_auto_reply is not None else should_reply_default
    reply_text: Optional[str] = None
    if should_reply and result.is_new:
        if templates:
            template = random.choice(templates) if (template_random or len(templates) > 1) else templates[0]
            reply_text = _render_template(template, result)
        elif default_reply:
            reply_text = _render_template(default_reply, result)

    if reply_text and client is not None:
        try:
            reply_message = await client.send_message(
                target.entity,
                reply_text,
                reply_to=message.id,
            )
            _schedule_username_guard(
                client,
                target.entity,
                message.id,
                reply_message.id,
                reply_text,
                guard_delay,
                guard_mapping,
                on_status,
            )
            await _dispatch_status(
                on_status,
                f"Отправлен автоответ в {target.title} на сообщение #{message.id}",
            )
        except Exception as exc:  # pragma: no cover
            log.exception("Не удалось отправить автоответ.")
            await _dispatch_error(
                error_callback,
                f"Не удалось отправить автоответ в {target.title}: {exc}",
            )

    return result.is_new


def _author_from_message(message: Message) -> str:
    sender = getattr(message, "sender", None)
    if sender is None:
        return "Неизвестно"
    for attr in ("first_name", "username", "title"):
        value = getattr(sender, attr, None)
        if value:
            return str(value)
    return "Неизвестно"


def _build_message_link(target: TargetDialog, message: Message) -> Optional[str]:
    direct = getattr(message, "link", None)
    if direct:
        return direct

    entity = target.entity
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message.id}"

    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        chat_id = getattr(entity, "id", None)
    if chat_id is None:
        peer = getattr(message, "peer_id", None)
        if peer is not None:
            chat_id = (
                getattr(peer, "channel_id", None)
                or getattr(peer, "chat_id", None)
                or getattr(peer, "user_id", None)
            )
    if chat_id is None:
        return None

    try:
        chat_id_int = abs(int(chat_id))
    except (TypeError, ValueError):
        return None
    if chat_id_int == 0:
        return None

    return f"https://t.me/c/{chat_id_int}/{message.id}"


def _render_template(template: str, match: MatchResult) -> str:
    context = {
        "author": match.author,
        "keyword": match.matched_keywords[0] if match.matched_keywords else "",
        "keywords": ", ".join(match.matched_keywords),
        "channel": match.target_title,
        "text": match.text,
    }
    try:
        return template.format(**context)
    except Exception:
        return template


async def _dispatch_status(callback: StatusCallback, message: str) -> None:
    if not callback:
        return
    try:
        result = callback(message)
        if inspect.isawaitable(result):
            await result
    except Exception:  # pragma: no cover
        log.exception("Ошибка обработчика статуса: %s", message)

async def _notify_client_ready(
    callback: Optional[
        Callable[[Optional[TelegramClient], Sequence[TargetDialog]], Awaitable[None] | None]
    ],
    client: Optional[TelegramClient],
    targets: Sequence[TargetDialog],
) -> None:
    if not callback:
        return
    try:
        result = callback(client, targets)
        if inspect.isawaitable(result):
            await result
    except Exception:  # pragma: no cover
        log.exception("�� ������� ���������� ���������� �� ������������� �������.")




async def _dispatch_error(callback: ErrorCallback, message: str) -> None:
    if not callback:
        return
    try:
        result = callback(message)
        if inspect.isawaitable(result):
            await result
    except Exception:  # pragma: no cover
        log.exception("Ошибка обработчика журнала ошибок: %s", message)


async def _dispatch_callback(callback: MatchCallback, result: MatchResult) -> None:
    if not callback:
        return
    try:
        maybe = callback(result)
        if inspect.isawaitable(maybe):
            await maybe
    except Exception:  # pragma: no cover
        log.exception("Ошибка обработчика совпадений.")



def _sanitize_username_hint(text: str, mapping: dict[str, str]) -> tuple[str, bool]:
    changed = False
    for handle, replacement in mapping.items():
        if handle in text:
            text = text.replace(handle, replacement)
            changed = True
    return text, changed


def _schedule_username_guard(
    client: TelegramClient,
    entity: Channel | Chat,
    reply_to_id: int,
    sent_message_id: int,
    original_text: str,
    guard_delay: float,
    mapping: dict[str, str],
    on_status: StatusCallback,
) -> None:
    if guard_delay <= 0:
        return
    if not mapping:
        return
    if not any(handle in original_text for handle in mapping):
        return
    asyncio.create_task(
        _ensure_reply_visibility(
            client,
            entity,
            reply_to_id,
            sent_message_id,
            original_text,
            guard_delay,
            mapping,
            on_status,
        )
    )


async def _ensure_reply_visibility(
    client: TelegramClient,
    entity: Channel | Chat,
    reply_to_id: int,
    sent_message_id: int,
    original_text: str,
    guard_delay: float,
    mapping: dict[str, str],
    on_status: StatusCallback,
) -> None:
    await asyncio.sleep(guard_delay)
    try:
        existing = await client.get_messages(entity, ids=sent_message_id)
    except Exception:
        existing = None

    missing = False
    if isinstance(existing, list):
        missing = not existing or existing[0] is None
    else:
        missing = existing is None

    if not missing:
        return

    fallback_text, changed = _sanitize_username_hint(original_text, mapping)
    if not changed:
        return

    await _dispatch_status(
        on_status,
        "Антиспам удалил сообщение с упоминанием — отправляю вариант без @.",
    )
    try:
        await client.send_message(
            entity,
            fallback_text,
            reply_to=reply_to_id,
        )
    except Exception:
        log.exception("Не удалось отправить резервный автоответ без упоминания.")

async def _discover_targets(
    app_cfg: AppConfig,
    *,
    code_callback: CodeCallback = None,
    password_callback: PasswordCallback = None,
    status_callback: StatusCallback = None,
    client: Optional[TelegramClient] = None,
) -> List[TargetDialog]:
    owns_client = client is None
    if owns_client:
        client = create_client(app_cfg)
    await ensure_authorized(
        client,
        app_cfg,
        code_callback=code_callback,
        password_callback=password_callback,
        status_callback=on_status,
    )

    notified_client = False
    try:
        if client_ready:
            await _notify_client_ready(client_ready, client, tuple(targets))
            notified_client = True

        target_map = {target.id: target for target in targets}
        chat_entities = [target.entity for target in targets]
        last_ids: Dict[int, int] = {}

        async with client:
            for target in targets:
                last_ids[target.id] = 0
                try:
                    async for message in client.iter_messages(target.entity, limit=1):
                        last_ids[target.id] = message.id
                        break
                except Exception as exc:
                    log.warning("�� 㤠���� ������� ��᫥���� ᮮ�饭�� ��� %s: %s", target.title, exc)

            @client.on(events.NewMessage(chats=chat_entities))
            async def _handler(event: events.NewMessage.Event) -> None:
                chat_id = getattr(event.message, "chat_id", None)
                target = target_map.get(chat_id)
                if target is None:
                    return
                try:
                    is_new = await _handle_incoming_message(
                        client,
                        target,
                        event.message,
                        chat_title=target.title,
                        keywords=keywords,
                        case_sensitive=case_sensitive,
                        storage=storage,
                        should_reply_default=should_reply_default,
                        templates=templates,
                        template_random=template_random,
                        default_reply=default_reply,
                        guard_mapping=guard_mapping,
                        guard_delay=guard_delay,
                        runtime_auto_reply=runtime_auto_reply,
                        on_status=on_status,
                        on_match=on_match,
                        error_callback=error_callback,
                    )
                    last_ids[target.id] = max(last_ids.get(target.id, 0), event.message.id)
                    if is_new:
                        await _dispatch_status(on_status, f"����� ᮢ������� �� {target.title}")
                except Exception as exc:
                    log.exception("�訡�� ��ࠡ�⪨ �室�饣� ᮮ�饭��.")
                    await _dispatch_error(error_callback, str(exc))

            async def _poll_loop() -> None:
                poll_limit = max(20, app_cfg.monitor.search_depth // 5 or 1)
                while True:
                    cycle_new = 0
                    for target in targets:
                        last_id = last_ids.get(target.id, 0)
                        new_messages: List[Message] = []
                        try:
                            async for message in client.iter_messages(
                                target.entity,
                                offset_id=last_id,
                                limit=poll_limit,
                            ):
                                if message.id <= last_id:
                                    break
                                new_messages.append(message)
                        except Exception as exc:
                            log.warning("�� 㤠���� ������� ᮮ�饭�� ��� %s: %s", target.title, exc)
                            continue

                        for message in reversed(new_messages):
                            try:
                                is_new = await _handle_incoming_message(
                                    client,
                                    target,
                                    message,
                                    chat_title=target.title,
                                    keywords=keywords,
                                    case_sensitive=case_sensitive,
                                    storage=storage,
                                    should_reply_default=should_reply_default,
                                    templates=templates,
                                    template_random=template_random,
                                    default_reply=default_reply,
                                    guard_mapping=guard_mapping,
                                    guard_delay=guard_delay,
                                    runtime_auto_reply=runtime_auto_reply,
                                    on_status=on_status,
                                    on_match=on_match,
                                    error_callback=error_callback,
                                )
                                if is_new:
                                    cycle_new += 1
                                last_ids[target.id] = max(last_ids.get(target.id, 0), message.id)
                            except Exception as exc:
                                log.exception("�訡�� ��ࠡ�⪨ ᮮ�饭�� �� ����.")
                                await _dispatch_error(error_callback, str(exc))

                    if cycle_new:
                        await _dispatch_status(on_status, f"����� ᮢ�������: {cycle_new}")

                    if stop_event and stop_event.is_set():
                        break

                    if stop_event is not None:
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=fetch_interval)
                            break
                        except asyncio.TimeoutError:
                            continue
                    else:
                        await asyncio.sleep(fetch_interval)

        await _poll_loop()
    finally:
        if client_ready and notified_client:
            await _notify_client_ready(client_ready, None, tuple())



def run_scan(config_path: str | None, limit: int | None) -> None:
    """Command-line wrapper for scan mode."""
    app_cfg = load_config(config_path)

    def _prompt_code(prompt: str) -> str:
        return input(prompt).strip()

    def _prompt_password(prompt: str) -> str:
        return getpass(prompt)

    async def _runner() -> None:
        matches = await scan_history(
            app_cfg,
            limit=limit,
            code_callback=_prompt_code,
            password_callback=_prompt_password,
        )
        if matches:
            table = Table(title=f"Совпадения (limit={limit or app_cfg.monitor.search_depth})")
            table.add_column("Канал/чат", style="cyan", no_wrap=True)
            table.add_column("Время", style="green", no_wrap=True)
            table.add_column("Автор", style="magenta", no_wrap=True)
            table.add_column("Сообщение")
            table.add_column("Ссылка", style="blue", no_wrap=True)
            for match in matches:
                table.add_row(
                    match.target_title,
                    _format_timestamp(match.timestamp),
                    match.author,
                    _highlight(
                        match.text,
                        match.matched_keywords,
                        case_sensitive=app_cfg.monitor.case_sensitive,
                    ),
                    match.link or "",
                )
            console.print(table)
        else:
            console.print("[green]Совпадений не найдено.[/green]")

    asyncio.run(_runner())


def run_watch(config_path: str | None, *, auto_join: bool = False) -> None:
    """Command-line wrapper for watch mode."""
    app_cfg = load_config(config_path)

    async def _printer(match: MatchResult) -> None:
        console.rule(title=f"{match.target_title} — {_format_timestamp(match.timestamp)}")
        console.print(f"[magenta]{match.author}[/magenta]")
        console.print(
            _highlight(
                match.text,
                match.matched_keywords,
                case_sensitive=app_cfg.monitor.case_sensitive,
            )
        )
        if match.link:
            console.print(f"[blue]{match.link}[/blue]")

    async def _runner() -> None:
        try:
            await watch_stream(
                app_cfg,
                on_match=_printer,
                code_callback=lambda prompt: input(prompt).strip(),
                password_callback=lambda prompt: getpass(prompt),
                auto_join=auto_join,
            )
        except asyncio.CancelledError:  # pragma: no cover
            pass

    try:
        asyncio.run(_runner())
    except KeyboardInterrupt:  # pragma: no cover
        console.print("\n[cyan]Мониторинг остановлен пользователем.[/cyan]")

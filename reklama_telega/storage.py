"""Persistent storage for matches using SQLite."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:  # pragma: no cover
    from .monitor import MatchResult


@dataclass(slots=True)
class StoredMatch:
    chat_id: int
    message_id: int
    chat_title: str
    timestamp: Optional[datetime]
    author: str
    text: str
    keywords: List[str]
    link: Optional[str]
    is_new: bool


class MatchStorage:
    """Async SQLite storage for match results."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open database connection and ensure schema."""
        if self._conn is not None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA busy_timeout = 30000;")
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                chat_title TEXT NOT NULL,
                timestamp TEXT,
                author TEXT,
                text TEXT,
                keywords TEXT,
                link TEXT,
                seen INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, message_id)
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_matches_seen ON matches(seen)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_matches_timestamp ON matches(timestamp DESC)"
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def save_match(self, match: "MatchResult") -> bool:
        """Save match to storage. Returns True if inserted as new."""
        if self._conn is None:
            raise RuntimeError("Storage is not opened.")

        timestamp = match.timestamp.isoformat() if match.timestamp else None
        keywords_json = json.dumps(match.matched_keywords, ensure_ascii=False)
        params = (
            match.chat_id,
            match.message_id,
            match.target_title,
            timestamp,
            match.author,
            match.text,
            keywords_json,
            match.link,
        )
        async with self._lock:
            cursor = await self._conn.execute(
                """
                INSERT OR IGNORE INTO matches
                    (chat_id, message_id, chat_title, timestamp, author, text, keywords, link, seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                params,
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                await self._conn.execute(
                    """
                    UPDATE matches
                       SET chat_title = ?,
                           timestamp = ?,
                           author = ?,
                           text = ?,
                           keywords = ?,
                           link = ?
                     WHERE chat_id = ? AND message_id = ?
                    """,
                    (
                        match.target_title,
                        timestamp,
                        match.author,
                        match.text,
                        keywords_json,
                        match.link,
                        match.chat_id,
                        match.message_id,
                    ),
                )
            await self._conn.commit()
        return inserted

    async def fetch_match(self, chat_id: int, message_id: int) -> Optional[StoredMatch]:
        """Retrieve a single match by identifiers."""
        if self._conn is None:
            raise RuntimeError("Storage is not opened.")

        async with self._lock:
            cursor = await self._conn.execute(
                """
                SELECT chat_id, message_id, chat_title, timestamp, author, text, keywords, link, seen
                  FROM matches
                 WHERE chat_id = ? AND message_id = ?
                """,
                (chat_id, message_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None

        (
            chat_id,
            message_id,
            chat_title,
            timestamp_str,
            author,
            text,
            keywords_json,
            link,
            seen,
        ) = row

        timestamp = None
        if timestamp_str:
            try:
                timestamp = datetime.fromisoformat(timestamp_str)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                else:
                    timestamp = timestamp.astimezone(timezone.utc)
            except ValueError:
                timestamp = None

        try:
            keywords = json.loads(keywords_json) if keywords_json else []
        except json.JSONDecodeError:
            keywords = []

        return StoredMatch(
            chat_id=chat_id,
            message_id=message_id,
            chat_title=chat_title,
            timestamp=timestamp,
            author=author or "",
            text=text or "",
            keywords=list(keywords),
            link=link,
            is_new=not bool(seen),
        )

    async def fetch_matches(
        self,
        *,
        channel: Optional[str] = None,
        author: Optional[str] = None,
        keyword: Optional[str] = None,
        only_new: bool = False,
    ) -> List[StoredMatch]:
        """Retrieve matches applying optional filters."""
        if self._conn is None:
            raise RuntimeError("Storage is not opened.")

        where: List[str] = []
        params: List[str] = []

        if channel:
            where.append("chat_title LIKE ?")
            params.append(f"%{channel}%")
        if author:
            where.append("author LIKE ?")
            params.append(f"%{author}%")
        if keyword:
            where.append("keywords LIKE ?")
            params.append(f"%{keyword}%")
        if only_new:
            where.append("seen = 0")

        sql = (
            "SELECT chat_id, message_id, chat_title, timestamp, author, text, keywords, link, seen "
            "FROM matches"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(timestamp, created_at) DESC, id DESC"

        async with self._lock:
            cursor = await self._conn.execute(sql, params)
            rows = await cursor.fetchall()
        results: List[StoredMatch] = []
        for row in rows:
            chat_id, message_id, chat_title, timestamp_str, author, text, keywords_json, link, seen = row
            timestamp = None
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str)
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    else:
                        timestamp = timestamp.astimezone(timezone.utc)
                except ValueError:
                    timestamp = None
            try:
                keywords = json.loads(keywords_json) if keywords_json else []
            except json.JSONDecodeError:
                keywords = []
            results.append(
                StoredMatch(
                    chat_id=chat_id,
                    message_id=message_id,
                    chat_title=chat_title,
                    timestamp=timestamp,
                    author=author or "",
                    text=text or "",
                    keywords=list(keywords),
                    link=link,
                    is_new=not bool(seen),
                )
            )
        return results

    async def mark_seen(self, keys: Sequence[Tuple[int, int]]) -> None:
        if self._conn is None or not keys:
            return
        async with self._lock:
            await self._conn.executemany(
                "UPDATE matches SET seen = 1 WHERE chat_id = ? AND message_id = ?",
                keys,
            )
            await self._conn.commit()

    async def mark_all_seen(self) -> None:
        if self._conn is None:
            return
        async with self._lock:
            await self._conn.execute("UPDATE matches SET seen = 1 WHERE seen = 0")
            await self._conn.commit()

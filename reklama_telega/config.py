"""Configuration helpers for reklama-telega."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - fallback for older interpreters
    import tomli as tomllib  # type: ignore[no-redef, import-not-found]


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


@dataclass(slots=True)
class TelegramConfig:
    api_id: int
    api_hash: str
    session_name: str = "reklama-telega"
    phone: Optional[str] = None
    force_sms: bool = False


@dataclass(slots=True)
class MonitorConfig:
    keywords: List[str] = field(default_factory=list)
    channels: List[str] = field(default_factory=list)
    search_depth: int = 500
    case_sensitive: bool = False
    highlight: bool = True
    fetch_interval_seconds: float = 2.0
    history_request_timeout: float = 10.0
    auto_reply_enabled: bool = False
    auto_reply_message: str = ""
    auto_reply_templates: List[str] = field(default_factory=list)
    auto_reply_randomize: bool = True
    username_guard_enabled: bool = True
    username_guard_delay: float = 3.0
    username_guard_replacements: List[str] = field(default_factory=list)

    def normalized_keywords(self) -> List[str]:
        """Return deduplicated keywords with stripped whitespace."""
        seen: set[str] = set()
        result: List[str] = []
        for raw in self.keywords:
            key = raw if self.case_sensitive else raw.lower()
            key = key.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(key)
        return result

    def username_guard_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for raw in self.username_guard_replacements:
            if not raw:
                continue
            if "=>" not in raw:
                continue
            original, replacement = raw.split("=>", 1)
            original = original.strip()
            replacement = replacement.strip()
            if not original or not replacement:
                continue
            mapping[original] = replacement
        return mapping


@dataclass(slots=True)
class AppConfig:
    telegram: TelegramConfig
    monitor: MonitorConfig

    @property
    def session_file(self) -> Path:
        return Path(f"{self.telegram.session_name}.session")


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load configuration file and return structured instance."""
    cfg_path = Path(path) if path else Path("config.toml")
    if not cfg_path.exists():
        raise ConfigError(
            f"Configuration file not found: {cfg_path}. "
            "Copy config.example.toml to config.toml and fill in your data."
        )

    with cfg_path.open("rb") as f:
        raw = tomllib.load(f)

    try:
        telegram_raw = raw["telegram"]
        monitor_raw = raw["monitor"]
    except KeyError as exc:  # pragma: no cover
        raise ConfigError(f"Missing section {exc.args[0]!r} in {cfg_path}") from exc

    telegram = TelegramConfig(
        api_id=_get_required_int(telegram_raw, "api_id", "telegram"),
        api_hash=_get_required_str(telegram_raw, "api_hash", "telegram"),
        session_name=str(telegram_raw.get("session_name", "reklama-telega")),
        phone=_get_optional_str(telegram_raw, "phone"),
        force_sms=bool(telegram_raw.get("force_sms", False)),
    )

    monitor = MonitorConfig(
        keywords=_ensure_str_list(monitor_raw.get("keywords", []), "monitor.keywords"),
        channels=_ensure_str_list(monitor_raw.get("channels", []), "monitor.channels"),
        search_depth=int(monitor_raw.get("search_depth", 500)),
        case_sensitive=bool(monitor_raw.get("case_sensitive", False)),
        highlight=bool(monitor_raw.get("highlight", True)),
        fetch_interval_seconds=float(monitor_raw.get("fetch_interval_seconds", 2.0)),
        history_request_timeout=float(monitor_raw.get("history_request_timeout", 10.0)),
        auto_reply_enabled=bool(monitor_raw.get("auto_reply_enabled", False)),
        auto_reply_message=str(monitor_raw.get("auto_reply_message", "")).strip(),
        auto_reply_templates=_ensure_str_list(
            monitor_raw.get("auto_reply_templates", []), "monitor.auto_reply_templates"
        ),
        auto_reply_randomize=bool(monitor_raw.get("auto_reply_randomize", True)),
        username_guard_enabled=bool(monitor_raw.get("username_guard_enabled", True)),
        username_guard_delay=float(monitor_raw.get("username_guard_delay", 3.0)),
        username_guard_replacements=_ensure_str_list(
            monitor_raw.get("username_guard_replacements", []),
            "monitor.username_guard_replacements",
        ),
    )

    if not monitor.keywords:
        raise ConfigError("monitor.keywords must contain at least one entry.")
    if not monitor.channels:
        raise ConfigError("monitor.channels must contain at least one channel or chat.")

    return AppConfig(telegram=telegram, monitor=monitor)


def _get_required_int(data: dict, key: str, section: str) -> int:
    try:
        value = int(data[key])
    except KeyError as exc:
        raise ConfigError(f"Missing field {key!r} in {section}.") from exc
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Field {key!r} in {section} must be an integer.") from exc
    return value


def _get_required_str(data: dict, key: str, section: str) -> str:
    try:
        value = str(data[key]).strip()
    except KeyError as exc:
        raise ConfigError(f"Missing field {key!r} in {section}.") from exc
    if not value:
        raise ConfigError(f"Field {key!r} in {section} cannot be empty.")
    return value


def _get_optional_str(data: dict, key: str) -> Optional[str]:
    value = data.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _ensure_str_list(value: Iterable[str], field_name: str) -> List[str]:
    try:
        items = [str(item).strip() for item in value]
    except TypeError as exc:
        raise ConfigError(f"{field_name} must be a list of strings.") from exc
    return [item for item in items if item]

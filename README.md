## reklama-telega

Приложение на Python для мониторинга комментариев в публичных Telegram-каналах. Использует Telethon (MTProto), хранит историю в локальной базе SQLite и предоставляет графический интерфейс с фильтрами, автоответами и журналами.

### Основные возможности
- **CLI** (`reklama-telega scan|watch`) с флагом `--auto-join`, который при необходимости подписывает аккаунт на перечисленные каналы перед запуском мониторинга.
- **GUI** (`reklama-telega gui`) с удобным редактированием `config.toml`, фильтрами (канал, автор, ключевое слово, режим «только новые»), подсветкой непрочитанных сообщений и журналом ошибок.
- **Автоответы** по шаблонам с плейсхолдерами `{author}`, `{keyword}`, `{keywords}`, `{channel}`, `{text}` и опцией случайного выбора, чтобы тексты не повторялись. Ответ отправляется как цитата к исходному комментарию.
- **Автоподписка и проверка доступа**: диалог при запуске мониторинга, вкладка со статусами подписки и кнопка «Подписаться на всё».
- **Логи**: отдельные вкладки для общих сообщений, Telethon DEBUG (включается чекбоксом) и журнала ошибок. Любое событие видно прямо в окне.

> ⚠️ Для работы необходимы `api_id`, `api_hash` и авторизация аккаунта Telegram (через SMS/код или пароль 2FA). Telegram позволяет просматривать только те каналы/обсуждения, к которым у аккаунта есть доступ.

---

### Быстрый старт
```powershell
git clone <repo>
cd reklama-telega
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item config.example.toml config.toml
```
Получите `api_id` и `api_hash` на [my.telegram.org](https://my.telegram.org). Перед запуском убедитесь, что аккаунт подписан на нужные каналы и вступил в их обсуждения.

---

### Запуск из командной строки
```powershell
reklama-telega scan --limit 1000        # разовый поиск по истории
reklama-telega watch --auto-join -v     # live-мониторинг, автоподписка, подробные логи
```

### Графический интерфейс
```powershell
reklama-telega gui
# либо с параметрами
reklama-telega-gui --config path\to\config.toml --log-dir logs
```

GUI позволяет:
- загружать/сохранять `config.toml`, редактировать ключевые слова, каналы и автоответы;
- просматривать историю совпадений (хранится в `matches.db`), фильтровать и помечать записи как прочитанные;
- проверять доступ к каналам и подписываться на всё одним кликом;
- отслеживать общие сообщения, детальные логи Telethon и ошибки на отдельных вкладках.

---

### Настройки `config.toml`
- `telegram.api_id`, `telegram.api_hash`, `telegram.phone` — данные MTProto;
- `monitor.keywords`, `monitor.channels`, `monitor.search_depth`, `monitor.case_sensitive`, `monitor.highlight`;
- `monitor.auto_reply_enabled`, `monitor.auto_reply_message`, `monitor.auto_reply_templates`, `monitor.auto_reply_randomize`;
- `monitor.fetch_interval_seconds`, `monitor.history_request_timeout`.

Config-файл можно редактировать вручную или через GUI.

---

### Сборка `.exe` (Windows)
```powershell
pip install pyinstaller
pyinstaller --name reklama-telega --noconsole --onefile ^
  --add-data "config.example.toml;." ^
  -m reklama_telega.gui.app
```
В каталоге `dist/` появится `reklama-telega.exe`. Для корректной работы рядом с ним должны лежать `config.toml`, база `matches.db` и файл сессии Telethon (`*.session`). Для CLI-версии: `pyinstaller --name reklama-telega-cli --onefile -m reklama_telega.cli`.

---

### Дальнейшие планы
- Экспорт истории в CSV/Excel или Google Sheets.
- Дополнительные каналы уведомлений (почта, вебхуки, отдельный бот).
- Профили для нескольких аккаунтов и фоновый режим в системном трее.

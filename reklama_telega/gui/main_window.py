"""Главное окно графического интерфейса reklama-telega."""

from __future__ import annotations

import asyncio
import json
import logging
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from qasync import asyncSlot
from reklama_telega.config import (
    AppConfig,
    ConfigError,
    MonitorConfig,
    TelegramConfig,
    load_config,
)
from reklama_telega.gui.logging_utils import QtLogHandler
from reklama_telega.gui.monitor_runner import MonitorRunner
from reklama_telega.monitor import MatchResult
from reklama_telega.storage import MatchStorage, StoredMatch

log = logging.getLogger(__name__)


def _format_timestamp(value: Optional[datetime]) -> str:
    if not value:
        return ""
    return value.strftime("%d.%m %H:%M")


def _split_lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


class MainWindow(QtWidgets.QMainWindow):
    """Главное окно приложения."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        config_path: Optional[Path] = None,
        storage_path: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("reklama-telega — монитор комментариев Telegram")
        self.resize(1280, 820)

        self._loop = loop
        self._config_path = Path(config_path) if config_path else Path("config.toml")
        self._storage_path = Path(storage_path) if storage_path else Path("matches.db")
        self._storage = MatchStorage(self._storage_path)
        self._runner = MonitorRunner(self._storage)

        self._row_keys: List[Tuple[int, int]] = []
        self._current_config: Optional[AppConfig] = None
        self._general_handler: Optional[QtLogHandler] = None
        self._telethon_handler: Optional[QtLogHandler] = None
        self._error_handler: Optional[QtLogHandler] = None
        self._telethon_logger = logging.getLogger("telethon")
        self._watch_monitor_task: Optional[asyncio.Task[None]] = None
        self._scan_task: Optional[asyncio.Task[None]] = None
        self._manual_reply_task: Optional[asyncio.Task[None]] = None

        self._setup_ui()
        self._setup_logging()
        self._connect_signals()
        self._update_controls_state()

        self._loop.create_task(self._init_storage())

        self.config_path_edit.setText(str(self._config_path))
        if self._config_path.exists():
            self._load_config_file(self._config_path)

    # ------------------------------------------------------------------ инициализация
    def _setup_ui(self) -> None:
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.setCentralWidget(splitter)

        tab_widget = QtWidgets.QTabWidget()
        splitter.addWidget(tab_widget)

        # Вкладка настроек
        settings_tab = QtWidgets.QWidget()
        settings_layout = QtWidgets.QVBoxLayout(settings_tab)
        settings_layout.addWidget(self._build_config_section())
        settings_layout.addStretch(1)
        tab_widget.addTab(settings_tab, "Настройки")

        # Вкладка мониторинга
        monitor_tab = QtWidgets.QWidget()
        monitor_layout = QtWidgets.QVBoxLayout(monitor_tab)

        controls_widget = QtWidgets.QWidget()
        controls_layout = QtWidgets.QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(monitor_layout.spacing())
        controls_layout.addWidget(self._build_runtime_section())
        controls_layout.addWidget(self._build_channel_status_section())
        controls_layout.addWidget(self._build_filters_section())
        controls_layout.addStretch(1)

        matches_widget = self._build_matches_section()

        monitor_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        monitor_splitter.addWidget(controls_widget)
        monitor_splitter.addWidget(matches_widget)
        monitor_splitter.setStretchFactor(0, 0)
        monitor_splitter.setStretchFactor(1, 1)
        monitor_splitter.setCollapsible(0, False)
        monitor_splitter.setCollapsible(1, False)

        monitor_layout.addWidget(monitor_splitter)
        tab_widget.addTab(monitor_tab, "Мониторинг")

        bottom_widget = self._build_log_section()
        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self.statusBar().showMessage("Готово.")

    def _build_config_section(self) -> QtWidgets.QWidget:
        group = QtWidgets.QGroupBox("Настройки подключения и поиска")
        layout = QtWidgets.QGridLayout(group)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

        row = 0
        layout.addWidget(QtWidgets.QLabel("Файл конфигурации"), row, 0)
        self.config_path_edit = QtWidgets.QLineEdit()
        self.config_path_edit.setPlaceholderText("config.toml")
        layout.addWidget(self.config_path_edit, row, 1, 1, 3)
        self.browse_button = QtWidgets.QPushButton("Обзор…")
        layout.addWidget(self.browse_button, row, 4)
        row += 1

        layout.addWidget(QtWidgets.QLabel("API ID"), row, 0)
        self.api_id_edit = QtWidgets.QLineEdit()
        self.api_id_edit.setPlaceholderText("123456")
        layout.addWidget(self.api_id_edit, row, 1)

        layout.addWidget(QtWidgets.QLabel("API hash"), row, 2)
        self.api_hash_edit = QtWidgets.QLineEdit()
        self.api_hash_edit.setPlaceholderText("0123456789abcdef0123456789abcdef")
        layout.addWidget(self.api_hash_edit, row, 3, 1, 2)
        row += 1

        layout.addWidget(QtWidgets.QLabel("Имя сессии"), row, 0)
        self.session_edit = QtWidgets.QLineEdit("reklama-telega")
        layout.addWidget(self.session_edit, row, 1)

        layout.addWidget(QtWidgets.QLabel("Телефон"), row, 2)
        self.phone_edit = QtWidgets.QLineEdit()
        self.phone_edit.setPlaceholderText("+79991234567")
        layout.addWidget(self.phone_edit, row, 3)
        self.force_sms_checkbox = QtWidgets.QCheckBox("Требовать SMS")
        layout.addWidget(self.force_sms_checkbox, row, 4)
        row += 1

        layout.addWidget(QtWidgets.QLabel("Ключевые слова"), row, 0)
        self.keywords_edit = QtWidgets.QPlainTextEdit()
        self.keywords_edit.setPlaceholderText("одно слово или фраза на строку")
        layout.addWidget(self.keywords_edit, row, 1, 2, 2)

        layout.addWidget(QtWidgets.QLabel("Каналы / обсуждения"), row, 3)
        self.channels_edit = QtWidgets.QPlainTextEdit()
        self.channels_edit.setPlaceholderText("@channel или https://t.me/...")
        layout.addWidget(self.channels_edit, row, 4, 2, 1)
        row += 2

        self.case_checkbox = QtWidgets.QCheckBox("Учитывать регистр")
        layout.addWidget(self.case_checkbox, row, 0)
        self.highlight_checkbox = QtWidgets.QCheckBox("Подсвечивать совпадения (CLI)")
        self.highlight_checkbox.setChecked(True)
        layout.addWidget(self.highlight_checkbox, row, 1)
        row += 1

        self.auto_reply_checkbox = QtWidgets.QCheckBox("Включить автоответ при совпадении")
        layout.addWidget(self.auto_reply_checkbox, row, 0, 1, 2)
        row += 1

        layout.addWidget(QtWidgets.QLabel("Основной автоответ"), row, 0)
        self.auto_reply_edit = QtWidgets.QPlainTextEdit()
        self.auto_reply_edit.setPlaceholderText("Привет! Мы видим ваш запрос...")
        layout.addWidget(self.auto_reply_edit, row, 1, 1, 4)
        row += 1

        layout.addWidget(QtWidgets.QLabel("Шаблоны автоответов"), row, 0)
        self.auto_reply_templates_edit = QtWidgets.QPlainTextEdit()
        self.auto_reply_templates_edit.setPlaceholderText(
            "Каждый шаблон на новой строке.\nДоступные плейсхолдеры: {author}, {keyword}, {keywords}, {channel}, {text}."
        )
        layout.addWidget(self.auto_reply_templates_edit, row, 1, 2, 4)
        row += 2

        self.auto_reply_random_checkbox = QtWidgets.QCheckBox("Случайный выбор шаблона")
        self.auto_reply_random_checkbox.setChecked(True)
        layout.addWidget(self.auto_reply_random_checkbox, row, 1)
        row += 1

        guard_group = QtWidgets.QGroupBox("Защита от антиспама")
        guard_layout = QtWidgets.QGridLayout(guard_group)
        guard_layout.setColumnStretch(1, 1)

        self.username_guard_checkbox = QtWidgets.QCheckBox("Проверять удаление автоответа")
        guard_layout.addWidget(self.username_guard_checkbox, 0, 0, 1, 2)

        guard_layout.addWidget(QtWidgets.QLabel("Задержка проверки, сек"), 1, 0)
        self.username_guard_delay_spin = QtWidgets.QDoubleSpinBox()
        self.username_guard_delay_spin.setDecimals(1)
        self.username_guard_delay_spin.setRange(0.5, 10.0)
        self.username_guard_delay_spin.setSingleStep(0.5)
        self.username_guard_delay_spin.setValue(3.0)
        guard_layout.addWidget(self.username_guard_delay_spin, 1, 1)

        guard_layout.addWidget(QtWidgets.QLabel('Сопоставления "@user => текст"'), 2, 0)
        self.username_guard_edit = QtWidgets.QPlainTextEdit()
        self.username_guard_edit.setPlaceholderText("@Freed0mNETbot => Freed0mNETbot (введите в поиске Telegram Freed0mNETbot)")
        guard_layout.addWidget(self.username_guard_edit, 2, 0, 1, 2)

        layout.addWidget(guard_group, row, 0, 1, 5)
        row += 1

        button_layout = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("Загрузить")
        self.save_button = QtWidgets.QPushButton("Сохранить")
        button_layout.addWidget(self.load_button)
        button_layout.addWidget(self.save_button)
        layout.addLayout(button_layout, row, 0, 1, 5)

        return group

    def _build_runtime_section(self) -> QtWidgets.QWidget:
        group = QtWidgets.QGroupBox("Управление мониторингом")
        layout = QtWidgets.QGridLayout(group)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

        row = 0
        layout.addWidget(QtWidgets.QLabel("Глубина истории (scan)"), row, 0)
        self.search_spin = QtWidgets.QSpinBox()
        self.search_spin.setRange(1, 100000)
        self.search_spin.setValue(500)
        layout.addWidget(self.search_spin, row, 1)

        layout.addWidget(QtWidgets.QLabel("Предел scan (0 = из настроек)"), row, 2)
        self.limit_spin = QtWidgets.QSpinBox()
        self.limit_spin.setRange(0, 100000)
        layout.addWidget(self.limit_spin, row, 3)
        row += 1

        layout.addWidget(QtWidgets.QLabel("Интервал опроса, сек"), row, 0)
        self.fetch_spin = QtWidgets.QDoubleSpinBox()
        self.fetch_spin.setDecimals(1)
        self.fetch_spin.setRange(0.1, 120.0)
        self.fetch_spin.setSingleStep(0.1)
        self.fetch_spin.setValue(2.0)
        layout.addWidget(self.fetch_spin, row, 1)

        layout.addWidget(QtWidgets.QLabel("Таймаут получения, сек"), row, 2)
        self.timeout_spin = QtWidgets.QDoubleSpinBox()
        self.timeout_spin.setDecimals(1)
        self.timeout_spin.setRange(1.0, 300.0)
        self.timeout_spin.setSingleStep(1.0)
        self.timeout_spin.setValue(10.0)
        layout.addWidget(self.timeout_spin, row, 3)
        row += 1

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch(1)
        self.scan_button = QtWidgets.QPushButton("Сканировать историю")
        self.watch_button = QtWidgets.QPushButton("Старт мониторинга")
        self.stop_button = QtWidgets.QPushButton("Стоп")
        button_layout.addWidget(self.scan_button)
        button_layout.addWidget(self.watch_button)
        button_layout.addWidget(self.stop_button)
        layout.addLayout(button_layout, row, 0, 1, 4)

        return group

    def _build_channel_status_section(self) -> QtWidgets.QWidget:
        group = QtWidgets.QGroupBox("Мониторинг и статусы")
        layout = QtWidgets.QVBoxLayout(group)

        controls = QtWidgets.QHBoxLayout()
        self.auto_join_checkbox = QtWidgets.QCheckBox("Автовступление перед мониторингом")
        controls.addWidget(self.auto_join_checkbox)
        self.runtime_auto_reply_checkbox = QtWidgets.QCheckBox("Автоответы во время мониторинга")
        controls.addWidget(self.runtime_auto_reply_checkbox)
        controls.addStretch(1)
        self.clear_status_button = QtWidgets.QPushButton("Очистить")
        controls.addWidget(self.clear_status_button)
        layout.addLayout(controls)

        self.status_list = QtWidgets.QListWidget()
        self.status_list.setAlternatingRowColors(True)
        self.status_list.setObjectName("statusList")
        layout.addWidget(self.status_list)

        return group

    def _build_filters_section(self) -> QtWidgets.QWidget:
        group = QtWidgets.QGroupBox("Фильтр совпадений")
        layout = QtWidgets.QGridLayout(group)
        layout.setColumnStretch(1, 1)

        layout.addWidget(QtWidgets.QLabel("Канал"), 0, 0)
        self.filter_channel_edit = QtWidgets.QLineEdit()
        layout.addWidget(self.filter_channel_edit, 0, 1)
        self.filter_only_new_checkbox = QtWidgets.QCheckBox("Только новые")
        layout.addWidget(self.filter_only_new_checkbox, 0, 2)

        layout.addWidget(QtWidgets.QLabel("Автор"), 1, 0)
        self.filter_author_edit = QtWidgets.QLineEdit()
        layout.addWidget(self.filter_author_edit, 1, 1)

        layout.addWidget(QtWidgets.QLabel("Ключевое слово"), 2, 0)
        self.filter_keyword_edit = QtWidgets.QLineEdit()
        layout.addWidget(self.filter_keyword_edit, 2, 1)

        button_layout = QtWidgets.QHBoxLayout()
        self.apply_filter_button = QtWidgets.QPushButton("Применить фильтр")
        self.reset_filter_button = QtWidgets.QPushButton("Сбросить")
        self.refresh_matches_button = QtWidgets.QPushButton("Обновить список")
        self.mark_seen_button = QtWidgets.QPushButton("Отметить выбранные как просмотренные")
        self.mark_all_seen_button = QtWidgets.QPushButton("Отметить всё как просмотренное")
        button_layout.addWidget(self.apply_filter_button)
        button_layout.addWidget(self.reset_filter_button)
        button_layout.addWidget(self.refresh_matches_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.mark_seen_button)
        button_layout.addWidget(self.mark_all_seen_button)
        layout.addLayout(button_layout, 3, 0, 1, 3)

        return group

    def _build_matches_section(self) -> QtWidgets.QWidget:
        group = QtWidgets.QGroupBox("Совпадения")
        layout = QtWidgets.QVBoxLayout(group)

        headers = [
            "Канал",
            "Время",
            "Автор",
            "Сообщение",
            "Ключевые слова",
            "Ссылка",
        ]
        self.matches_table = QtWidgets.QTableWidget(0, len(headers))
        self.matches_table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.matches_table.setHorizontalHeaderLabels(headers)
        self.matches_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.matches_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.matches_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matches_table.setAlternatingRowColors(True)
        self.matches_table.verticalHeader().setVisible(False)

        header = self.matches_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self.matches_table)
        return group

    def _build_log_section(self) -> QtWidgets.QWidget:
        group = QtWidgets.QGroupBox("Журналы")
        layout = QtWidgets.QVBoxLayout(group)

        self.log_tabs = QtWidgets.QTabWidget()
        self.general_log_edit = QtWidgets.QPlainTextEdit()
        self.general_log_edit.setReadOnly(True)
        self.telethon_log_edit = QtWidgets.QPlainTextEdit()
        self.telethon_log_edit.setReadOnly(True)
        self.error_log_edit = QtWidgets.QPlainTextEdit()
        self.error_log_edit.setReadOnly(True)

        self.log_tabs.addTab(self.general_log_edit, "Общий журнал")
        self.log_tabs.addTab(self.telethon_log_edit, "Telethon")
        self.log_tabs.addTab(self.error_log_edit, "Ошибки")
        layout.addWidget(self.log_tabs)

        return group

    def _setup_logging(self) -> None:
        formatter = logging.Formatter("%H:%M:%S [%(levelname)s] %(name)s: %(message)s")

        self._general_handler = QtLogHandler(level=logging.INFO, formatter=formatter)
        self._general_handler.connect(self._append_general_log)
        logging.getLogger().addHandler(self._general_handler)

        self._telethon_handler = QtLogHandler(level=logging.INFO, formatter=formatter)
        self._telethon_handler.connect(self._append_telethon_log)
        self._telethon_logger.addHandler(self._telethon_handler)
        self._telethon_logger.setLevel(logging.INFO)

        self._error_handler = QtLogHandler(level=logging.ERROR, formatter=formatter)
        self._error_handler.connect(self._append_error_log)
        logging.getLogger().addHandler(self._error_handler)

    def _connect_signals(self) -> None:
        self.browse_button.clicked.connect(self._on_browse_config)
        self.load_button.clicked.connect(self._on_load_clicked)
        self.save_button.clicked.connect(self._on_save_clicked)
        self.scan_button.clicked.connect(self._on_scan_clicked)
        self.watch_button.clicked.connect(self._on_watch_clicked)
        self.stop_button.clicked.connect(self._on_stop_clicked)

        self.clear_status_button.clicked.connect(self._on_clear_statuses)
        self.apply_filter_button.clicked.connect(self._on_apply_filters)
        self.reset_filter_button.clicked.connect(self._on_reset_filters)
        self.refresh_matches_button.clicked.connect(self._on_refresh_matches)
        self.mark_seen_button.clicked.connect(self._on_mark_seen_clicked)
        self.mark_all_seen_button.clicked.connect(self._on_mark_all_seen_clicked)
        self.auto_reply_checkbox.toggled.connect(self._on_autoreply_toggled)
        self.username_guard_checkbox.toggled.connect(self._on_username_guard_toggled)

        self.matches_table.itemSelectionChanged.connect(self._update_mark_seen_state)
        self.matches_table.cellDoubleClicked.connect(self._on_match_double_clicked)
        self.matches_table.customContextMenuRequested.connect(self._on_matches_context_menu)

        self._on_autoreply_toggled(self.auto_reply_checkbox.isChecked())
        self._on_username_guard_toggled(self.username_guard_checkbox.isChecked())

    async def _init_storage(self) -> None:
        try:
            await self._storage.open()
            await self._refresh_from_storage()
        except Exception as exc:  # pragma: no cover
            log.exception("Не удалось открыть хранилище совпадений.")
            QtWidgets.QMessageBox.critical(
                self,
                "Ошибка хранилища",
                f"Не удалось открыть базу совпадений:\n{exc}",
            )

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # pragma: no cover
        if self._watch_monitor_task:
            self._watch_monitor_task.cancel()
            self._watch_monitor_task = None
        if self._runner.watching:
            self._loop.create_task(self._runner.stop_watch())
        self._loop.create_task(self._storage.close())
        for handler in (self._general_handler, self._error_handler):
            if handler and handler in logging.getLogger().handlers:
                logging.getLogger().removeHandler(handler)
        if self._telethon_handler and self._telethon_handler in self._telethon_logger.handlers:
            self._telethon_logger.removeHandler(self._telethon_handler)
        super().closeEvent(event)

    def _on_browse_config(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Выбор config.toml",
            str(self._config_path.parent if self._config_path else Path.cwd()),
            "TOML (*.toml);;Все файлы (*.*)",
        )
        if path:
            self.config_path_edit.setText(path)
            self._config_path = Path(path)

    def _on_load_clicked(self) -> None:
        path = Path(self.config_path_edit.text().strip() or "config.toml")
        if not path.exists():
            QtWidgets.QMessageBox.warning(
                self,
                "Файл не найден",
                f"Указанный файл не существует:\n{path}",
            )
            return
        self._load_config_file(path)

    def _on_save_clicked(self) -> None:
        try:
            config = self._collect_config()
        except ConfigError as exc:
            self._show_error(str(exc))
            return

        path = Path(self.config_path_edit.text().strip() or "config.toml")
        try:
            self._save_config_to_file(config, path)
        except OSError as exc:
            self._handle_exception("Не удалось сохранить конфигурацию", exc)
            return

        self._config_path = path
        self.statusBar().showMessage(f"Конфигурация сохранена: {path}", 5000)

    @asyncSlot()
    async def _on_scan_clicked(self) -> None:
        if self._scan_task and not self._scan_task.done():
            QtWidgets.QMessageBox.information(
                self,
                "Сканирование уже запущено",
                "Дождитесь завершения или нажмите «Стоп», чтобы прервать текущий процесс.",
            )
            return
        try:
            config = self._collect_config()
        except ConfigError as exc:
            self._show_error(str(exc))
            return

        limit = self.limit_spin.value() or None
        async def _scan_runner() -> None:
            try:
                await self._runner.scan(
                    config,
                    limit,
                    on_match=self._handle_match,
                    on_status=self._handle_status,
                    on_error=self._handle_error,
                    code_callback=self._prompt_code,
                    password_callback=self._prompt_password,
                    storage=self._storage,
                )
            except asyncio.CancelledError:
                self.statusBar().showMessage("Сканирование остановлено.", 5000)
                raise
            except Exception as exc:
                self._handle_exception("Не удалось выполнить сканирование", exc)
            else:
                await self._refresh_from_storage()
                self.statusBar().showMessage("Сканирование завершено.", 5000)
            finally:
                self._scan_task = None
                self._update_controls_state()

        self._scan_task = self._loop.create_task(_scan_runner())
        self.statusBar().showMessage("Сканирование запущено…")
        self._update_controls_state()

    @asyncSlot()
    async def _on_watch_clicked(self) -> None:
        if self._runner.watching:
            QtWidgets.QMessageBox.information(
                self,
                "Мониторинг уже запущен",
                "Мониторинг уже активен.",
            )
            return

        try:
            config = self._collect_config()
        except ConfigError as exc:
            self._show_error(str(exc))
            return

        runtime_override: Optional[bool]
        if self.runtime_auto_reply_checkbox.isEnabled():
            runtime_override = self.runtime_auto_reply_checkbox.isChecked()
        else:
            runtime_override = None

        try:
            await self._runner.start_watch(
                config,
                on_match=self._handle_match,
                on_status=self._handle_status,
                on_error=self._handle_error,
                code_callback=self._prompt_code,
                password_callback=self._prompt_password,
                auto_join=self.auto_join_checkbox.isChecked(),
                runtime_auto_reply=runtime_override,
                storage=self._storage,
            )
        except Exception as exc:
            self._handle_exception("Не удалось запустить мониторинг", exc)
            return

        self._update_controls_state()
        if self._watch_monitor_task:
            self._watch_monitor_task.cancel()
        self._watch_monitor_task = self._loop.create_task(self._watch_completion_monitor())
        self.statusBar().showMessage("Мониторинг запущен.", 5000)

    @asyncSlot()
    async def _on_stop_clicked(self) -> None:
        if self._scan_task and not self._scan_task.done():
            task = self._scan_task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await self._refresh_from_storage()
            self._update_controls_state()
            return

        if not self._runner.watching:
            return
        try:
            await self._runner.stop_watch()
        except Exception as exc:
            self._handle_exception("Не удалось остановить мониторинг", exc)
            return

        if self._watch_monitor_task:
            self._watch_monitor_task.cancel()
            self._watch_monitor_task = None

        self._update_controls_state()
        await self._refresh_from_storage()
        self.statusBar().showMessage("Мониторинг остановлен.", 5000)

    def _on_clear_statuses(self) -> None:
        self.status_list.clear()

    def _on_apply_filters(self) -> None:
        self._loop.create_task(self._refresh_from_storage())

    def _on_reset_filters(self) -> None:
        self.filter_channel_edit.clear()
        self.filter_author_edit.clear()
        self.filter_keyword_edit.clear()
        self.filter_only_new_checkbox.setChecked(False)
        self._loop.create_task(self._refresh_from_storage())

    def _on_refresh_matches(self) -> None:
        self._loop.create_task(self._refresh_from_storage())

    @asyncSlot()
    async def _on_mark_seen_clicked(self) -> None:
        if not self._storage:
            return
        rows = {index.row() for index in self.matches_table.selectionModel().selectedRows()}
        if not rows:
            return
        keys = [self._row_keys[row] for row in rows if row < len(self._row_keys)]
        try:
            await self._storage.mark_seen(keys)
        except Exception as exc:
            self._handle_exception("Не удалось отметить выбранные записи", exc)
            return
        await self._refresh_from_storage()

    @asyncSlot()
    async def _on_mark_all_seen_clicked(self) -> None:
        if not self._storage:
            return
        try:
            await self._storage.mark_all_seen()
        except Exception as exc:
            self._handle_exception("Не удалось отметить все записи", exc)
            return
        await self._refresh_from_storage()

    def _on_autoreply_toggled(self, checked: bool) -> None:
        self.auto_reply_edit.setEnabled(checked)
        self.auto_reply_templates_edit.setEnabled(checked)
        self.auto_reply_random_checkbox.setEnabled(checked)
        self.runtime_auto_reply_checkbox.setEnabled(checked)
        if not checked:
            self.runtime_auto_reply_checkbox.setChecked(False)

    def _on_username_guard_toggled(self, checked: bool) -> None:
        running = self._runner.watching or (self._scan_task is not None and not self._scan_task.done())
        enabled = checked and not running
        self.username_guard_delay_spin.setEnabled(enabled)
        self.username_guard_edit.setEnabled(enabled)

    def _on_match_double_clicked(self, row: int, column: int) -> None:
        link_item = self.matches_table.item(row, 5)
        if link_item:
            link = link_item.text().strip()
            if link:
                webbrowser.open(link)

    def _on_matches_context_menu(self, pos: QtCore.QPoint) -> None:
        if self.matches_table.rowCount() == 0:
            return
        index = self.matches_table.indexAt(pos)
        row = index.row()
        if row < 0 or row >= len(self._row_keys):
            return

        selection_model = self.matches_table.selectionModel()
        if selection_model and not selection_model.isRowSelected(row):
            self.matches_table.selectRow(row)

        menu = QtWidgets.QMenu(self.matches_table)
        reply_action = menu.addAction("Ответить")
        reply_action.setEnabled(self._can_send_manual_reply())

        global_pos = self.matches_table.viewport().mapToGlobal(pos)
        action = menu.exec(global_pos)
        if action == reply_action:
            self._start_manual_reply(row)

    def _start_manual_reply(self, row: int) -> None:
        if self._manual_reply_task and not self._manual_reply_task.done():
            QtWidgets.QMessageBox.information(
                self,
                "Отправка",
                "Ответ уже отправляется. Дождитесь завершения.",
            )
            return
        task = self._loop.create_task(self._send_manual_reply(row))
        self._manual_reply_task = task
        task.add_done_callback(lambda _: setattr(self, "_manual_reply_task", None))

    async def _send_manual_reply(self, row: int) -> None:
        if not self._storage:
            self._show_error("Хранилище совпадений недоступно.")
            return
        if not self._current_config:
            self._show_error("Сначала загрузите config.toml.")
            return
        if row >= len(self._row_keys):
            return

        chat_id, message_id = self._row_keys[row]
        try:
            match = await self._storage.fetch_match(chat_id, message_id)
        except Exception as exc:
            self._handle_exception("Не удалось получить данные совпадения", exc)
            return
        if match is None:
            self._show_error("Совпадение не найдено в базе.")
            return

        try:
            await self._runner.send_manual_reply(
                self._current_config,
                match,
                on_status=self._handle_status,
                on_error=self._handle_error,
                code_callback=self._prompt_code,
                password_callback=self._prompt_password,
            )
        except Exception as exc:
            self._handle_exception("Не удалось отправить ответ", exc)
            return

        try:
            await self._storage.mark_seen([(chat_id, message_id)])
        except Exception as exc:
            log.warning(
                "Не удалось отметить совпадение #%s/%s: %s",
                chat_id,
                message_id,
                exc,
                exc_info=True,
            )
        await self._refresh_from_storage()

    def _can_send_manual_reply(self) -> bool:
        if not self._current_config:
            return False
        monitor_cfg = self._current_config.monitor
        return bool(
            monitor_cfg.auto_reply_templates or monitor_cfg.auto_reply_message.strip()
        )
    async def _handle_status(self, message: str) -> None:
        self._append_status(message)

    async def _handle_error(self, message: str) -> None:
        self._append_status(f"Ошибка: {message}")
        self._append_error_log(message)
        QtWidgets.QMessageBox.critical(self, "Ошибка", message)

    async def _handle_match(self, match: MatchResult) -> None:
        try:
            is_new = await self._storage.save_match(match)
        except Exception as exc:
            self._handle_exception("Не удалось сохранить совпадение", exc)
            return

        stored = StoredMatch(
            chat_id=match.chat_id,
            message_id=match.message_id,
            chat_title=match.target_title,
            timestamp=match.timestamp,
            author=match.author,
            text=match.text,
            keywords=list(match.matched_keywords),
            link=match.link,
            is_new=match.is_new or is_new,
        )
        self._update_or_insert_match(stored, prepend=True)

    # ------------------------------------------------------------------ служебные методы
    def _collect_config(self) -> AppConfig:
        try:
            api_id = int(self.api_id_edit.text().strip())
        except ValueError as exc:
            raise ConfigError("API ID должен быть числом.") from exc

        api_hash = self.api_hash_edit.text().strip()
        if not api_hash:
            raise ConfigError("API hash не может быть пустым.")

        session_name = self.session_edit.text().strip() or "reklama-telega"
        phone = self.phone_edit.text().strip() or None

        keywords = _split_lines(self.keywords_edit.toPlainText())
        if not keywords:
            raise ConfigError("Укажите хотя бы одно ключевое слово.")

        channels = _split_lines(self.channels_edit.toPlainText())
        if not channels:
            raise ConfigError("Укажите хотя бы один канал или обсуждение.")

        monitor = MonitorConfig(
            keywords=keywords,
            channels=channels,
            search_depth=int(self.search_spin.value()),
            case_sensitive=self.case_checkbox.isChecked(),
            highlight=self.highlight_checkbox.isChecked(),
            fetch_interval_seconds=float(self.fetch_spin.value()),
            history_request_timeout=float(self.timeout_spin.value()),
            auto_reply_enabled=self.auto_reply_checkbox.isChecked(),
            auto_reply_message=self.auto_reply_edit.toPlainText().strip(),
            auto_reply_templates=_split_lines(self.auto_reply_templates_edit.toPlainText()),
            auto_reply_randomize=self.auto_reply_random_checkbox.isChecked(),
            username_guard_enabled=self.username_guard_checkbox.isChecked(),
            username_guard_delay=float(self.username_guard_delay_spin.value()),
            username_guard_replacements=_split_lines(self.username_guard_edit.toPlainText()),
        )

        telegram = TelegramConfig(
            api_id=api_id,
            api_hash=api_hash,
            session_name=session_name,
            phone=phone,
            force_sms=self.force_sms_checkbox.isChecked(),
        )

        config = AppConfig(telegram=telegram, monitor=monitor)
        self._current_config = config
        return config

    def _load_config_file(self, path: Path) -> None:
        try:
            config = load_config(path)
        except ConfigError as exc:
            self._show_error(str(exc))
            return
        except Exception as exc:
            self._handle_exception("Не удалось прочитать конфигурацию", exc)
            return

        self._current_config = config
        self._config_path = path
        self.config_path_edit.setText(str(path))

        self.api_id_edit.setText(str(config.telegram.api_id))
        self.api_hash_edit.setText(config.telegram.api_hash)
        self.session_edit.setText(config.telegram.session_name)
        self.phone_edit.setText(config.telegram.phone or "")
        self.force_sms_checkbox.setChecked(config.telegram.force_sms)

        self.keywords_edit.setPlainText("\n".join(config.monitor.keywords))
        self.channels_edit.setPlainText("\n".join(config.monitor.channels))
        self.case_checkbox.setChecked(config.monitor.case_sensitive)
        self.highlight_checkbox.setChecked(config.monitor.highlight)
        self.search_spin.setValue(config.monitor.search_depth)
        self.fetch_spin.setValue(config.monitor.fetch_interval_seconds)
        self.timeout_spin.setValue(config.monitor.history_request_timeout)
        self.auto_reply_checkbox.setChecked(config.monitor.auto_reply_enabled)
        self.auto_reply_edit.setPlainText(config.monitor.auto_reply_message)
        self.auto_reply_templates_edit.setPlainText("\n".join(config.monitor.auto_reply_templates))
        self.auto_reply_random_checkbox.setChecked(config.monitor.auto_reply_randomize)
        self.runtime_auto_reply_checkbox.setChecked(config.monitor.auto_reply_enabled)
        self.username_guard_checkbox.setChecked(config.monitor.username_guard_enabled)
        self.username_guard_delay_spin.setValue(config.monitor.username_guard_delay)
        self.username_guard_edit.setPlainText("\n".join(config.monitor.username_guard_replacements))

        self._on_autoreply_toggled(self.auto_reply_checkbox.isChecked())
        self._on_username_guard_toggled(self.username_guard_checkbox.isChecked())
        self.statusBar().showMessage(f"Конфигурация загружена: {path}", 5000)

    def _save_config_to_file(self, config: AppConfig, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        def _dump_list(key: str, values: Sequence[str]) -> str:
            if not values:
                return f"{key} = []"
            body = "\n".join(f"  {json.dumps(value, ensure_ascii=False)}," for value in values)
            return f"{key} = [\n{body}\n]"

        lines = [
            "[telegram]",
            f"api_id = {config.telegram.api_id}",
            f"api_hash = {json.dumps(config.telegram.api_hash, ensure_ascii=False)}",
            f"session_name = {json.dumps(config.telegram.session_name, ensure_ascii=False)}",
            f"phone = {json.dumps(config.telegram.phone or '', ensure_ascii=False)}",
            f"force_sms = {str(config.telegram.force_sms).lower()}",
            "",
            "[monitor]",
            _dump_list("keywords", config.monitor.keywords),
            "",
            _dump_list("channels", config.monitor.channels),
            "",
            f"search_depth = {config.monitor.search_depth}",
            f"case_sensitive = {str(config.monitor.case_sensitive).lower()}",
            f"highlight = {str(config.monitor.highlight).lower()}",
            f"fetch_interval_seconds = {config.monitor.fetch_interval_seconds}",
            f"history_request_timeout = {config.monitor.history_request_timeout}",
            f"auto_reply_enabled = {str(config.monitor.auto_reply_enabled).lower()}",
            f"auto_reply_message = {json.dumps(config.monitor.auto_reply_message, ensure_ascii=False)}",
            _dump_list("auto_reply_templates", config.monitor.auto_reply_templates),
            f"auto_reply_randomize = {str(config.monitor.auto_reply_randomize).lower()}",
            f"username_guard_enabled = {str(config.monitor.username_guard_enabled).lower()}",
            f"username_guard_delay = {config.monitor.username_guard_delay}",
            _dump_list("username_guard_replacements", config.monitor.username_guard_replacements),
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")

    async def _refresh_from_storage(self) -> None:
        if not self._storage:
            return
        filters = {
            "channel": self.filter_channel_edit.text().strip() or None,
            "author": self.filter_author_edit.text().strip() or None,
            "keyword": self.filter_keyword_edit.text().strip() or None,
            "only_new": self.filter_only_new_checkbox.isChecked(),
        }
        try:
            matches = await self._storage.fetch_matches(**filters)
        except Exception as exc:
            self._handle_exception("Не удалось загрузить совпадения", exc)
            return

        self.matches_table.setRowCount(0)
        self._row_keys.clear()
        for match in matches:
            self._add_match_row(match)
        self._update_mark_seen_state()

    def _add_match_row(self, match: StoredMatch, *, prepend: bool = False) -> None:
        row = 0 if prepend else self.matches_table.rowCount()
        self.matches_table.insertRow(row)
        self._row_keys.insert(row, (match.chat_id, match.message_id))

        values = [
            match.chat_title,
            _format_timestamp(match.timestamp),
            match.author,
            match.text,
            ", ".join(match.keywords),
            match.link or "",
        ]
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(value)
            item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            if column == 5 and value:
                item.setForeground(QtGui.QBrush(QtGui.QColor("blue")))
            self.matches_table.setItem(row, column, item)

        if match.is_new:
            self._highlight_row(row, True)

    def _highlight_row(self, row: int, highlight: bool) -> None:
        color = QtGui.QColor("#fff4ce") if highlight else QtGui.QColor("transparent")
        for column in range(self.matches_table.columnCount()):
            item = self.matches_table.item(row, column)
            if item:
                item.setBackground(color)

    def _update_or_insert_match(self, match: StoredMatch, *, prepend: bool = False) -> None:
        key = (match.chat_id, match.message_id)
        try:
            row = self._row_keys.index(key)
        except ValueError:
            self._add_match_row(match, prepend=prepend)
            return

        values = [
            match.chat_title,
            _format_timestamp(match.timestamp),
            match.author,
            match.text,
            ", ".join(match.keywords),
            match.link or "",
        ]
        for column, value in enumerate(values):
            item = self.matches_table.item(row, column)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.matches_table.setItem(row, column, item)
            item.setText(value)
            if column == 5:
                item.setForeground(QtGui.QBrush(QtGui.QColor("blue")) if value else QtGui.QBrush())
        self._highlight_row(row, match.is_new)
        if match.is_new:
            self.matches_table.scrollToItem(self.matches_table.item(row, 0))
    def _append_status(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.status_list.addItem(f"{timestamp}  {message}")
        self.status_list.scrollToBottom()
        while self.status_list.count() > 500:
            item = self.status_list.takeItem(0)
            del item

    def _append_general_log(self, message: str) -> None:
        self._append_log_message(self.general_log_edit, message)

    def _append_telethon_log(self, message: str) -> None:
        self._append_log_message(self.telethon_log_edit, message)

    def _append_error_log(self, message: str) -> None:
        self._append_log_message(self.error_log_edit, message)

    def _append_log_message(self, widget: QtWidgets.QPlainTextEdit, message: str) -> None:
        widget.appendPlainText(message)
        widget.verticalScrollBar().setValue(widget.verticalScrollBar().maximum())

    def _update_controls_state(self) -> None:
        watching = self._runner.watching
        scanning = self._scan_task is not None and not self._scan_task.done()
        running = watching or scanning

        self.watch_button.setEnabled(not watching and not scanning)
        self.scan_button.setEnabled(not scanning)
        self.stop_button.setEnabled(running)
        self.load_button.setEnabled(not running)
        self.save_button.setEnabled(not running)
        self.auto_join_checkbox.setEnabled(not running)

        for spin in (self.search_spin, self.limit_spin, self.fetch_spin, self.timeout_spin):
            spin.setEnabled(not running)

        self.username_guard_checkbox.setEnabled(not running)
        self._on_username_guard_toggled(self.username_guard_checkbox.isChecked())

    async def _watch_completion_monitor(self) -> None:
        try:
            while self._runner.watching:
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            return
        finally:
            self._watch_monitor_task = None
        self._update_controls_state()
        await self._refresh_from_storage()
        self.statusBar().showMessage("Мониторинг завершён.", 5000)

    def _prompt_code(self, prompt: str) -> str:
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "Код подтверждения",
            prompt,
        )
        if not ok:
            raise RuntimeError("Ввод кода отменён пользователем.")
        return text.strip()

    def _prompt_password(self, prompt: str) -> str:
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "Пароль",
            prompt,
            QtWidgets.QLineEdit.EchoMode.Password,
        )
        if not ok:
            raise RuntimeError("Ввод пароля отменён пользователем.")
        return text.strip()

    def _handle_exception(self, context: str, exc: Exception) -> None:
        log.exception("%s: %s", context, exc)
        QtWidgets.QMessageBox.critical(self, "Ошибка", f"{context}:\n{exc}")

    def _show_error(self, message: str) -> None:
        QtWidgets.QMessageBox.warning(self, "Ошибка конфигурации", message)

    def _update_mark_seen_state(self) -> None:
        selection = self.matches_table.selectionModel()
        has_selection = bool(selection.selectedRows()) if selection else False
        self.mark_seen_button.setEnabled(has_selection)

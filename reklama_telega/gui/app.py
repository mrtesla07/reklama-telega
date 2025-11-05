"""Entrypoint for the reklama-telega GUI application."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from reklama_telega.gui.main_window import MainWindow


def _configure_logging(level: int, log_dir: Path) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("reklama_telega").setLevel(min(level, logging.INFO))
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "reklama-telega.log", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(file_handler)




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reklama-telega-gui",
        description="Графический интерфейс для мониторинга ключевых слов в Telegram.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Путь до config.toml (по умолчанию ./config.toml).",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="Каталог для сохранения логов (по умолчанию ./logs).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Уровень логирования (-v, -vv).",
    )
    return parser


def _level_from_verbosity(verbosity: int) -> int:
    if verbosity >= 2:
        return logging.DEBUG
    if verbosity == 1:
        return logging.INFO
    return logging.WARNING


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    _configure_logging(_level_from_verbosity(args.verbose), args.log_dir)

    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow(loop=loop, config_path=args.config)
    window.show()

    with loop:
        loop.run_forever()


if __name__ == "__main__":  # pragma: no cover
    main()

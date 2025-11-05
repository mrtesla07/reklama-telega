"""Entry point for reklama-telega command line interface."""

from __future__ import annotations

import argparse
import logging
from typing import Optional

from .monitor import run_scan, run_watch
from .version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reklama-telega",
        description="Search for keywords in Telegram channel comments.",
    )
    parser.add_argument(
        "-c",
        "--config",
        help="Path to config.toml (default: ./config.toml).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v, -vv).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="Scan message history for configured keywords."
    )
    scan_parser.add_argument(
        "--limit",
        type=int,
        help="Override monitor.search_depth for this run.",
    )

    watch_parser = subparsers.add_parser(
        "watch", help="Monitor new messages in real time."
    )
    watch_parser.add_argument(
        "--auto-join",
        action="store_true",
        help="Join listed channels/discussions before monitoring.",
    )

    gui_parser = subparsers.add_parser(
        "gui", help="Launch the graphical interface."
    )
    gui_parser.add_argument(
        "-c",
        "--config",
        help="Path to config.toml (default: ./config.toml).",
    )
    gui_parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="GUI log verbosity (-v, -vv).",
    )
    gui_parser.add_argument(
        "--log-dir",
        help="Directory for GUI logs (default: ./logs).",
    )

    return parser


def configure_logging(verbosity: int) -> None:
    """Configure logging level based on verbosity flag."""
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if args.command == "scan":
        run_scan(args.config, limit=args.limit)
    elif args.command == "watch":
        run_watch(args.config, auto_join=args.auto_join)
    elif args.command == "gui":
        from .gui.app import main as gui_main

        gui_args: list[str] = []
        if args.config:
            gui_args.extend(["--config", args.config])
        if args.log_dir:
            gui_args.extend(["--log-dir", args.log_dir])
        if args.verbose:
            gui_args.append("-" + "v" * args.verbose)
        gui_main(gui_args or None)
    else:  # pragma: no cover
        parser.print_help()


if __name__ == "__main__":  # pragma: no cover
    main()

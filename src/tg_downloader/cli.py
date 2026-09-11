"""Command-line entry point; diagnostic commands never open a Telegram session."""

from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import platform
import sys

from . import __version__


def doctor() -> int:
    config_dir = Path.home() / ".config" / "telegram-downloader"
    print(f"TG Downloader {__version__}")
    print(f"Python: {platform.python_version()}")
    print(f"Interpretador: {sys.executable}")
    print(f"Pacote: {Path(__file__).resolve().parent}")
    healthy = True
    for name in ("textual", "telethon"):
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = "NAO INSTALADO"
            healthy = False
        print(f"{name}: {version}")
    for label, path in (
        ("Configuracao", config_dir / "config.json"),
        ("Sessao", config_dir / "sessions" / "session_string"),
    ):
        print(f"{label}: {path} ({'presente' if path.is_file() else 'ausente'})")
    print("Diagnostico local; nenhuma conexao com Telegram foi realizada.")
    return 0 if healthy else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tg-downloader",
        description="Gerenciador interativo de downloads do Telegram.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--demo", action="store_true", help="abrir simulacao sem conectar ao Telegram"
    )
    mode.add_argument(
        "--offline", action="store_true", help="abrir o painel sem conexao ao Telegram"
    )
    parser.add_argument(
        "--doctor", action="store_true", help="verificar instalacao local sem conectar"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.doctor:
        return doctor()
    try:
        from .app import DownloaderApp

        DownloaderApp(demo=args.demo, offline=args.offline).run()
    except KeyboardInterrupt:
        return 130
    return 0


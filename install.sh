#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

case "${1:-}" in
  "")
    if [ ! -x "$VENV_DIR/bin/python" ]; then
      python3 -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/python" -m pip install -e "$PROJECT_DIR"
    ;;
  --skip-deps)
    "$VENV_DIR/bin/python" -c 'import textual, telethon, tg_downloader'
    ;;
  *)
    printf 'Uso: %s [--skip-deps]\n' "$0" >&2
    exit 2
    ;;
esac

"$VENV_DIR/bin/python" - "$PROJECT_DIR" <<'PY'
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys

project = Path(sys.argv[1]).resolve()
marker = "Managed by tg-downloader installer"
launcher = Path.home() / ".local" / "bin" / "tg-downloader"
data_dir = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
icon = data_dir / "icons" / "hicolor" / "scalable" / "apps" / "tg-downloader.svg"
application = data_dir / "applications" / "tg-downloader.desktop"
desktop_dir = Path.home() / "Desktop"
if shutil.which("xdg-user-dir"):
    result = subprocess.run(
        ["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, check=False
    )
    if result.returncode == 0 and result.stdout.strip():
        desktop_dir = Path(result.stdout.strip())
desktop = desktop_dir / "tg-downloader.desktop"

def exec_quote(value):
    value = str(value).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return '"' + value + '"'

launcher_text = (
    f"#!/bin/sh\n# {marker}\n"
    f"exec {shlex.quote(str(project / '.venv' / 'bin' / 'python'))} "
    '-m tg_downloader "$@"\n'
)
desktop_text = (
    f"[Desktop Entry]\n# {marker}\n"
    "Type=Application\nVersion=1.0\nName=TG Downloader\n"
    "Comment=Gerenciador de downloads do Telegram\n"
    f"Exec={exec_quote(launcher)}\nIcon={icon}\n"
    "Terminal=true\nCategories=Network;FileTransfer;\n"
    "Keywords=Telegram;download;terminal;\nStartupNotify=false\n"
)
artifacts = {
    launcher: (launcher_text, 0o755),
    icon: ((project / "src" / "tg_downloader" / "assets" / "tg-downloader.svg").read_text(), 0o644),
    application: (desktop_text, 0o644),
    desktop: (desktop_text, 0o755),
}
for path in artifacts:
    if path.is_symlink() or (path.exists() and (
        not path.is_file() or marker not in path.read_text(errors="replace")
    )):
        raise SystemExit(f"Instalacao interrompida: arquivo existente de outra origem: {path}")
for path, (content, mode) in artifacts.items():
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    print(f"Instalado: {path}")
if shutil.which("update-desktop-database"):
    subprocess.run(["update-desktop-database", str(application.parent)], check=False)
if shutil.which("gio"):
    result = subprocess.run(
        ["gio", "set", str(desktop), "metadata::trusted", "true"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        print("O ambiente grafico pode solicitar 'Permitir iniciar' no primeiro uso do atalho.")
if str(launcher.parent) not in os.environ.get("PATH", "").split(os.pathsep):
    print(f"Inclua {launcher.parent} no PATH para chamar tg-downloader neste terminal.")
print("Pronto. Execute tg-downloader de qualquer pasta.")
PY

"""Keyboard-friendly dialogs for the downloader.

Stable input IDs: new-link, new-destination, new-concurrency;
config-api-id, config-api-hash, config-concurrency; prompt-value.
Action IDs use new-, config-, prompt-, message- and confirm- prefixes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlparse

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from .engine import CONFIG_FILE, MAX_CONCURRENT_DOWNLOADS, secure_dir


def _read_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Nao foi possivel ler config.json; verifique o arquivo.") from exc
    if not isinstance(config, dict):
        raise ValueError("config.json precisa conter um objeto JSON.")
    return config


def _save_config(updates: dict[str, Any]) -> None:
    config = _read_config()
    config.update(updates)
    secure_dir(CONFIG_FILE.parent)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=CONFIG_FILE.parent,
            prefix=".config-", delete=False,
        ) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONFIG_FILE)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _concurrency(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(
            f"Downloads simultaneos: informe um numero de 1 a {MAX_CONCURRENT_DOWNLOADS}."
        ) from exc
    if not 1 <= number <= MAX_CONCURRENT_DOWNLOADS:
        raise ValueError(
            f"Downloads simultaneos: informe um numero de 1 a {MAX_CONCURRENT_DOWNLOADS}."
        )
    return number


def _system_clipboard() -> str:
    """Read the desktop clipboard for terminals that send Ctrl+V as a key."""
    commands = []
    if shutil.which("wl-paste"):
        commands.append(["wl-paste", "--no-newline"])
    if shutil.which("xclip"):
        commands.append(["xclip", "-selection", "clipboard", "-o"])
    if shutil.which("xsel"):
        commands.append(["xsel", "--clipboard", "--output"])
    for command in commands:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=0.75, check=False
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout:
            return result.stdout.splitlines()[0].strip()
    return ""


def _recognized_link(value: str) -> bool:
    """Check the local shape of a Telegram chat reference before connecting."""
    value = value.strip()
    if not value:
        return False
    if re.fullmatch(r"-?\d+", value):
        return True
    if re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{3,}", value):
        return True
    normalized = value if "://" in value else "https://" + value
    parsed = urlparse(normalized)
    if parsed.hostname not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
        return False
    return bool([part for part in parsed.path.split("/") if part])


# Raizes onde o Linux monta disco secundario e externo. Empacotado como snap,
# elas so ficam visiveis depois que a interface `removable-media` e conectada —
# e ela nao conecta sozinha, nem para firefox ou brave.
MONTAGENS_EXTERNAS = ("/media/", "/run/media/", "/mnt/")


def _bloqueio_do_snap(destination: Path) -> str:
    """A linha que resolve, quando quem barrou o destino foi o confinamento.

    Fora de um snap devolve vazio e nada muda — o erro de permissao comum
    continua vindo de onde vinha. Dentro, um "permissao negada" seco nao diz o
    que fazer, e o que falta e um comando de uma linha. Vale avisar aqui, no
    dialogo, enquanto a pessoa ainda pode agir, e nao no meio do download.
    """
    nome = os.environ.get("SNAP_INSTANCE_NAME") or os.environ.get("SNAP_NAME")
    if not nome or not str(destination).startswith(MONTAGENS_EXTERNAS):
        return ""
    # O destino ainda nao existe: quem responde pela escrita e a pasta mais
    # proxima que ja existe.
    existente = destination
    while not existente.exists() and existente != existente.parent:
        existente = existente.parent
    if os.access(existente, os.W_OK | os.X_OK):
        return ""
    return (
        "Este disco ainda nao foi liberado para o aplicativo. Rode uma vez no "
        f"terminal: sudo snap connect {nome}:removable-media"
    )


def _destination_path(value: str) -> Path:
    raw_value = value.strip()
    if not raw_value or "\x00" in raw_value:
        raise ValueError("Informe uma pasta de destino absoluta.")
    destination = Path(raw_value).expanduser()
    if not destination.is_absolute():
        raise ValueError("Use um caminho absoluto, como /home/usuario/Downloads.")
    if destination.exists() and not destination.is_dir():
        raise ValueError("O destino precisa ser uma pasta.")
    bloqueio = _bloqueio_do_snap(destination)
    if bloqueio:
        raise ValueError(bloqueio)
    return destination


class ClipboardInput(Input):
    """Input with desktop clipboard shortcuts in addition to terminal paste events."""

    BINDINGS = [
        *Input.BINDINGS,
        Binding("ctrl+shift+v", "paste_system", "Colar do sistema", show=False),
        Binding("shift+insert", "paste_system", "Colar do sistema", show=False),
    ]

    def _paste_system_clipboard(self) -> bool:
        value = _system_clipboard()
        if not value:
            return False
        value = value.splitlines()[0].strip()
        self.replace(value, *self.selection)
        return True

    def _on_paste(self, event: events.Paste) -> None:
        value = event.text.splitlines()[0].strip() if event.text else ""
        if value:
            self.replace(value, *self.selection)
        event.stop()

    def action_paste(self) -> None:
        if not self._paste_system_clipboard():
            super().action_paste()

    def action_paste_system(self) -> None:
        if not self._paste_system_clipboard():
            self.app.notify("O clipboard do sistema esta vazio ou indisponivel.", severity="warning")


class _Dialog(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancelar", priority=True)]

    DEFAULT_CSS = """
    _Dialog {
        align: center middle;
        background: #030812 85%;
    }
    _Dialog .dialog {
        width: 72;
        max-width: 96%;
        height: auto;
        max-height: 94%;
        padding: 1 2;
        border: round #00d9ef;
        background: #030812;
        scrollbar-color: #00d9ef;
        scrollbar-color-hover: #f02ce0;
        scrollbar-color-active: #f02ce0;
    }
    _Dialog .dialog-title {
        height: auto;
        width: 1fr;
        color: #f02ce0;
        text-style: bold;
        margin-bottom: 1;
    }
    _Dialog Label {
        height: auto;
        width: 1fr;
        color: #00d9ef;
    }
    _Dialog Input {
        width: 1fr;
        height: 3;
        background: #071327;
        color: #daf9ff;
        border: tall #163d57;
        margin-bottom: 1;
    }
    _Dialog Input:focus {
        border: tall #f02ce0;
    }
    _Dialog .dialog-actions {
        width: 1fr;
        height: 3;
        margin-top: 1;
    }
    _Dialog Button {
        min-width: 8;
        width: 1fr;
        margin-right: 1;
        color: #00d9ef;
        background: #102138;
        border: tall #163d57;
    }
    _Dialog Button:last-child {
        margin-right: 0;
    }
    _Dialog Button.-primary {
        color: #36ef94;
        background: #093124;
    }
    _Dialog Button.-warning {
        color: #ffd75f;
        background: #352410;
    }
    _Dialog Button:focus {
        text-style: bold;
        border: tall #f02ce0;
    }
    _Dialog .dialog-error {
        height: auto;
        color: #ffd75f;
    }
    _Dialog .dialog-status {
        height: auto;
        margin-top: -1;
        color: #7e91ad;
    }
    _Dialog .dialog-status.valid {
        color: #36ef94;
    }
    _Dialog .dialog-status.invalid {
        color: #ff507a;
    }
    _Dialog .dialog-body {
        height: auto;
        width: 1fr;
        color: #daf9ff;
    }
    /* Classe propria em vez de margem no .dialog-body: aquele e o corpo de
       MessageScreen e ConfirmScreen, onde o texto e a tela inteira e nao
       precisa de folga por baixo. */
    _Dialog .dialog-intro {
        height: auto;
        width: 1fr;
        color: #7e91ad;
        margin-bottom: 1;
    }
    """

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _error(self, selector: str, message: str, focus: str | None = None) -> None:
        self.query_one(selector, Static).update(message)
        if focus:
            self.query_one(focus, Input).focus()


class NewDownloadScreen(_Dialog):
    """Return link, absolute destination and concurrency, or None on cancel."""

    def __init__(self, default_destination: str, concurrency: int) -> None:
        super().__init__()
        self.default_destination = default_destination
        self.concurrency = concurrency

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static("NOVO DOWNLOAD", classes="dialog-title", markup=False)
            yield Label("Canal ou link do Telegram")
            yield ClipboardInput(placeholder="https://t.me/canal ou @canal", id="new-link")
            yield Static(
                "Cole com Ctrl+Shift+V ou Shift+Insert",
                id="new-link-status",
                classes="dialog-status",
                markup=False,
            )
            yield Label("Pasta de destino")
            yield ClipboardInput(self.default_destination, id="new-destination")
            yield Static("", id="new-destination-status", classes="dialog-status", markup=False)
            yield Label("Downloads simultaneos (1 a 10)")
            yield Input(str(self.concurrency), type="integer", id="new-concurrency")
            yield Static("", id="new-error", classes="dialog-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancelar", id="new-cancel")
                yield Button("Iniciar", variant="primary", id="new-start")

    def on_mount(self) -> None:
        self._update_link_feedback(self.query_one("#new-link", Input).value)
        self._update_destination_feedback(self.query_one("#new-destination", Input).value)
        self.query_one("#new-link", Input).focus()

    def _set_status(self, selector: str, message: str, valid: bool | None) -> None:
        status = self.query_one(selector, Static)
        status.update(message)
        status.remove_class("valid", "invalid")
        if valid is not None:
            status.add_class("valid" if valid else "invalid")

    def _update_link_feedback(self, value: str) -> bool:
        value = value.strip()
        if not value:
            self._set_status(
                "#new-link-status",
                "Cole com Ctrl+Shift+V ou Shift+Insert",
                None,
            )
            self.query_one("#new-start", Button).disabled = True
            return False
        valid = _recognized_link(value)
        self._set_status(
            "#new-link-status",
            "[OK] Link reconhecido; sera verificado ao iniciar."
            if valid else
            "[ERRO] Informe @canal, ID numerico ou um link t.me valido.",
            valid,
        )
        self.query_one("#new-start", Button).disabled = not valid
        if valid:
            self.query_one("#new-error", Static).update("")
        return valid

    def _update_destination_feedback(self, value: str) -> bool:
        try:
            destination = _destination_path(value)
        except (OSError, RuntimeError, ValueError) as exc:
            self._set_status("#new-destination-status", f"[ERRO] {exc}", False)
            return False
        if destination.exists():
            message = "[OK] Pasta de destino pronta."
        else:
            message = "[OK] A pasta sera criada quando o download iniciar."
        self._set_status("#new-destination-status", message, True)
        return True

    @on(Button.Pressed, "#new-cancel")
    def cancel(self) -> None:
        self.action_cancel()

    @on(Input.Changed)
    def changed(self, event: Input.Changed) -> None:
        if event.input.id == "new-link":
            self._update_link_feedback(event.value)
        elif event.input.id == "new-destination":
            self._update_destination_feedback(event.value)

    @on(Input.Submitted)
    def submit_input(self, event: Input.Submitted) -> None:
        if event.input.id == "new-link":
            if self._update_link_feedback(event.value):
                self.query_one("#new-destination", Input).focus()
        elif event.input.id == "new-destination":
            if self._update_destination_feedback(event.value):
                self.query_one("#new-concurrency", Input).focus()
        else:
            self.submit()

    @on(Button.Pressed, "#new-start")
    def submit(self) -> None:
        link = self.query_one("#new-link", Input).value.strip()
        if not self._update_link_feedback(link):
            self._error("#new-error", "Informe um canal, ID ou link Telegram valido.", "#new-link")
            return
        raw_destination = self.query_one("#new-destination", Input).value.strip()
        try:
            destination = _destination_path(raw_destination)
        except (OSError, RuntimeError, ValueError) as exc:
            self._update_destination_feedback(raw_destination)
            self._error("#new-error", str(exc), "#new-destination")
            return
        try:
            concurrency = _concurrency(self.query_one("#new-concurrency", Input).value)
        except ValueError as exc:
            self._error("#new-error", str(exc), "#new-concurrency")
            return
        self.dismiss({"link": link, "destination": str(destination), "concurrency": concurrency})


class SettingsScreen(_Dialog):
    """Merge config securely; return concurrency and optional login=True."""

    def __init__(self, concurrency: int) -> None:
        super().__init__()
        self.concurrency = concurrency
        self.initial_error = ""
        try:
            self.config = _read_config()
        except ValueError as exc:
            self.config = {}
            self.initial_error = str(exc)

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static("CONFIGURACOES", classes="dialog-title", markup=False)
            yield Static(
                "O programa baixa pela SUA conta, sem servidor intermediario: "
                "os dois valores abaixo sao seus e sao obrigatorios. Pegue em "
                "my.telegram.org, em API development tools.",
                classes="dialog-intro",
                markup=False,
            )
            yield Label("API ID")
            yield Input(str(self.config.get("api_id") or ""), type="integer", id="config-api-id")
            yield Label("API Hash")
            yield Input(str(self.config.get("api_hash") or ""), password=True, id="config-api-hash")
            yield Label("Downloads simultaneos (1 a 10)")
            yield Input(str(self.concurrency), type="integer", id="config-concurrency")
            yield Static(self.initial_error, id="config-error", classes="dialog-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancelar", id="config-cancel")
                yield Button("Salvar", variant="primary", id="config-save")
                yield Button("Conectar", id="config-connect")

    def on_mount(self) -> None:
        selector = "#config-concurrency" if self.config.get("api_id") else "#config-api-id"
        self.query_one(selector, Input).focus()

    @on(Button.Pressed, "#config-cancel")
    def cancel(self) -> None:
        self.action_cancel()

    @on(Input.Submitted)
    def submit_input(self, event: Input.Submitted) -> None:
        if event.input.id == "config-api-id":
            self.query_one("#config-api-hash", Input).focus()
        elif event.input.id == "config-api-hash":
            self.query_one("#config-concurrency", Input).focus()
        else:
            self._save(False)

    @on(Button.Pressed, "#config-save")
    def save(self) -> None:
        self._save(False)

    @on(Button.Pressed, "#config-connect")
    def connect(self) -> None:
        self._save(True)

    def _save(self, login: bool) -> None:
        try:
            concurrency = _concurrency(self.query_one("#config-concurrency", Input).value)
        except ValueError as exc:
            self._error("#config-error", str(exc), "#config-concurrency")
            return
        api_id = self.query_one("#config-api-id", Input).value.strip()
        api_hash = self.query_one("#config-api-hash", Input).value.strip()
        updates: dict[str, Any] = {"concurrent_downloads": concurrency}
        # Os dois sao requisito, nao preferencia: `engine.load_config` recusa a
        # conexao sem eles e nao existe credencial embutida no programa. Antes
        # so o botao Conectar cobrava, e o formulario chamava os campos de
        # opcionais — quem salvava vazio saia daqui achando que estava pronto.
        if not api_id:
            self._error("#config-error", "Preencha o API ID; ele vem de my.telegram.org.", "#config-api-id")
            return
        if not api_id.isascii() or not api_id.isdecimal() or int(api_id) <= 0:
            self._error("#config-error", "API ID precisa ser um numero positivo.", "#config-api-id")
            return
        updates["api_id"] = int(api_id)
        if not api_hash:
            self._error("#config-error", "Preencha o API Hash; ele vem de my.telegram.org.", "#config-api-hash")
            return
        if len(api_hash) != 32 or any(char not in "0123456789abcdefABCDEF" for char in api_hash):
            self._error("#config-error", "API Hash precisa ter 32 caracteres hexadecimais.", "#config-api-hash")
            return
        updates["api_hash"] = api_hash
        try:
            _save_config(updates)
        except (OSError, ValueError) as exc:
            self._error("#config-error", str(exc))
            return
        result: dict[str, Any] = {"concurrency": concurrency}
        if login:
            result["login"] = True
        self.dismiss(result)


class PromptScreen(_Dialog):
    """Return a nonempty value from prompt-value, or None on cancel."""

    def __init__(self, title: str, label: str, password: bool = False) -> None:
        super().__init__()
        self.dialog_title = title
        self.prompt_label = label
        self.password = password

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static(self.dialog_title, classes="dialog-title", markup=False)
            yield Label(self.prompt_label, markup=False)
            yield Input(password=self.password, id="prompt-value")
            yield Static("", id="prompt-error", classes="dialog-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancelar", id="prompt-cancel")
                yield Button("Continuar", variant="primary", id="prompt-submit")

    def on_mount(self) -> None:
        self.query_one("#prompt-value", Input).focus()

    @on(Button.Pressed, "#prompt-cancel")
    def cancel(self) -> None:
        self.action_cancel()

    @on(Input.Submitted, "#prompt-value")
    @on(Button.Pressed, "#prompt-submit")
    def submit(self) -> None:
        raw_value = self.query_one("#prompt-value", Input).value
        value = raw_value if self.password else raw_value.strip()
        if not value:
            self._error("#prompt-error", "Preencha este campo.", "#prompt-value")
            return
        self.dismiss(value)


class MessageScreen(_Dialog):
    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.dialog_title = title
        self.message = message

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static(self.dialog_title, classes="dialog-title", markup=False)
            yield Static(self.message, id="message-body", classes="dialog-body", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Fechar", id="message-close")

    @on(Button.Pressed, "#message-close")
    def close(self) -> None:
        self.dismiss(None)


class ConfirmScreen(_Dialog):
    def __init__(self, title: str, message: str, confirm_label: str = "Confirmar") -> None:
        super().__init__()
        self.dialog_title = title
        self.message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static(self.dialog_title, classes="dialog-title", markup=False)
            yield Static(self.message, id="confirm-body", classes="dialog-body", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancelar", id="confirm-cancel")
                yield Button(self.confirm_label, variant="warning", id="confirm-accept")

    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirm-cancel")
    def cancel(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#confirm-accept")
    def accept(self) -> None:
        self.dismiss(True)

"""Keyboard-first Telegram download dashboard."""

import asyncio
import contextlib
import math
import subprocess
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Static, Tab, Tabs

from . import __version__
from . import engine
from .auth import AuthCancelled
from .broker import shared_client
from .diagnostics import logger
from .dialogs import ConfirmScreen, MessageScreen, NewDownloadScreen, PromptScreen, SettingsScreen
from .widgets import BLUE, CYAN, GREEN, MUTED, PINK, RED, WHITE, YELLOW
from .widgets import FileTable, FocusPanel, TransferGraph, gradient, meter

STATUS = {
    "queued": ("Na fila", MUTED), "downloading": ("Baixando", GREEN),
    "paused": ("Pausado", YELLOW), "complete": ("Concluido", GREEN),
    "skipped": ("Ja existe", BLUE), "error": ("Erro", RED),
    "removed": ("Removido", MUTED),
}
FINISHED = {"complete", "skipped", "error", "removed"}


@contextlib.contextmanager
def _telethon_loop_context():
    """Use the regular asyncio task scheduler while Telethon is running.

    Textual enables Python 3.14's eager task factory in ``App.run``.  The
    Telethon connection and exported file-DC handshake can remain pending
    with that factory, although the same operations complete normally under
    the regular scheduler. Keep Textual's setting outside Telethon operations.
    """
    loop = asyncio.get_running_loop()
    previous_factory = loop.get_task_factory()
    eager_factory = getattr(asyncio, "eager_task_factory", None)
    disabled = eager_factory is not None and previous_factory is eager_factory
    if disabled:
        loop.set_task_factory(None)
    try:
        yield
    finally:
        if disabled:
            loop.set_task_factory(previous_factory)


class DownloaderApp(App):
    TITLE = "ANCHOR DOWNLOAD"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: #030812; color: #d7e6f4; }
    #brand { height: 2; padding: 0 1; border-bottom: solid #00a9cd; }
    #overview { height: 11; margin: 0 1; }
    .panel { border: round #00a9cd; border-title-color: #f02ce0;
        border-title-style: bold; background: #030812; padding: 0 1; }
    .panel:focus, .panel:focus-within { border: round #f02ce0; }
    #account { width: 34%; margin-right: 1; }
    #transfer-column { width: 41%; margin-right: 1; }
    #total { height: 5; margin-bottom: 1; }
    #transfer { width: 100%; height: 1fr; }
    #activity { width: 25%; }
    #workspace { height: 1fr; margin: 0 1; }
    #files-panel { width: 66%; padding: 0; margin-right: 1; }
    #file-tabs { height: 2; }
    Tabs { background: #030812; }
    Tab { color: #7e91ad; padding: 0 1; }
    Tab.-active { color: #00d9ef; text-style: bold; }
    Underline > .underline--bar { color: #00d9ef; background: #030812; }
    #search { display: none; height: 3; border: tall #00a9cd; }
    #search.visible { display: block; }
    #files { height: 1fr; background: #030812; scrollbar-size: 1 1; }
    DataTable > .datatable--header { background: #081522; color: #00d9ef; text-style: bold; }
    DataTable > .datatable--cursor { background: #123449; color: #ffffff; }
    DataTable > .datatable--hover { background: #0b2235; }
    DataTable > .datatable--odd-row { background: #050c18; }
    #empty { height: 3; content-align: center middle; color: #7e91ad; }
    #inspector { width: 34%; margin-right: 0; }
    #details { height: 2fr; }
    #instances { height: 1fr; min-height: 5; }
    #summary { height: 1; margin: 0 2; color: #7e91ad; }
    Footer { background: #071320; color: #00d9ef; }
    FooterKey > .footer-key--key { background: #142139; color: #f02ce0; }
    .compact #files-panel { width: 66%; }
    .compact #inspector { width: 34%; margin-right: 0; }
    .narrow #overview { height: 7; }
    .narrow #account { width: 100%; margin-right: 0; }
    .narrow #transfer-column, .narrow #activity { display: none; }
    .narrow #files-panel { width: 100%; margin-right: 0; }
    .narrow #inspector { display: none; }
    .short #overview { height: 6; }
    """
    BINDINGS = [
        Binding("n", "new_download", "Novo"),
        Binding("space", "pause_selected", "Pausa"),
        Binding("p", "pause_all", "Pausar todos", show=False),
        Binding("delete", "remove_selected", "Remover"),
        Binding("r", "reconnect", "Reconectar"),
        Binding("f3", "settings", "Opcoes"),
        Binding("slash", "search", "Buscar", show=False),
        Binding("f1,question_mark", "help", "Ajuda"),
        Binding("O", "open_destination", "Abrir pasta", key_display="shift+o"),
        Binding("q", "request_quit", "Sair"),
        Binding("ctrl+c", "request_quit", "Sair", show=False, priority=True),
        Binding("escape", "back", "Voltar", show=False),
        Binding("1", "focus_files", "Arquivos", show=False),
        Binding("2", "details", "Detalhes", show=False),
        Binding("4", "show_instances", "Instancias", show=False),
    ]

    def __init__(self, demo=False, offline=False):
        super().__init__()
        self.demo = demo
        self.offline = offline
        self.state = engine.create_state(0, 4)
        self.state.setdefault("downloads", [])
        self.state["phase_label"] = "MODO DEMONSTRACAO" if demo else "DESCONECTADO"
        self.client = None
        self.slot_handle = None
        self.connection_task = None
        self.download_task = None
        self.visible_downloads = []
        self.filter_name = "all"
        self.search_text = ""
        self.selected_id = None
        self.row_signature = None
        self.column_signature = None
        self.row_snapshots = {}
        self.last_sample = 0.0
        self.history = deque(maxlen=240)
        self.instances = []
        self.tick_count = 0
        self.closing = False
        self.connection_error = ""
        self.last_destination = str(Path.home() / "Downloads" / "Telegram")

    def compose(self) -> ComposeResult:
        yield Static(id="brand")
        with Horizontal(id="overview"):
            yield FocusPanel(id="account", classes="panel")
            with Vertical(id="transfer-column"):
                yield FocusPanel(id="total", classes="panel")
                yield TransferGraph(id="transfer", classes="panel")
            yield FocusPanel(id="activity", classes="panel")
        with Horizontal(id="workspace"):
            with Vertical(id="files-panel", classes="panel"):
                yield Tabs(Tab("Todos", id="all"), Tab("Ativos", id="active"),
                           Tab("Fila", id="queued"), Tab("Concluidos", id="done"),
                           Tab("Erros", id="errors"), id="file-tabs")
                yield Input(placeholder="Nome do arquivo", id="search")
                yield FileTable(id="files", cursor_type="row", zebra_stripes=True, cell_padding=1)
                yield Static("Nenhum arquivo na fila", id="empty")
            with Vertical(id="inspector"):
                yield FocusPanel(id="details", classes="panel")
                yield FocusPanel(id="instances", classes="panel")
        yield Static(id="summary")
        yield Footer()

    def on_mount(self):
        titles = {"account": "CONTA / SESSAO", "total": "TOTAL DA FILA",
                  "transfer": "TRANSFERENCIA TOTAL",
                  "activity": "ATIVIDADE ATUAL", "files-panel": "ARQUIVOS",
                  "details": "DETALHES DO ARQUIVO", "instances": "INSTANCIAS ATIVAS"}
        for identifier, title in titles.items():
            self.query_one(f"#{identifier}").border_title = title
        if self.demo:
            self._seed_demo()
        else:
            try:
                self.state["slot"], self.slot_handle = engine.acquire_slot()
            except RuntimeError as error:
                engine.add_log(self.state, "ERR", str(error))
            engine.add_log(self.state, "INFO", "Interface iniciada")
            if not self.offline:
                self.connection_task = asyncio.create_task(self._connect(interactive=False))
        self._resize_layout()
        # A atualização anterior, quatro vezes por segundo, reescrevia todas as
        # células da tabela mesmo quando nada mudava. Em filas grandes isso
        # saturava o loop do Textual e fazia o consumo de memória crescer sem
        # limite. Um segundo continua fluido para uma dashboard de downloads.
        self.set_interval(1.0, self.refresh_dashboard)
        self.query_one("#files", FileTable).focus()
        self.refresh_dashboard()

    def on_resize(self):
        if self.is_mounted:
            self._resize_layout()

    def _resize_layout(self):
        self.screen.set_class(self.size.width < 145, "compact")
        self.screen.set_class(self.size.width < 90, "narrow")
        self.screen.set_class(self.size.height < 28, "short")
        self.column_signature = None

    def _seed_demo(self):
        self.state.update(account="Leandro", username="demo", connected=False,
                          chat_title="Biblioteca Telegram", phase="demo")
        names = ["16_508_arquivos_58.2_GB.zip", "F213_01_Assistir_aula_completa.mp4",
                 "Pessoal_Projetos.zip", "Documentos_2026.zip", "Musicas_Colecao.rar",
                 "Filmes_SciFi.mkv", "Fotos_Viagem.zip", "Backup_Telegram.tar.gz",
                 "Series_Temp_2.zip", "Linux_ISOs.zip"]
        sizes = [58.2, 0.651, 1.0, 0.238, 3.3, 6.5, 0.413, 1.8, 6.3, 14]
        fractions = [.67, .23, .12, 0, .54, .18, 0, 0, 1, 0]
        for index, (name, size, fraction) in enumerate(zip(names, sizes, fractions), 1):
            item = engine.Download(index, SimpleNamespace(id=index, chat_id=1), name,
                                   Path(self.last_destination) / name, "Documentos",
                                   int(size * 1024 ** 3), "", {"title": "Demo", "reference": "", "id": "1", "link": ""})
            item.current_bytes = int(item.expected_size * fraction)
            item.status = "complete" if fraction == 1 else ("downloading" if fraction else "queued")
            item.speed = [42.6, 2.1, 1.8, 0, 3.4, 2.7, 0, 0, 0, 0][index - 1] * 1024 ** 2
            self.state["downloads"].append(item)
            if item.status == "downloading":
                self.state["active"][index] = {"obj": item}
        for index in range(100):
            self.history.append((28 + 10 * math.sin(index * .7) + 15 * abs(math.sin(index * 1.9))) * 1024 ** 2)
        engine.add_log(self.state, "INFO", "Demonstracao local: dados simulados")
        engine.add_log(self.state, "INFO", "Verificacao de arquivos concluida")
        engine.add_log(self.state, "OK", "Series_Temp_2.zip concluido")
        engine.add_log(self.state, "INFO", "Fila de transferencias ativa")

    def _advance_demo(self):
        self.tick_count += 1
        for item in self.state["downloads"]:
            if item.removed:
                item.status, item.speed = "removed", 0
                self.state["active"].pop(item.index, None)
            elif item.status in FINISHED:
                continue
            elif not item.resume_event.is_set():
                item.status, item.speed = "paused", 0
            elif item.index in self.state["active"]:
                item.status = "downloading"
                base = 42.6 if item.index == 1 else 2.3
                item.speed = base * (0.85 + .15 * math.sin(self.tick_count / 3 + item.index)) * 1024 ** 2
                item.current_bytes = min(item.expected_size, item.current_bytes + int(item.speed))
                item.eta = (item.expected_size - item.current_bytes) / max(1, item.speed)
                if item.current_bytes >= item.expected_size:
                    item.status, item.speed = "complete", 0
                    self.state["active"].pop(item.index, None)
        self.state["completed"] = sum(item.status == "complete" for item in self.state["downloads"])
        self.state["_completed_bytes"] = sum(item.expected_size for item in self.state["downloads"] if item.status == "complete")
        catalog_bytes = sum(item.expected_size for item in self.state["downloads"])
        catalog_files = len(self.state["downloads"])
        self.state.update(
            global_total_bytes=catalog_bytes,
            total_files=catalog_files,
            catalog_total_bytes=catalog_bytes,
            catalog_total_files=catalog_files,
            catalog_complete=True,
            discovered_files=catalog_files,
        )

    def refresh_dashboard(self):
        if self.closing:
            return
        if self.demo:
            self._advance_demo()
        if (
            self.client
            and getattr(self.client, "heartbeat", None)
            and self.client.heartbeat.done()
            and self.state["connected"]
        ):
            self.state.update(connected=False, phase_label="SERVICO DESCONECTADO")
            self.connection_error = "O servico compartilhado parou; pressione R para reconectar."
            engine.add_log(self.state, "ERR", self.connection_error)
        engine.recompute_transfer_state(self.state)
        now = time.monotonic()
        if now - self.last_sample >= 1:
            self.last_sample = now
            if self.slot_handle:
                engine.publish_instance_state(self.state["slot"], {
                    "chat": self.state["chat_title"], "speed": self.state["speed"],
                    "completed": self.state["completed"], "total": self.state["total_files"],
                    "skipped": self.state["skipped"], "errors": self.state["errors"],
                    "removed": self.state["removed"], "pending": self.state["pending_count"],
                    "bytes": self.state["global_bytes"], "active": len(self.state["active"]),
                    "phase": self.state["phase"], "phase_label": self.state["phase_label"],
                    "total_bytes": self.state["global_total_bytes"],
                    "available_bytes": self.state["global_bytes"],
                    "catalog_complete": self.state["catalog_complete"],
                })
            self.instances = [] if self.demo else engine.read_active_instances()
            total_speed = sum(item.get("speed", 0) for item in self.instances) if self.instances else self.state["speed"]
            self.history.append(total_speed)
        self._update_header()
        self._update_overview()
        self._update_files()
        self.query_one("#details", FocusPanel).update(self._detail_text())
        self.query_one("#instances", FocusPanel).update(self._instance_text())
        counts = self.state.get("status_counts", {})
        summary = Text()
        for label, count, color in [("Baixando", counts["downloading"], GREEN),
                                    ("Fila", counts["queued"], CYAN), ("Pausados", counts["paused"], YELLOW),
                                    ("Concluidos", counts["complete"] + counts["skipped"], GREEN),
                                    ("Erros", counts["error"], RED)]:
            summary.append(f"{label}: {count}   ", color)
        summary.append(f"Pulados: {counts['skipped']}", YELLOW)
        self.query_one("#summary", Static).update(summary)

    def _update_header(self):
        text = gradient("ANCHOR DOWNLOAD", bold=True)
        text.append(f"  v{__version__}", PINK)
        if self.size.width >= 100:
            text.append("  |  " + self.state.get("account", "")[:24], CYAN)
        status = "DEMO / DADOS SIMULADOS" if self.demo else self.state["phase_label"]
        text.append("   " + status, YELLOW if self.demo else GREEN if self.state["connected"] else MUTED)
        if self.size.width >= 125:
            text.append(datetime.now().strftime("   %H:%M:%S  %d/%m/%Y"), CYAN)
        text.no_wrap = True
        text.overflow = "ellipsis"
        self.query_one("#brand", Static).update(text)

    def _update_overview(self):
        state = self.state
        account = Text()
        account.append(state["account"] + "\n", "bold " + WHITE)
        username = state.get("username", "")
        account.append(("@" + username if username else "Sem usuario publico") + "\n", CYAN)
        if self.demo:
            session = "Simulacao"
        elif state["connected"]:
            session = "StringSession ativa"
        elif self.connection_task and not self.connection_task.done():
            session = "Conectando..."
        elif self.connection_error:
            session = "Falha na conexao"
        else:
            session = "Desconectada"
        account.append("Sessao   ", MUTED).append(session + "\n", GREEN if state["connected"] else YELLOW)
        account.append("Canal    ", MUTED).append((state["chat_title"] or "--") + "\n", WHITE)
        account.append(f"Slot {state['slot'] or '--'}   |   {state['concurrency']} downloads paralelos", BLUE)
        if self.connection_error:
            account.append("\nErro     ", RED).append(self.connection_error[:42], RED)
        self.query_one("#account", FocusPanel).update(account)
        total = self.query_one("#total", FocusPanel)
        total_bytes = state["global_total_bytes"]
        total_fraction = min(1, state["global_bytes"] / max(1, total_bytes))
        total_text = Text()
        sizing = state.get("phase") == "sizing" and not state.get("catalog_complete")
        total_text.append("Volume   ", MUTED).append(engine.format_bytes(total_bytes), WHITE)
        total_text.append("  calculando..." if sizing else "  confirmado", YELLOW if sizing else GREEN)
        total_text.append("\nDispon.  ", CYAN)
        total_text.append(
            f"{engine.format_bytes(state['global_bytes'])} / {engine.format_bytes(total_bytes)}"
            f"  ({total_fraction * 100:.1f}%)\n",
            CYAN,
        )
        total_text.append("Progresso ", MUTED)
        total_text.append_text(meter(total_fraction, max(6, min(24, total.content_size.width - 12))))
        total.update(total_text)
        graph = self.query_one("#transfer", TransferGraph)
        graph.history = list(self.history)
        graph.caption = f"{engine.format_speed(self.history[-1] if self.history else 0)}   /   {len(self.instances) or (1 if self.demo else 0)} instancia(s)"
        graph.refresh()
        activity = Text()
        activity.append("Download  ", CYAN).append(engine.format_speed(state["speed"]) + "\n", WHITE)
        activity.append("Pico      ", MUTED).append(engine.format_speed(state["peak_speed"]) + "\n", PINK)
        activity.append("Dispon.   ", MUTED).append(engine.format_bytes(state["global_bytes"]) + "\n", BLUE)
        activity.append("Total     ", MUTED).append(engine.format_bytes(total_bytes) + "\n", WHITE)
        activity.append("ETA       ", MUTED).append(engine.format_eta(state.get("eta")) + "\n", WHITE)
        activity.append("Arquivos  ", MUTED).append(
            f"{state['completed'] + state['skipped']}/{state['total_files']}\n", WHITE
        )
        activity.append("Pulados   ", MUTED).append(str(state["skipped"]) + "\n", YELLOW)
        remaining = max(0, total_bytes - state["global_bytes"])
        activity.append("Restante  ", MUTED).append(engine.format_bytes(remaining), WHITE)
        self.query_one("#activity", FocusPanel).update(activity)

    @staticmethod
    def _status(item):
        if item.removed:
            return "removed"
        if item.status not in FINISHED and not item.resume_event.is_set():
            return "paused"
        return item.status

    def _matches(self, item):
        status = self._status(item)
        groups = {"all": set(STATUS), "active": {"downloading", "paused"},
                  "queued": {"queued"}, "done": {"complete", "skipped"}, "errors": {"error"}}
        return status in groups[self.filter_name] and self.search_text.casefold() in item.filename.casefold()

    def _update_files(self):
        table = self.query_one("#files", FileTable)
        width = max(30, table.size.width - 2)
        if width >= 112:
            specs = [("#", 4), ("Arquivo", width - 88), ("Progresso", 17), ("Velocidade", 12),
                     ("Tamanho", 17), ("ETA", 8), ("Status", 12)]
        elif width >= 80:
            specs = [("#", 4), ("Arquivo", width - 55), ("Progresso", 17), ("Velocidade", 12), ("Status", 12)]
        else:
            specs = [("#", 3), ("Arquivo", max(10, width - 35)), ("Progresso", 13), ("Status", 11)]
        signature = tuple(specs)
        filtered = [item for item in self.state["downloads"] if self._matches(item)]
        ids = tuple(item.index for item in filtered)
        columns_changed = signature != self.column_signature
        cache_invalidated = self.row_signature is None
        previous_ids = self.row_signature or ()
        rendered_keys = tuple(row_key.value for row_key in table.rows)
        cache_matches_table = rendered_keys == tuple(str(index) for index in previous_ids)
        append_only = (
            not cache_invalidated
            and not columns_changed
            and cache_matches_table
            and len(ids) >= len(previous_ids)
            and ids[:len(previous_ids)] == previous_ids
        )
        rebuild = (
            columns_changed
            or cache_invalidated
            or not cache_matches_table
            or (ids != previous_ids and not append_only)
        )
        selected = self.selected_id
        old_row = table.cursor_row
        self.visible_downloads = filtered
        if columns_changed:
            table.clear(columns=True)
            for name, col_width in specs:
                table.add_column(name, width=col_width, key=name)
            self.row_snapshots.clear()
        elif rebuild:
            table.clear()
            self.row_snapshots.clear()

        start = 0 if rebuild else len(previous_ids)
        for item in filtered[:start]:
            snapshot = self._file_snapshot(item)
            if self.row_snapshots.get(item.index) == snapshot:
                continue
            cells = self._file_cells(item, width)
            for name, _ in specs:
                table.update_cell(str(item.index), name, cells[name], update_width=False)
            self.row_snapshots[item.index] = snapshot

        for item in filtered[start:]:
            cells = self._file_cells(item, width)
            table.add_row(*(cells[name] for name, _ in specs), key=str(item.index))
            self.row_snapshots[item.index] = self._file_snapshot(item)

        if rebuild and filtered:
            index = next((i for i, item in enumerate(filtered) if item.index == selected), min(old_row, len(filtered) - 1))
            table.move_cursor(row=index, animate=False)
            self.selected_id = filtered[index].index
        elif filtered and self.selected_id is None:
            table.move_cursor(row=0, animate=False)
            self.selected_id = filtered[0].index
        elif not filtered:
            self.selected_id = None
        self.column_signature, self.row_signature = signature, ids
        empty = self.query_one("#empty", Static)
        empty.display = not filtered
        empty.update("Nenhum resultado" if self.state["downloads"] else "Nenhum arquivo na fila")
        self.query_one("#files-panel").border_title = f"ARQUIVOS ({len(filtered)}/{len(self.state['downloads'])})"

    def _file_snapshot(self, item):
        """Somente valores primitivos que alteram uma linha da tabela."""
        return (
            item.current_bytes,
            round(item.speed),
            round(item.eta) if item.eta is not None else None,
            self._status(item),
            item.filename,
            item.expected_size,
        )

    def _file_cells(self, item, width):
        fraction = item.current_bytes / max(1, item.expected_size)
        status, color = STATUS.get(self._status(item), (item.status, WHITE))
        progress = meter(fraction, 8 if width < 80 else 11)
        progress.append(f" {min(100, fraction * 100):3.0f}%", CYAN)
        return {"#": Text(f"{item.index:02d}", MUTED), "Arquivo": Text(item.filename, WHITE),
                "Progresso": progress, "Velocidade": Text(engine.format_speed(item.speed), CYAN),
                "Tamanho": Text(engine.format_bytes(item.expected_size), BLUE),
                "ETA": Text(engine.format_eta(item.eta), MUTED), "Status": Text(status, color)}

    def _selected(self):
        return next((item for item in self.state["downloads"] if item.index == self.selected_id), None)

    @on(DataTable.RowHighlighted, "#files")
    def row_highlighted(self, event):
        if event.row_key.value is not None:
            self.selected_id = int(event.row_key.value)

    @on(DataTable.RowSelected, "#files")
    def row_selected(self):
        self.action_details()

    def _detail_text(self):
        item = self._selected()
        if item is None:
            return Text("Nenhum arquivo selecionado", MUTED)
        result = Text(item.filename + "\n\n", "bold " + CYAN)
        fraction = item.current_bytes / max(1, item.expected_size)
        result.append_text(meter(fraction, 17))
        result.append(f" {min(100, fraction * 100):.0f}%\n", CYAN)
        fields = [("Estado", STATUS[self._status(item)][0]),
                  ("Recebido", engine.format_bytes(item.current_bytes)),
                  ("Tamanho", engine.format_bytes(item.expected_size)),
                  ("Velocidade", engine.format_speed(item.speed)),
                  ("ETA", engine.format_eta(item.eta)), ("Categoria", item.category),
                  ("Destino", str(item.target)), ("Origem", item.message_link or self.state["chat_title"])]
        if getattr(item, "error", ""):
            fields.append(("Erro", item.error))
        for name, value in fields:
            result.append(name + "  ", MUTED).append(str(value) + "\n", WHITE)
        return result

    def _instance_text(self):
        result = Text()
        if self.demo:
            result.append("DEMO / sem conexao real\n", YELLOW)
            result.append_text(meter(.67, 15))
            return result
        for instance in self.instances:
            finished = instance.get("completed", 0) + instance.get("skipped", 0)
            total = instance.get("total", 0)
            phase = instance.get("phase_label") or ("ATIVA" if instance.get("active") else "AGUARDANDO")
            result.append(f"#{instance.get('slot', '?')}  ", CYAN)
            result.append(f"{phase[:18]}  ", WHITE)
            result.append(f"{finished}/{total}  ", BLUE)
            result.append(engine.format_speed(instance.get("speed", 0)) + "\n", GREEN)
            result.append((instance.get("chat") or "Aguardando canal") + "\n", MUTED)
        if not self.instances:
            result.append("Nenhuma instancia publicada", MUTED)
        return result

    @on(Tabs.TabActivated, "#file-tabs")
    def filter_changed(self, event):
        self.filter_name = event.tab.id
        self.row_signature = None

    @on(Input.Changed, "#search")
    def search_changed(self, event):
        self.search_text = event.value
        self.row_signature = None

    @on(Input.Submitted, "#search")
    def search_submitted(self):
        self.query_one("#files", FileTable).focus()

    def action_search(self):
        search = self.query_one("#search", Input)
        search.add_class("visible")
        search.focus()

    def action_back(self):
        if isinstance(self.screen, ModalScreen):
            return
        search = self.query_one("#search", Input)
        search.value = ""
        search.remove_class("visible")
        self.action_focus_files()

    def action_focus_files(self):
        self.query_one("#files", FileTable).focus()

    def action_details(self):
        self.push_screen(MessageScreen("Detalhes do arquivo", self._detail_text().plain))

    def action_show_instances(self):
        self.push_screen(MessageScreen("Instancias ativas", self._instance_text().plain))

    def action_pause_selected(self):
        item = self._selected()
        if item is None or self._status(item) in FINISHED:
            return
        if item.resume_event.is_set():
            item.resume_event.clear()
            item.speed = 0
            engine.add_log(self.state, "WARN", f"Pausado: {item.filename}")
        else:
            item.resume_event.set()
            engine.add_log(self.state, "INFO", f"Retomado: {item.filename}")
        self.refresh_dashboard()

    def action_pause_all(self):
        items = [item for item in self.state["downloads"] if self._status(item) not in FINISHED]
        pause = any(item.resume_event.is_set() for item in items)
        for item in items:
            if pause:
                item.resume_event.clear()
                item.speed = 0
            else:
                item.resume_event.set()
        engine.add_log(self.state, "INFO", "Fila pausada" if pause else "Fila retomada")
        self.refresh_dashboard()

    def action_remove_selected(self):
        item = self._selected()
        if item is None or self._status(item) in FINISHED:
            return
        item.removed = True
        item.resume_event.set()
        item.speed = 0
        engine.add_log(self.state, "WARN", f"Removido da fila: {item.filename}")
        self.refresh_dashboard()

    def action_new_download(self):
        if self.demo:
            self.notify("Demonstracao: downloads reais desativados.")
            return
        if self.download_task and not self.download_task.done():
            self.notify("Uma fila ja esta em andamento nesta instancia.", severity="warning")
            return
        self.push_screen(NewDownloadScreen(self.last_destination, self.state["concurrency"]), self._new_download)

    def _new_download(self, values):
        if values:
            self.run_worker(self._start_download(values), exclusive=True, group="start")

    async def _start_download(self, values):
        if not self.state["connected"]:
            if self.connection_task and not self.connection_task.done():
                await self.connection_task
            if not self.state["connected"]:
                self.notify("Conecte a conta em F3 antes de iniciar.", severity="warning")
                return
        preserved = {key: self.state.get(key) for key in ("slot", "account", "username", "connected")}
        self.state = engine.create_state(preserved["slot"], values["concurrency"])
        self.state.update(preserved)
        self.state["destination"] = values["destination"]
        self.last_destination = values["destination"]
        self.row_signature = None
        self.query_one("#file-tabs", Tabs).active = "all"
        self.query_one("#search", Input).value = ""
        self.download_task = asyncio.create_task(self._download(values))
        self.action_focus_files()

    async def _download(self, values):
        # The task itself is created while Textual's eager factory is active,
        # so the context must cover the whole coroutine, including exported
        # Telegram file-DC connections opened by iter_download().
        with _telethon_loop_context():
            try:
                self.state["phase_label"] = "RESOLVENDO CANAL"
                chat = await engine.resolve_chat(self.client, values["link"])
                await engine.download_messages(self.client, chat, values["destination"], self.state)
                log_path = self.state.get("log_path")
                message = "Fila finalizada"
                if log_path:
                    message += f"\nLog salvo em: {log_path}"
                self.notify(message, title="Anchor Download", timeout=8)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Falha na fila de download")
                self.state["phase_label"] = "ERRO NA TRANSFERENCIA"
                engine.add_log(self.state, "ERR", str(error))
                message = str(error)
                if self.state.get("log_path"):
                    message += f"\nLog salvo em: {self.state['log_path']}"
                self.notify(message, severity="error", timeout=8)

    def action_settings(self):
        if self.demo:
            self.notify("Configuracoes reais indisponiveis na demonstracao.")
            return
        self.push_screen(SettingsScreen(self.state["concurrency"]), self._settings_saved)

    def _settings_saved(self, values):
        if not values:
            return
        self.state["concurrency"] = values["concurrency"]
        engine.add_log(self.state, "INFO", "Configuracao salva; concorrencia aplicada a proxima fila")
        if values.get("login"):
            if self.download_task and not self.download_task.done():
                self.notify("Aguarde a fila terminar antes de reconectar.", severity="warning")
            elif not self.connection_task or self.connection_task.done():
                self.connection_task = asyncio.create_task(self._connect(interactive=True))

    async def _prompt(self, title, label, password=False):
        future = asyncio.get_running_loop().create_future()
        def finish(value):
            if not future.done():
                future.set_result(value)
        self.push_screen(PromptScreen(title, label, password=password), finish)
        return await future

    async def _connect(self, interactive=False):
        with _telethon_loop_context():
            try:
                self.connection_error = ""
                self.state["phase_label"] = "CONECTANDO"
                if self.client:
                    await self.client.disconnect()
                    self.client = None
                self.state["connected"] = False
                self.client, concurrency = await shared_client(
                    self._prompt if interactive else None,
                )
                me = await self.client.get_me()
                self.state.update(connected=True, account=getattr(me, "first_name", "") or "Telegram",
                                  username=getattr(me, "username", "") or "", concurrency=concurrency,
                                  phase_label="PRONTO", phase="ready")
                engine.add_log(self.state, "OK", "Conta conectada ao Telegram")
            except asyncio.CancelledError:
                raise
            except (Exception, AuthCancelled) as error:
                logger.exception("Falha ao conectar a interface ao Telegram")
                detail = str(error).strip()
                self.connection_error = f"{type(error).__name__}: {detail}" if detail else type(error).__name__
                self.state.update(connected=False, phase_label="FALHA NA CONEXAO")
                engine.add_log(self.state, "ERR", self.connection_error)
            finally:
                if not self.state["connected"] and self.client:
                    await self.client.disconnect()
                    self.client = None

    def action_reconnect(self):
        if self.demo:
            self.notify("A demonstracao nao usa uma conexao real.")
            return
        if self.offline:
            self.notify("O modo offline esta ativo; reinicie sem --offline para conectar.", severity="warning")
            return
        if self.state["connected"]:
            self.notify("A conta ja esta conectada.")
            return
        if self.connection_task and not self.connection_task.done():
            self.notify("A conexao ja esta em andamento.")
            return
        self.connection_task = asyncio.create_task(self._connect(interactive=False))

    def action_open_destination(self):
        destination = Path(self.state["destination"] or self.last_destination)
        if self.demo or not destination.is_dir():
            self.notify("Pasta de destino ainda nao existe.")
            return
        subprocess.Popen(["xdg-open", str(destination)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def action_help(self):
        self.push_screen(MessageScreen("Teclas", "\n".join([
            "Setas / j k     Selecionar arquivo", "Tab / Shift+Tab Mudar de painel ou campo",
            "PgUp / PgDn     Rolar lista", "Enter           Detalhes do arquivo",
            "Espaco          Pausar / retomar selecionado", "P               Pausar / retomar toda a fila",
            "Delete          Remover da fila", "/               Buscar arquivo",
            "N               Novo download", "F3              Configuracoes e conta",
            "R               Reconectar usando a sessao salva",
            "1 / 2 / 4       Arquivos / detalhes / instancias",
            "Log             Arquivo TXT na pasta de destino", "Shift+O         Abrir pasta de destino",
            "Esc             Voltar", "Q / Ctrl+C      Sair",
        ])))

    def action_request_quit(self):
        if self.closing:
            return
        if self.download_task and not self.download_task.done():
            self.push_screen(ConfirmScreen("Encerrar downloads", "Os arquivos parciais serao mantidos para retomada.", "Encerrar"),
                             lambda answer: self.run_worker(self._quit()) if answer else None)
        else:
            self.run_worker(self._quit())

    async def _quit(self):
        self.closing = True
        await self._cleanup()
        self.exit()

    async def _cleanup(self):
        self.state["stop"] = True
        for task in (self.download_task, self.connection_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self.client:
            with contextlib.suppress(Exception):
                await self.client.disconnect()
            self.client = None
        if self.slot_handle:
            with contextlib.suppress(OSError):
                engine.instance_state_path(self.state["slot"]).unlink(missing_ok=True)
            self.slot_handle.close()
            self.slot_handle = None

    async def on_unmount(self):
        await self._cleanup()

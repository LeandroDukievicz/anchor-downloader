"""Gera as capturas de tela da documentacao a partir do modo demonstracao.

A interface real e aberta num terminal virtual (`App.run_test`), navegada tela
por tela e exportada em SVG; o SVG vira PNG pelo Chrome headless, que e o unico
conversor local que respeita a fonte monoespacada do terminal — com um
conversor comum as colunas saem desalinhadas.

Nenhuma conta e tocada: o app roda em modo demonstracao, com dados simulados, e
a configuracao vem de um diretorio temporario — assim o dialogo de
configuracoes aparece vazio em vez de mostrar o API ID e o API Hash de quem
gerou as imagens. O destino tambem e fixado em /home/usuario/... pelo mesmo
motivo: o caminho pessoal nao vai para a documentacao.

Uso:
    .venv/bin/python tools/screenshots.py [--keep-svg]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "screenshots"
DESTINATION = "/home/usuario/Downloads/Telegram"

# Tamanhos em celulas do terminal.
# 150 colunas mantem os tres paineis do topo visiveis (abaixo de 145 o app
# entra no layout compacto) e 44 linhas cabem a lista inteira sem rolagem.
WIDE = (150, 44)
# Os modais tem 72 colunas fixas: um terminal de 100 os enquadra sem sobrar
# tela vazia em volta.
MODAL = (100, 32)
# Abaixo de 90 colunas o app esconde os paineis laterais; e o layout de quem
# usa um terminal dividido ao meio.
NARROW = (84, 30)
# A tabela de arquivos so ganha as colunas Tamanho e ETA quando sobram 112
# celulas para ela — o que pede um terminal perto de 180 colunas.
EXTRA_WIDE = (190, 44)

sys.path.insert(0, str(ROOT / "src"))


def _sandbox() -> Path:
    """Diretorio de configuracao descartavel, isolado do usuario real."""
    directory = Path(tempfile.mkdtemp(prefix="anchor-screenshots-"))
    os.environ["ANCHOR_DOWNLOADER_HOME"] = str(directory)
    return directory


# Definido antes de importar o pacote: o modulo de diagnostico decide onde
# gravar o log ja no import.
SANDBOX = _sandbox()

from textual.widgets import Input  # noqa: E402

from anchor_downloader import splash  # noqa: E402
from anchor_downloader.app import DownloaderApp  # noqa: E402
from anchor_downloader.dialogs import (  # noqa: E402
    ConfirmScreen,
    NewDownloadScreen,
    PromptScreen,
    SettingsScreen,
)


class Session:
    """Um app de demonstracao aberto num terminal virtual de tamanho fixo."""

    def __init__(self, svg_dir: Path, names: list[str], size) -> None:
        self.svg_dir = svg_dir
        self.names = names
        self.size = size

    async def __aenter__(self):
        self.app = DownloaderApp(demo=True, opening=False)
        # Definido antes do mount: `_seed_demo` monta os caminhos dos arquivos
        # simulados a partir daqui, e e este valor que aparece em DETALHES DO
        # ARQUIVO.
        self.app.last_destination = DESTINATION
        self._session = self.app.run_test(size=self.size)
        self.pilot = await self._session.__aenter__()
        await self.pilot.pause()
        # Um ciclo de atualizacao tira as barras de progresso do zero.
        self.app.refresh_dashboard()
        await self.pilot.pause()
        return self

    async def __aexit__(self, *exc):
        return await self._session.__aexit__(*exc)

    async def shot(self, name: str) -> None:
        await self.pilot.pause()
        (self.svg_dir / f"{name}.svg").write_text(
            self.app.export_screenshot(), encoding="utf-8"
        )
        self.names.append(name)

    async def modal(self, screen, name: str, fill: dict[str, str] | None = None) -> None:
        """Abre um modal, preenche campos opcionais, captura e fecha.

        Em demonstracao as teclas `n` e `f3` so avisam que nao ha conta real,
        entao os dialogos sao empilhados direto — sao as mesmas telas de uma
        sessao autenticada, sem nada por tras.
        """
        self.app.push_screen(screen)
        await self.pilot.pause()
        for selector, value in (fill or {}).items():
            self.app.screen.query_one(selector, Input).value = value
        await self.pilot.pause()
        await self.shot(name)
        await self.pilot.press("escape")
        await self.pilot.pause()


async def _opening(svg_dir: Path, names: list[str]) -> None:
    """Dois instantes da abertura: a ancora descendo e a marca formada.

    A abertura e uma tela do app como outra qualquer, entao ela e capturada
    subindo o app de verdade e adiantando os quadros a mao — sem esperar os
    segundos passarem, que deixariam a geracao das imagens lenta e instavel.
    """
    # No instante exato do piso a abertura se fecha e quem aparece e o painel.
    # O ultimo quadro antes disso e onde a marca ja esta inteira na tela.
    ultimo = splash.FLOOR_SECONDS - splash.FRAME_SECONDS
    for name, target in (("abertura", 2.2), ("abertura-marca", ultimo)):
        app = DownloaderApp(demo=True, opening=True)
        app.last_destination = DESTINATION
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            tela = app.screen
            assert isinstance(tela, splash.SplashScreen), "a abertura nao subiu"
            # Sem congelar o timer, o tempo real corre durante o `pause()` e a
            # abertura passa do instante pedido — chegando a se fechar sozinha.
            tela.timer.pause()
            # Um quadro a menos que o alvo: o proprio _tick soma o ultimo.
            tela.elapsed = target - splash.FRAME_SECONDS
            tela._tick()
            await pilot.pause()
            assert isinstance(app.screen, splash.SplashScreen), (
                f"a abertura fechou antes de {name} ser capturada"
            )
            (svg_dir / f"{name}.svg").write_text(app.export_screenshot(), encoding="utf-8")
            names.append(name)


async def capture(svg_dir: Path) -> list[str]:
    names: list[str] = []

    await _opening(svg_dir, names)

    # ── Painel completo, abas, busca e pausa ─────────────────────────────
    async with Session(svg_dir, names, WIDE) as session:
        app, pilot = session.app, session.pilot
        await session.shot("dashboard")

        for name in ("aba-ativos", "aba-fila", "aba-concluidos", "aba-erros"):
            app.query_one("#file-tabs").focus()
            await pilot.press("right")
            await pilot.pause()
            app.refresh_dashboard()
            await session.shot(name)

        app.query_one("#file-tabs").active = "all"
        app.refresh_dashboard()
        await pilot.pause()

        await pilot.press("slash")
        app.query_one("#search", Input).value = "zip"
        await pilot.pause()
        app.refresh_dashboard()
        await session.shot("busca")
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("p")
        await pilot.pause()
        await session.shot("fila-pausada")

    # ── Layouts por largura de terminal ──────────────────────────────────
    async with Session(svg_dir, names, EXTRA_WIDE) as session:
        await session.shot("tabela-completa")

    async with Session(svg_dir, names, NARROW) as session:
        await session.shot("layout-estreito")

    # ── Modais ───────────────────────────────────────────────────────────
    async with Session(svg_dir, names, MODAL) as session:
        pilot = session.pilot

        await pilot.press("2")
        await session.shot("detalhes-do-arquivo")
        await pilot.press("escape")
        await pilot.pause()

        # O painel de instancias so tem o que mostrar quando ha varias janelas
        # abertas, e a demonstracao nao publica estado nenhum. Estes tres
        # registros simulados passam pelo renderizador de verdade da interface.
        app = session.app
        app.demo = False
        app.instances = [
            {"slot": 1, "phase_label": "BAIXANDO", "completed": 812, "skipped": 44,
             "total": 1620, "speed": 42.6 * 1024 ** 2, "chat": "Biblioteca Telegram"},
            {"slot": 2, "phase_label": "CALCULANDO VOLUME", "completed": 0,
             "skipped": 0, "total": 0, "speed": 0, "chat": "Arquivo de Aulas"},
            {"slot": 3, "phase_label": "BAIXANDO", "completed": 97, "skipped": 3,
             "total": 210, "speed": 7.4 * 1024 ** 2, "chat": "Fotos de Viagem"},
        ]
        app.action_show_instances()
        await session.shot("instancias")
        await pilot.press("escape")
        await pilot.pause()
        app.demo = True

        await pilot.press("f1")
        await session.shot("ajuda")
        await pilot.press("escape")
        await pilot.pause()

        await session.modal(
            NewDownloadScreen(DESTINATION, 4), "novo-download",
            {"#new-link": "https://t.me/telegram"},
        )
        await session.modal(
            NewDownloadScreen(DESTINATION, 4), "novo-download-link-invalido",
            {"#new-link": "meu canal favorito"},
        )
        await session.modal(SettingsScreen(4), "configuracoes")
        await session.modal(
            PromptScreen("CONECTAR AO TELEGRAM",
                         "Telefone com codigo do pais (ex.: +5511999999999)"),
            "login-telefone", {"#prompt-value": "+5511999999999"},
        )
        await session.modal(
            PromptScreen("CODIGO DO TELEGRAM",
                         "Codigo de verificacao recebido no Telegram ou SMS"),
            "login-codigo",
        )
        await session.modal(
            PromptScreen("VERIFICACAO EM DUAS ETAPAS",
                         "Senha de verificacao em duas etapas", password=True),
            "login-duas-etapas", {"#prompt-value": "senha-de-exemplo"},
        )
        await session.modal(
            ConfirmScreen("Encerrar downloads",
                          "Os arquivos parciais serao mantidos para retomada.",
                          "Encerrar"),
            "sair",
        )

    return names


def _browser() -> str:
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit(
        "Nenhum Chrome/Chromium encontrado; ele e usado para converter SVG em PNG."
    )


def to_png(svg_dir: Path, names: list[str]) -> None:
    """Rasteriza cada SVG no tamanho exato do seu viewBox."""
    browser = _browser()
    profile = Path(tempfile.mkdtemp(prefix="anchor-screenshots-chrome-"))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        for name in names:
            source = svg_dir / f"{name}.svg"
            markup = source.read_text(encoding="utf-8")
            box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', markup)
            width, height = (round(float(value)) for value in box.groups())
            # O SVG do Rich so traz viewBox; sem largura e altura explicitas o
            # Chrome o desenharia esticado no tamanho da janela.
            sized = svg_dir / f"{name}.sized.svg"
            sized.write_text(
                markup.replace('<svg class="rich-terminal"',
                               f'<svg width="{width}" height="{height}" class="rich-terminal"', 1),
                encoding="utf-8",
            )
            target = OUTPUT / f"{name}.png"
            subprocess.run(
                [browser, "--headless", "--disable-gpu", "--no-sandbox",
                 "--hide-scrollbars", "--force-device-scale-factor=1",
                 f"--user-data-dir={profile}",
                 f"--window-size={width},{height}",
                 f"--screenshot={target}", str(sized)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if not target.is_file():
                raise SystemExit(f"O Chrome nao gerou {target.name}.")
            print(f"  {target.relative_to(ROOT)}  {width}x{height}")
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera as capturas do README.")
    parser.add_argument("--keep-svg", action="store_true",
                        help="manter os SVG intermediarios para inspecao")
    args = parser.parse_args()
    svg_dir = Path(tempfile.mkdtemp(prefix="anchor-screenshots-svg-"))
    try:
        names = asyncio.run(capture(svg_dir))
        print(f"{len(names)} telas capturadas; convertendo para PNG:")
        to_png(svg_dir, names)
        if args.keep_svg:
            print(f"SVG em {svg_dir}")
    finally:
        if not args.keep_svg:
            shutil.rmtree(svg_dir, ignore_errors=True)
        shutil.rmtree(SANDBOX, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

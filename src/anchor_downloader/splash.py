"""Abertura: a ancora desce, fundeia, e o mar entrega a marca.

O desenho de cada quadro e uma funcao pura de (tempo, largura, altura), sem
tocar em widget nenhum. Isso deixa a animacao testavel sem subir a interface e
permite renderizar quadro a quadro para conferir o resultado com os olhos.

A abertura nao e enfeite parado: enquanto ela roda, a conexao com o Telegram
acontece por baixo. Quem ja tem sessao salva chega no painel ja conectado, e o
tempo da animacao foi gasto em algo util em vez de virar espera.

Detalhe que decide o visual: a agua e pintada como cor de FUNDO das celulas.
Espaco com cor de frente nao pinta nada, e a primeira versao desta tela ficou
um vazio preto justamente por isso.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from rich.text import Text
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from .widgets import color_at, CYAN

# Orcamento de tempo. O piso existe para o movimento ser lido como movimento; o
# teto existe para a abertura nunca virar sala de espera quando a rede esta
# ruim. Entre os dois quem manda e a conexao: assim que ela responde, a abertura
# termina no proximo marco visual.
DESCENT_SECONDS = 3.1
IMPACT_SECONDS = 0.8
BRAND_SECONDS = 1.6
FLOOR_SECONDS = DESCENT_SECONDS + IMPACT_SECONDS + BRAND_SECONDS  # 5.5
CEILING_SECONDS = 7.0
FRAME_SECONDS = 1 / 24

# Abaixo disto nao cabe o desenho, e uma abertura espremida e pior do que
# nenhuma: o app entra direto no painel.
MIN_WIDTH = 48
MIN_HEIGHT = 20

# Acima disto a marca aparece em letras garrafais em vez de texto espacado.
BLOCK_WIDTH = 66
BLOCK_HEIGHT = 27

SURFACE = "#0e5b82"   # linha d'agua, onde a luz bate
SHALLOW = "#0a4260"   # agua logo abaixo da superficie
ABYSS = "#04101c"     # fundo, para onde tudo escurece
BED_FILL = "#16232f"  # terra abaixo do leito
BED_GLYPH = "#33485f" # relevo do leito
SAND = "#c6b489"      # areia levantada no impacto
CHAIN = "#5d738c"     # corrente que desce da superficie

ANCHOR = (
    "        ▄███▄        ",
    "       ██▀ ▀██       ",
    "       ██   ██       ",
    "        ▀█▄█▀        ",
    "  ▄▄▄▄▄▄▄███▄▄▄▄▄▄▄  ",
    "  ▀▀▀▀▀▀▀███▀▀▀▀▀▀▀  ",
    "         ███         ",
    "  ██     ███     ██  ",
    "  ██▄    ███    ▄██  ",
    "   ▀██▄▄▄███▄▄▄██▀   ",
    "     ▀▀███████▀▀     ",
)
ANCHOR_WIDTH = len(ANCHOR[0])
ANCHOR_HEIGHT = len(ANCHOR)

# Fonte de bloco 5x5, so com as letras de ANCHOR e DOWNLOADER.
GLYPHS = {
    "A": (" ███ ", "█   █", "█████", "█   █", "█   █"),
    "C": (" ████", "█    ", "█    ", "█    ", " ████"),
    "D": ("████ ", "█   █", "█   █", "█   █", "████ "),
    "E": ("█████", "█    ", "████ ", "█    ", "█████"),
    "H": ("█   █", "█   █", "█████", "█   █", "█   █"),
    "L": ("█    ", "█    ", "█    ", "█    ", "█████"),
    "N": ("█   █", "██  █", "█ █ █", "█  ██", "█   █"),
    "O": (" ███ ", "█   █", "█   █", "█   █", " ███ "),
    "R": ("████ ", "█   █", "████ ", "█  █ ", "█   █"),
    "W": ("█   █", "█   █", "█ █ █", "██ ██", "█   █"),
}
GLYPH_HEIGHT = 5

SEABED = "▂▃▂▄▃▂▃▄▂▃▂▄▃▂▃▄▃▂▄▂▃"


def _mix(start: str, end: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    channels = [
        round(int(start[i:i + 2], 16) * (1 - amount) + int(end[i:i + 2], 16) * amount)
        for i in (1, 3, 5)
    ]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _ease_in(value: float) -> float:
    """A ancora acelera enquanto afunda, como qualquer peso na agua."""
    return value * value


def fits(width: int, height: int) -> bool:
    return width >= MIN_WIDTH and height >= MIN_HEIGHT


@dataclass
class Cell:
    glyph: str = " "
    fore: str = ""
    back: str = ""
    bold: bool = False

    @property
    def style(self) -> str:
        parts = []
        if self.bold:
            parts.append("bold")
        if self.fore:
            parts.append(self.fore)
        if self.back:
            parts.append("on " + self.back)
        return " ".join(parts)


@dataclass
class Scene:
    width: int
    height: int
    cells: list[list[Cell]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cells = [[Cell() for _ in range(self.width)] for _ in range(self.height)]

    @property
    def bed_row(self) -> int:
        """Primeira linha do fundo do mar."""
        return self.height - 3

    @property
    def rest_row(self) -> int:
        """Linha do topo da ancora depois de fundeada."""
        return self.bed_row - ANCHOR_HEIGHT + 1

    @property
    def axis(self) -> int:
        return self.width // 2

    def put(self, row: int, column: int, glyph: str, fore: str = "",
            bold: bool = False) -> None:
        if 0 <= row < self.height and 0 <= column < self.width:
            cell = self.cells[row][column]
            cell.glyph = glyph
            if fore:
                cell.fore = fore
            cell.bold = bold

    def to_text(self) -> Text:
        result = Text(no_wrap=True)
        for index, row in enumerate(self.cells):
            for cell in row:
                result.append(cell.glyph, cell.style)
            if index != self.height - 1:
                result.append("\n")
        return result


def _paint_water(scene: Scene, elapsed: float) -> None:
    """Agua como fundo, do azul de superficie ao abismo."""
    for row in range(scene.bed_row):
        depth = row / max(1, scene.bed_row - 1)
        shade = _mix(SHALLOW, ABYSS, depth ** 0.8)
        for column in range(scene.width):
            scene.cells[row][column].back = shade
    # Superficie: ondas correndo devagar, com a luz vindo de cima.
    for column in range(scene.width):
        wave = math.sin(column / 3.7 + elapsed * 1.4)
        scene.cells[0][column].back = SURFACE
        scene.put(0, column, "~" if wave > 0.1 else "-" if wave > -0.5 else " ",
                  _mix(CYAN, SURFACE, 0.25))
    # Feixes de luz descendo em diagonal, bem discretos.
    for beam in range(3):
        origin = int(scene.width * (0.2 + 0.3 * beam))
        for row in range(1, min(scene.bed_row, scene.height // 2)):
            column = origin + row
            if 0 <= column < scene.width:
                cell = scene.cells[row][column]
                cell.back = _mix(cell.back, SURFACE, 0.22 * (1 - row / (scene.height / 2)))


def _paint_bed(scene: Scene) -> None:
    for column in range(scene.width):
        scene.cells[scene.bed_row][column].back = _mix(SHALLOW, ABYSS, 0.9)
        scene.put(scene.bed_row, column, SEABED[column % len(SEABED)], BED_GLYPH)
        for row in range(scene.bed_row + 1, scene.height):
            scene.cells[row][column].back = BED_FILL
            scene.put(row, column, " ")


def _paint_bubbles(scene: Scene, elapsed: float) -> None:
    """Bolhas subindo em posicoes deterministicas.

    Sem `random`: a posicao sai de senos, entao a animacao e identica a cada
    execucao e um teste consegue afirmar sobre ela.
    """
    for index in range(18):
        column = int(abs(math.sin(index * 12.9898) * 43758.5453) % scene.width)
        speed = 2.2 + (index % 5) * 0.8
        phase = (index * 0.37 + elapsed * speed / max(1, scene.bed_row)) % 1.0
        row = int((1 - phase) * (scene.bed_row - 2)) + 1
        if 0 < row < scene.bed_row:
            scene.put(row, column, "·∘°"[index % 3], _mix(CYAN, ABYSS, 0.35))


def _anchor_row(elapsed: float, scene: Scene) -> tuple[int, bool]:
    if elapsed >= DESCENT_SECONDS:
        return scene.rest_row, True
    progress = _ease_in(elapsed / DESCENT_SECONDS)
    # Comeca com a ancora ja mordendo a borda de cima: saindo de -ANCHOR_HEIGHT,
    # a aceleracao quadratica deixava quase um segundo de tela vazia no inicio.
    start = -ANCHOR_HEIGHT + 4
    return round(start + (scene.rest_row - start) * progress), False


def _paint_chain(scene: Scene, top_row: int) -> None:
    for row in range(1, max(1, top_row + 1)):
        scene.put(row, scene.axis, "┆", _mix(CHAIN, ABYSS, row / max(1, scene.height)))


def _paint_anchor(scene: Scene, top_row: int, fade: float) -> None:
    left = scene.axis - ANCHOR_WIDTH // 2
    for line_index, line in enumerate(ANCHOR):
        row = top_row + line_index
        if not 0 <= row < scene.height:
            continue
        for offset, glyph in enumerate(line):
            if glyph == " ":
                continue
            tone = color_at(line_index / (ANCHOR_HEIGHT - 1))
            scene.put(row, left + offset, glyph, _mix(tone, ABYSS, fade * 0.55))


def _paint_impact(scene: Scene, since: float) -> None:
    """Areia levantada pelo impacto, abrindo para os lados e assentando."""
    ratio = since / IMPACT_SECONDS
    spread = int(ratio * 20) + 4
    # A nuvem clareia no choque e assenta depois; sem esse pico ela passava
    # despercebida no meio da agua escura.
    tone = _mix(SAND, ABYSS, min(1.0, ratio ** 0.7))
    for offset in range(-spread, spread + 1):
        if abs(offset) < spread - 6:
            continue
        column = scene.axis + offset
        lift_count = 3 if abs(offset) < spread - 3 else 2
        for lift in range(lift_count):
            scene.put(scene.bed_row - 1 - lift, column,
                      "▪" if lift == 0 else "·" if lift == 1 else "˙", tone)


def _brand_progress(elapsed: float) -> tuple[str, str, float]:
    """Trecho visivel de cada palavra e o quanto a ancora ja desbotou."""
    since = elapsed - DESCENT_SECONDS - IMPACT_SECONDS
    if since < 0:
        return "", "", 0.0
    first, second = "ANCHOR", "DOWNLOADER"
    top = first[:round(len(first) * min(1.0, since / (BRAND_SECONDS * 0.4)))]
    tail = (since - BRAND_SECONDS * 0.45) / (BRAND_SECONDS * 0.45)
    bottom = second[:round(len(second) * max(0.0, min(1.0, tail)))]
    return top, bottom, min(1.0, since / (BRAND_SECONDS * 0.6))


def _word_width(word: str) -> int:
    return len(word) * 6 - 1 if word else 0


def _paint_word(scene: Scene, word: str, top_row: int, full: str) -> None:
    """Escreve uma palavra em letras de bloco, com o gradiente da marca."""
    left = (scene.width - _word_width(full)) // 2
    for position, letter in enumerate(word):
        shape = GLYPHS.get(letter)
        if shape is None:
            continue
        for line_index, line in enumerate(shape):
            for offset, glyph in enumerate(line):
                if glyph == " ":
                    continue
                column = left + position * 6 + offset
                tone = color_at(column / max(1, scene.width - 1))
                scene.put(top_row + line_index, column, glyph, tone, bold=True)


def _paint_plain(scene: Scene, word: str, row: int, full: str, bold: bool) -> None:
    spaced = " ".join(full)
    left = (scene.width - len(spaced)) // 2
    for offset, letter in enumerate(" ".join(word)):
        if letter == " ":
            continue
        column = left + offset
        scene.put(row, column, letter, color_at(column / max(1, scene.width - 1)), bold)


def render_frame(elapsed: float, width: int, height: int, status: str = "") -> Text:
    """Desenha um quadro inteiro. Funcao pura: mesma entrada, mesmo quadro."""
    scene = Scene(width, height)
    _paint_water(scene, elapsed)
    _paint_bed(scene)
    _paint_bubbles(scene, elapsed)

    top_row, landed = _anchor_row(elapsed, scene)
    _paint_chain(scene, top_row)

    if landed and elapsed - DESCENT_SECONDS < IMPACT_SECONDS:
        _paint_impact(scene, elapsed - DESCENT_SECONDS)

    first, second, fade = _brand_progress(elapsed)
    _paint_anchor(scene, top_row, fade)

    big = width >= BLOCK_WIDTH and height >= BLOCK_HEIGHT
    if first:
        if big:
            _paint_word(scene, first, 3, "ANCHOR")
        else:
            _paint_plain(scene, first, max(1, height // 2 - 2), "ANCHOR", True)
    if second:
        if big:
            _paint_word(scene, second, 3 + GLYPH_HEIGHT + 1, "DOWNLOADER")
        else:
            _paint_plain(scene, second, max(2, height // 2), "DOWNLOADER", False)

    if status:
        row = height - 2
        left = (width - len(status)) // 2
        for offset, glyph in enumerate(status):
            scene.put(row, left + offset, glyph, _mix(CYAN, BED_FILL, 0.3))
    return scene.to_text()


class SplashScreen(Screen[None]):
    """Mostra a abertura e sai sozinha quando a conexao resolve ou no teto."""

    DEFAULT_CSS = """
    SplashScreen { background: #04101c; }
    SplashScreen #splash-canvas { width: 100%; height: 100%; }
    """

    def __init__(self, waiting_for=None, status: str = "") -> None:
        super().__init__()
        self.waiting_for = waiting_for
        self.status = status
        self.elapsed = 0.0
        # Guardado para quem gera a documentacao poder congelar a animacao num
        # instante exato; em uso normal ninguem toca nele.
        self.timer = None

    def compose(self) -> ComposeResult:
        yield Static(id="splash-canvas")

    def on_mount(self) -> None:
        self.timer = self.set_interval(FRAME_SECONDS, self._tick)
        self._tick()

    def _tick(self) -> None:
        self.elapsed += FRAME_SECONDS
        size = self.size
        if not fits(size.width, size.height):
            self.dismiss(None)
            return
        label = self.status if self.elapsed > DESCENT_SECONDS else ""
        self.query_one("#splash-canvas", Static).update(
            render_frame(self.elapsed, size.width, size.height, label)
        )
        if self.should_close():
            self.dismiss(None)

    def should_close(self) -> bool:
        """Sai no piso quando a conexao ja resolveu, e no teto de qualquer jeito.

        Metodo proprio, e nao um `if` dentro do `_tick`, para o teste poder
        exercitar a regra de verdade em vez de uma copia dela.
        """
        settled = self.waiting_for is None or self.waiting_for.done()
        return (self.elapsed >= FLOOR_SECONDS and settled) or self.elapsed >= CEILING_SECONDS

    def on_key(self, event) -> None:
        """Qualquer tecla pula a abertura; ninguem deveria ser obrigado a ver."""
        event.stop()
        self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)

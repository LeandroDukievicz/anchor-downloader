"""Terminal renderables for the cyberpunk dashboard."""

from rich.text import Text
from textual.widgets import DataTable, Static

CYAN = "#00d9ef"
BLUE = "#438eff"
PINK = "#f02ce0"
GREEN = "#36ef94"
YELLOW = "#ffd75f"
RED = "#ff507a"
MUTED = "#7e91ad"
WHITE = "#d7e6f4"
STOPS = (CYAN, BLUE, PINK)


def color_at(position: float) -> str:
    position = max(0, min(1, position)) * (len(STOPS) - 1)
    index = min(int(position), len(STOPS) - 2)
    mix = position - index
    left, right = STOPS[index:index + 2]
    channels = [round(int(left[i:i + 2], 16) * (1 - mix) + int(right[i:i + 2], 16) * mix)
                for i in (1, 3, 5)]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def gradient(value: str, bold: bool = False) -> Text:
    result = Text()
    for index, character in enumerate(value):
        result.append(character, ("bold " if bold else "") + color_at(index / max(1, len(value) - 1)))
    return result


def meter(fraction: float, width: int = 14) -> Text:
    fraction = max(0, min(1, fraction))
    filled = round(fraction * width)
    result = Text()
    for index in range(width):
        result.append("━", color_at(index / max(1, width - 1)) if index < filled else "#172445")
    return result


class FocusPanel(Static, can_focus=True):
    pass


class FileTable(DataTable):
    BINDINGS = [
        ("j", "cursor_down", ""),
        ("k", "cursor_up", ""),
        ("home", "scroll_top", ""),
        ("end", "scroll_bottom", ""),
    ]


class TransferGraph(Static):
    history: list[float] = []
    caption = "0 B/s"

    def render(self) -> Text:
        width = max(1, self.content_size.width)
        height = max(1, self.content_size.height - 2)
        points = self.history[-width:]
        points = [0.0] * (width - len(points)) + points
        peak = max(max(points, default=0), 1024)
        result = Text(no_wrap=True)
        result.append(self.caption[:width], CYAN)
        result.append("\n")
        for row in range(height):
            for column, value in enumerate(points):
                level = value / peak * height
                fill = max(0, min(1, level - (height - row - 1)))
                character = " ▁▂▃▄▅▆▇█"[round(fill * 8)]
                if fill:
                    result.append(character, color_at(column / max(1, width - 1)))
                else:
                    result.append("┄" if row == height - 1 else "·", "#142a40")
            if row != height - 1:
                result.append("\n")
        return result

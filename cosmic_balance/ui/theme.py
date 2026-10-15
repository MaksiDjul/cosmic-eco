"""
Presentation theme (doc 7 "Visual Aesthetic").

"Clean, scannable dark-mode strategy interface utilizing flat stylized
iconography, crisp text layouts, and non-animated layout structures."

Nothing here touches simulation state.
"""

import pygame

# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------
BASE_W, BASE_H = 1920, 1080          # native design resolution (doc 7)

# ---------------------------------------------------------------------------
# Palette -- flat, dark, high-contrast, colour used only to carry meaning
# ---------------------------------------------------------------------------
BG            = (14, 16, 21)
PANEL         = (21, 24, 31)
PANEL_ALT     = (26, 30, 38)
PANEL_HI      = (33, 38, 48)
BORDER        = (44, 50, 62)
BORDER_HI     = (74, 84, 102)

TEXT          = (214, 220, 230)
TEXT_DIM      = (139, 148, 164)
TEXT_FAINT    = (94, 102, 117)
TEXT_BRIGHT   = (240, 244, 250)

ACCENT        = (86, 160, 232)       # selection / focus
GOOD          = (104, 198, 130)      # profit, growth, surplus
BAD           = (226, 98, 104)       # loss, decline, shortage
WARN          = (226, 172, 86)       # caution, piracy, unrest
INFO          = (140, 158, 232)

GRID          = (34, 39, 49)
CHART_LINE    = (86, 160, 232)
CHART_FILL    = (36, 62, 92)

# Commodity class colours, so a commodity reads the same everywhere.
CLASS_COLORS = {
    "RAW":        (168, 142, 104),
    "REFINED":    (124, 160, 180),
    "HITECH":     (158, 140, 214),
    "LOGISTIC":   (206, 158, 112),
    "SUSTENANCE": (118, 190, 136),
    "UTILITY":    (198, 182, 112),
}

# Map overlay ramps (low -> high)
RAMP_HEAT = [(44, 62, 92), (72, 110, 150), (132, 164, 120), (214, 186, 96),
             (222, 128, 84), (216, 84, 92)]


def ramp(colors, t_bp, bp=10_000):
    """Sample a colour ramp at t in basis points."""
    if not colors:
        return TEXT
    t = max(0, min(bp, t_bp)) / bp
    if t >= 1:
        return colors[-1]
    span = 1.0 / (len(colors) - 1) if len(colors) > 1 else 1.0
    i = int(t / span)
    i = min(i, len(colors) - 2)
    local = (t - i * span) / span
    a, b = colors[i], colors[i + 1]
    return (int(a[0] + (b[0] - a[0]) * local),
            int(a[1] + (b[1] - a[1]) * local),
            int(a[2] + (b[2] - a[2]) * local))


def mix(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


def dim(color, factor=0.5):
    return (int(color[0] * factor), int(color[1] * factor), int(color[2] * factor))


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
class Fonts:
    """Lazily built font set. Monospace for numeric tables so columns line up,
    a UI sans for labels and headings."""

    def __init__(self):
        pygame.font.init()
        mono = self._pick(["Consolas", "DejaVu Sans Mono", "Courier New",
                           "monospace"])
        sans = self._pick(["Segoe UI", "Calibri", "DejaVu Sans", "Arial",
                           "sans"])
        self.mono_s = pygame.font.SysFont(mono, 13)
        self.mono = pygame.font.SysFont(mono, 15)
        self.mono_b = pygame.font.SysFont(mono, 15, bold=True)
        self.mono_l = pygame.font.SysFont(mono, 18)
        self.ui_s = pygame.font.SysFont(sans, 13)
        self.ui = pygame.font.SysFont(sans, 15)
        self.ui_b = pygame.font.SysFont(sans, 15, bold=True)
        self.ui_l = pygame.font.SysFont(sans, 19, bold=True)
        self.ui_xl = pygame.font.SysFont(sans, 25, bold=True)
        self.tiny = pygame.font.SysFont(sans, 11)

    @staticmethod
    def _pick(names):
        available = set(pygame.font.get_fonts())
        for n in names:
            if n.lower().replace(" ", "") in available:
                return n.lower().replace(" ", "")
        return names[-1]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
PAD = 12
ROW_H = 21
HEADER_H = 52
TAB_H = 36
LOG_H = 128

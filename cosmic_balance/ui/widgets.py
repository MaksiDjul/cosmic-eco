"""
Reusable presentation widgets: panels, tables, charts, buttons, sliders.

Strictly a VIEWER toolkit (doc 1.1). Widgets render simulation data and report
user intent back to the caller; they never hold or mutate simulation state.
Interactive widgets return a value when the user acts on them, and the view
turns that into a Command for the engine.
"""

import pygame

from . import theme as T


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------

def panel(surf, rect, title=None, fonts=None, fill=T.PANEL, border=T.BORDER):
    pygame.draw.rect(surf, fill, rect)
    pygame.draw.rect(surf, border, rect, 1)
    inner = pygame.Rect(rect.x, rect.y, rect.w, rect.h)
    if title and fonts:
        bar = pygame.Rect(rect.x + 1, rect.y + 1, rect.w - 2, 26)
        pygame.draw.rect(surf, T.PANEL_ALT, bar)
        pygame.draw.line(surf, border, (rect.x, rect.y + 27),
                         (rect.right - 1, rect.y + 27))
        surf.blit(fonts.ui_b.render(title, True, T.TEXT_BRIGHT),
                  (rect.x + 10, rect.y + 6))
        inner = pygame.Rect(rect.x, rect.y + 28, rect.w, rect.h - 28)
    return inner.inflate(-2 * T.PAD, -2 * T.PAD) if title else \
        inner.inflate(-2 * T.PAD, -2 * T.PAD)


def text(surf, s, pos, font, color=T.TEXT, right=None, center=None):
    img = font.render(str(s), True, color)
    if right is not None:
        surf.blit(img, (right - img.get_width(), pos[1]))
    elif center is not None:
        surf.blit(img, (center - img.get_width() // 2, pos[1]))
    else:
        surf.blit(img, pos)
    return img.get_width()


def hrule(surf, x1, x2, y, color=T.BORDER):
    pygame.draw.line(surf, color, (x1, y), (x2, y))


def bar(surf, rect, frac, color, bg=T.PANEL_HI, border=None):
    pygame.draw.rect(surf, bg, rect)
    w = int(rect.w * max(0.0, min(1.0, frac)))
    if w > 0:
        pygame.draw.rect(surf, color, (rect.x, rect.y, w, rect.h))
    if border:
        pygame.draw.rect(surf, border, rect, 1)


def delta_color(value):
    if value > 0:
        return T.GOOD
    if value < 0:
        return T.BAD
    return T.TEXT_DIM


# ---------------------------------------------------------------------------
# Button
# ---------------------------------------------------------------------------
class Button:
    def __init__(self, label, key=None, tooltip=""):
        self.label = label
        self.key = key
        self.tooltip = tooltip
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.enabled = True
        self.active = False

    def draw(self, surf, rect, fonts, mouse, enabled=True, active=False):
        self.rect = pygame.Rect(rect)
        self.enabled = enabled
        self.active = active
        hovered = enabled and self.rect.collidepoint(mouse)
        if not enabled:
            fill, fg = T.PANEL, T.TEXT_FAINT
        elif active:
            fill, fg = T.ACCENT, (12, 16, 22)
        elif hovered:
            fill, fg = T.PANEL_HI, T.TEXT_BRIGHT
        else:
            fill, fg = T.PANEL_ALT, T.TEXT
        pygame.draw.rect(surf, fill, self.rect)
        pygame.draw.rect(surf, T.BORDER_HI if hovered else T.BORDER,
                         self.rect, 1)
        img = fonts.ui.render(self.label, True, fg)
        surf.blit(img, (self.rect.centerx - img.get_width() // 2,
                        self.rect.centery - img.get_height() // 2))
        return hovered

    def hit(self, pos):
        return self.enabled and self.rect.collidepoint(pos)


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------
class Table:
    """A scannable fixed-column data table with optional row selection.

    ``columns`` is a list of (header, width, align) where align is
    'l' | 'r' | 'c'. Rows are lists of (text, color) tuples.
    """

    def __init__(self, columns, row_h=T.ROW_H):
        self.columns = columns
        self.row_h = row_h
        self.scroll = 0
        self.rects = []
        self.selected = None

    @property
    def width(self):
        return sum(c[1] for c in self.columns)

    def draw(self, surf, rect, rows, fonts, mouse, selected_index=None,
             header=True, zebra=True):
        self.rects = []
        y = rect.y
        if header:
            pygame.draw.rect(surf, T.PANEL_ALT,
                             (rect.x, y, rect.w, self.row_h))
            x = rect.x + 6
            for name, w, align in self.columns:
                if align == "r":
                    text(surf, name, (x, y + 3), fonts.ui_s, T.TEXT_DIM,
                         right=x + w - 8)
                elif align == "c":
                    text(surf, name, (x, y + 3), fonts.ui_s, T.TEXT_DIM,
                         center=x + w // 2)
                else:
                    text(surf, name, (x, y + 3), fonts.ui_s, T.TEXT_DIM)
                x += w
            y += self.row_h
            hrule(surf, rect.x, rect.right, y - 1)

        visible = max(0, (rect.bottom - y) // self.row_h)
        total = len(rows)
        self.scroll = max(0, min(self.scroll, max(0, total - visible)))
        shown = rows[self.scroll:self.scroll + visible]

        for i, row in enumerate(shown):
            idx = i + self.scroll
            rr = pygame.Rect(rect.x, y, rect.w, self.row_h)
            self.rects.append((idx, rr))
            hovered = rr.collidepoint(mouse)
            if idx == selected_index:
                pygame.draw.rect(surf, T.PANEL_HI, rr)
                pygame.draw.rect(surf, T.ACCENT, rr, 1)
            elif hovered:
                pygame.draw.rect(surf, T.PANEL_ALT, rr)
            elif zebra and idx % 2:
                pygame.draw.rect(surf, (24, 27, 34), rr)

            x = rect.x + 6
            for ci, (name, w, align) in enumerate(self.columns):
                if ci >= len(row):
                    break
                cell = row[ci]
                if cell is None:
                    x += w
                    continue
                value, color = cell if isinstance(cell, tuple) else (cell, T.TEXT)
                font = fonts.mono_s if align == "r" else fonts.ui_s
                if align == "r":
                    text(surf, value, (x, y + 3), font, color, right=x + w - 8)
                elif align == "c":
                    text(surf, value, (x, y + 3), font, color,
                         center=x + w // 2)
                else:
                    text(surf, value, (x, y + 3), font, color)
                x += w
            y += self.row_h

        if total > visible:
            # scrollbar
            track = pygame.Rect(rect.right - 4, rect.y + self.row_h, 3,
                                rect.h - self.row_h)
            pygame.draw.rect(surf, T.PANEL_HI, track)
            h = max(14, int(track.h * visible / total))
            off = int(track.h * self.scroll / total)
            pygame.draw.rect(surf, T.BORDER_HI, (track.x, track.y + off, 3, h))
        return visible

    def row_at(self, pos):
        for idx, rr in self.rects:
            if rr.collidepoint(pos):
                return idx
        return None

    def on_wheel(self, dy):
        self.scroll = max(0, self.scroll - dy * 3)


# ---------------------------------------------------------------------------
# Charts (doc 7 "Analytical Dashboards")
# ---------------------------------------------------------------------------

def line_chart(surf, rect, series_list, fonts, labels=True, zero_base=False,
               value_fmt=None, title=None):
    """Draw one or more history Series as a line chart.

    ``series_list`` is a list of (series, colour) pairs. All share one scale so
    they are directly comparable.
    """
    pygame.draw.rect(surf, (17, 20, 26), rect)
    pygame.draw.rect(surf, T.BORDER, rect, 1)
    if title:
        text(surf, title, (rect.x + 8, rect.y + 5), fonts.ui_s, T.TEXT_DIM)

    plot = pygame.Rect(rect.x + 58, rect.y + (22 if title else 8),
                       rect.w - 70, rect.h - (34 if title else 20))
    if plot.w <= 4 or plot.h <= 4:
        return

    live = [(s, c) for s, c in series_list if s is not None and len(s) > 1]
    if not live:
        text(surf, "no data yet", (plot.centerx - 30, plot.centery - 8),
             fonts.ui_s, T.TEXT_FAINT)
        return

    lo = min(s.bounds()[0] for s, _ in live)
    hi = max(s.bounds()[1] for s, _ in live)
    if zero_base:
        lo = min(0, lo)
    if hi == lo:
        hi = lo + 1
    span = hi - lo

    # gridlines + axis labels
    for i in range(5):
        gy = plot.y + plot.h - int(plot.h * i / 4)
        pygame.draw.line(surf, T.GRID, (plot.x, gy), (plot.right, gy))
        if labels:
            v = lo + span * i / 4
            s = value_fmt(v) if value_fmt else f"{v:,.0f}"
            text(surf, s, (rect.x + 4, gy - 7), fonts.tiny, T.TEXT_FAINT,
                 right=plot.x - 6)

    for series, color in live:
        pts = []
        vals = series.values()
        n = len(vals)
        step = plot.w / max(1, n - 1)
        for i, v in enumerate(vals):
            x = plot.x + i * step
            y = plot.y + plot.h - (v - lo) * plot.h / span
            pts.append((x, y))
        if len(pts) > 1:
            pygame.draw.aalines(surf, color, False, pts)


def spark(surf, rect, values, color=T.CHART_LINE, base=None):
    """Tiny inline sparkline for dense tables."""
    if not values or len(values) < 2:
        return
    lo, hi = min(values), max(values)
    if base is not None:
        lo, hi = min(lo, base), max(hi, base)
    if hi == lo:
        hi = lo + 1
    step = rect.w / (len(values) - 1)
    pts = [(rect.x + i * step,
            rect.y + rect.h - (v - lo) * rect.h / (hi - lo))
           for i, v in enumerate(values)]
    pygame.draw.aalines(surf, color, False, pts)


def bar_chart(surf, rect, items, fonts, max_value=None):
    """Horizontal labelled bars: items are (label, value, colour)."""
    if not items:
        return
    peak = max_value or max(abs(v) for _, v, _ in items) or 1
    row = min(T.ROW_H + 2, rect.h // max(1, len(items)))
    y = rect.y
    label_w = 150
    for label, value, color in items:
        text(surf, label, (rect.x, y + 2), fonts.ui_s, T.TEXT_DIM)
        track = pygame.Rect(rect.x + label_w, y + 4, rect.w - label_w - 90,
                            row - 8)
        bar(surf, track, abs(value) / peak, color)
        text(surf, f"{value:,.0f}", (0, y + 2), fonts.mono_s, T.TEXT,
             right=rect.right)
        y += row


# ---------------------------------------------------------------------------
# Slider (the only widget that injects a continuous value)
# ---------------------------------------------------------------------------
class Slider:
    def __init__(self, lo, hi, label="", fmt=None):
        self.lo, self.hi = lo, hi
        self.label = label
        self.fmt = fmt or (lambda v: str(v))
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.dragging = False

    def draw(self, surf, rect, value, fonts, mouse, enabled=True):
        self.rect = pygame.Rect(rect)
        frac = (value - self.lo) / max(1, self.hi - self.lo)
        text(surf, self.label, (rect.x, rect.y - 17), fonts.ui_s,
             T.TEXT_DIM if enabled else T.TEXT_FAINT)
        text(surf, self.fmt(value), (0, rect.y - 17), fonts.mono_s,
             T.TEXT if enabled else T.TEXT_FAINT, right=rect.right)
        track = pygame.Rect(rect.x, rect.centery - 2, rect.w, 4)
        pygame.draw.rect(surf, T.PANEL_HI, track)
        fw = int(rect.w * frac)
        pygame.draw.rect(surf, T.ACCENT if enabled else T.BORDER,
                         (rect.x, track.y, fw, 4))
        knob = pygame.Rect(rect.x + fw - 4, rect.centery - 7, 8, 14)
        pygame.draw.rect(surf, T.TEXT_BRIGHT if enabled else T.TEXT_FAINT, knob)

    def value_at(self, pos):
        if self.rect.w <= 0:
            return None
        frac = (pos[0] - self.rect.x) / self.rect.w
        frac = max(0.0, min(1.0, frac))
        return int(self.lo + (self.hi - self.lo) * frac)

    def hit(self, pos):
        return self.rect.inflate(0, 10).collidepoint(pos)


# ---------------------------------------------------------------------------
# Tab strip
# ---------------------------------------------------------------------------
class Tabs:
    def __init__(self, names):
        self.names = names
        self.rects = []

    def draw(self, surf, rect, current, fonts, mouse):
        self.rects = []
        x = rect.x
        for i, name in enumerate(self.names):
            w = fonts.ui_b.size(name)[0] + 32
            r = pygame.Rect(x, rect.y, w, rect.h)
            self.rects.append(r)
            hovered = r.collidepoint(mouse)
            if i == current:
                pygame.draw.rect(surf, T.PANEL, r)
                pygame.draw.line(surf, T.ACCENT, (r.x, r.y),
                                 (r.right - 1, r.y), 2)
                color = T.TEXT_BRIGHT
            else:
                pygame.draw.rect(surf, T.BG, r)
                color = T.TEXT_BRIGHT if hovered else T.TEXT_DIM
            img = fonts.ui_b.render(name, True, color)
            surf.blit(img, (r.centerx - img.get_width() // 2,
                            r.centery - img.get_height() // 2))
            pygame.draw.line(surf, T.BORDER, (r.right, r.y + 6),
                             (r.right, r.bottom - 6))
            x += w
        return x

    def hit(self, pos):
        for i, r in enumerate(self.rects):
            if r.collidepoint(pos):
                return i
        return None

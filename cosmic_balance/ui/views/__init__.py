"""Screens. Each is a read-only renderer plus a command emitter (doc 1.1)."""

import pygame

from .. import theme as T
from .. import widgets as W


class View:
    """Base screen. Subclasses render simulation state and may only mutate it
    by calling ``ctx.push(...)``, which queues a Command for the engine."""

    name = "view"

    def draw(self, surf, rect, ctx):
        raise NotImplementedError

    def on_key(self, key, ctx):
        return False

    # -- shared layout helpers -----------------------------------------
    @staticmethod
    def split_h(rect, left_w, gap=T.PAD):
        left = pygame.Rect(rect.x + gap, rect.y + gap, left_w,
                           rect.h - 2 * gap)
        right = pygame.Rect(left.right + gap, rect.y + gap,
                            rect.right - left.right - 2 * gap, rect.h - 2 * gap)
        return left, right

    @staticmethod
    def split_v(rect, top_h, gap=T.PAD):
        top = pygame.Rect(rect.x, rect.y, rect.w, top_h)
        bottom = pygame.Rect(rect.x, top.bottom + gap, rect.w,
                             rect.h - top_h - gap)
        return top, bottom

    @staticmethod
    def kv(surf, rect, pairs, fonts, y=None, line=19, key_w=None, max_y=None):
        """Render a label/value block. Values may be (text, colour).

        ``max_y`` stops the block short rather than letting it spill over
        whatever is drawn beneath it (buttons, the next panel).
        """
        y = rect.y if y is None else y
        limit = max_y if max_y is not None else rect.bottom
        for label, value in pairs:
            if y + line > limit:
                break
            if label is None:
                y += line // 2
                continue
            W.text(surf, label, (rect.x, y), fonts.ui_s, T.TEXT_DIM)
            if isinstance(value, tuple):
                value, color = value
            else:
                color = T.TEXT
            W.text(surf, value, (0, y), fonts.mono_s, color,
                   right=rect.right if key_w is None else rect.x + key_w)
            y += line
        return y


from .map_view import MapView                  # noqa: E402
from .sector_view import SectorView            # noqa: E402
from .market_view import MarketView            # noqa: E402
from .finance_view import FinanceView          # noqa: E402
from .trade_view import TradeView              # noqa: E402
from .military_view import MilitaryView        # noqa: E402
from .diplomacy_view import DiplomacyView      # noqa: E402

__all__ = ["View", "MapView", "SectorView", "MarketView", "FinanceView",
           "TradeView", "MilitaryView", "DiplomacyView"]

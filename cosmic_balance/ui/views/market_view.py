"""
MARKET ANALYTICS (doc 7 "Analytical Dashboards" -- commodity price charts).

Prices are computed per sector, so the interesting question is never "what is
the price" but "where is it cheap and where is it dear" -- which is exactly
what a merchant needs and what makes the trade layer meaningful.
"""

import pygame

from ...sim import config
from ...sim.commodities import COMMODITIES
from ...sim.fixed import bp_str, money_short, money_str, qty_short
from .. import theme as T
from .. import widgets as W
from . import View


class MarketView(View):
    name = "Markets"

    def __init__(self):
        self.sector_table = W.Table([
            ("SECTOR", 150, "l"),
            ("OWNER", 120, "l"),
            ("PRICE", 90, "r"),
            ("VS ANCHOR", 90, "r"),
            ("DEMAND", 80, "r"),
            ("SUPPLY", 80, "r"),
            ("TRADED", 80, "r"),
            ("STOCKPILE", 90, "r"),
            ("TREND", 90, "l"),
        ])
        self.commodity_table = W.Table([
            ("COMMODITY", 128, "l"),
            ("CLASS", 74, "l"),
            ("AVG", 82, "r"),
            ("LOW", 76, "r"),
            ("HIGH", 76, "r"),
            ("SPREAD", 62, "r"),
            ("TRADED", 70, "r"),
        ])

    # ------------------------------------------------------------------
    def draw(self, surf, rect, ctx):
        left, right = self.split_h(rect, rect.w - 560)
        chart_rect, table_rect = self.split_v(left, int(left.h * 0.45))
        self.draw_chart(surf, chart_rect, ctx)
        self.draw_sector_table(surf, table_rect, ctx)
        self.draw_commodity_list(surf, right, ctx)

    # ------------------------------------------------------------------
    def draw_chart(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        key = ctx.commodity
        c = COMMODITIES[key]
        inner = W.panel(surf, rect,
                        f"{c.name} -- price history by sector "
                        f"({c.cls.lower()})", f)

        # Chart the player's sectors plus the extremes, so the picture stays
        # readable instead of 60 overlapping lines.
        sectors = sorted(world.galaxy,
                         key=lambda s: -s.market.spot(key))
        picks = []
        if sectors:
            picks.append((sectors[0], T.BAD))                # dearest
            picks.append((sectors[-1], T.GOOD))              # cheapest
            mid = sectors[len(sectors) // 2]
            picks.append((mid, T.INFO))
        if ctx.sector is not None and all(ctx.sector is not s for s, _ in picks):
            picks.append((ctx.sector, T.ACCENT))

        series = [(world.history.price(s.name, key), col) for s, col in picks]
        W.line_chart(surf, inner, series, f,
                     value_fmt=lambda v: money_short(v))

        # legend
        x = inner.x + 70
        y = inner.bottom - 16
        for s, col in picks:
            pygame.draw.rect(surf, col, (x, y + 4, 9, 9))
            W.text(surf, f"{s.name} {money_short(s.market.spot(key))}",
                   (x + 14, y), f.tiny, T.TEXT_DIM)
            x += f.tiny.size(s.name)[0] + 110

    # ------------------------------------------------------------------
    def draw_sector_table(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        key = ctx.commodity
        inner = W.panel(surf, rect,
                        f"{key} across every Local Market Hub", f)

        rows = []
        sectors = list(world.galaxy)
        for s in sectors:
            m = s.market
            price = m.spot(key)
            anchor = m.cost_anchor(COMMODITIES[key])
            rel = (price * 100 // anchor) if anchor else 100
            fac = world.factions.get(s.owner)
            hist = world.history.price(s.name, key)
            rows.append([
                (s.name, T.TEXT_BRIGHT if s is ctx.sector else T.TEXT),
                (fac.name if fac else "-", fac.color if fac else T.TEXT_DIM),
                (money_short(price), T.TEXT_BRIGHT),
                (f"{rel}%", T.BAD if rel > 140 else
                 (T.GOOD if rel < 70 else T.TEXT_DIM)),
                (qty_short(m.last_demand[key]), T.TEXT),
                (qty_short(m.last_supply[key]), T.TEXT),
                (qty_short(m.last_traded[key]), T.ACCENT),
                (qty_short(self.stockpile(s, key)), T.TEXT_DIM),
                None,
            ])

        idx = sectors.index(ctx.sector) if ctx.sector in sectors else None
        self.sector_table.draw(surf, inner, rows, f, ctx.mouse,
                               selected_index=idx)
        # sparklines in the TREND column
        col_x = inner.x + 6 + sum(w for _, w, _ in
                                  self.sector_table.columns[:-1])
        for i, (row_idx, rr) in enumerate(self.sector_table.rects):
            if row_idx >= len(sectors):
                break
            hist = world.history.price(sectors[row_idx].name, key)
            vals = hist.values()[-40:]
            if len(vals) > 1:
                W.spark(surf, pygame.Rect(col_x, rr.y + 4, 80, rr.h - 8),
                        vals, T.CHART_LINE)

        if ctx.clicked and inner.collidepoint(ctx.clicked):
            hit = self.sector_table.row_at(ctx.clicked)
            if hit is not None and hit < len(sectors):
                ctx.sector = sectors[hit]
        if ctx.wheel and inner.collidepoint(ctx.mouse):
            self.sector_table.on_wheel(ctx.wheel)

    @staticmethod
    def stockpile(sector, key):
        total = sum(st.store.get(key) for st in sector.stations)
        total += sum(p.store.get(key) for p in sector.pops)
        if sector.pirate_base is not None:
            total += sector.pirate_base.hoard.get(key)
        return total

    # ------------------------------------------------------------------
    def draw_commodity_list(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect, "Commodity Matrix", f)

        rows = []
        for c in COMMODITIES:
            prices = [s.market.spot(c.key) for s in world.galaxy]
            if not prices:
                continue
            avg = sum(prices) // len(prices)
            lo, hi = min(prices), max(prices)
            spread = (hi - lo) * 100 // max(1, lo)
            traded = sum(s.market.last_traded[c.key] for s in world.galaxy)
            rows.append([
                (c.name, T.CLASS_COLORS.get(c.cls, T.TEXT)),
                (c.cls.lower(), T.TEXT_FAINT),
                (money_short(avg), T.TEXT_BRIGHT),
                (money_short(lo), T.GOOD),
                (money_short(hi), T.BAD),
                (f"{spread}%", T.WARN if spread > 60 else T.TEXT_DIM),
                (qty_short(traded), T.ACCENT),
            ])

        idx = next((i for i, c in enumerate(COMMODITIES)
                    if c.key == ctx.commodity), None)
        self.commodity_table.draw(surf, inner, rows, f, ctx.mouse,
                                  selected_index=idx)
        if ctx.clicked and inner.collidepoint(ctx.clicked):
            hit = self.commodity_table.row_at(ctx.clicked)
            if hit is not None and hit < len(COMMODITIES):
                ctx.commodity = COMMODITIES.all[hit].key

        # explanation of the spread, which is the trader's whole business
        y = inner.y + (len(rows) + 2) * self.commodity_table.row_h + 16
        if y < inner.bottom - 90:
            W.text(surf, "A wide spread is an unexploited trade route:",
                   (inner.x, y), f.ui_s, T.TEXT_DIM)
            W.text(surf, "merchant firms arbitrage it until distance,",
                   (inner.x, y + 17), f.tiny, T.TEXT_FAINT)
            W.text(surf, "tariffs and piracy make the next haul unprofitable.",
                   (inner.x, y + 32), f.tiny, T.TEXT_FAINT)

    # ------------------------------------------------------------------
    def on_key(self, key, ctx):
        keys = [c.key for c in COMMODITIES]
        i = keys.index(ctx.commodity) if ctx.commodity in keys else 0
        if key == pygame.K_DOWN:
            ctx.commodity = keys[(i + 1) % len(keys)]
            return True
        if key == pygame.K_UP:
            ctx.commodity = keys[(i - 1) % len(keys)]
            return True
        return False

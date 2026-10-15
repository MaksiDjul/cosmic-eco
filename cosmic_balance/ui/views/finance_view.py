"""
FINANCIAL DASHBOARDS (doc 7) -- faction net-worth graphs, the hard-capped
money supply, floating exchange rates and factory profit logs.

The money-supply panel is the visible proof of doc 1.1's Conservation of
Value: those totals are fixed at scenario start and never move again.
"""

import pygame

from ...sim import config
from ...sim.fixed import bp_str, money_short, money_str
from .. import theme as T
from .. import widgets as W
from . import View


class FinanceView(View):
    name = "Finance"

    def __init__(self):
        self.profit_table = W.Table([
            ("STATION", 110, "l"),
            ("SECTOR", 130, "l"),
            ("PRODUCES", 110, "l"),
            ("LVL", 40, "r"),
            ("MARGIN/TICK", 110, "r"),
            ("CASHFLOW", 100, "r"),
            ("LIFETIME", 110, "r"),
            ("DIVIDENDS", 100, "r"),
            ("TREND", 80, "l"),
        ])
        self.sort_modes = ["margin", "lifetime", "worst"]
        self.sort_index = 0
        self.sort_buttons = [W.Button(s) for s in
                             ["best margin", "lifetime", "worst"]]

    # ------------------------------------------------------------------
    def draw(self, surf, rect, ctx):
        top = pygame.Rect(rect.x + T.PAD, rect.y + T.PAD,
                          rect.w - 2 * T.PAD, int(rect.h * 0.46))
        bottom = pygame.Rect(rect.x + T.PAD, top.bottom + T.PAD,
                             rect.w - 2 * T.PAD,
                             rect.bottom - top.bottom - 2 * T.PAD)

        w3 = (top.w - 2 * T.PAD) // 3
        self.draw_networth(surf, pygame.Rect(top.x, top.y, w3, top.h), ctx)
        self.draw_money(surf, pygame.Rect(top.x + w3 + T.PAD, top.y, w3,
                                          top.h), ctx)
        self.draw_fx(surf, pygame.Rect(top.x + 2 * (w3 + T.PAD), top.y,
                                       top.w - 2 * (w3 + T.PAD), top.h), ctx)
        self.draw_profit_log(surf, bottom, ctx)

    # ------------------------------------------------------------------
    def draw_networth(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect, "Faction net worth", f)
        chart = pygame.Rect(inner.x, inner.y, inner.w, inner.h - 120)
        series = []
        for key, fac in world.factions.items():
            if world.is_pirate(key):
                continue
            series.append((world.history.worth(key), fac.color))
        W.line_chart(surf, chart, series, f, zero_base=True,
                     value_fmt=lambda v: money_short(v))

        y = chart.bottom + 8
        for key, fac in world.factions.items():
            if world.is_pirate(key):
                continue
            pygame.draw.rect(surf, fac.color, (inner.x, y + 4, 9, 9))
            W.text(surf, fac.name, (inner.x + 14, y), f.ui_s, T.TEXT)
            W.text(surf, money_short(fac.net_worth(world)), (0, y), f.mono_s,
                   T.TEXT_BRIGHT, right=inner.right)
            y += 18

    # ------------------------------------------------------------------
    def draw_money(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect, "Money supply (hard-capped)", f)
        W.text(surf, "Issued once at scenario start; never minted again.",
               (inner.x, inner.y), f.tiny, T.TEXT_FAINT)

        y = inner.y + 22
        for code, cur in world.fx.currencies.items():
            supply = world.fx.supply_of(code)
            fac = world.factions.get(cur.issuer)
            pygame.draw.rect(surf, fac.color if fac else T.TEXT_FAINT,
                             (inner.x, y + 4, 4, 11))
            W.text(surf, f"{code}  {cur.name}", (inner.x + 10, y), f.ui_s,
                   T.TEXT)
            W.text(surf, money_short(supply, cur.symbol), (0, y), f.mono_s,
                   T.TEXT_BRIGHT, right=inner.right)
            y += 19

        y += 10
        W.hrule(surf, inner.x, inner.right, y)
        y += 10
        W.text(surf, "WHERE THE MONEY SITS", (inner.x, y), f.ui_s, T.TEXT_DIM)
        y += 20
        ref = world.factions[world.player_key].currency.code \
            if world.player_key else None
        if ref:
            buckets = [
                ("Households", sum(p.wallet.total_in(ref, world.fx)
                                   for p in world.pops), T.GOOD),
                ("Stations", sum(s.wallet.total_in(ref, world.fx)
                                 for s in world.stations), T.ACCENT),
                ("Treasuries", sum(fa.wallet.total_in(ref, world.fx)
                                   for fa in world.factions.values()), T.WARN),
                ("Merchant firms", sum(fi.wallet.total_in(ref, world.fx)
                                       for fi in world.firms), T.INFO),
                ("Military bases", sum(b.wallet.total_in(ref, world.fx)
                                       for b in world.bases), T.TEXT_DIM),
                ("Black market", sum(b.wallet.total_in(ref, world.fx)
                                     for b in world.pirate_bases), T.BAD),
            ]
            total = sum(v for _, v, _ in buckets) or 1
            for label, value, color in buckets:
                W.text(surf, label, (inner.x, y), f.ui_s, T.TEXT_DIM)
                W.bar(surf, pygame.Rect(inner.x + 120, y + 4,
                                        inner.w - 190, 9),
                      value / total, color)
                W.text(surf, f"{value * 100 // total}%", (0, y), f.mono_s,
                       T.TEXT, right=inner.right)
                y += 19

        if world.audit_problems:
            W.text(surf, "AUDIT FAILED -- value leaked", (inner.x, y + 8),
                   f.ui_b, T.BAD)
        else:
            W.text(surf, "conservation audit: clean", (inner.x, y + 8),
                   f.ui_s, T.GOOD)

    # ------------------------------------------------------------------
    def draw_fx(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect, "Exchange rates (global desk)", f)
        chart = pygame.Rect(inner.x, inner.y, inner.w, inner.h - 150)
        series = [(world.history.rate(code),
                   world.factions[cur.issuer].color
                   if cur.issuer in world.factions else T.TEXT_DIM)
                  for code, cur in world.fx.currencies.items()]
        W.line_chart(surf, chart, series, f,
                     value_fmt=lambda v: f"{v / config.FX_RATE_SCALE:.2f}")

        y = chart.bottom + 8
        player = ctx.player
        base = player.currency.code if player else None
        W.text(surf, f"rate vs {base}" if base else "rate", (inner.x, y),
               f.ui_s, T.TEXT_DIM)
        y += 20
        for code, cur in world.fx.currencies.items():
            fac = world.factions.get(cur.issuer)
            pygame.draw.rect(surf, fac.color if fac else T.TEXT_FAINT,
                             (inner.x, y + 4, 9, 9))
            W.text(surf, code, (inner.x + 14, y), f.ui_s, T.TEXT)
            val = cur.value / config.FX_RATE_SCALE
            rate = world.fx.rate(code, base) / config.FX_RATE_SCALE if base \
                else val
            W.text(surf, f"{rate:.4f}", (0, y), f.mono_s,
                   T.GOOD if rate >= 1 else T.BAD, right=inner.right)
            W.text(surf, f"vol {money_short(world.fx.last_volume.get(code, 0))}",
                   (inner.x + 70, y), f.tiny, T.TEXT_FAINT)
            y += 19

    # ------------------------------------------------------------------
    def draw_profit_log(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect, "Factory profit log", f)

        bx = inner.right - 330
        for i, b in enumerate(self.sort_buttons):
            b.draw(surf, pygame.Rect(bx, inner.y - 24, 104, 22), f, ctx.mouse,
                   active=(i == self.sort_index))
            if ctx.clicked and b.hit(ctx.clicked):
                self.sort_index = i
            bx += 108

        mode = self.sort_modes[self.sort_index]
        stations = list(world.stations)
        if mode == "margin":
            stations.sort(key=lambda s: -s.margin_last)
        elif mode == "lifetime":
            stations.sort(key=lambda s: -s.profit_total)
        else:
            stations.sort(key=lambda s: s.margin_last)

        rows = []
        for st in stations[:200]:
            fac = world.factions.get(st.controller)
            rows.append([
                (st.name, fac.color if fac else T.TEXT),
                (st.sector.name, T.TEXT_DIM),
                (", ".join(st.method.outputs) or "-", T.TEXT_DIM),
                (str(st.level), T.TEXT),
                (money_short(st.margin_last), W.delta_color(st.margin_last)),
                (money_short(st.cashflow_last),
                 W.delta_color(st.cashflow_last)),
                (money_short(st.profit_total),
                 W.delta_color(st.profit_total)),
                (money_short(st.dividends_total), T.TEXT_DIM),
                None,
            ])

        self.profit_table.draw(surf, inner, rows, f, ctx.mouse)
        col_x = inner.x + 6 + sum(w for _, w, _ in
                                  self.profit_table.columns[:-1])
        for row_idx, rr in self.profit_table.rects:
            if row_idx >= len(stations[:200]):
                break
            st = stations[row_idx]
            if len(st.profit_log) > 1:
                W.spark(surf, pygame.Rect(col_x, rr.y + 4, 70, rr.h - 8),
                        st.profit_log[-40:],
                        T.GOOD if st.margin_last >= 0 else T.BAD, base=0)

        if ctx.clicked and inner.collidepoint(ctx.clicked):
            hit = self.profit_table.row_at(ctx.clicked)
            if hit is not None and hit < len(stations):
                ctx.station = stations[hit]
                ctx.sector = stations[hit].sector
        if ctx.wheel and inner.collidepoint(ctx.mouse):
            self.profit_table.on_wheel(ctx.wheel)

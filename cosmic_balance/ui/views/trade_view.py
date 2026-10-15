"""
MERCHANT FIRMS & TRADE ROUTES (doc 3.4).

Routes are macroeconomic pipelines rather than individual ships, so this
screen shows the pipeline economics: the spread being arbitraged, the volume
malus distance imposes, the tariffs levied en route, and the risk that will
make a firm abandon a corridor entirely.
"""

import pygame

from ...sim import config
from ...sim.commodities import COMMODITIES
from ...sim.fixed import bp_str, money_short, qty_short
from .. import theme as T
from .. import widgets as W
from . import View


class TradeView(View):
    name = "Trade"

    def __init__(self):
        self.firm_table = W.Table([
            ("FIRM", 100, "l"),
            ("HOME", 140, "l"),
            ("OWNER", 120, "l"),
            ("ROUTES", 70, "r"),
            ("CARGO", 70, "r"),
            ("CAPACITY", 90, "r"),
            ("USED", 80, "r"),
            ("PROFIT/TICK", 110, "r"),
            ("LIFETIME", 110, "r"),
            ("DIVIDENDS", 100, "r"),
        ])
        self.route_table = W.Table([
            ("COMMODITY", 120, "l"),
            ("FROM", 130, "l"),
            ("TO", 130, "l"),
            ("HOPS", 50, "r"),
            ("LVL", 42, "r"),
            ("VOLUME", 75, "r"),
            ("DIST MALUS", 95, "r"),
            ("BUY", 85, "r"),
            ("SELL", 85, "r"),
            ("SPREAD", 75, "r"),
            ("PROFIT", 95, "r"),
            ("STATE", 100, "l"),
        ])
        self.selected_firm = 0

    # ------------------------------------------------------------------
    def draw(self, surf, rect, ctx):
        top, bottom = self.split_v(
            pygame.Rect(rect.x + T.PAD, rect.y + T.PAD, rect.w - 2 * T.PAD,
                        rect.h - 2 * T.PAD), int(rect.h * 0.40))
        self.draw_firms(surf, top, ctx)
        left, right = self.split_h(bottom, bottom.w - 420, gap=0)
        right = pygame.Rect(left.right + T.PAD, bottom.y,
                            bottom.right - left.right - T.PAD, bottom.h)
        self.draw_routes(surf, left, ctx)
        self.draw_summary(surf, right, ctx)

    # ------------------------------------------------------------------
    def draw_firms(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect,
                        f"Merchant firms -- {len(world.firms)} independent "
                        f"trader stations", f)
        firms = world.firms
        rows = []
        for fi in firms:
            fac = world.factions.get(fi.owner)
            cap = fi.capacity()
            used = fi.committed_volume()
            rows.append([
                (fi.name, T.TEXT_BRIGHT),
                (fi.home.name, T.TEXT_DIM),
                (fac.name if fac else "-", fac.color if fac else T.TEXT_DIM),
                (str(len(fi.routes)), T.TEXT),
                (str(fi.cargo_units), T.TEXT),
                (qty_short(cap), T.TEXT_DIM),
                (f"{used * 100 // max(1, cap)}%",
                 T.BAD if used > cap else T.TEXT_DIM),
                (money_short(fi.profit_last), W.delta_color(fi.profit_last)),
                (money_short(fi.profit_total), W.delta_color(fi.profit_total)),
                (money_short(fi.dividends_total), T.TEXT_DIM),
            ])
        self.selected_firm = min(self.selected_firm, max(0, len(firms) - 1))
        self.firm_table.draw(surf, inner, rows, f, ctx.mouse,
                             selected_index=self.selected_firm)
        if ctx.clicked and inner.collidepoint(ctx.clicked):
            hit = self.firm_table.row_at(ctx.clicked)
            if hit is not None:
                self.selected_firm = hit
        if ctx.wheel and inner.collidepoint(ctx.mouse):
            self.firm_table.on_wheel(ctx.wheel)

    # ------------------------------------------------------------------
    def firm(self, ctx):
        firms = ctx.world.firms
        if not firms:
            return None
        return firms[min(self.selected_firm, len(firms) - 1)]

    def draw_routes(self, surf, rect, ctx):
        f = ctx.fonts
        fi = self.firm(ctx)
        title = f"Trade routes -- {fi.name}" if fi else "Trade routes"
        inner = W.panel(surf, rect, title, f)
        if fi is None:
            W.text(surf, "no merchant firms", (inner.x, inner.y), f.ui_s,
                   T.TEXT_DIM)
            return
        if not fi.routes:
            W.text(surf, "This firm is holding no standing routes. It will "
                         "re-scan connected markets shortly.",
                   (inner.x, inner.y), f.ui_s, T.TEXT_DIM)
            return

        rows = []
        for r in fi.routes:
            buy = r.src.market.spot(r.key)
            sell = r.dst.market.spot(r.key)
            spread = (sell - buy) * 100 // max(1, buy)
            if r.loss_ticks:
                state, color = f"throttled {r.loss_ticks}t", T.WARN
            elif r.level >= config.ROUTE_MAX_LEVEL:
                state, color = "max level", T.GOOD
            else:
                state, color = "running", T.TEXT_DIM
            rows.append([
                (r.key, T.CLASS_COLORS.get(COMMODITIES[r.key].cls, T.TEXT)),
                (r.src.name, T.GOOD),
                (r.dst.name, T.BAD),
                (str(r.hops), T.TEXT),
                (str(r.level), T.TEXT),
                (qty_short(r.volume()), T.ACCENT),
                (bp_str(r.distance_penalty_bp()),
                 T.WARN if r.distance_penalty_bp() > 2000 else T.TEXT_DIM),
                (money_short(buy), T.TEXT_DIM),
                (money_short(sell), T.TEXT_DIM),
                (f"{spread}%", T.GOOD if spread > 0 else T.BAD),
                (money_short(r.profit_total),
                 W.delta_color(r.profit_total)),
                (state, color),
            ])
        self.route_table.draw(surf, inner, rows, f, ctx.mouse)
        if ctx.wheel and inner.collidepoint(ctx.mouse):
            self.route_table.on_wheel(ctx.wheel)

    # ------------------------------------------------------------------
    def draw_summary(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        fi = self.firm(ctx)
        inner = W.panel(surf, rect, "Logistics", f)

        rows = [
            ("Jump range", f"{config.ROUTE_JUMP_RANGE} sectors"),
            ("Volume per level", str(config.ROUTE_VOLUME_PER_LEVEL)),
            ("Malus per extra hop", bp_str(config.ROUTE_DISTANCE_PENALTY_BP)),
            ("Min margin to open", bp_str(config.ROUTE_MIN_MARGIN_BP)),
            ("Piracy abort above", bp_str(config.ROUTE_PIRACY_ABORT_BP)),
            (None, None),
            ("Firms", str(len(world.firms))),
            ("Active routes", str(sum(len(x.routes) for x in world.firms))),
            ("Routes dissolved", str(sum(x.dissolved_routes
                                         for x in world.firms))),
            ("Goods stolen (tick)", str(world.stolen_this_tick)),
        ]
        y = self.kv(surf, inner, rows, f)

        if fi is not None:
            y += 10
            W.hrule(surf, inner.x, inner.right, y)
            y += 10
            W.text(surf, f"{fi.name} HOLD", (inner.x, y), f.ui_s, T.TEXT_DIM)
            y += 20
            hold = fi.hold.nonzero()
            if not hold:
                W.text(surf, "empty", (inner.x, y), f.ui_s, T.TEXT_FAINT)
                y += 18
            for key, qty in sorted(hold.items()):
                W.text(surf, key, (inner.x, y), f.ui_s,
                       T.CLASS_COLORS.get(COMMODITIES[key].cls, T.TEXT))
                W.text(surf, qty_short(qty), (0, y), f.mono_s, T.TEXT,
                       right=inner.right)
                y += 17

            y += 10
            W.text(surf, "CASH BY CURRENCY", (inner.x, y), f.ui_s, T.TEXT_DIM)
            y += 20
            for cur, acc in fi.wallet.accounts.items():
                if acc.balance <= 0:
                    continue
                W.text(surf, cur, (inner.x, y), f.ui_s, T.TEXT)
                W.text(surf, money_short(acc.balance), (0, y), f.mono_s,
                       T.TEXT_BRIGHT, right=inner.right)
                y += 17
            W.text(surf, "a firm must hold the sovereign currency of every",
                   (inner.x, y + 8), f.tiny, T.TEXT_FAINT)
            W.text(surf, "market it buys in -- hence the FX desk.",
                   (inner.x, y + 22), f.tiny, T.TEXT_FAINT)

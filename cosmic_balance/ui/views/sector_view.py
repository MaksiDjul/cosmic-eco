"""
SECTOR DETAIL -- the Local Market Hub, its industry, its people, its policy.

This is where the microeconomics is legible: the single clearing price for
every commodity, the order book that produced it, each station's recipe and
throughput state, and the tax levers a sovereign can actually pull.
"""

import pygame

from ...sim import config
from ...sim.commodities import COMMODITIES, PRODUCTION_METHODS
from ...sim.fixed import bp_str, money_short, money_str, qty_short
from .. import theme as T
from .. import widgets as W
from . import View


class SectorView(View):
    name = "Sector"

    def __init__(self):
        self.market_table = W.Table([
            ("COMMODITY", 150, "l"),
            ("PRICE", 95, "r"),
            ("CHANGE", 80, "r"),
            ("ANCHOR", 90, "r"),
            ("DEMAND", 80, "r"),
            ("SUPPLY", 80, "r"),
            ("TRADED", 80, "r"),
            ("BOOK", 90, "r"),
        ])
        self.station_table = W.Table([
            ("STATION", 110, "l"),
            ("PRODUCES", 120, "l"),
            ("LVL", 42, "r"),
            ("STATE", 120, "l"),
            ("CONDITION", 80, "r"),
            ("MARGIN/TICK", 110, "r"),
            ("CASH", 100, "r"),
            ("OWNER", 110, "l"),
        ])
        self.tax_slider = W.Slider(0, config.MAX_SECTOR_TAX_BP, "Income tax",
                                   bp_str)
        self.prop_slider = W.Slider(0, config.MAX_PROPERTY_TAX_BP,
                                    "Property tax", bp_str)
        self.tariff_slider = W.Slider(0, config.MAX_TARIFF_BP, "Tariff",
                                      bp_str)
        self.upgrade_btn = W.Button("Expand station")
        self.cancel_btn = W.Button("Cancel build")
        self.found_buttons = []
        self.dragging = None

    # ------------------------------------------------------------------
    def draw(self, surf, rect, ctx):
        sector = ctx.sector
        if sector is None:
            W.text(surf, "Select a sector on the Galaxy screen.",
                   (rect.x + T.PAD, rect.y + T.PAD), ctx.fonts.ui, T.TEXT_DIM)
            return

        left, right = self.split_h(rect, rect.w - 470)
        top, bottom = self.split_v(left, int(left.h * 0.46))
        self.draw_market(surf, top, ctx)
        self.draw_stations(surf, bottom, ctx)
        self.draw_side(surf, right, ctx)

    # ------------------------------------------------------------------
    def draw_market(self, surf, rect, ctx):
        f = ctx.fonts
        sector = ctx.sector
        m = sector.market
        inner = W.panel(surf, rect,
                        f"Local Market Hub -- {sector.name}  "
                        f"[{m.currency}]  single clearing price, pro-rata "
                        f"allocation", f)

        rows = []
        for c in COMMODITIES:
            price = m.spot(c.key)
            prev = m.prev_price[c.key]
            change = price - prev
            anchor = m.cost_anchor(c)
            d, s = m.last_demand[c.key], m.last_supply[c.key]
            if d == 0 and s == 0:
                book, book_color = "idle", T.TEXT_FAINT
            elif s == 0:
                book, book_color = "no sellers", T.BAD
            elif d == 0:
                book, book_color = "no buyers", T.INFO
            else:
                ratio = d * 100 // s
                book = f"{ratio}%"
                book_color = T.BAD if ratio > 115 else (
                    T.INFO if ratio < 85 else T.TEXT_DIM)
            rows.append([
                (c.name, T.CLASS_COLORS.get(c.cls, T.TEXT)),
                (money_short(price), T.TEXT_BRIGHT),
                (f"{change:+,}" if change else "-", W.delta_color(change)),
                (money_short(anchor), T.TEXT_FAINT),
                (qty_short(d), T.TEXT),
                (qty_short(s), T.TEXT),
                (qty_short(m.last_traded[c.key]), T.ACCENT),
                (book, book_color),
            ])

        self.market_table.draw(surf, inner, rows, f, ctx.mouse,
                               selected_index=self._commodity_index(ctx))
        if ctx.clicked and inner.collidepoint(ctx.clicked):
            idx = self.market_table.row_at(ctx.clicked)
            if idx is not None and idx < len(COMMODITIES):
                ctx.commodity = COMMODITIES.all[idx].key
        if ctx.wheel and inner.collidepoint(ctx.mouse):
            self.market_table.on_wheel(ctx.wheel)

    def _commodity_index(self, ctx):
        for i, c in enumerate(COMMODITIES):
            if c.key == ctx.commodity:
                return i
        return None

    # ------------------------------------------------------------------
    def draw_stations(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        sector = ctx.sector
        inner = W.panel(surf, rect,
                        f"Industry -- {len(sector.stations)} stations", f)

        stations = sector.stations
        rows = []
        for st in stations:
            owner = world.factions.get(st.controller)
            state = st.status_text()
            if st.construction is not None:
                color = T.WARN
            elif not st.ran_last_tick:
                color = T.BAD
            elif st.clock == "overclock":
                color = T.GOOD
            elif st.clock == "underclock":
                color = T.WARN
            else:
                color = T.TEXT_DIM
            produces = ", ".join(st.method.outputs) or "-"
            rows.append([
                (st.name, T.TEXT_BRIGHT),
                (produces, T.TEXT_DIM),
                (str(st.level), T.TEXT),
                (state, color),
                (bp_str(st.condition),
                 T.BAD if st.condition < 4000 else T.TEXT_DIM),
                (money_short(st.margin_last), W.delta_color(st.margin_last)),
                (money_short(st.wallet.cash()), T.TEXT),
                (owner.name if owner else "-",
                 owner.color if owner else T.TEXT_DIM),
            ])

        idx = stations.index(ctx.station) if ctx.station in stations else None
        self.station_table.draw(surf, inner, rows, f, ctx.mouse,
                                selected_index=idx)
        if ctx.clicked and inner.collidepoint(ctx.clicked):
            hit = self.station_table.row_at(ctx.clicked)
            if hit is not None and hit < len(stations):
                ctx.station = stations[hit]
        if ctx.wheel and inner.collidepoint(ctx.mouse):
            self.station_table.on_wheel(ctx.wheel)

    # ------------------------------------------------------------------
    def draw_side(self, surf, rect, ctx):
        f = ctx.fonts
        top = pygame.Rect(rect.x, rect.y, rect.w, 292)
        mid = pygame.Rect(rect.x, top.bottom + T.PAD, rect.w, 232)
        bot = pygame.Rect(rect.x, mid.bottom + T.PAD, rect.w,
                          rect.bottom - mid.bottom - T.PAD)
        self.draw_policy(surf, top, ctx)
        self.draw_population(surf, mid, ctx)
        self.draw_station_detail(surf, bot, ctx)

    # ------------------------------------------------------------------
    def draw_policy(self, surf, rect, ctx):
        f = ctx.fonts
        sector = ctx.sector
        owns = ctx.owns(sector)
        inner = W.panel(surf, rect, "Sector Policy", f)

        if not owns:
            W.text(surf, "You are not the sovereign here.",
                   (inner.x, inner.y), f.ui_s, T.TEXT_DIM)
            W.text(surf, "Rates are shown read-only.",
                   (inner.x, inner.y + 18), f.tiny, T.TEXT_FAINT)

        y = inner.y + 46
        specs = [
            (self.tax_slider, sector.tax_bp, "set_tax"),
            (self.prop_slider, sector.property_tax_bp, "set_property_tax"),
            (self.tariff_slider, sector.tariff_bp, "set_tariff"),
        ]
        for slider, value, command in specs:
            r = pygame.Rect(inner.x, y, inner.w, 16)
            slider.draw(surf, r, value, f, ctx.mouse, enabled=owns)
            if owns:
                pressed = pygame.mouse.get_pressed()[0]
                if ctx.clicked and slider.hit(ctx.clicked):
                    self.dragging = command
                if self.dragging == command and pressed:
                    nv = slider.value_at(ctx.mouse)
                    if nv is not None and nv != value:
                        ctx.push(command, sector=sector, value=nv)
                if not pressed and self.dragging == command:
                    self.dragging = None
                    ctx.notify(f"{sector.name}: {command} updated")
            y += 54

        y += 4
        rows = [
            ("Tax income/tick", money_short(sector.last_tax_income)),
            ("Collected total", money_short(sector.tax_collected_total)),
            ("Ownership changes", str(sector.ownership_changes)),
        ]
        self.kv(surf, inner, rows, f, y=y)

    # ------------------------------------------------------------------
    def draw_population(self, surf, rect, ctx):
        f = ctx.fonts
        sector = ctx.sector
        inner = W.panel(surf, rect, "Demographics & Labour", f)
        y = inner.y
        if not sector.pops:
            W.text(surf, "uninhabited", (inner.x, y), f.ui_s, T.TEXT_FAINT)
            return
        for p in sector.pops:
            status = p.status()
            color = {"growing": T.GOOD, "starving": T.BAD}.get(status,
                                                               T.TEXT_DIM)
            W.text(surf, p.name, (inner.x, y), f.ui_b, T.TEXT_BRIGHT)
            W.text(surf, f"{p.size:,}", (0, y), f.mono_b, T.TEXT,
                   right=inner.right)
            y += 19
            W.text(surf, status, (inner.x, y), f.ui_s, color)
            W.text(surf, f"satisfaction {bp_str(p.satisfaction_bp)}", (0, y),
                   f.mono_s, color, right=inner.right)
            y += 19
            for key in config.SUSTENANCE_NEEDS:
                fill = p.need_fill_bp.get(key, 0)
                W.text(surf, key, (inner.x + 6, y), f.tiny, T.TEXT_DIM)
                W.bar(surf, pygame.Rect(inner.x + 70, y + 3, 110, 8),
                      fill / config.BP,
                      T.GOOD if fill >= 9000 else
                      (T.WARN if fill >= 6000 else T.BAD))
                W.text(surf, bp_str(fill), (0, y), f.tiny, T.TEXT_DIM,
                       right=inner.right)
                y += 16
            rows = [
                ("Labour offered", qty_short(p.last_labor_offered)),
                ("Labour sold", qty_short(p.last_labor_sold)),
                ("Wages/tick", money_short(p.last_income)),
                ("Public works", money_short(p.public_income_last)),
                ("Tax paid", money_short(p.last_tax)),
                ("Spent on goods", money_short(p.last_spend)),
                ("Savings", money_short(p.cash)),
            ]
            y = self.kv(surf, inner, rows, f, y=y + 4, line=17) + 8
            if y > inner.bottom - 20:
                break

    # ------------------------------------------------------------------
    def draw_station_detail(self, surf, rect, ctx):
        f = ctx.fonts
        st = ctx.station
        inner = W.panel(surf, rect, "Station Detail", f)
        if st is None or st.sector is not ctx.sector:
            W.text(surf, "Select a station from the industry table.",
                   (inner.x, inner.y), f.ui_s, T.TEXT_DIM)
            self.draw_found_options(surf, inner, ctx, y=inner.y + 28)
            return

        market = st.sector.market
        # Reserve the strip at the bottom for the command button so the
        # detail block can never run underneath it.
        content_bottom = inner.bottom - 36
        W.text(surf, f"{st.name}  --  {st.method.name}", (inner.x, inner.y),
               f.ui_b, T.TEXT_BRIGHT)
        y = inner.y + 24

        recipe_in = "  ".join(f"{k} {v * max(1, st.level)}"
                              for k, v in st.method.inputs.items()) or "-"
        recipe_out = "  ".join(f"{k} {v * max(1, st.level)}"
                               for k, v in st.method.outputs.items()) or "-"
        W.text(surf, f"in   {recipe_in}", (inner.x, y), f.mono_s, T.TEXT_DIM)
        y += 17
        W.text(surf, f"out  {recipe_out}", (inner.x, y), f.mono_s, T.GOOD)
        y += 22

        equity = "  ".join(
            f"{ctx.world.factions[k].name.split()[0]} {v}%"
            for k, v in sorted(st.equity.items(), key=lambda kv: -kv[1])
            if k in ctx.world.factions)
        rows = [
            ("Level / throughput", f"{st.level}  ({st.clock})"),
            ("Equity (100 shares)", equity or "-"),
            ("Condition", (bp_str(st.condition),
                           T.BAD if st.condition < 4000 else T.TEXT)),
            ("Margin/tick", (money_short(st.margin_last),
                             W.delta_color(st.margin_last))),
            ("Cash flow/tick", (money_short(st.cashflow_last),
                                W.delta_color(st.cashflow_last))),
            ("Lifetime profit", (money_short(st.profit_total),
                                 W.delta_color(st.profit_total))),
            ("Dividends paid", money_short(st.dividends_total)),
            ("Tax + interest/tick",
             money_short(st.property_tax_last + st.interest_last)),
            ("Cargo assets", f"{st.cargo_units()} units"),
        ]
        if st.loan is not None:
            rows.append(("Debt principal", money_short(st.loan.principal)))
            rows.append(("Debt arrears",
                         (money_short(st.loan.arrears),
                          T.BAD if st.loan.arrears else T.TEXT_DIM)))
        y = self.kv(surf, inner, rows, f, y=y, line=17,
                    max_y=content_bottom)

        # construction progress takes priority over the warehouse listing
        if st.construction is not None and y < content_bottom - 70:
            y += 4
            cw = st.construction
            W.text(surf, f"CONSTRUCTION -> L{cw.target_level}",
                   (inner.x, y), f.ui_s, T.WARN)
            y += 18
            W.bar(surf, pygame.Rect(inner.x, y, inner.w, 10),
                  cw.progress_bp() / config.BP, T.WARN)
            y += 16
            need = "  ".join(f"{k} {v}" for k, v in cw.remaining().items()
                             if v > 0)
            W.text(surf, f"awaiting  {need or 'nothing'}", (inner.x, y),
                   f.tiny, T.TEXT_DIM)
            y += 20

        # stock levels, with whatever room is left
        if y < content_bottom - 34:
            W.text(surf, "WAREHOUSE", (inner.x, y), f.ui_s, T.TEXT_DIM)
            y += 18
            for key, qty in sorted(st.store.nonzero().items()):
                if y > content_bottom - 15:
                    W.text(surf, "...", (inner.x, y), f.tiny, T.TEXT_FAINT)
                    break
                cap = st.store.cap(key)
                W.text(surf, key, (inner.x, y), f.tiny, T.TEXT_DIM)
                W.bar(surf, pygame.Rect(inner.x + 90, y + 3, 110, 8),
                      qty / cap if cap else 0, T.ACCENT)
                W.text(surf, f"{qty}/{cap}", (0, y), f.tiny, T.TEXT_DIM,
                       right=inner.right)
                y += 15

        # commands
        can = ctx.controls(st)
        by = inner.bottom - 30
        if st.construction is None:
            self.upgrade_btn.draw(surf, pygame.Rect(inner.x, by, 150, 26), f,
                                  ctx.mouse, enabled=can)
            if can and ctx.clicked and self.upgrade_btn.hit(ctx.clicked):
                ctx.push("upgrade_station", station=st)
                ctx.push("lend_capital", faction=ctx.world.player_key,
                         station=st, amount=config.LOAN_PRINCIPAL_PER_LEVEL)
                ctx.notify(f"{st.name}: construction warehouse deployed")
        else:
            self.cancel_btn.draw(surf, pygame.Rect(inner.x, by, 150, 26), f,
                                 ctx.mouse, enabled=can)
            if can and ctx.clicked and self.cancel_btn.hit(ctx.clicked):
                ctx.push("cancel_upgrade", station=st)
                ctx.notify(f"{st.name}: construction cancelled")
        if not can:
            W.text(surf, "you do not control this station",
                   (inner.x + 162, by + 5), f.tiny, T.TEXT_FAINT)

    # ------------------------------------------------------------------
    def draw_found_options(self, surf, inner, ctx, y):
        """Let the player commission new industry in their own space."""
        f = ctx.fonts
        sector = ctx.sector
        if not ctx.owns(sector):
            return
        W.text(surf, "COMMISSION NEW INDUSTRY", (inner.x, y), f.ui_s,
               T.TEXT_DIM)
        W.text(surf, "financed by a treasury loan; the construction "
                     "warehouse buys its materials on this market",
               (inner.x, y + 17), f.tiny, T.TEXT_FAINT)
        y += 40
        self.found_buttons = []
        x = inner.x
        for key, method in PRODUCTION_METHODS.items():
            if method.extracts and sector.node(method.extracts) is None:
                continue
            b = W.Button(method.short)
            w = 48
            if x + w > inner.right:
                x = inner.x
                y += 30
            if y > inner.bottom - 26:
                break
            b.draw(surf, pygame.Rect(x, y, w, 24), f, ctx.mouse)
            self.found_buttons.append((b, key, method))
            if ctx.clicked and b.hit(ctx.clicked):
                ctx.push("found_station", sector=sector, method=key,
                         faction=ctx.world.player_key, financed=True)
                ctx.notify(f"{method.name} commissioned in {sector.name}")
            x += w + 4

        hovered = next((m for b, _, m in self.found_buttons
                        if b.rect.collidepoint(ctx.mouse)), None)
        if hovered is not None:
            W.text(surf, f"{hovered.name}: "
                         f"{', '.join(hovered.inputs) or 'nothing'} -> "
                         f"{', '.join(hovered.outputs)}",
                   (inner.x, inner.bottom - 16), f.tiny, T.ACCENT)

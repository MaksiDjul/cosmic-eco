"""
MILITARY & SECURITY (doc 5).

Fleets are abstract strength values built by buying Ship Tonnage on the
open market and crewed by buying Labour Tokens locally, so this screen is as
much a logistics report as an order-of-battle.
"""

import pygame

from ...sim import config
from ...sim.fixed import bp_str, money_short, qty_short
from ...sim.military import ATTACK, DEFEND, PATROL
from .. import theme as T
from .. import widgets as W
from . import View

STANCES = [PATROL, DEFEND, ATTACK]


class MilitaryView(View):
    name = "Military"

    def __init__(self):
        self.fleet_table = W.Table([
            ("FLEET", 170, "l"),
            ("OWNER", 130, "l"),
            ("LOCATION", 140, "l"),
            ("STANCE", 85, "l"),
            ("STRENGTH", 90, "r"),
            ("EFFECTIVE", 95, "r"),
            ("CREWED", 80, "r"),
            ("LOSSES", 80, "r"),
            ("ORDERS", 150, "l"),
        ])
        self.base_table = W.Table([
            ("BASE", 110, "l"),
            ("SECTOR", 140, "l"),
            ("FLEETS", 65, "r"),
            ("CREW NEED", 90, "r"),
            ("SHORTFALL", 90, "r"),
            ("CREWED", 80, "r"),
            ("SPEND/TICK", 100, "r"),
            ("CASH", 100, "r"),
        ])
        self.stance_buttons = [W.Button(s.upper()) for s in STANCES]
        self.mothball_btn = W.Button("Mothball")

    # ------------------------------------------------------------------
    def draw(self, surf, rect, ctx):
        body = pygame.Rect(rect.x + T.PAD, rect.y + T.PAD,
                           rect.w - 2 * T.PAD, rect.h - 2 * T.PAD)
        left, right = self.split_h(body, body.w - 400, gap=0)
        right = pygame.Rect(left.right + T.PAD, body.y,
                            body.right - left.right - T.PAD, body.h)
        top, bottom = self.split_v(left, int(left.h * 0.55))
        self.draw_fleets(surf, top, ctx)
        self.draw_bases(surf, bottom, ctx)
        self.draw_side(surf, right, ctx)

    # ------------------------------------------------------------------
    def draw_fleets(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect, "Order of battle", f)

        fleets = sorted(world.fleets,
                        key=lambda fl: (fl.owner != world.player_key,
                                        -fl.strength))
        rows = []
        for fl in fleets:
            fac = world.factions.get(fl.owner)
            crew = fl.crew_bp()
            if fl.path:
                orders = f"-> {fl.destination.name if fl.destination else '?'}"
                ocolor = T.ACCENT
            elif fl.engaged_last_tick:
                orders = "ENGAGED"
                ocolor = T.BAD
            else:
                orders = "holding"
                ocolor = T.TEXT_DIM
            rows.append([
                (fl.name, T.TEXT_BRIGHT),
                (fac.name if fac else "Corsairs",
                 fac.color if fac else T.BAD),
                (fl.sector.name, T.TEXT_DIM),
                (fl.stance, T.TEXT),
                (qty_short(fl.strength), T.TEXT),
                (qty_short(fl.effective_strength()), T.ACCENT),
                (bp_str(crew), T.BAD if crew < 8000 else T.GOOD),
                (qty_short(fl.losses_total), T.TEXT_DIM),
                (orders, ocolor),
            ])

        idx = fleets.index(ctx.fleet) if ctx.fleet in fleets else None
        self.fleet_table.draw(surf, inner, rows, f, ctx.mouse,
                              selected_index=idx)
        if ctx.clicked and inner.collidepoint(ctx.clicked):
            hit = self.fleet_table.row_at(ctx.clicked)
            if hit is not None and hit < len(fleets):
                ctx.fleet = fleets[hit]
                ctx.sector = fleets[hit].sector
        if ctx.wheel and inner.collidepoint(ctx.mouse):
            self.fleet_table.on_wheel(ctx.wheel)

    # ------------------------------------------------------------------
    def draw_bases(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect, "Logistical bases", f)
        rows = []
        for b in world.bases:
            fac = world.factions.get(b.owner)
            rows.append([
                (b.name, fac.color if fac else T.TEXT),
                (b.sector.name, T.TEXT_DIM),
                (str(len(b.fleets)), T.TEXT),
                (qty_short(b.labor_need()), T.TEXT),
                (qty_short(b.labor_shortfall),
                 T.BAD if b.labor_shortfall else T.TEXT_DIM),
                (bp_str(b.crew_bp),
                 T.BAD if b.crew_bp < 8000 else T.GOOD),
                (money_short(b.spend_last), T.TEXT_DIM),
                (money_short(b.wallet.cash()), T.TEXT),
            ])
        self.base_table.draw(surf, inner, rows, f, ctx.mouse)
        if ctx.wheel and inner.collidepoint(ctx.mouse):
            self.base_table.on_wheel(ctx.wheel)

    # ------------------------------------------------------------------
    def draw_side(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        top = pygame.Rect(rect.x, rect.y, rect.w, 372)
        bot = pygame.Rect(rect.x, top.bottom + T.PAD, rect.w,
                          rect.bottom - top.bottom - T.PAD)

        inner = W.panel(surf, top, "Selected fleet", f)
        fl = ctx.fleet
        if fl is None:
            W.text(surf, "Select a fleet from the order of battle.",
                   (inner.x, inner.y), f.ui_s, T.TEXT_DIM)
        else:
            fac = world.factions.get(fl.owner)
            W.text(surf, fl.name, (inner.x, inner.y), f.ui_l, T.TEXT_BRIGHT)
            W.text(surf, fac.name if fac else "Corsairs",
                   (inner.x, inner.y + 24), f.ui,
                   fac.color if fac else T.BAD)
            rows = [
                ("Location", fl.sector.name),
                ("In transit", "yes" if fl.path else "no"),
                ("Mothballed", ("YES" if fl.mothballed else "no",
                                T.WARN if fl.mothballed else T.TEXT_DIM)),
                ("Raw strength", f"{fl.strength:,}"),
                ("Effective", f"{fl.effective_strength():,}"),
                ("Crewing", bp_str(fl.crew_bp())),
                ("Losses taken", f"{fl.losses_total:,}"),
                ("Hull value", money_short(fl.asset_value(world))),
            ]
            y = self.kv(surf, inner, rows, f, y=inner.y + 54)

            mine = fl.owner == world.player_key
            y += 10
            W.text(surf, "STANCE", (inner.x, y), f.ui_s, T.TEXT_DIM)
            y += 20
            bx = inner.x
            for i, stance in enumerate(STANCES):
                b = self.stance_buttons[i]
                b.draw(surf, pygame.Rect(bx, y, 82, 26), f, ctx.mouse,
                       enabled=mine, active=(fl.stance == stance))
                if mine and ctx.clicked and b.hit(ctx.clicked):
                    ctx.push("fleet_stance", fleet=fl, stance=stance)
                    ctx.notify(f"{fl.name} -> {stance}")
                bx += 86
            self.mothball_btn.draw(
                surf, pygame.Rect(bx, y, 82, 26), f, ctx.mouse,
                enabled=(mine and fl.can_mothball()),
                active=fl.mothballed)
            if mine and ctx.clicked and self.mothball_btn.hit(ctx.clicked):
                ctx.push("fleet_mothball", fleet=fl,
                         value=not fl.mothballed)
                ctx.notify(f"{fl.name}: {'mothballed' if not fl.mothballed else 'reactivated'}")
            y += 34
            if mine:
                W.text(surf, "Right-click a sector on the Galaxy screen",
                       (inner.x, y), f.tiny, T.TEXT_FAINT)
                W.text(surf, "to order this fleet to move there.",
                       (inner.x, y + 14), f.tiny, T.TEXT_FAINT)
            else:
                W.text(surf, "Not under your command.", (inner.x, y), f.tiny,
                       T.TEXT_FAINT)

        # strategic summary
        inner = W.panel(surf, bot, "Strategic balance", f)
        rows = []
        for key, fac in world.factions.items():
            rows.append((fac.name, world.faction_strength(key), fac.color))
        rows.sort(key=lambda r: -r[1])
        chart_h = min(150, len(rows) * 24)
        W.bar_chart(surf, pygame.Rect(inner.x, inner.y, inner.w, chart_h),
                    rows, f)

        y = inner.y + chart_h + 14
        W.text(surf, "CONQUEST IN PROGRESS", (inner.x, y), f.ui_s, T.TEXT_DIM)
        y += 20
        contested = [s for s in world.galaxy if s.conquest_progress > 0]
        if not contested:
            W.text(surf, "no sector is being annexed", (inner.x, y), f.ui_s,
                   T.TEXT_FAINT)
            y += 19
        for s in contested[:6]:
            claimant = world.factions.get(s.conquest_claimant)
            W.text(surf, s.name, (inner.x, y), f.ui_s, T.TEXT)
            W.bar(surf, pygame.Rect(inner.x + 150, y + 4, inner.w - 200, 9),
                  s.conquest_progress / config.CONQUEST_PROGRESS_MAX,
                  claimant.color if claimant else T.WARN)
            y += 19

        y = min(y + 10, inner.bottom - 34)
        W.text(surf, f"Supremacy needed: "
                     f"{bp_str(config.CONQUEST_SUPREMACY_BP)} of local "
                     f"strength", (inner.x, y), f.tiny, T.TEXT_FAINT)
        W.text(surf, f"Attrition per tick: "
                     f"{bp_str(config.COMBAT_ATTRITION_BP)} -- wars are "
                     f"campaigns, not wipes",
               (inner.x, y + 14), f.tiny, T.TEXT_FAINT)

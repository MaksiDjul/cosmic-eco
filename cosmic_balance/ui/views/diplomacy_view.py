"""
DIPLOMACY (doc 6) -- the relation matrix, opinion, and war exhaustion.

The matrix is the AI evaluation table made visible: who borders whom, who is
strong enough to risk a war, and who is too exhausted to keep fighting.
"""

import pygame

from ...sim import config
from ...sim.faction import ALLIANCE, PEACE, WAR
from ...sim.fixed import bp_str, money_short
from .. import theme as T
from .. import widgets as W
from . import View

STANCE_COLOR = {PEACE: T.TEXT_DIM, WAR: T.BAD, ALLIANCE: T.GOOD}
STANCE_LABEL = {PEACE: "peace", WAR: "WAR", ALLIANCE: "allied"}


class DiplomacyView(View):
    name = "Diplomacy"

    def __init__(self):
        self.faction_table = W.Table([
            ("FACTION", 170, "l"),
            ("SECTORS", 75, "r"),
            ("POPULATION", 110, "r"),
            ("TREASURY", 110, "r"),
            ("NET WORTH", 115, "r"),
            ("FLEET", 80, "r"),
            ("EXHAUSTION", 95, "r"),
            ("FX", 70, "r"),
            ("STANCE TO YOU", 120, "l"),
        ])
        self.war_btn = W.Button("Declare war")
        self.peace_btn = W.Button("Offer peace")
        self.cells = []
        self.target = None

    # ------------------------------------------------------------------
    def draw(self, surf, rect, ctx):
        body = pygame.Rect(rect.x + T.PAD, rect.y + T.PAD,
                           rect.w - 2 * T.PAD, rect.h - 2 * T.PAD)
        top, bottom = self.split_v(body, int(body.h * 0.42))
        self.draw_factions(surf, top, ctx)
        left, right = self.split_h(bottom, bottom.w - 430, gap=0)
        right = pygame.Rect(left.right + T.PAD, bottom.y,
                            bottom.right - left.right - T.PAD, bottom.h)
        self.draw_matrix(surf, left, ctx)
        self.draw_actions(surf, right, ctx)

    # ------------------------------------------------------------------
    def powers(self, ctx):
        return [(k, f) for k, f in ctx.world.factions.items()
                if not ctx.world.is_pirate(k)]

    # ------------------------------------------------------------------
    def draw_factions(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect, "Great powers", f)
        me = world.player_key
        powers = self.powers(ctx)
        rows = []
        for key, fac in powers:
            stance = world.factions[key].stance(me) if key != me else "self"
            rows.append([
                (fac.name, fac.color),
                (str(len(world.faction_sectors(key))), T.TEXT),
                (f"{world.faction_population(key):,}", T.TEXT),
                (money_short(fac.treasury, fac.currency.symbol), T.TEXT),
                (money_short(fac.net_worth(world)), T.TEXT_BRIGHT),
                (f"{world.faction_strength(key):,}", T.TEXT),
                (bp_str(fac.exhaustion),
                 T.BAD if fac.exhaustion > config.EXHAUSTION_PEACE_THRESHOLD
                 else T.TEXT_DIM),
                (f"{fac.currency.value / config.FX_RATE_SCALE:.3f}",
                 T.TEXT_DIM),
                (STANCE_LABEL.get(stance, stance),
                 STANCE_COLOR.get(stance, T.ACCENT)),
            ])
        idx = next((i for i, (k, _) in enumerate(powers) if k == self.target),
                   None)
        self.faction_table.draw(surf, inner, rows, f, ctx.mouse,
                                selected_index=idx)
        if ctx.clicked and inner.collidepoint(ctx.clicked):
            hit = self.faction_table.row_at(ctx.clicked)
            if hit is not None and hit < len(powers):
                self.target = powers[hit][0]

    # ------------------------------------------------------------------
    def draw_matrix(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect,
                        "Relation matrix -- stance, and opinion held of the "
                        "column by the row", f)
        powers = self.powers(ctx)
        n = len(powers)
        if n == 0:
            return
        label_w = 150
        cell_w = min(130, max(70, (inner.w - label_w) // max(1, n)))
        cell_h = 34

        # header
        x = inner.x + label_w
        for key, fac in powers:
            W.text(surf, fac.name.split()[0], (x + 6, inner.y), f.ui_s,
                   fac.color)
            x += cell_w

        self.cells = []
        y = inner.y + 22
        for rkey, rfac in powers:
            W.text(surf, rfac.name, (inner.x, y + 8), f.ui_s, rfac.color)
            x = inner.x + label_w
            for ckey, cfac in powers:
                cell = pygame.Rect(x + 2, y, cell_w - 4, cell_h - 4)
                if rkey == ckey:
                    pygame.draw.rect(surf, T.PANEL_ALT, cell)
                    W.text(surf, "--", (0, y + 8), f.ui_s, T.TEXT_FAINT,
                           center=cell.centerx)
                else:
                    stance = rfac.stance(ckey)
                    opinion = rfac.opinion[ckey]
                    color = STANCE_COLOR.get(stance, T.TEXT_DIM)
                    pygame.draw.rect(surf, T.PANEL_ALT, cell)
                    pygame.draw.rect(surf, color, cell, 1)
                    W.text(surf, STANCE_LABEL.get(stance, stance),
                           (0, y + 2), f.tiny, color, center=cell.centerx)
                    # opinion bar, centred on neutral
                    br = pygame.Rect(cell.x + 6, cell.y + 18,
                                     cell.w - 12, 7)
                    pygame.draw.rect(surf, T.PANEL_HI, br)
                    mid = br.centerx
                    frac = max(-1.0, min(1.0,
                                         opinion / config.DIPLO_OPINION_MAX))
                    width = int((br.w // 2) * abs(frac))
                    if width:
                        if frac >= 0:
                            pygame.draw.rect(surf, T.GOOD,
                                             (mid, br.y, width, br.h))
                        else:
                            pygame.draw.rect(surf, T.BAD,
                                             (mid - width, br.y, width, br.h))
                    pygame.draw.line(surf, T.TEXT_FAINT, (mid, br.y),
                                     (mid, br.bottom))
                    self.cells.append((cell, rkey, ckey, opinion))
                x += cell_w
            y += cell_h
            if y > inner.bottom - cell_h:
                break

        hovered = next((c for c in self.cells
                        if c[0].collidepoint(ctx.mouse)), None)
        if hovered is not None:
            _, rkey, ckey, opinion = hovered
            W.text(surf, f"{world.factions[rkey].name} opinion of "
                         f"{world.factions[ckey].name}: {opinion:+,}",
                   (inner.x, inner.bottom - 16), f.ui_s, T.ACCENT)

    # ------------------------------------------------------------------
    def draw_actions(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        inner = W.panel(surf, rect, "Statecraft", f)
        me = world.player_key
        player = ctx.player
        if player is None:
            return

        if self.target is None or self.target == me:
            W.text(surf, "Select another power above.", (inner.x, inner.y),
                   f.ui_s, T.TEXT_DIM)
            self.draw_doctrine(surf, inner, ctx, inner.y + 30)
            return

        other = world.factions[self.target]
        stance = player.stance(self.target)
        W.text(surf, other.name, (inner.x, inner.y), f.ui_l, other.color)
        y = inner.y + 30

        rows = [
            ("Current stance", (STANCE_LABEL.get(stance, stance),
                                STANCE_COLOR.get(stance, T.TEXT))),
            ("Your opinion of them", f"{player.opinion[self.target]:+,}"),
            ("Their opinion of you", f"{other.opinion[me]:+,}"),
            ("Shares a border", "yes" if world.shares_border(me, self.target)
             else "no"),
            (None, None),
            ("Their fleet", f"{world.faction_strength(self.target):,}"),
            ("Your fleet", f"{world.faction_strength(me):,}"),
            ("Their exhaustion", bp_str(other.exhaustion)),
            ("Your exhaustion", bp_str(player.exhaustion)),
        ]
        y = self.kv(surf, inner, rows, f, y=y)

        y += 14
        if stance == WAR:
            self.peace_btn.draw(surf, pygame.Rect(inner.x, y, 160, 28), f,
                                ctx.mouse)
            if ctx.clicked and self.peace_btn.hit(ctx.clicked):
                ctx.push("make_peace", a=me, b=self.target)
                ctx.notify(f"peace offered to {other.name}")
            W.text(surf, "A white peace ends the war and halves both sides'",
                   (inner.x, y + 36), f.tiny, T.TEXT_FAINT)
            W.text(surf, "war exhaustion.", (inner.x, y + 50), f.tiny,
                   T.TEXT_FAINT)
        elif stance == PEACE:
            self.war_btn.draw(surf, pygame.Rect(inner.x, y, 160, 28), f,
                              ctx.mouse)
            if ctx.clicked and self.war_btn.hit(ctx.clicked):
                ctx.push("declare_war", a=me, b=self.target)
                ctx.notify(f"war declared on {other.name}")
            W.text(surf, "War lets your fleets contest their sectors, but",
                   (inner.x, y + 36), f.tiny, T.TEXT_FAINT)
            W.text(surf, "merchants will abandon routes through the war zone.",
                   (inner.x, y + 50), f.tiny, T.TEXT_FAINT)
        else:
            W.text(surf, "You are allied. Alliances dissolve only as opinion",
                   (inner.x, y), f.tiny, T.TEXT_FAINT)
            W.text(surf, "decays.", (inner.x, y + 14), f.tiny, T.TEXT_FAINT)

        self.draw_doctrine(surf, inner, ctx, y + 80)

    def draw_doctrine(self, surf, inner, ctx, y):
        f = ctx.fonts
        W.text(surf, "AI DOCTRINE", (inner.x, y), f.ui_s, T.TEXT_DIM)
        y += 20
        rows = [
            ("Evaluation interval", f"{config.DIPLO_EVAL_INTERVAL} ticks"),
            ("War considered below", f"{config.DIPLO_WAR_OPINION:+,} opinion"),
            ("Needs strength ratio", bp_str(config.DIPLO_WAR_STRENGTH_RATIO_BP)),
            ("Alliance above", f"{config.DIPLO_ALLY_OPINION:+,} opinion"),
            ("Sues for peace above", bp_str(config.EXHAUSTION_PEACE_THRESHOLD)),
            ("Border friction", f"-{config.DIPLO_BORDER_FRICTION}/eval"),
        ]
        self.kv(surf, inner, rows, f, y=y, line=17)

"""
THE PRESENTATION LAYER (doc 7, doc 1.1).

"The user interface (UI) must act strictly as a read-only viewer and an
action-command injector. It must never contain or alter simulation state
variables directly."

Accordingly: this package imports from ``sim`` but the engine never imports
anything from here. Every mutation the player triggers is turned into a
``Command`` and pushed onto ``World.commands``, which the engine drains at the
start of its next tick. The views read state and nothing else.
"""

import pygame

from ..sim import config, scenarios
from ..sim.fixed import money_short, bp_str
from . import theme as T
from . import widgets as W
from .views import (DiplomacyView, FinanceView, MapView, MarketView,
                    MilitaryView, SectorView, TradeView)


class Context:
    """Everything a view needs: the world (read-only), fonts, input state and
    the single sanctioned write path (``push``)."""

    def __init__(self, world, fonts):
        self.world = world
        self.fonts = fonts
        self.mouse = (0, 0)
        self.clicked = None          # position of a left click this frame
        self.right_clicked = None
        self.wheel = 0
        self.keys = set()

        # selection state -- presentation-only, never read by the engine
        self.sector = world.galaxy.order[0] if len(world.galaxy) else None
        self.station = None
        self.commodity = "FOOD"
        self.fleet = None
        self.faction = world.player_key
        self.overlay = "owner"
        self.messages = []
        self.pending_found = None

    # -- the ONLY write path into the simulation -------------------------
    def push(self, kind, **args):
        self.world.push(kind, **args)
        return True

    def notify(self, text):
        self.messages.append((self.world.tick, text))
        if len(self.messages) > 40:
            self.messages.pop(0)

    @property
    def player(self):
        return self.world.factions.get(self.world.player_key)

    def owns(self, sector):
        return sector is not None and sector.owner == self.world.player_key

    def controls(self, station):
        return station is not None and station.controller == self.world.player_key


class App:
    TAB_NAMES = ["Galaxy", "Sector", "Markets", "Finance", "Trade",
                 "Military", "Diplomacy"]

    def __init__(self, scenario="sandbox", seed=None):
        pygame.init()
        pygame.display.set_caption("Project Cosmic Balance")
        self.screen = pygame.display.set_mode(
            (T.BASE_W, T.BASE_H), pygame.SCALED | pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.fonts = T.Fonts()

        self.world = scenarios.build(scenario, seed)
        self.ctx = Context(self.world, self.fonts)

        self.views = [MapView(), SectorView(), MarketView(), FinanceView(),
                      TradeView(), MilitaryView(), DiplomacyView()]
        self.tabs = W.Tabs(self.TAB_NAMES)
        self.current = 0

        self.speed_index = config.DEFAULT_SPEED_INDEX
        self.accumulator = 0.0
        self.running = True
        self.show_help = False
        self.last_tick_ms = 0.0

        self.speed_buttons = [W.Button(s) for s in
                              ["||", "1x", "2x", "4x", "10x", "30x"]]
        self.help_button = W.Button("?")
        self.minimize_button = W.Button("–")   # en-dash as minimize icon
        self.close_button = W.Button("✕")      # multiplication X as close icon

    # ------------------------------------------------------------------
    @property
    def speed(self):
        return config.SPEED_STEPS[self.speed_index]

    def run(self):
        while self.running:
            dt = self.clock.tick(60) / 1000.0
            self.handle_events()
            self.advance(dt)
            self.draw()
            pygame.display.flip()
        pygame.quit()

    # ------------------------------------------------------------------
    def advance(self, dt):
        """Step the engine on a wall-clock budget.

        The number of ticks per frame is capped so a big galaxy cannot freeze
        the window: at high speeds the simulation simply falls behind rather
        than locking up the UI.
        """
        if self.speed <= 0:
            self.accumulator = 0.0
            return
        self.accumulator += dt * self.speed
        budget = 0
        t0 = pygame.time.get_ticks()
        while self.accumulator >= 1.0 and budget < 40:
            self.world.step()
            self.accumulator -= 1.0
            budget += 1
            if pygame.time.get_ticks() - t0 > 120:
                self.accumulator = 0.0      # drop the backlog, stay responsive
                break
        if budget:
            self.last_tick_ms = (pygame.time.get_ticks() - t0) / budget

    # ------------------------------------------------------------------
    def handle_events(self):
        ctx = self.ctx
        ctx.clicked = None
        ctx.right_clicked = None
        ctx.wheel = 0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.MOUSEMOTION:
                ctx.mouse = event.pos
            elif event.type == pygame.MOUSEBUTTONDOWN:
                ctx.mouse = event.pos
                if event.button == 1:
                    ctx.clicked = event.pos
                elif event.button == 3:
                    ctx.right_clicked = event.pos
                elif event.button == 4:
                    ctx.wheel = 1
                elif event.button == 5:
                    ctx.wheel = -1
            elif event.type == pygame.MOUSEWHEEL:
                ctx.wheel = event.y
            elif event.type == pygame.KEYDOWN:
                self.on_key(event)

        # tab strip / speed controls consume clicks before the views see them
        if ctx.clicked:
            hit = self.tabs.hit(ctx.clicked)
            if hit is not None:
                self.current = hit
                ctx.clicked = None
            elif self.help_button.hit(ctx.clicked):
                self.show_help = not self.show_help
                ctx.clicked = None
            elif self.close_button.hit(ctx.clicked):
                self.running = False
                ctx.clicked = None
            elif self.minimize_button.hit(ctx.clicked):
                pygame.display.iconify()
                ctx.clicked = None
            else:
                for i, b in enumerate(self.speed_buttons):
                    if b.hit(ctx.clicked):
                        self.speed_index = i
                        ctx.clicked = None
                        break

        if ctx.clicked and self.show_help:
            self.show_help = False
            ctx.clicked = None

    def on_key(self, event):
        k = event.key
        if k == pygame.K_ESCAPE:
            if self.show_help:
                self.show_help = False
            else:
                self.running = False
        elif k == pygame.K_F11:
            pygame.display.toggle_fullscreen()
        elif k == pygame.K_SPACE:
            self.speed_index = 0 if self.speed_index else 2
        elif pygame.K_1 <= k <= pygame.K_6:
            self.speed_index = min(k - pygame.K_1, len(config.SPEED_STEPS) - 1)
        elif k == pygame.K_PERIOD:
            self.speed_index = min(self.speed_index + 1,
                                   len(config.SPEED_STEPS) - 1)
        elif k == pygame.K_COMMA:
            self.speed_index = max(0, self.speed_index - 1)
        elif k == pygame.K_TAB:
            self.current = (self.current + 1) % len(self.views)
        elif k in (pygame.K_SLASH, pygame.K_QUESTION, pygame.K_h):
            self.show_help = not self.show_help
        elif k == pygame.K_n and self.speed <= 0:
            self.world.step()            # single-step while paused
        else:
            self.views[self.current].on_key(k, self.ctx)

    # ------------------------------------------------------------------
    def draw(self):
        s = self.screen
        s.fill(T.BG)
        self.draw_header(s)

        tab_rect = pygame.Rect(0, T.HEADER_H, T.BASE_W, T.TAB_H)
        pygame.draw.rect(s, T.BG, tab_rect)
        self.tabs.draw(s, tab_rect, self.current, self.fonts, self.ctx.mouse)
        W.hrule(s, 0, T.BASE_W, tab_rect.bottom)

        body = pygame.Rect(0, tab_rect.bottom + 1, T.BASE_W,
                           T.BASE_H - tab_rect.bottom - 1 - T.LOG_H)
        self.views[self.current].draw(s, body, self.ctx)

        self.draw_log(s, pygame.Rect(0, body.bottom, T.BASE_W, T.LOG_H))

        if self.show_help:
            self.draw_help(s)

    # ------------------------------------------------------------------
    def draw_header(self, s):
        w = self.world
        f = self.fonts
        rect = pygame.Rect(0, 0, T.BASE_W, T.HEADER_H)
        pygame.draw.rect(s, T.PANEL, rect)
        W.hrule(s, 0, T.BASE_W, T.HEADER_H - 1, T.BORDER)

        W.text(s, "COSMIC BALANCE", (T.PAD, 6), f.ui_l, T.TEXT_BRIGHT)
        W.text(s, w.scenario_name, (T.PAD, 29), f.ui_s, T.TEXT_DIM)

        # clock + speed
        x = 330
        W.text(s, f"TICK {w.tick}", (x, 8), f.mono_b, T.TEXT_BRIGHT)
        W.text(s, f"{self.last_tick_ms:.0f} ms/tick   {self.clock.get_fps():.0f} fps",
               (x, 30), f.tiny, T.TEXT_FAINT)
        bx = x + 100
        for i, b in enumerate(self.speed_buttons):
            b.draw(s, pygame.Rect(bx, 12, 40, 26), f, self.ctx.mouse,
                   active=(i == self.speed_index))
            bx += 42

        # player faction summary
        fac = self.ctx.player
        if fac is not None:
            px = 700
            pygame.draw.rect(s, fac.color, (px, 12, 4, 26))
            W.text(s, fac.name, (px + 12, 6), f.ui_b, T.TEXT_BRIGHT)
            W.text(s, f"{len(w.faction_sectors(fac.key))} sectors   "
                      f"{w.faction_population(fac.key):,} citizens",
                   (px + 12, 28), f.ui_s, T.TEXT_DIM)

            stats = [
                ("TREASURY", money_short(fac.treasury, fac.currency.symbol)),
                ("NET WORTH", money_short(fac.net_worth(w), fac.currency.symbol)),
                ("FLEET", f"{w.faction_strength(fac.key):,}"),
                ("FX", f"{fac.currency.value / config.FX_RATE_SCALE:.3f}"),
                ("EXHAUSTION", bp_str(fac.exhaustion)),
            ]
            sx = 1080
            for label, value in stats:
                W.text(s, label, (sx, 8), f.tiny, T.TEXT_FAINT)
                W.text(s, value, (sx, 24), f.mono_b, T.TEXT)
                sx += 165

        bx = T.BASE_W - 120
        self.help_button.draw(s, pygame.Rect(bx, 12, 28, 26),
                              f, self.ctx.mouse)
        bx += 30
        self.minimize_button.draw(s, pygame.Rect(bx, 12, 28, 26),
                                   f, self.ctx.mouse)
        bx += 30
        self.close_button.draw(s, pygame.Rect(bx, 12, 28, 26),
                                f, self.ctx.mouse)

        if w.audit_problems:
            W.text(s, "CONSERVATION AUDIT FAILED", (T.BASE_W - 300, 32),
                   f.ui_s, T.BAD)

    # ------------------------------------------------------------------
    def draw_log(self, s, rect):
        f = self.fonts
        pygame.draw.rect(s, T.PANEL, rect)
        W.hrule(s, rect.x, rect.right, rect.y, T.BORDER)
        W.text(s, "GALACTIC EVENTS", (T.PAD, rect.y + 5), f.ui_s, T.TEXT_DIM)

        events = self.world.events[-6:]
        y = rect.y + 24
        for tick, line in events:
            W.text(s, f"{tick:>6}", (T.PAD, y), f.mono_s, T.TEXT_FAINT)
            W.text(s, line, (T.PAD + 60, y), f.ui_s, T.TEXT)
            y += 17

        # player action feedback on the right
        if self.ctx.messages:
            W.text(s, "ORDERS ISSUED", (T.BASE_W // 2 + 60, rect.y + 5),
                   f.ui_s, T.TEXT_DIM)
            y = rect.y + 24
            for tick, line in self.ctx.messages[-5:]:
                W.text(s, f"{tick:>6}", (T.BASE_W // 2 + 60, y), f.mono_s,
                       T.TEXT_FAINT)
                W.text(s, line, (T.BASE_W // 2 + 120, y), f.ui_s, T.ACCENT)
                y += 17

    # ------------------------------------------------------------------
    def draw_help(self, s):
        f = self.fonts
        overlay = pygame.Surface((T.BASE_W, T.BASE_H), pygame.SRCALPHA)
        overlay.fill((8, 10, 14, 220))
        s.blit(overlay, (0, 0))
        box = pygame.Rect(T.BASE_W // 2 - 420, 150, 840, 700)
        inner = W.panel(s, box, "Controls & Concepts", f)

        lines = [
            ("", ""),
            ("SPACE", "pause / resume"),
            ("1 - 6", "set simulation speed (pause, 1x, 2x, 4x, 10x, 30x)"),
            (", / .", "slower / faster"),
            ("N", "advance exactly one tick while paused"),
            ("TAB", "next screen"),
            ("F11", "toggle fullscreen"),
            ("ESC", "close this panel, then quit"),
            ("", ""),
            ("GALAXY", "click a sector or fleet to select it; right-click to "
                       "move the selected fleet"),
            ("", "fleet panel shows stance buttons (patrol/defend/attack); "
                 "O cycles overlay"),
            ("SECTOR", "inspect the local Market Hub, stations, populations "
                       "and resource nodes"),
            ("", "tax, property-tax and tariff sliders work in your own space"),
            ("MARKETS", "price history per commodity across every sector"),
            ("FINANCE", "faction net worth, money supply, FX, factory profit "
                        "logs"),
            ("TRADE", "merchant firms and their standing trade routes"),
            ("MILITARY", "fleets, bases, crewing and conquest progress"),
            ("DIPLOMACY", "stances, opinion, war exhaustion; declare or sue "
                          "for peace"),
            ("", ""),
            ("THE RULES", "money is finite and per-faction; nothing is ever "
                          "created from nothing"),
            ("", "every price is set locally by its sector's order book"),
            ("", "building anything buys real commodities on a real market"),
        ]
        y = inner.y
        for key, desc in lines:
            if key:
                W.text(s, key, (inner.x, y), f.mono_b, T.ACCENT)
            if desc:
                W.text(s, desc, (inner.x + 110, y), f.ui, T.TEXT)
            y += 24 if key or desc else 10


def main(scenario="sandbox", seed=None):
    App(scenario, seed).run()

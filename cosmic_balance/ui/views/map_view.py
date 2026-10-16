"""
THE GALACTIC BOARD (doc 2) -- hexagonal sector map with data overlays.

Visually inspired by the structural connectivity of X4: sectors are tiles,
adjacency is the jump network, and every strategic quantity the player needs
can be painted onto the same board.
"""

import math

import pygame

from ...sim import config
from ...sim.commodities import COMMODITIES
from ...sim.fixed import bp_str, money_short, qty_short
from ...sim.military import PATROL, DEFEND, ATTACK
from .. import theme as T
from .. import widgets as W
from . import View

OVERLAYS = [
    ("owner", "Sovereignty"),
    ("piracy", "Piracy index"),
    ("price", "Commodity price"),
    ("people", "Population"),
    ("industry", "Industry"),
    ("supply", "Local balance"),
]


class MapView(View):
    name = "Galaxy"

    def __init__(self):
        self.hex_centers = {}
        self.hex_size = 44
        self.overlay_buttons = [W.Button(label) for _, label in OVERLAYS]
        self.commodity_buttons = []
        self.stance_buttons = [W.Button(s.upper()) for s in
                               (PATROL, DEFEND, ATTACK)]

    # ------------------------------------------------------------------
    def draw(self, surf, rect, ctx):
        board, side = self.split_h(rect, rect.w - 430)
        self.draw_board(surf, board, ctx)
        self.draw_side(surf, side, ctx)

    # ------------------------------------------------------------------
    def layout(self, rect, world):
        """Fit the whole galaxy inside the board rect."""
        from ...sim.galaxy import hex_to_pixel
        if not len(world.galaxy):
            return
        qmin, qmax, rmin, rmax = world.galaxy.bounds()
        # Measure the extent at unit size, then scale to fit.
        pts = [hex_to_pixel(q, r, 1.0) for q, r in world.galaxy.sectors]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        span_x = (max(xs) - min(xs)) + 2.2
        span_y = (max(ys) - min(ys)) + 2.2
        size = min((rect.w - 40) / span_x, (rect.h - 40) / span_y)
        self.hex_size = max(12, size)

        cx = (max(xs) + min(xs)) / 2 * self.hex_size
        cy = (max(ys) + min(ys)) / 2 * self.hex_size
        ox = rect.centerx - cx
        oy = rect.centery - cy

        self.hex_centers = {}
        for coord, sector in world.galaxy.sectors.items():
            x, y = hex_to_pixel(coord[0], coord[1], self.hex_size)
            self.hex_centers[coord] = (x + ox, y + oy)

    @staticmethod
    def hex_points(cx, cy, size):
        return [(cx + size * math.cos(math.radians(60 * i)),
                 cy + size * math.sin(math.radians(60 * i)))
                for i in range(6)]

    # ------------------------------------------------------------------
    def tile_color(self, sector, ctx):
        world = ctx.world
        mode = ctx.overlay
        if mode == "owner":
            fac = world.factions.get(sector.owner)
            base = fac.color if fac else T.TEXT_FAINT
            return T.mix(T.PANEL, base, 0.55)
        if mode == "piracy":
            return T.ramp(T.RAMP_HEAT, sector.piracy_bp, config.PIRACY_MAX_BP)
        if mode == "price":
            c = COMMODITIES[ctx.commodity]
            price = sector.market.spot(c.key)
            anchor = sector.market.cost_anchor(c)
            # 0.5x anchor -> cold, 2x anchor -> hot
            t = int((price * config.BP) // max(1, anchor * 2))
            return T.ramp(T.RAMP_HEAT, t)
        if mode == "people":
            peak = max((s.population for s in world.galaxy), default=1) or 1
            return T.ramp(T.RAMP_HEAT, sector.population * config.BP // peak)
        if mode == "industry":
            peak = max((len(s.stations) for s in world.galaxy), default=1) or 1
            return T.ramp(T.RAMP_HEAT,
                          len(sector.stations) * config.BP // peak)
        if mode == "supply":
            m = sector.market
            key = ctx.commodity
            d, s = m.last_demand[key], m.last_supply[key]
            if d == 0 and s == 0:
                return T.PANEL_ALT
            total = d + s
            # all supply -> cold, all demand -> hot
            return T.ramp(T.RAMP_HEAT, d * config.BP // max(1, total))
        return T.PANEL_ALT

    # ------------------------------------------------------------------
    def draw_board(self, surf, rect, ctx):
        world = ctx.world
        f = ctx.fonts
        pygame.draw.rect(surf, (11, 13, 17), rect)
        pygame.draw.rect(surf, T.BORDER, rect, 1)
        self.layout(rect, world)

        # --- jump links ------------------------------------------------
        drawn = set()
        for sector in world.galaxy:
            a = self.hex_centers.get(sector.coord)
            if not a:
                continue
            for nb in world.galaxy.neighbors(sector):
                key = tuple(sorted([sector.coord, nb.coord]))
                if key in drawn:
                    continue
                drawn.add(key)
                b = self.hex_centers.get(nb.coord)
                if b:
                    pygame.draw.line(surf, T.GRID, a, b, 1)

        # --- tiles ------------------------------------------------------
        hovered = None
        for sector in world.galaxy:
            cx, cy = self.hex_centers[sector.coord]
            pts = self.hex_points(cx, cy, self.hex_size * 0.92)
            pygame.draw.polygon(surf, self.tile_color(sector, ctx), pts)

            fac = world.factions.get(sector.owner)
            edge = fac.color if fac else T.BORDER
            selected = sector is ctx.sector
            pygame.draw.polygon(surf, T.ACCENT if selected else edge, pts,
                                3 if selected else 1)

            inside = self.point_in_hex((cx, cy), ctx.mouse,
                                       self.hex_size * 0.92)
            if inside:
                hovered = sector

            if self.hex_size > 26:
                self.draw_tile_detail(surf, sector, cx, cy, ctx)

        # --- interaction -------------------------------------------------
        if ctx.clicked and rect.collidepoint(ctx.clicked):
            fleet_hit = self._fleet_at(world, ctx.clicked)
            if fleet_hit is not None:
                ctx.fleet = fleet_hit
                ctx.sector = fleet_hit.sector
                ctx.station = None
            else:
                for sector in world.galaxy:
                    cx, cy = self.hex_centers[sector.coord]
                    if self.point_in_hex((cx, cy), ctx.clicked,
                                         self.hex_size * 0.92):
                        ctx.sector = sector
                        ctx.station = None
                        break
        if ctx.right_clicked and rect.collidepoint(ctx.right_clicked):
            for sector in world.galaxy:
                cx, cy = self.hex_centers[sector.coord]
                if self.point_in_hex((cx, cy), ctx.right_clicked,
                                     self.hex_size * 0.92):
                    self.order_move(ctx, sector)
                    break

        # --- overlay selector -------------------------------------------
        y = rect.y + 8
        x = rect.x + 10
        for i, (mode, label) in enumerate(OVERLAYS):
            b = self.overlay_buttons[i]
            w = f.ui.size(label)[0] + 22
            b.draw(surf, pygame.Rect(x, y, w, 24), f, ctx.mouse,
                   active=(ctx.overlay == mode))
            if ctx.clicked and b.hit(ctx.clicked):
                ctx.overlay = mode
            x += w + 4

        if ctx.overlay in ("price", "supply"):
            self.draw_commodity_picker(surf, rect, ctx)

        if hovered is not None:
            self.draw_tooltip(surf, hovered, ctx)

        W.text(surf, "click: select sector / fleet    right-click: move fleet"
                     "    O: cycle overlay",
               (rect.x + 10, rect.bottom - 20), f.tiny, T.TEXT_FAINT)

    def draw_commodity_picker(self, surf, rect, ctx):
        f = ctx.fonts
        self.commodity_buttons = []
        x = rect.x + 10
        y = rect.y + 38
        for c in COMMODITIES:
            b = W.Button(c.key[:4])
            w = 44
            b.draw(surf, pygame.Rect(x, y, w, 22), f, ctx.mouse,
                   active=(ctx.commodity == c.key))
            self.commodity_buttons.append((b, c.key))
            if ctx.clicked and b.hit(ctx.clicked):
                ctx.commodity = c.key
            x += w + 3

    def draw_tile_detail(self, surf, sector, cx, cy, ctx):
        f = ctx.fonts
        world = ctx.world
        label = sector.name if self.hex_size > 40 else sector.name.split()[0]
        img = f.ui_s.render(label, True, T.TEXT_BRIGHT)
        surf.blit(img, (cx - img.get_width() // 2, cy - self.hex_size * 0.55))

        if self.hex_size > 34:
            W.text(surf, qty_short(sector.population), (0, cy - 8), f.mono_s,
                   T.TEXT, center=cx)
            W.text(surf, f"{len(sector.stations)} st", (0, cy + 7), f.tiny,
                   T.TEXT_DIM, center=cx)

        # fleets present — draw individual ship markers
        fleet_list = [fl for fl in sector.fleets if fl.strength > 0]
        if fleet_list:
            marker_w = max(10, min(16, int(self.hex_size * 0.28)))
            total_w = len(fleet_list) * (marker_w + 3) - 3
            fx = cx - total_w // 2
            fy = int(cy + self.hex_size * 0.32)
            for fl in fleet_list:
                fac = world.factions.get(fl.owner)
                color = fac.color if fac else T.BAD
                selected = fl is ctx.fleet
                half = marker_w // 2
                # diamond / ship shape
                pts = [(fx + half, fy - half),
                       (fx + marker_w, fy),
                       (fx + half, fy + half),
                       (fx, fy)]
                pygame.draw.polygon(surf, color, pts)
                if selected:
                    pygame.draw.polygon(surf, T.ACCENT, pts, 2)
                fl._map_rect = pygame.Rect(fx - 2, fy - half - 2,
                                           marker_w + 4, marker_w + 4)
                fx += marker_w + 3

        if sector.pirate_base is not None:
            pygame.draw.circle(surf, T.BAD,
                               (int(cx + self.hex_size * 0.5),
                                int(cy - self.hex_size * 0.45)), 4)

        # conquest meter
        if sector.conquest_progress > 0:
            frac = sector.conquest_progress / config.CONQUEST_PROGRESS_MAX
            w = int(self.hex_size * 1.1)
            r = pygame.Rect(int(cx - w / 2), int(cy + self.hex_size * 0.6),
                            w, 4)
            claimant = ctx.world.factions.get(sector.conquest_claimant)
            W.bar(surf, r, frac, claimant.color if claimant else T.WARN)

    def draw_tooltip(self, surf, sector, ctx):
        f = ctx.fonts
        world = ctx.world
        fac = world.factions.get(sector.owner)
        lines = [
            (sector.name, T.TEXT_BRIGHT),
            (f"{fac.name if fac else '-'}", fac.color if fac else T.TEXT_DIM),
            (f"pop {sector.population:,}   stations {len(sector.stations)}",
             T.TEXT_DIM),
            (f"tax {bp_str(sector.tax_bp)}   tariff {bp_str(sector.tariff_bp)}",
             T.TEXT_DIM),
            (f"piracy {bp_str(sector.piracy_bp)}",
             T.BAD if sector.piracy_bp > 3000 else T.TEXT_DIM),
        ]
        if ctx.overlay in ("price", "supply"):
            m = sector.market
            k = ctx.commodity
            lines.append((f"{k} {money_short(m.spot(k))}  "
                          f"D{m.last_demand[k]} S{m.last_supply[k]}", T.ACCENT))
        w = max(f.ui_s.size(t)[0] for t, _ in lines) + 20
        h = len(lines) * 17 + 12
        mx, my = ctx.mouse
        box = pygame.Rect(mx + 16, my + 8, w, h)
        box.clamp_ip(pygame.Rect(0, 0, T.BASE_W, T.BASE_H))
        pygame.draw.rect(surf, T.PANEL_ALT, box)
        pygame.draw.rect(surf, T.BORDER_HI, box, 1)
        y = box.y + 6
        for t, color in lines:
            W.text(surf, t, (box.x + 10, y), f.ui_s, color)
            y += 17

    SQRT3 = 1.7320508075688772

    @classmethod
    def point_in_hex(cls, center, point, size):
        """Exact point-in-hexagon test for a flat-top hex of circumradius
        ``size`` (vertices at 0, 60, ... degrees).

        Such a hexagon is the intersection of |y| <= size*sqrt(3)/2 with
        sqrt(3)*|x| + |y| <= sqrt(3)*size.
        """
        ax = abs(point[0] - center[0])
        ay = abs(point[1] - center[1])
        if ay > size * cls.SQRT3 / 2:
            return False
        return cls.SQRT3 * ax + ay <= cls.SQRT3 * size

    # ------------------------------------------------------------------
    def order_move(self, ctx, sector):
        fleet = ctx.fleet
        if fleet is None:
            ctx.notify("click a fleet on the map first")
            return
        if fleet.owner != ctx.world.player_key:
            ctx.notify("that fleet is not yours to command")
            return
        ctx.push("fleet_move", fleet=fleet, sector=sector)
        ctx.notify(f"{fleet.name} ordered to {sector.name}")

    # ------------------------------------------------------------------
    @staticmethod
    def _fleet_at(world, pos):
        for sector in world.galaxy:
            for fl in sector.fleets:
                if fl.strength <= 0:
                    continue
                r = getattr(fl, "_map_rect", None)
                if r is not None and r.collidepoint(pos):
                    return fl
        return None

    # ------------------------------------------------------------------
    def draw_side(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        sector = ctx.sector

        # --- fleet command panel (top) when a fleet is selected ----------
        fleet = ctx.fleet
        if fleet is not None and fleet.strength <= 0:
            ctx.fleet = None
            fleet = None

        fleet_h = 210 if fleet else 0
        if fleet:
            fl_rect = pygame.Rect(rect.x, rect.y, rect.w, fleet_h)
            self._draw_fleet_panel(surf, fl_rect, ctx)

        # --- sector info panel (remainder) --------------------------------
        sec_rect = pygame.Rect(rect.x, rect.y + fleet_h, rect.w,
                               rect.h - fleet_h)
        inner = W.panel(surf, sec_rect, "Selected Sector", f)
        if sector is None:
            W.text(surf, "no sector selected", (inner.x, inner.y), f.ui,
                   T.TEXT_DIM)
            return

        fac = world.factions.get(sector.owner)
        W.text(surf, sector.name, (inner.x, inner.y), f.ui_l, T.TEXT_BRIGHT)
        W.text(surf, fac.name if fac else "unclaimed",
               (inner.x, inner.y + 24), f.ui, fac.color if fac else T.TEXT_DIM)

        y = inner.y + 54
        rows = [
            ("Coordinates", f"{sector.coord[0]}, {sector.coord[1]}"),
            ("Population", f"{sector.population:,}"),
            ("Labour supply", f"{sector.labor_supply:,}"),
            ("Stations", str(len(sector.stations))),
            ("Market currency", sector.market.currency),
            (None, None),
            ("Income tax", bp_str(sector.tax_bp)),
            ("Property tax", bp_str(sector.property_tax_bp)),
            ("Tariff", bp_str(sector.tariff_bp)),
            ("Tax collected/tick", money_short(sector.last_tax_income)),
            (None, None),
            ("Piracy index", (bp_str(sector.piracy_bp),
                              T.BAD if sector.piracy_bp > 3000 else T.TEXT)),
        ]
        if sector.conquest_progress > 0:
            claimant = world.factions.get(sector.conquest_claimant)
            rows.append(("Being annexed by",
                         (claimant.name if claimant else "?", T.WARN)))
            rows.append(("Conquest progress",
                         (f"{sector.conquest_progress * 100 // config.CONQUEST_PROGRESS_MAX}%",
                          T.WARN)))
        y = self.kv(surf, inner, rows, f, y=y)

        # resource nodes
        y += 8
        W.text(surf, "RESOURCE DEPOSITS", (inner.x, y), f.ui_s, T.TEXT_DIM)
        y += 20
        if not sector.nodes:
            W.text(surf, "none surveyed", (inner.x, y), f.ui_s, T.TEXT_FAINT)
            y += 18
        for kind, node in sector.nodes.items():
            if y + 20 > inner.bottom:
                break
            W.text(surf, kind, (inner.x, y), f.ui_s, T.CLASS_COLORS["RAW"])
            W.bar(surf, pygame.Rect(inner.x + 70, y + 4, 120, 8),
                  node.fill_bp / config.BP, T.CLASS_COLORS["RAW"])
            W.text(surf, f"{bp_str(node.yield_bp)} yield", (0, y),
                   f.mono_s, T.TEXT_DIM, right=inner.right)
            y += 20

        # fleets present (clickable list)
        y += 10
        W.text(surf, "FORCES PRESENT", (inner.x, y), f.ui_s, T.TEXT_DIM)
        y += 20
        present = [fl for fl in sector.fleets if fl.strength > 0]
        if not present:
            W.text(surf, "no fleets", (inner.x, y), f.ui_s, T.TEXT_FAINT)
        for fl in present[:7]:
            if y + 18 > inner.bottom - 20:
                break
            owner = world.factions.get(fl.owner)
            is_sel = fl is fleet
            row_rect = pygame.Rect(inner.x - 4, y - 1,
                                   inner.right - inner.x + 8, 18)
            if is_sel:
                pygame.draw.rect(surf, T.PANEL_HI, row_rect)
                pygame.draw.rect(surf, T.ACCENT, row_rect, 1)
            elif row_rect.collidepoint(ctx.mouse):
                pygame.draw.rect(surf, T.PANEL_ALT, row_rect)
            if ctx.clicked and row_rect.collidepoint(ctx.clicked):
                ctx.fleet = fl
                ctx.clicked = None
            pygame.draw.rect(surf, owner.color if owner else T.BAD,
                             (inner.x, y + 4, 8, 8))
            label = fl.name if fl.is_present() else f"{fl.name} (transit)"
            W.text(surf, label, (inner.x + 14, y), f.ui_s,
                   T.TEXT_BRIGHT if is_sel else T.TEXT)
            W.text(surf, f"{fl.effective_strength():,}", (0, y), f.mono_s,
                   T.TEXT, right=inner.right)
            y += 18

        W.text(surf, "Click a fleet to select it. Right-click map to move.",
               (inner.x, inner.bottom - 18), f.tiny, T.TEXT_FAINT)

    # ------------------------------------------------------------------
    def _draw_fleet_panel(self, surf, rect, ctx):
        f = ctx.fonts
        world = ctx.world
        fl = ctx.fleet
        inner = W.panel(surf, rect, "Fleet Orders", f)

        owner = world.factions.get(fl.owner)
        pygame.draw.rect(surf, owner.color if owner else T.BAD,
                         (inner.x, inner.y + 3, 4, 18))
        W.text(surf, fl.name, (inner.x + 10, inner.y), f.ui_b, T.TEXT_BRIGHT)
        W.text(surf, owner.name if owner else "?",
               (inner.x + 10, inner.y + 20), f.ui_s,
               owner.color if owner else T.TEXT_DIM)

        y = inner.y + 44
        rows = [
            ("Strength", f"{fl.effective_strength():,} / {fl.strength:,}"),
            ("Sector", fl.sector.name),
            ("Stance", fl.stance.upper()),
            ("Status", "in transit" if fl.path else "present"),
        ]
        if fl.destination:
            rows.append(("Destination", fl.destination.name))
        for label, value in rows:
            W.text(surf, label, (inner.x, y), f.ui_s, T.TEXT_DIM)
            W.text(surf, str(value), (0, y), f.mono_s, T.TEXT,
                   right=inner.right)
            y += 18

        # stance buttons — only for the player's own fleets
        is_mine = fl.owner == world.player_key
        y += 6
        W.text(surf, "STANCE", (inner.x, y), f.ui_s, T.TEXT_DIM)
        y += 18
        stances = [PATROL, DEFEND, ATTACK]
        bx = inner.x
        bw = (inner.right - inner.x - 8) // 3
        for i, stance in enumerate(stances):
            b = self.stance_buttons[i]
            b.draw(surf, pygame.Rect(bx, y, bw, 24), f, ctx.mouse,
                   enabled=is_mine, active=(fl.stance == stance))
            if is_mine and ctx.clicked and b.hit(ctx.clicked):
                ctx.push("fleet_stance", fleet=fl, stance=stance)
                ctx.clicked = None
            bx += bw + 4

    # ------------------------------------------------------------------
    def on_key(self, key, ctx):
        if key == pygame.K_o:
            modes = [m for m, _ in OVERLAYS]
            i = modes.index(ctx.overlay) if ctx.overlay in modes else 0
            ctx.overlay = modes[(i + 1) % len(modes)]
            return True
        return False

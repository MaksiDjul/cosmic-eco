"""
THE WORLD -- simulation engine and tick orchestrator.

Doc 1.1 "Strict Separation of Concerns": this module and its siblings know
nothing whatsoever about pygame, rendering, fonts or input. The presentation
layer is a read-only viewer, and the ONLY way it may affect the simulation is
by pushing a Command onto ``World.commands``, which is drained at the start of
the next tick.

TICK PIPELINE (order matters a great deal for a closed economy)
---------------------------------------------------------------
 1. drain queued player/UI commands
 2. resource nodes replenish
 3. entities issue FX orders; the global exchange clears
 4. merchant firms re-evaluate trade routes (on interval)
 5. every entity submits market orders into its LOCAL hub
 6. every sector hub clears: single price, pro-rata, exact settlement
 7. piracy interception operates on the actual fills
 8. entities settle: taxes, interest, upkeep, production, consumption
 9. military: movement, sustained engagements, conquest meters, piracy index
10. diplomacy evaluation (on interval)
11. history sampling (on interval)
12. conservation audit (on interval)
"""

import random
from collections import defaultdict

from . import config, diplomacy
from .commodities import COMMODITIES
from .currency import FXDesk
from .faction import PEACE, WAR, ALLIANCE
from .galaxy import Galaxy, hex_distance
from .history import History
from .ledger import Ledger
from .military import (Fleet, MilitaryBase, PATROL, DEFEND, ATTACK,
                       resolve_sector_combat, update_conquest)
from .piracy import PirateBase, run_interceptions, update_piracy_index


class Command:
    """An action-command injected by the presentation layer."""

    __slots__ = ("kind", "args")

    def __init__(self, kind, **args):
        self.kind = kind
        self.args = args

    def __repr__(self):
        return f"<Command {self.kind} {self.args}>"


class World:
    PIRATE_KEY = "PIRATE"

    def __init__(self, seed=None):
        self.ledger = Ledger()
        self.fx = FXDesk(self.ledger)
        self.galaxy = Galaxy()
        self.history = History()
        self.commodities = COMMODITIES

        self.factions = {}
        self.stations = []
        self.firms = []
        self.bases = []
        self.fleets = []
        self.pops = []
        self.pirate_bases = []

        self.tick = 0
        self.rng = random.Random(seed if seed is not None else config.RANDOM_SEED)
        self.commands = []
        self.events = []
        self.scenario_name = "(none)"
        self.player_key = None
        self.pirate_key = World.PIRATE_KEY

        self.stolen_this_tick = 0
        self.migration_last = 0
        self.stations_by_controller = defaultdict(list)
        self.audit_problems = []
        self.paused = True
        self._border_cache = None
        self._border_cache_tick = -1

    # ==================================================================
    # logging
    # ==================================================================
    def log(self, text):
        self.events.append((self.tick, text))
        if len(self.events) > 400:
            del self.events[:100]

    # ==================================================================
    # queries used across the engine (and read-only by the UI)
    # ==================================================================
    def is_pirate(self, key) -> bool:
        return key == self.pirate_key

    def are_hostile(self, a, b) -> bool:
        if a == b:
            return False
        if self.is_pirate(a) or self.is_pirate(b):
            return True          # pirates are hostile to everyone
        fa = self.factions.get(a)
        return fa is not None and fa.stance(b) == WAR

    def shares_border(self, a, b) -> bool:
        if self._border_cache_tick != self.tick or self._border_cache is None:
            cache = defaultdict(set)
            for sector in self.galaxy:
                for nb in self.galaxy.neighbors(sector):
                    if nb.owner != sector.owner:
                        cache[sector.owner].add(nb.owner)
            self._border_cache = cache
            self._border_cache_tick = self.tick
        return b in self._border_cache.get(a, ())

    def faction_strength(self, key) -> int:
        return sum(f.effective_strength() for f in self.fleets if f.owner == key)

    def faction_population(self, key) -> int:
        return sum(s.population for s in self.galaxy if s.owner == key)

    def total_population(self) -> int:
        return sum(p.size for p in self.pops)

    def faction_sectors(self, key):
        return self.galaxy.owned_by(key)

    def wants_military(self, key) -> bool:
        """Whether a faction's bases should keep buying military tonnage."""
        fac = self.factions.get(key)
        if fac is None:
            return False
        at_war = any(fac.stance(k) == WAR for k in self.factions if k != key)
        target = max(60, len(self.faction_sectors(key)) * 70)
        if at_war:
            target *= 2
        return self.faction_strength(key) < target

    def nearest_pirate_base(self, sector):
        if not self.pirate_bases:
            return None
        if sector.pirate_base is not None:
            return sector.pirate_base
        return min(self.pirate_bases,
                   key=lambda b: hex_distance(b.sector.coord, sector.coord))

    def stations_in(self, sector):
        return [s for s in self.stations if s.sector is sector]

    def firms_in(self, sector):
        return [f for f in self.firms if f.home is sector]

    def bases_in(self, sector):
        return [b for b in self.bases if b.sector is sector]

    # ==================================================================
    # THE TICK
    # ==================================================================
    def step(self):
        self.tick += 1
        self.ledger.tick_reset_flows()
        self.stolen_this_tick = 0
        self.migration_last = 0

        # Index stations by controlling faction once, instead of every faction
        # rescanning the entire galaxy's industry during fiscal policy.
        self.stations_by_controller = defaultdict(list)
        for st in self.stations:
            self.stations_by_controller[st.controller].append(st)

        for fac in self.factions.values():
            fac.tax_income_last = 0
            fac.interest_income_last = 0
            fac.military_spend_last = 0
        for sector in self.galaxy:
            sector.last_tax_income = 0

        # 1 -- player / UI commands
        self._drain_commands()

        # 2 -- resource replenishment
        for sector in self.galaxy:
            sector.replenish_nodes()

        # 3 -- foreign exchange
        for st in self.stations:
            st.submit_fx(self.fx)
        for pop in self.pops:
            pop.submit_fx(self.fx)
        for base in self.bases:
            base.submit_fx(self.fx)
        for pb in self.pirate_bases:
            pb.submit_fx(self.fx)
        for firm in self.firms:
            firm.submit_fx(self.fx, self)
        for key, fac in self.factions.items():
            fac.submit_fx(self.fx)
        self.fx.clear()

        # 4 -- trade route evaluation
        for firm in self.firms:
            firm.evaluate_routes(self)

        # 5 -- order submission into local hubs
        for sector in self.galaxy:
            market = sector.market
            for pop in sector.pops:
                pop.submit_orders(market, self.fx)
        for st in self.stations:
            st.submit_orders(st.sector.market, self)
        for base in self.bases:
            base.submit_orders(self)
        for pb in self.pirate_bases:
            pb.submit_orders(self)
        for firm in self.firms:
            firm.submit_orders(self)

        # 6 -- market clearing
        for sector in self.galaxy:
            sector.market.clear_all()

        # 7 -- piracy interception on the realised fills
        for sector in self.galaxy:
            run_interceptions(sector, self, self.rng)

        # 8 -- settlement, production and consumption
        for st in self.stations:
            st.post_market(self)
        for sector in self.galaxy:
            for pop in list(sector.pops):
                pop.post_market(self)
        for firm in self.firms:
            firm.post_market(self)
        for base in self.bases:
            base.post_market(self)
        for pb in self.pirate_bases:
            pb.post_market(self)

        # 8a -- station auto-downgrade check
        if self.tick % config.STATION_DOWNGRADE_INTERVAL == 0:
            for st in list(self.stations):
                if st.should_auto_downgrade():
                    st._downgrade(self)

        # 8b -- corporate distributions and state budgets.
        # Both are required for the closed currency system to keep circulating:
        # see Station.pay_dividends and Faction.run_fiscal_policy.
        for st in self.stations:
            st.pay_dividends(self)
        for firm in self.firms:
            firm.pay_dividends(self)
        for key, fac in self.factions.items():
            if not self.is_pirate(key):
                fac.run_fiscal_policy(self)

        # 8c -- Abstract Utility Units expire (doc 4)
        self._expire_nonstorable()

        # 9 -- military layer
        for fleet in list(self.fleets):
            fleet.advance(self)
        for sector in self.galaxy:
            resolve_sector_combat(sector, self, self.rng)
        self._cull_dead_fleets()
        for sector in self.galaxy:
            update_conquest(sector, self)
            update_piracy_index(sector, self)
        self._ai_fleet_orders()

        # 9b -- labour mobility and autonomous industrial expansion
        self._migration()
        self._ai_industry()

        # 10 -- diplomacy
        if self.tick % config.DIPLO_EVAL_INTERVAL == 0:
            diplomacy.evaluate(self)

        # 11 -- history
        if self.tick % config.HISTORY_SAMPLE_INTERVAL == 0:
            self.history.record(self)

        # 12 -- conservation audit
        if self.tick % config.AUDIT_INTERVAL == 0:
            self.audit_problems = self.ledger.audit(
                raise_on_fail=config.AUDIT_RAISE_ON_FAIL)

    # ------------------------------------------------------------------
    def _expire_nonstorable(self):
        """Abstract Utility Units are intangible: whatever was not consumed in
        the tick it was offered in simply ceases to exist (doc 4).

        This has to sweep EVERY inventory, not just populations. Labour left
        sitting in a factory's hopper would otherwise be carried into the next
        tick, which quietly turns an unstorable utility into a warehousable
        commodity and lets a halted plant hoard a sector's entire workforce.
        The destruction is booked through the ledger, so conservation still
        balances exactly.
        """
        perishable = [c.key for c in COMMODITIES if not c.storable]
        if not perishable:
            return
        for inv in self.ledger.inventories:
            for key in perishable:
                qty = inv.stock.get(key, 0)
                if qty:
                    self.ledger.consume(inv, key, qty, "utility_expiry")

    def _cull_dead_fleets(self):
        dead = [f for f in self.fleets if f.strength <= 0]
        for f in dead:
            if f in f.sector.fleets:
                f.sector.fleets.remove(f)
            if f.base is not None and f in f.base.fleets:
                f.base.fleets.remove(f)
            self.fleets.remove(f)
            for pb in self.pirate_bases:
                if f in pb.raiders:
                    pb.raiders.remove(f)
            if not self.is_pirate(f.owner):
                self.log(f"{f.name} ({self.factions[f.owner].name}) destroyed "
                         f"in {f.sector.name}")

    def _ai_fleet_orders(self):
        """Light-touch AI deployment: defend the homeland, press a war front,
        patrol against piracy. The player's fleets are never auto-ordered."""
        if self.tick % 5 != 0:
            return
        for fleet in self.fleets:
            if self.is_pirate(fleet.owner):
                self._raider_orders(fleet)
                continue
            fac = self.factions.get(fleet.owner)
            if fac is None or fac.is_player:
                continue
            if not fleet.is_present():
                continue

            enemies = [k for k in self.factions
                       if k != fleet.owner and fac.stance(k) == WAR
                       and not self.is_pirate(k)]
            if enemies and fleet.stance == ATTACK:
                targets = [s for k in enemies for s in self.faction_sectors(k)]
                if targets:
                    targets.sort(key=lambda s: hex_distance(s.coord,
                                                            fleet.coord))
                    if fleet.sector not in targets[:1]:
                        fleet.order_move(self, targets[0])
                    continue
            # Otherwise garrison the most lawless owned sector.
            own = self.faction_sectors(fleet.owner)
            if own:
                worst = max(own, key=lambda s: s.piracy_bp)
                if worst is not fleet.sector and worst.piracy_bp > 2_000:
                    fleet.stance = PATROL
                    fleet.order_move(self, worst)

    def food_headroom(self, sector) -> int:
        """How many MORE citizens this sector could feed.

        Judged on the LARGER of what its own farms could produce and what its
        market actually offered last tick. Both terms are needed:

        * market supply alone counts imports, so a sector fed by trade is
          correctly seen as habitable -- but it reads zero for a sector that
          has been emptied of people, because there is nobody left to work its
          farms. Gating migration on that alone creates a depopulation
          ratchet: the first wave of refugees leaves, supply collapses, and
          the gate then refuses to let anyone return to a region full of
          perfectly good idle farmland.

        * installed capacity alone would ignore imports and exports entirely.
        """
        from .balance import sector_food_capacity
        per_1000 = config.SUSTENANCE_NEEDS.get("FOOD", 0)
        if per_1000 <= 0:
            return 10 ** 9
        available = max(sector_food_capacity(sector),
                        sector.market.last_supply["FOOD"])
        consumed = (sector.population * per_1000) // 1000
        return ((available - consumed) * 1000) // per_1000

    def food_staffing_floor(self, sector) -> int:
        """Population needed to actually work this sector's food industry.

        Emigration must never strip a region below this: taking the last
        farmhands out of a hungry sector guarantees it can never feed itself
        again.
        """
        labour = sum(
            st.method.inputs.get("LABOR", 0) * max(1, st.level)
            for st in sector.stations
            if any(k in config.SUSTENANCE_NEEDS for k in st.method.outputs))
        return (labour * 1000) // max(1, config.POP_LABOR_PER_1000)

    def _migration(self):
        """Move people between sectors (see config.MIGRATION_*).

        Labour is the only commodity merchants cannot carry, so without
        mobility a sector that loses its workforce can never recover: too few
        workers to run its own farms, hence no food, hence no growth, hence
        too few workers. Mobility is what keeps industry and demographics in
        step.

        It runs in two directions, and the second one matters as much as the
        first:

        PULL -- labour-short sectors attract workers, but ONLY up to the food
        their market can actually supply. Migration driven by labour demand
        alone is actively destructive: the galaxy's biggest industrial sectors
        have the biggest labour deficits, so they drew in population without
        limit, far past what their farms (competing for the same scarce
        building slots) could feed. The result was a handful of enormous,
        permanently starving industrial hubs surrounded by half-empty
        agricultural ones.

        PUSH -- a starving sector sheds refugees toward anywhere with food to
        spare, regardless of labour demand. Without this, a famine sector that
        is ALSO labour-short (which is exactly what the overcrowded hubs were)
        can never shrink back to a size it can feed.
        """
        if self.tick % config.MIGRATION_INTERVAL != 0:
            return
        from .balance import sector_labor_demand
        from .fixed import apply_bp

        gaps = []
        for sector in self.galaxy:
            demand = sector_labor_demand(self, sector)
            gaps.append((sector, demand - sector.labor_supply))

        deficits = sorted((g for g in gaps if g[1] > config.MIGRATION_MIN_GAP),
                          key=lambda t: -t[1])
        surpluses = sorted((g for g in gaps if g[1] < -config.MIGRATION_MIN_GAP),
                           key=lambda t: t[1])

        spare = {s: (-gap * 1000) // config.POP_LABOR_PER_1000
                 for s, gap in surpluses}

        # --- PUSH: famine refugees ---------------------------------------
        self._emigrate_from_famine(spare)

        if not deficits or not surpluses:
            return

        for dst, gap in deficits:
            want = (gap * 1000) // config.POP_LABOR_PER_1000
            # Never send more people than the destination can feed.
            want = min(want, max(0, self.food_headroom(dst)))
            if want <= 0:
                continue
            for src, _ in surpluses:
                if want <= 0:
                    break
                if spare.get(src, 0) <= 0 or src is dst:
                    continue
                if hex_distance(src.coord, dst.coord) > config.MIGRATION_MAX_HOPS:
                    continue
                if not src.pops:
                    continue
                source = max(src.pops, key=lambda p: p.size)
                headroom = source.size - config.POP_MIN_SIZE
                if headroom <= 0:
                    continue
                movers = min(want, spare[src], headroom,
                             apply_bp(source.size, config.MIGRATION_RATE_BP))
                moved = self._relocate(source, dst, movers)
                if moved <= 0:
                    continue
                spare[src] -= moved
                want -= moved

    # ------------------------------------------------------------------
    def _relocate(self, source, dst, movers) -> int:
        """Move ``movers`` citizens from a pop entity to another sector.

        They take a proportional share of their savings with them, so the
        money supply is completely untouched by the move.
        """
        from .fixed import apply_bp
        from .pop import Pop

        movers = min(int(movers), source.size - config.POP_MIN_SIZE)
        if movers <= 0:
            return 0

        share_bp = (movers * config.BP) // max(1, source.size)
        target = dst.pops[0] if dst.pops else None
        if target is None:
            target = Pop(dst, 0, self.ledger, dst.market.currency)
            dst.pops.append(target)
            self.pops.append(target)
        for cur, acc in list(source.wallet.accounts.items()):
            if acc.balance <= 0:
                continue
            self.ledger.transfer(acc, target.wallet.account(cur),
                                 apply_bp(acc.balance, share_bp), "migration")
        source.size -= movers
        target.size += movers
        source._resize_storage()
        target._resize_storage()
        self.migration_last += movers
        return movers

    def _emigrate_from_famine(self, spare):
        """Starving sectors shed refugees toward sectors with food to spare.

        This is the relief valve for an overgrown industrial hub: it is both
        starving AND labour-short, so the labour-driven pull above would only
        ever send it MORE mouths. Hunger has to be able to move people out as
        well as jobs moving them in.
        """
        from .fixed import apply_bp

        starving = [s for s in self.galaxy
                    if s.pops and min(p.food_fill_bp for p in s.pops)
                    < config.MIGRATION_FAMINE_BP]
        if not starving:
            return

        for src in starving:
            source = max(src.pops, key=lambda p: p.size)
            # Never strip a sector below the workforce its own farms need.
            floor = max(config.POP_MIN_SIZE, self.food_staffing_floor(src))
            if source.size <= floor:
                continue
            leaving = min(apply_bp(source.size, config.MIGRATION_REFUGEE_BP),
                          source.size - floor)
            if leaving <= 0:
                continue
            # Nearest sectors with genuine food headroom, best first.
            options = [(self.food_headroom(s), s)
                       for s in self.galaxy
                       if s is not src and
                       hex_distance(s.coord, src.coord) <=
                       config.MIGRATION_MAX_HOPS]
            options = [(h, s) for h, s in options if h > 0]
            if not options:
                continue
            options.sort(key=lambda t: -t[0])
            for headroom, dst in options[:4]:
                if leaving <= 0:
                    break
                moved = self._relocate(source, dst, min(leaving, headroom))
                if moved > 0:
                    leaving -= moved
                    if dst in spare:
                        spare[dst] = max(0, spare[dst] - moved)

    def can_source_construction(self, sector) -> bool:
        """Could a construction warehouse here actually buy its materials?

        A new station's bill of materials is bought on the LOCAL market, so
        siting one in a sector where those goods never appear produces a
        project that stalls at partial completion indefinitely. Requiring a
        live local supply (or a stock on hand) before committing is far
        cheaper than discovering it 150 ticks later.
        """
        market = sector.market
        for key in config.CONSTRUCTION_GOODS:
            if market.last_supply[key] > 0:
                continue
            if any(st.store.get(key) > 0 for st in sector.stations):
                continue
            return False
        return True

    def subsistence_exempt(self, sector, key) -> bool:
        """May this build ignore the spare-labour gate?

        Only for subsistence, only where people actually live, and only where
        the sector genuinely cannot feed its own population. Applying the gate
        to food was a deadlock -- a sector that had outgrown its farms is by
        definition labour-short, so the gate blocked the one thing that could
        fix it. But exempting food unconditionally was worse: the AI happily
        planted farms in sectors with no workforce at all, which could never
        run and only deepened the apparent labour shortage.
        """
        from .balance import sector_food_capacity
        if key not in config.SUSTENANCE_NEEDS:
            return False
        pop = sector.population
        if pop <= config.POP_MIN_SIZE * 2:
            return False
        need = (pop * config.SUSTENANCE_NEEDS.get(key, 0)) // 1000
        if key == "FOOD":
            return sector_food_capacity(sector) < need
        return True

    def sector_has_spare_labor(self, sector, method, levels=1) -> bool:
        """Is there an unemployed workforce in this sector to staff a build?"""
        from .balance import sector_labor_demand
        need = method.inputs.get("LABOR", 0) * levels
        spare = sector.labor_supply - sector_labor_demand(self, sector)
        return spare >= need

    def _ai_industry(self):
        """Autonomous industrial expansion (doc 1.1 "Agent Autonomy").

        Station levels are otherwise frozen for the life of a game, so a
        sustained shortage would never be answered with new capacity and the
        industrial base could never grow with its population. Each faction
        periodically looks for the good its own sectors are most short of --
        trading well above its anchor price AND showing unfilled demand -- and
        commissions capacity for it, financed by a loan from the treasury.

        Everything it does goes through the ordinary mechanisms: a
        Construction Warehouse buying real commodities on the open market, and
        a real debt serviced with interest.
        """
        if self.tick % config.AI_EXPAND_INTERVAL != 0:
            return
        from .balance import SUPPLIER, station_in
        from .commodities import PRODUCTION_METHODS

        for key, fac in self.factions.items():
            if self.is_pirate(key) or fac.is_player:
                continue
            if fac.treasury < config.AI_EXPAND_MIN_TREASURY:
                continue
            in_progress = [st for st in self.stations
                           if st.controller == key
                           and st.construction is not None]
            building = len(in_progress)
            subsistence_builds = sum(
                1 for st in in_progress
                if any(k in config.SUSTENANCE_NEEDS for k in st.method.outputs))
            # Ordinary builds are rationed; subsistence gets its own, larger
            # allowance on top, because a famine is the one shortage that
            # compounds across the whole economy.
            allowance = config.AI_EXPAND_MAX_CONCURRENT +                 config.AI_SUBSISTENCE_CONCURRENT
            if building >= allowance:
                continue
            ordinary_full = (building - subsistence_builds) >=                 config.AI_EXPAND_MAX_CONCURRENT

            best = None
            for sector in self.faction_sectors(key):
                market = sector.market
                for c in COMMODITIES:
                    if not c.storable:
                        continue
                    price = market.spot(c.key)
                    if price * config.BP < c.base_price * config.AI_EXPAND_PRICE_TRIGGER_BP:
                        continue
                    if market.last_demand[c.key] <= market.last_supply[c.key]:
                        continue
                    method_key = SUPPLIER.get(c.key)
                    if method_key is None:
                        continue
                    method = PRODUCTION_METHODS[method_key]
                    if method.extracts and sector.node(method.extracts) is None:
                        continue
                    # Only build where there are workers to run it. Labour is
                    # non-storable and untradeable, so capacity planted in a
                    # sector with no spare workforce cannot run -- it just bids
                    # the local wage up and starves the plants already there,
                    # which is how an expansion drive turns into a famine.
                    #
                    # SUBSISTENCE IS EXEMPT. Applying the gate to food was a
                    # deadlock: a sector that had outgrown its farms was by
                    # definition labour-short, so the gate blocked the one
                    # thing that could have fixed it, and the famine became
                    # permanent. Feeding people is what creates workers.
                    if not self.sector_has_spare_labor(sector, method) and                             not self.subsistence_exempt(sector, c.key):
                        continue
                    subsistence = c.key in config.SUSTENANCE_NEEDS
                    if ordinary_full and not subsistence:
                        continue
                    score = (price * config.BP) // max(1, c.base_price)
                    if subsistence:
                        score *= config.AI_SUBSISTENCE_PRIORITY
                    if best is None or score > best[0]:
                        best = (score, sector, c.key, method_key)
            if best is None:
                continue

            _, sector, commodity, method_key = best
            existing = station_in(sector, method_key)
            # Raising an existing plant uses the same warehouse mechanism as
            # founding one, so both need sourceable materials.
            if not self.can_source_construction(sector):
                continue
            if existing is not None and existing.construction is None and \
                    existing.level < config.STATION_MAX_LEVEL:
                if existing.start_upgrade(self):
                    # Fund the build through the normal lending channel.
                    self.lend_capital(key, existing,
                                      config.LOAN_PRINCIPAL_PER_LEVEL)
                    self.log(f"{fac.name} expands {existing.name} "
                             f"({commodity} scarce in {sector.name})")
            else:
                st = self.found_station(sector, method_key, key, financed=True)
                if st is not None:
                    self.log(f"{fac.name} commissions {st.name} in "
                             f"{sector.name} ({commodity} scarce)")

    def _raider_orders(self, fleet):
        if not fleet.is_present():
            return
        # Raiders drift toward rich, poorly patrolled markets.
        options = self.galaxy.neighbors(fleet.sector)
        if not options:
            return
        def score(s):
            traffic = sum(s.market.last_traded[c.key] for c in COMMODITIES)
            patrol = sum(f.effective_strength() for f in s.fleets
                         if not self.is_pirate(f.owner) and f.is_present())
            return traffic - patrol * 4
        best = max(options, key=score)
        if score(best) > 0:
            fleet.order_move(self, best)

    # ==================================================================
    # COMMAND INJECTION (the UI's only write path)
    # ==================================================================
    def push(self, kind, **args):
        self.commands.append(Command(kind, **args))

    def _drain_commands(self):
        queued, self.commands = self.commands, []
        for cmd in queued:
            try:
                self._apply_command(cmd)
            except Exception as exc:                       # never kill the sim
                self.log(f"command {cmd.kind} failed: {exc}")

    def _apply_command(self, cmd):
        k, a = cmd.kind, cmd.args

        if k == "set_tax":
            a["sector"].set_tax(a["value"])
        elif k == "set_property_tax":
            a["sector"].set_property_tax(a["value"])
        elif k == "set_tariff":
            a["sector"].set_tariff(a["value"])

        elif k == "upgrade_station":
            a["station"].start_upgrade(self)
        elif k == "cancel_upgrade":
            a["station"].cancel_upgrade(self)

        elif k == "found_station":
            self.found_station(a["sector"], a["method"], a["faction"],
                               financed=a.get("financed", True))

        elif k == "downsize_station":
            a["station"].downsize(self)

        elif k == "fleet_move":
            a["fleet"].order_move(self, a["sector"])
        elif k == "fleet_stance":
            a["fleet"].stance = a["stance"]
        elif k == "fleet_mothball":
            a["fleet"].set_mothball(a["value"])

        elif k == "declare_war":
            diplomacy.declare_war(self, a["a"], a["b"])
        elif k == "make_peace":
            diplomacy.make_peace(self, a["a"], a["b"])

        elif k == "lend_capital":
            self.lend_capital(a["faction"], a["station"], a["amount"])

        else:
            self.log(f"unknown command {k}")

    # ==================================================================
    # construction / finance helpers (also used by scenarios)
    # ==================================================================
    def found_station(self, sector, method_key, faction_key, financed=True,
                      level=0, cash=0, name=None):
        """Create a station at level 0 with a Construction Warehouse already
        deployed, financed by a faction loan (doc 3.3).

        No credits are conjured: the loan moves EXISTING money out of the
        lender's treasury, and the construction warehouse then buys real
        commodities on the open market.
        """
        from .station import Station
        fac = self.factions[faction_key]
        currency = sector.market.currency
        st = Station(sector, method_key, self.ledger, currency, faction_key,
                     level=level, name=name, cash=0)
        sector.stations.append(st)
        self.stations.append(st)

        if cash:
            self.ledger.mint(st.wallet.account(currency), cash, "station_seed")

        if financed:
            amount = config.LOAN_PRINCIPAL_PER_LEVEL
            self.lend_capital(faction_key, st, amount)

        if level <= 0:
            st.start_upgrade(self)
        self.log(f"{fac.name} founded {st.name} in {sector.name}")
        return st

    def lend_capital(self, faction_key, station, amount) -> bool:
        """Capital Financing (doc 3.3): a faction lends starting capital at a
        defined interest rate. The money is a transfer, never a mint."""
        from .faction import Loan
        fac = self.factions.get(faction_key)
        if fac is None or amount <= 0:
            return False
        src = fac.wallet.account(fac.currency.code)
        dst = station.wallet.account(fac.currency.code)
        moved = self.ledger.transfer(src, dst, amount, "loan")
        if moved <= 0:
            return False
        if station.loan is None:
            station.loan = Loan(faction_key, moved,
                                config.DEFAULT_LOAN_INTEREST_BP, station)
            fac.loans.append(station.loan)
            station.loan.active = station.level >= 1
        else:
            station.loan.principal += moved
        return True

    # ==================================================================
    # read-only aggregate views for the UI
    # ==================================================================
    def market_snapshot(self, sector):
        """Rows of (commodity, price, prev, demand, supply, traded)."""
        m = sector.market
        rows = []
        for c in COMMODITIES:
            rows.append((c, m.price[c.key], m.prev_price[c.key],
                         m.last_demand[c.key], m.last_supply[c.key],
                         m.last_traded[c.key]))
        return rows

    def money_supply_table(self):
        return {code: self.fx.supply_of(code) for code in self.fx.currencies}

    def verify_zero_storage(self):
        """Doc 3.2 "Zero Storage Clearing" -- every hub must be empty between
        ticks, and the FX desk must hold no reserves."""
        bad = [s.name for s in self.galaxy if not s.market.is_float_empty()]
        if not self.fx.is_empty():
            bad.append("FX desk")
        return bad

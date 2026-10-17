"""
ABSTRACT LOGISTICAL SHIPPING -- THE MERCHANT FIRMS (doc 3.4).

*   Independent Trader Stations -- cheap commercial stations whose inventory of
    commercial cargo units dictates total trade capacity.
*   Inter-Market Trade Routes -- macroeconomic pipelines that transport
    commodity volume between distinct sector markets (Victoria-3 style trade
    loops) rather than simulating individual ships.
*   Algorithmic Pathfinding & Distance Sinks -- routing is restricted to an
    adjustable jump-range radius for performance, and distance is simulated as
    an automatic percentage volume penalty per hop, per route level.
*   Route Recalibration -- loss-making routes are throttled immediately and
    dissolved if they stay unprofitable; profitable ones level up.
*   Hostile Hazard Avoidance -- routes through war zones or high-piracy
    sectors are refused or terminated.

Cross-border trading also drives the FX market: a firm buying in a foreign
sector must first acquire that sovereign's currency.
"""

from . import config
from .commodities import COMMODITIES
from .fixed import apply_bp, clamp
from .galaxy import hex_distance
from .ledger import Inventory, Wallet


class TradeRoute:
    """A standing pipeline moving one commodity between two markets."""

    __slots__ = ("key", "src", "dst", "hops", "level", "profit_total",
                 "loss_ticks", "throttle_bp", "last_profit", "transit",
                 "last_bought", "last_sold", "cost_basis")

    def __init__(self, key, src, dst, hops, transit, level=1):
        self.key = key
        self.src = src                # source Sector (buy cheap)
        self.dst = dst                # destination Sector (sell dear)
        self.hops = hops
        self.transit = transit        # intermediate sectors, for tariffs/risk
        self.level = level
        self.profit_total = 0
        self.last_profit = 0
        self.loss_ticks = 0
        self.throttle_bp = config.BP
        self.last_bought = 0
        self.last_sold = 0
        self.cost_basis = 0           # cents paid for goods currently in transit

    def distance_penalty_bp(self) -> int:
        """Automatic volume penalty: a percentage malus per hop beyond the
        home market, applied per route level (doc 3.4)."""
        extra = max(0, self.hops - 1)
        pen = config.ROUTE_DISTANCE_PENALTY_BP * extra * self.level
        return clamp(pen, 0, config.BP - 500)

    def volume(self) -> int:
        base = config.ROUTE_VOLUME_PER_LEVEL * self.level
        after_distance = apply_bp(base, config.BP - self.distance_penalty_bp())
        return max(0, apply_bp(after_distance, self.throttle_bp))

    def label(self):
        return f"{self.key} {self.src.name}->{self.dst.name} L{self.level}"


class MerchantFirm:
    """An independent trader station."""

    _seq = 0
    # How many of the cheapest sources / dearest sinks to pair up per
    # commodity, and how many shortlisted ideas get a real path resolved.
    SCAN_WIDTH = 3
    SHORTLIST = 12

    def __init__(self, home, ledger, currency_code, owner, name=None, cash=0):
        MerchantFirm._seq += 1
        self.id = MerchantFirm._seq
        self.home = home
        self.sector = home
        self.owner = owner
        self.ledger = ledger
        self.name = name or f"Firm-{self.id}"

        self.wallet = Wallet(ledger, f"firm:{self.id}", currency_code)
        self.wallet.account(currency_code)
        if cash:
            ledger.mint(self.wallet.account(currency_code), cash, "firm_seed")

        self.hold = Inventory(ledger, f"firm_hold:{self.id}",
                              default_capacity=10_000)
        self.routes = []
        self.last_eval_tick = -999
        self.profit_total = 0
        self.profit_last = 0
        self.dividends_total = 0
        self.dissolved_routes = 0

    # ------------------------------------------------------------------
    @property
    def cargo_units(self) -> int:
        return self.hold.get("SHIP_TONNAGE")

    def capacity(self) -> int:
        """Total trade capacity dictated by ship tonnage inventory."""
        return (config.STATION_BASE_TRADE_VOLUME +
                self.cargo_units * config.TONNAGE_VOLUME_PER_UNIT)

    def committed_volume(self) -> int:
        return sum(r.volume() * max(1, COMMODITIES[r.key].volume)
                   for r in self.routes)

    # ------------------------------------------------------------------
    # Hazard assessment (doc 3.4 "Hostile Hazard Avoidance")
    # ------------------------------------------------------------------
    def sector_is_hazardous(self, sector, world) -> bool:
        if sector.piracy_bp > config.ROUTE_PIRACY_ABORT_BP:
            return True
        mine = world.factions.get(self.owner)
        if mine is not None and sector.owner != self.owner:
            if mine.at_war_with(sector.owner):
                return True
        return False

    def route_is_hazardous(self, route, world) -> bool:
        if self.sector_is_hazardous(route.src, world):
            return True
        if self.sector_is_hazardous(route.dst, world):
            return True
        return any(self.sector_is_hazardous(s, world) for s in route.transit)

    # ------------------------------------------------------------------
    # Route evaluation (doc 3.4)
    # ------------------------------------------------------------------
    def evaluate_routes(self, world):
        """Scan connected markets, compute price deltas, factor in the distance
        volume malus and local tariffs, and keep the highest-margin routes."""
        if world.tick - self.last_eval_tick < config.ROUTE_EVALUATION_INTERVAL:
            return
        self.last_eval_tick = world.tick

        reachable = world.galaxy.within_range(self.home, config.ROUTE_JUMP_RANGE)
        pool = [(self.home, 0)] + reachable
        # Only consider sectors we are willing to operate in at all.
        pool = [(s, h) for s, h in pool
                if not self.sector_is_hazardous(s, world)]
        if len(pool) < 2:
            return

        # Rather than evaluating every sector pair (which is quadratic and the
        # dominant cost on a large galaxy), look for the actual arbitrage: the
        # cheapest supplied markets against the dearest demanding ones. The map
        # is a filled hexagon, so hop count equals axial hex distance and no
        # breadth-first search is needed to price a candidate.
        candidates = []
        for key in COMMODITIES.storable_keys:
            if key == "SHIP_TONNAGE":
                continue
            sources = [(s.market.spot(key), s) for s, _ in pool
                       if s.market.last_supply[key] > 0 and s.market.spot(key) > 0]
            if not sources:
                continue
            sources.sort(key=lambda t: t[0])
            sinks = [(s.market.spot(key), s) for s, _ in pool]
            sinks.sort(key=lambda t: -t[0])

            for buy_price, src in sources[:self.SCAN_WIDTH]:
                for sell_price, dst in sinks[:self.SCAN_WIDTH]:
                    if dst is src or sell_price <= buy_price:
                        continue
                    hops = hex_distance(src.coord, dst.coord)
                    if hops <= 0 or hops > config.ROUTE_JUMP_RANGE:
                        continue
                    gross_bp = ((sell_price - buy_price) * config.BP) // buy_price
                    dist_bp = config.ROUTE_DISTANCE_PENALTY_BP * max(0, hops - 1)
                    # Tariffs of the destination are known without the path;
                    # transit tariffs are added once the path is resolved.
                    tariff_bp = dst.tariff_bp if dst.owner != self.owner else 0
                    net_bp = gross_bp - tariff_bp - dist_bp
                    if net_bp < config.ROUTE_MIN_MARGIN_BP:
                        continue
                    # Prefer destinations with genuinely unfilled demand over
                    # those merely quoting a wide speculative spread.
                    score = net_bp
                    dm = dst.market
                    if dm.last_demand[key] > dm.last_supply[key]:
                        score += config.ROUTE_SHORTAGE_BONUS_BP
                    candidates.append((score, key, src, dst, hops))

        candidates.sort(key=lambda c: -c[0])

        # Resolve the actual corridor only for the shortlist, so the expensive
        # pathfinding runs a handful of times instead of thousands.
        shortlist = []
        for net_bp, key, src, dst, hops in candidates[:self.SHORTLIST]:
            path = world.galaxy.path(src, dst)
            if not path:
                continue
            transit = path[1:-1]
            if any(self.sector_is_hazardous(s, world) for s in transit):
                continue
            extra_tariff = sum(s.tariff_bp for s in transit
                               if s.owner != self.owner)
            net_bp -= extra_tariff
            if net_bp < config.ROUTE_MIN_MARGIN_BP:
                continue
            shortlist.append((net_bp, key, src, dst, len(path) - 1, transit))
        shortlist.sort(key=lambda c: -c[0])
        candidates = shortlist

        # Keep existing profitable routes; replace the worst with better ideas.
        existing = {(r.key, r.src.coord, r.dst.coord) for r in self.routes}
        for net_bp, key, src, dst, hops, transit in candidates:
            if len(self.routes) >= config.ROUTE_MAX_PER_FIRM:
                break
            sig = (key, src.coord, dst.coord)
            if sig in existing:
                continue
            route = TradeRoute(key, src, dst, hops, transit)
            if self.committed_volume() + route.volume() > self.capacity():
                break
            self.routes.append(route)
            existing.add(sig)

    def total_tariff_bp(self, dst, transit) -> int:
        """Tariffs of the destination plus every transit sector that is not
        ours (doc 3.4)."""
        total = 0
        if dst.owner != self.owner:
            total += dst.tariff_bp
        for s in transit:
            if s.owner != self.owner:
                total += s.tariff_bp
        return total

    # ------------------------------------------------------------------
    # PHASE 1: order submission
    # ------------------------------------------------------------------
    def submit_orders(self, world):
        self._orders = []
        # Top up ship tonnage at the home market.
        want = config.TRADER_CARGO_TARGET - self.cargo_units
        if want > 0:
            acc = self.wallet.account(self.home.market.currency)
            o = self.home.market.submit_buy(
                "SHIP_TONNAGE", min(want, config.TRADER_CARGO_BUY_MAX_PER_TICK),
                acc, self.hold, self, tag=f"{self.name} cargo")
            if o:
                self._orders.append(("cargo", None, o))

        for route in list(self.routes):
            if self.route_is_hazardous(route, world):
                # Terminate operations through hostile space immediately.
                self.routes.remove(route)
                self.dissolved_routes += 1
                world.log(f"{self.name} abandoned {route.label()} (hazard)")
                continue

            volume = route.volume()
            if volume <= 0:
                continue
            per_unit = max(1, COMMODITIES[route.key].volume)
            qty = volume // per_unit
            if qty <= 0:
                continue

            # Sell whatever the pipeline already delivered into the hold.
            have = self.hold.get(route.key)
            if have > 0:
                acc_dst = self.wallet.account(route.dst.market.currency)
                o = route.dst.market.submit_sell(
                    route.key, min(have, qty), acc_dst, self.hold, self,
                    tag=f"{self.name} {route.key}")
                if o:
                    self._orders.append(("sell", route, o))

            # Buy the next consignment at the source market.
            acc_src = self.wallet.account(route.src.market.currency)
            o = route.src.market.submit_buy(
                route.key, qty, acc_src, self.hold, self,
                tag=f"{self.name} {route.key}")
            if o:
                self._orders.append(("buy", route, o))

    # ------------------------------------------------------------------
    # FX: a firm must hold the sovereign currency of each market it buys in
    # ------------------------------------------------------------------
    def submit_fx(self, fx, world):
        needs = {}
        for route in self.routes:
            cur = route.src.market.currency
            price = route.src.market.spot(route.key)
            qty = route.volume() // max(1, COMMODITIES[route.key].volume)
            needs[cur] = needs.get(cur, 0) + price * qty
        needs[self.home.market.currency] = needs.get(
            self.home.market.currency, 0)

        operating = self.wallet.operating
        # Acquire shortfalls in currencies we need.
        for cur, need in needs.items():
            if cur == operating:
                continue
            have = self.wallet.cash(cur)
            short = need - have
            if short >= config.FX_MIN_ORDER_CENTS:
                fx.submit(self.wallet, operating, cur, short, self)
        # Repatriate currencies we no longer need.
        for cur, bal in self.wallet.foreign_balances():
            if bal <= 0:
                continue
            keep = needs.get(cur, 0)
            surplus = bal - keep
            if surplus >= config.FX_MIN_ORDER_CENTS:
                fx.submit(self.wallet, cur, operating, surplus, self)

    # ------------------------------------------------------------------
    # PHASE 2: settle route economics, pay tariffs, recalibrate
    # ------------------------------------------------------------------
    def post_market(self, world):
        self.profit_last = 0
        for kind, route, order in getattr(self, "_orders", ()):
            if route is None:
                continue
            if kind == "buy":
                route.last_bought = order.filled
                route.cost_basis += order.value
                route.last_profit = -order.value
            elif kind == "sell":
                route.last_sold = order.filled
                revenue = order.value
                # Tariffs on the realised sale value (doc 3.4).
                tariff = self.pay_tariffs(route, revenue, world)
                # Cost of goods actually sold, from the running cost basis.
                cogs = 0
                if route.last_sold > 0 and self.hold.get(route.key) + route.last_sold > 0:
                    held = self.hold.get(route.key) + route.last_sold
                    cogs = (route.cost_basis * route.last_sold) // held
                    route.cost_basis -= cogs
                net = revenue - tariff - cogs
                route.last_profit = route.last_profit + net \
                    if route.last_profit < 0 else net
                route.profit_total += net
                self.profit_last += net

        self.profit_total += self.profit_last
        self.recalibrate(world)

    def pay_tariffs(self, route, revenue, world) -> int:
        """Levy destination and transit tariffs on realised trade value."""
        if revenue <= 0:
            return 0
        paid_total = 0
        acc = self.wallet.account(route.dst.market.currency)
        targets = []
        if route.dst.owner != self.owner:
            targets.append(route.dst)
        targets.extend(s for s in route.transit if s.owner != self.owner)
        for sector in targets:
            due = apply_bp(revenue, sector.tariff_bp)
            if due <= 0:
                continue
            lord = world.factions.get(sector.owner)
            if lord is None:
                continue
            dst_acc = lord.wallet.account(acc.currency)
            if dst_acc.currency != acc.currency:
                continue
            paid = self.ledger.transfer(acc, dst_acc, due, "tariff")
            paid_total += paid
            sector.tax_collected_total += paid
            sector.last_tax_income += paid
            lord.tax_income_last += paid
        return paid_total

    def recalibrate(self, world):
        """Route Recalibration (doc 3.4)."""
        for route in list(self.routes):
            if route.last_profit < 0:
                route.loss_ticks += 1
                route.throttle_bp = config.ROUTE_LOSS_THROTTLE_BP
                if route.loss_ticks >= config.ROUTE_DISSOLVE_AFTER_LOSS_TICKS:
                    self.routes.remove(route)
                    self.dissolved_routes += 1
                    # Deliberately not logged: firms open and close marginal
                    # corridors constantly as spreads close, and logging each
                    # one buries the events that actually matter (wars,
                    # annexations, construction, piracy) under trade churn.
                    # The running count is on the Trade screen instead.
                    continue
            else:
                route.loss_ticks = 0
                route.throttle_bp = config.BP
                if route.profit_total >= config.ROUTE_LEVEL_UP_PROFIT and \
                        route.level < config.ROUTE_MAX_LEVEL:
                    trial = route.level + 1
                    old = route.level
                    route.level = trial
                    if self.committed_volume() > self.capacity():
                        route.level = old      # not enough cargo capacity yet
                    else:
                        route.profit_total = 0
                        world.log(f"{self.name} upgraded {route.label()}")

        # If trade capacity dropped (piracy destroyed cargo), down-level or
        # dissolve the lowest performers until we fit inside capacity again.
        while self.routes and self.committed_volume() > self.capacity():
            worst = min(self.routes, key=lambda r: r.profit_total)
            if worst.level > 1:
                worst.level -= 1
            else:
                self.routes.remove(worst)
                self.dissolved_routes += 1

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def working_capital(self) -> int:
        """Cash needed to keep every route's next consignment funded."""
        need = 0
        for route in self.routes:
            qty = route.volume() // max(1, COMMODITIES[route.key].volume)
            need += qty * route.src.market.spot(route.key)
        need += config.TRADER_CARGO_TARGET * \
            self.home.market.spot("SHIP_TONNAGE") // 4
        return max(need, 1) * config.TRADER_WORKING_CAPITAL_TICKS

    def pay_dividends(self, world) -> int:
        """Distribute trading profits to the firm's owner.

        Merchant firms buy low and sell high every tick, so they are the most
        profitable agents in the game -- and without a payout they are also the
        most dangerous. Left to accumulate, the firms' share of the galaxy's
        hard-capped money supply climbed past half: households and factories
        were squeezed out of the circulating medium entirely, factories could
        no longer make payroll, and the economy collapsed into a liquidity
        trap while the warehouses stayed full. Arbitrage profits have to flow
        back to their owner, who taxes and spends them.
        """
        fac = world.factions.get(self.owner)
        if fac is None:
            return 0

        # Pay out of EVERY currency the firm holds, not just its operating
        # one. A firm trading across three sovereigns banks its profits in
        # three currencies, and a dividend test that looked only at the home
        # account saw a near-empty till while the firm quietly accumulated a
        # third of the galaxy's money in foreign notes.
        reserves = self.currency_reserves()
        paid_total = 0
        for cur, acc in list(self.wallet.accounts.items()):
            if acc.balance <= 0:
                continue
            wc = reserves.get(cur, 0)
            if acc.balance * config.BP < wc * config.TRADER_DIVIDEND_COVER_BP:
                continue
            surplus = acc.balance - wc
            if surplus < config.DIVIDEND_MIN:
                continue
            payout = apply_bp(surplus, config.TRADER_DIVIDEND_PAYOUT_BP)
            if payout <= 0:
                continue
            paid_total += self.ledger.transfer(
                acc, fac.wallet.account(cur), payout, "dividend")

        self.dividends_total += paid_total
        return paid_total

    def currency_reserves(self):
        """Working capital needed per currency, i.e. per sovereign market the
        firm must be able to buy in next tick."""
        out = {}
        for route in self.routes:
            cur = route.src.market.currency
            qty = route.volume() // max(1, COMMODITIES[route.key].volume)
            out[cur] = out.get(cur, 0) + qty * route.src.market.spot(route.key)
        home = self.home.market.currency
        out[home] = out.get(home, 0) + \
            config.TRADER_CARGO_TARGET * self.home.market.spot("SHIP_TONNAGE") // 4
        return {k: max(1, v) * config.TRADER_WORKING_CAPITAL_TICKS
                for k, v in out.items()}

    def on_sovereignty_change(self, old_owner, new_owner, new_currency, world):
        if self.home.owner == new_owner:
            self.wallet.set_operating(new_currency)

    def book_value(self, world) -> int:
        cur = self.wallet.operating
        total = self.wallet.total_in(cur, world.fx)
        for key, qty in self.hold.stock.items():
            if qty:
                total += qty * self.home.market.spot(key)
        return total

    def __repr__(self):
        return f"<Firm {self.name} routes={len(self.routes)}>"

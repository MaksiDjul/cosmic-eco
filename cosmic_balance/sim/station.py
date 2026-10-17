"""
FACTORY & STATION INFRASTRUCTURE (doc 3.3).

Implements, in order of the design document:

*   Production Methods -- an extensible input->output matrix per tick.
*   Level Scaling -- each level multiplies base recipe throughput by 100%.
*   Physical Construction Sinks -- upgrades never consume an abstract lump of
    credits. A Construction Warehouse is deployed which places real buy orders
    for real construction commodities on the local market (X4 style).
*   Inventory Management -- per-commodity storage with a multi-tick input
    safety buffer.
*   Dynamic Throughput Tuning -- overclocking (more output, disproportionately
    worse input efficiency) and underclocking (throttle to limit losses).
*   Financial Halts -- never run with a missing input, never run with full
    output storage, never let cash go negative.
*   Operational Costs -- maintenance commodities per level per tick, and
    property tax to a foreign sector lord.
*   Corporate Governance -- exactly 100 equity shares; strict majority
    controls operations.
*   Capital Financing -- faction loans serviced with per-tick interest that
    starts only once Level 1 completes.
*   Abstract Shipping Fleets -- cargo assets cap how much volume the station
    can move through the market each tick; they are destroyed only by piracy.
"""

from collections import defaultdict

from . import config
from .commodities import COMMODITIES, PRODUCTION_METHODS
from .fixed import apply_bp, clamp
from .ledger import Inventory, Wallet

NORMAL, OVERCLOCK, UNDERCLOCK = "normal", "overclock", "underclock"
LABOR_NORMAL, LABOR_EFFICIENT, LABOR_INEFFICIENT = "labor_normal", "labor_efficient", "labor_inefficient"


def _restock_order(per_tick: int, target: int, stock: int) -> int:
    """How much to order this tick: replace what will be consumed, plus a
    fraction of any shortfall against the safety buffer.

    See config.RESTOCK_SPREAD_TICKS -- ordering the whole buffer at once
    overstates demand by a large multiple and destabilises the price ticker.
    """
    shortfall = target - stock
    if shortfall <= 0:
        return 0
    order = per_tick + shortfall // config.RESTOCK_SPREAD_TICKS
    return min(order, shortfall)


class ConstructionWarehouse:
    """A physical construction sink (doc 3.3).

    It is deployed next to the station, buys actual construction commodities on
    the local market using the station's cash, and only when the full physical
    bill of materials has arrived does the level complete. The goods are then
    consumed into the structure.
    """

    __slots__ = ("station", "target_level", "required", "store", "ledger",
                 "orders", "spent", "stalled_ticks", "last_progress")

    def __init__(self, station, target_level, ledger):
        self.station = station
        self.target_level = target_level
        self.ledger = ledger
        self.required = self.compute_requirements(target_level)
        self.store = Inventory(ledger, f"construction:{station.id}")
        for k, v in self.required.items():
            self.store.capacity[k] = v
        self.orders = []
        self.spent = 0
        self.stalled_ticks = 0
        self.last_progress = 0

    @staticmethod
    def compute_requirements(target_level):
        """Each level costs CONSTRUCTION_COST_LEVEL_SCALING_BP of the last."""
        req = {}
        scale = config.BP
        for _ in range(max(0, target_level - 1)):
            scale = (scale * config.CONSTRUCTION_COST_LEVEL_SCALING_BP) // config.BP
        for key, base in config.CONSTRUCTION_COST_PER_LEVEL.items():
            req[key] = max(1, (base * scale) // config.BP)
        return req

    def remaining(self):
        return {k: max(0, v - self.store.get(k)) for k, v in self.required.items()}

    def progress_bp(self) -> int:
        need = sum(self.required.values())
        if need <= 0:
            return config.BP
        have = sum(min(self.store.get(k), v) for k, v in self.required.items())
        return (have * config.BP) // need

    def submit_orders(self, market):
        """Place real market buy orders for the outstanding bill of materials."""
        self.orders = []
        acc = self.station.wallet.account(market.currency)
        for key, short in self.remaining().items():
            if short <= 0:
                continue
            qty = min(short, config.CONSTRUCTION_MAX_ORDER_PER_TICK)
            o = market.submit_buy(key, qty, acc, self.store, self.station,
                                  tag=f"{self.station.name} build")
            if o:
                self.orders.append(o)

    def settle(self):
        self.spent += sum(o.value for o in self.orders)
        progress = self.progress_bp()
        if progress > self.last_progress:
            self.last_progress = progress
            self.stalled_ticks = 0
        else:
            self.stalled_ticks += 1

    def is_stalled(self) -> bool:
        return self.stalled_ticks >= config.CONSTRUCTION_ABANDON_TICKS

    def is_complete(self) -> bool:
        return all(self.store.get(k) >= v for k, v in self.required.items())

    def consume_into_structure(self):
        """Burn the delivered materials into the new station level."""
        for key, qty in self.required.items():
            self.ledger.consume(self.store, key, qty, "construction")
        # Return any over-delivery to the station's own stores.
        for key in list(self.store.stock.keys()):
            left = self.store.get(key)
            if left > 0:
                self.ledger.move_goods(self.store, self.station.store, key,
                                       left, capped=False)


class Station:
    """A productive economic entity sitting in a sector."""

    _seq = 0

    def __init__(self, sector, method_key, ledger, currency_code, owner,
                 level=1, name=None, cash=0):
        Station._seq += 1
        self.id = Station._seq
        self.sector = sector
        self.method = PRODUCTION_METHODS[method_key]
        self.ledger = ledger
        self.level = 0                       # raised by construction
        self.name = name or f"{self.method.short}-{self.id}"

        # Corporate Governance (doc 3.3): exactly 100 shares.
        self.equity = {owner: config.EQUITY_TOTAL_SHARES}
        self._controller_cache = None
        self._storage_level = -1

        self.wallet = Wallet(ledger, f"station:{self.id}", currency_code)
        self.wallet.account(currency_code)
        if cash:
            ledger.mint(self.wallet.account(currency_code), cash, "station_seed")

        self.store = Inventory(ledger, f"station_store:{self.id}")
        self.condition = config.CONDITION_MAX
        # Fractional maintenance draw carried between ticks, in basis points.
        self.maint_accrual = defaultdict(int)
        # Maintenance goods: accumulated missed deliveries trigger downgrade.
        self.maint_missed = 0

        # Throughput tuning state
        self.clock = NORMAL
        self.labor_mode = LABOR_NORMAL
        self.labor_short_ticks = 0
        self.labor_surplus_ticks = 0
        self.halt_reason = None
        self.ran_last_tick = False

        # Finance
        self.loan = None
        self.revenue_last = 0
        self.costs_last = 0
        self.cashflow_last = 0
        # Economic margin: output value less input value and upkeep. This, not
        # cash flow, is what "net profitability" means for throughput tuning.
        self.margin_last = 0
        self.margin_ema = 0
        self.bad_ticks = 0
        self.good_ticks = 0
        self.profit_last = 0
        self.profit_total = 0
        self.profit_log = []                 # recent per-tick net results
        self.property_tax_last = 0
        self.interest_last = 0
        self.dividends_total = 0
        self.dividend_last = 0

        # Auto-downgrade tracking
        self.idle_ticks = 0

        # Construction
        self.construction = None
        self._sell_orders = []
        self._buy_orders = []
        self.output_depleted = False

        # Bring it up to its starting level instantly at scenario init.
        for _ in range(level):
            self._apply_level_up()
        self.begin_upgrade_if_requested = False
        self.upgrade_requested = False

    # ------------------------------------------------------------------
    # identity / governance
    # ------------------------------------------------------------------
    @property
    def controller(self):
        """The entity holding the strict majority of shares assumes
        operational control (doc 3.3). Cached: this is read thousands of times
        per tick and the share register changes very rarely."""
        if self._controller_cache is not None:
            return self._controller_cache
        self._controller_cache = self._compute_controller()
        return self._controller_cache

    def _compute_controller(self):
        best, best_shares = None, 0
        for holder, shares in self.equity.items():
            if shares > best_shares:
                best, best_shares = holder, shares
        if best_shares >= config.EQUITY_CONTROL_THRESHOLD:
            return best
        return best        # largest minority runs it if nobody has a majority

    def has_majority(self) -> bool:
        return max(self.equity.values(), default=0) >= config.EQUITY_CONTROL_THRESHOLD

    def transfer_shares(self, src, dst, shares):
        shares = min(shares, self.equity.get(src, 0))
        if shares <= 0:
            return 0
        self.equity[src] -= shares
        if self.equity[src] == 0:
            del self.equity[src]
        self.equity[dst] = self.equity.get(dst, 0) + shares
        self._controller_cache = None
        return shares

    @property
    def inputs_needed(self):
        return self.method.inputs

    @property
    def outputs_made(self):
        return self.method.outputs

    @property
    def is_extractor(self) -> bool:
        return self.method.extracts is not None

    # ------------------------------------------------------------------
    # storage sizing
    # ------------------------------------------------------------------
    def _resize_storage(self, force=False):
        """Recompute per-commodity capacities. Only depends on the station's
        level, so it is skipped unless that has changed."""
        if not force and self._storage_level == self.level:
            return
        self._storage_level = self.level
        lvl = max(1, self.level)
        for key, per in self.method.inputs.items():
            self.store.capacity[key] = max(
                config.STORAGE_MIN_CAPACITY,
                per * lvl * config.STORAGE_PER_LEVEL_INPUT_TICKS)
        for key, per in self.method.outputs.items():
            self.store.capacity[key] = max(
                config.STORAGE_MIN_CAPACITY,
                per * lvl * config.STORAGE_PER_LEVEL_OUTPUT_TICKS)
        for key, per_bp in config.MAINTENANCE_PER_LEVEL_BP.items():
            cur = self.store.capacity.get(key, 0)
            need = max(config.STORAGE_MIN_CAPACITY,
                       self.maintenance_per_tick(key) *
                       config.MAINTENANCE_BUFFER_TICKS * 2)
            self.store.capacity[key] = max(cur, need)
        # Maintenance goods storage
        mg_need = config.MAINT_GOODS_PER_LEVEL * max(1, self.level)
        self.store.capacity["MAINT_GOODS"] = max(
            self.store.capacity.get("MAINT_GOODS", 0),
            config.STORAGE_MIN_CAPACITY,
            mg_need * config.MAINT_BUFFER_TICKS * 2)
        self.store.capacity["SHIP_TONNAGE"] = max(
            self.store.capacity.get("SHIP_TONNAGE", 0),
            config.STATION_CARGO_PER_LEVEL * lvl + 2)
        # Construction goods need a bay big enough to take back a whole bill
        # of materials: a completed or abandoned project returns whatever it
        # had already received, and that must fit rather than overflow.
        req = ConstructionWarehouse.compute_requirements(
            min(config.STATION_MAX_LEVEL, lvl + 1))
        for key in config.CONSTRUCTION_GOODS:
            self.store.capacity[key] = max(
                self.store.capacity.get(key, 0),
                config.STORAGE_MIN_CAPACITY, req.get(key, 0) * 2)

    def maintenance_per_tick(self, key) -> int:
        """Whole-unit maintenance draw per tick, rounded up so the ordering
        logic always asks for at least as much as will be consumed."""
        per_bp = config.MAINTENANCE_PER_LEVEL_BP.get(key, 0)
        total_bp = per_bp * max(1, self.level)
        return -(-total_bp // config.BP)      # ceiling division

    def seed_stock(self, ticks=None):
        """Scenario-initialisation helper: pre-load the input, maintenance and
        cargo stock a going concern would already have on hand.

        Without this, every station in a freshly built scenario starts with
        empty silos, nothing can produce on tick 1, and the whole chain
        deadlocks before any market has had a chance to form a price.
        """
        # Half the safety buffer: enough that tick one can produce, but not so
        # much that it hides the sector's true input demand from the price
        # ticker for the first dozen ticks (which lets upstream extractors
        # mistake a stocked larder for a glut and throttle themselves).
        ticks = ticks or max(1, config.INPUT_BUFFER_TICKS // 2)
        self._resize_storage(force=True)
        lvl = max(1, self.level)
        for key, per in self.method.inputs.items():
            if key == "LABOR":
                continue           # labour tokens seeded from population
            self.ledger.produce(self.store, key, per * lvl * ticks,
                                "scenario_seed")
        for key in config.MAINTENANCE_PER_LEVEL_BP:
            self.ledger.produce(self.store, key,
                                self.maintenance_per_tick(key) *
                                config.MAINTENANCE_BUFFER_TICKS,
                                "scenario_seed")
        # Seed maintenance goods
        mg_need = config.MAINT_GOODS_PER_LEVEL * max(1, self.level)
        self.ledger.produce(self.store, "MAINT_GOODS",
                            mg_need * config.MAINT_BUFFER_TICKS,
                            "scenario_seed")
        self.ledger.produce(self.store, "SHIP_TONNAGE",
                            config.STATION_CARGO_PER_LEVEL * lvl,
                            "scenario_seed")

    def _apply_level_up(self):
        self.level = min(config.STATION_MAX_LEVEL, self.level + 1)
        self._resize_storage()
        if self.loan is not None and self.level >= 1:
            # Interest obligations begin only once Level 1 completes (doc 3.3).
            self.loan.active = True

    # ------------------------------------------------------------------
    # throughput factors
    # ------------------------------------------------------------------
    def output_factor_bp(self) -> int:
        base = config.NORMAL_BP
        if self.clock == OVERCLOCK:
            base = config.OVERCLOCK_THROUGHPUT_BP
        elif self.clock == UNDERCLOCK:
            base = config.UNDERCLOCK_THROUGHPUT_BP
        if self.labor_mode == LABOR_EFFICIENT:
            base = (base * config.LABOR_EFFICIENT_OUTPUT_BP) // config.BP
        elif self.labor_mode == LABOR_INEFFICIENT:
            base = (base * config.LABOR_INEFFICIENT_OUTPUT_BP) // config.BP
        return base

    def input_factor_bp(self, key=None) -> int:
        base = config.NORMAL_BP
        if self.clock == OVERCLOCK:
            base = config.OVERCLOCK_INPUT_PENALTY_BP
        elif self.clock == UNDERCLOCK:
            base = config.UNDERCLOCK_INPUT_BP
        if key == "LABOR":
            if self.labor_mode == LABOR_EFFICIENT:
                base = (base * config.LABOR_EFFICIENT_LABOR_BP) // config.BP
            elif self.labor_mode == LABOR_INEFFICIENT:
                base = (base * config.LABOR_INEFFICIENT_LABOR_BP) // config.BP
        elif key is not None and key not in config.MAINTENANCE_PER_LEVEL_BP:
            if self.labor_mode == LABOR_EFFICIENT:
                base = (base * config.LABOR_EFFICIENT_INPUT_BP) // config.BP
            elif self.labor_mode == LABOR_INEFFICIENT:
                base = (base * config.LABOR_INEFFICIENT_INPUT_BP) // config.BP
        return base

    # ------------------------------------------------------------------
    # shipping capacity (doc 3.4 "Abstract Shipping Fleets")
    # ------------------------------------------------------------------
    def cargo_units(self) -> int:
        return self.store.get("SHIP_TONNAGE")

    def recipe_is_viable(self, market) -> bool:
        """Would one tick of production pay for its own inputs right now?"""
        lvl = max(1, self.level)
        in_cost = sum(per * lvl * market.spot(key)
                      for key, per in self.method.inputs.items())
        out_value = sum(per * lvl * market.spot(key)
                        for key, per in self.method.outputs.items())
        return out_value >= in_cost

    def trade_volume_budget(self) -> int:
        return (config.STATION_BASE_TRADE_VOLUME +
                self.cargo_units() * config.TONNAGE_VOLUME_PER_UNIT)

    # ------------------------------------------------------------------
    # PHASE 1: order submission
    # ------------------------------------------------------------------
    def submit_orders(self, market, world):
        self._resize_storage()
        self._sell_orders = []
        self._buy_orders = []
        acc = self.wallet.account(market.currency)
        budget_volume = self.trade_volume_budget()
        # Reserve part of the hold for inbound freight. Without this, a tick's
        # worth of exports can consume the entire shipping budget and leave the
        # plant unable to buy its own feedstock or upkeep.
        sell_budget = apply_bp(budget_volume, config.SELL_VOLUME_SHARE_BP)

        def vol(key, qty):
            return COMMODITIES[key].volume * qty

        def cap_by_volume(key, qty, budget):
            unit = COMMODITIES[key].volume
            if unit <= 0:
                return qty
            return min(qty, budget // unit)

        # --- 1. sell finished output ------------------------------------
        for key in self.method.outputs:
            have = self.store.get(key)
            reserve = 0
            qty = have - reserve
            if qty <= 0:
                continue
            qty = cap_by_volume(key, qty, sell_budget)
            if qty <= 0:
                continue
            o = market.submit_sell(key, qty, acc, self.store, self, tag=self.name)
            if o:
                self._sell_orders.append(o)
                sell_budget -= vol(key, o.qty)
                budget_volume -= vol(key, o.qty)

        # --- 2. buy maintenance commodities FIRST -----------------------
        # Upkeep outranks production inputs in the queue: a plant that
        # skips maintenance to buy feedstock decays toward uselessness,
        # and an idle-but-maintained plant can resume the moment its
        # inputs arrive.
        for key in config.MAINTENANCE_PER_LEVEL_BP:
            per_tick = self.maintenance_per_tick(key)
            target = max(1, per_tick * config.MAINTENANCE_BUFFER_TICKS)
            short = _restock_order(per_tick, target, self.store.get(key))
            if short <= 0:
                continue
            qty = cap_by_volume(key, min(short, self.store.space(key)),
                                budget_volume)
            if qty <= 0:
                continue
            o = market.submit_buy(key, qty, acc, self.store, self,
                                  tag=f"{self.name} upkeep")
            if o:
                self._buy_orders.append(o)
                budget_volume -= vol(key, o.qty)

        # --- 2b. buy MAINT_GOODS (station-level maintenance) -------------
        # Habitation stations draw maintenance money from population cash.
        mg_acc = acc
        if self.is_habitation and self.sector.pops:
            pop = max(self.sector.pops, key=lambda p: p.cash)
            mg_acc = pop.wallet.account(market.currency)
        mg_per_tick = config.MAINT_GOODS_PER_LEVEL * max(1, self.level)
        mg_target = max(1, mg_per_tick * config.MAINT_BUFFER_TICKS)
        mg_short = _restock_order(mg_per_tick, mg_target,
                                  self.store.get("MAINT_GOODS"))
        if mg_short > 0:
            qty = cap_by_volume("MAINT_GOODS",
                                min(mg_short, self.store.space("MAINT_GOODS")),
                                budget_volume)
            if qty > 0:
                o = market.submit_buy("MAINT_GOODS", qty, mg_acc, self.store,
                                      self, tag=f"{self.name} maint")
                if o:
                    self._buy_orders.append(o)
                    budget_volume -= vol("MAINT_GOODS", o.qty)

        # --- 3. buy production inputs with a safety buffer --------------
        # Demand is price-elastic: a plant whose recipe is underwater at
        # current quotes buys only enough to keep running, and stops
        # speculatively filling its warehouses. Without this, loss-making
        # plants keep bidding for stock they cannot profitably use, which holds
        # the order book permanently short and ratchets input prices higher --
        # deepening the very losses that caused it.
        viable = self.recipe_is_viable(market)
        lvl = max(1, self.level)
        for key, per in self.method.inputs.items():
            per_tick = per * lvl
            buffer_ticks = config.INPUT_BUFFER_TICKS
            if not viable:
                buffer_ticks = 1
            target = per_tick * buffer_ticks
            short = _restock_order(per_tick, target, self.store.get(key))
            if short <= 0:
                continue
            qty = cap_by_volume(key, min(short, self.store.space(key)),
                                budget_volume)
            if qty <= 0:
                continue
            o = market.submit_buy(key, qty, acc, self.store, self, tag=self.name)
            if o:
                self._buy_orders.append(o)
                budget_volume -= vol(key, o.qty)

        # --- 4. top up ship tonnage for shipping ----------------------------
        want_cargo = config.STATION_CARGO_PER_LEVEL * lvl - self.cargo_units()
        if want_cargo > 0:
            qty = min(want_cargo, config.STATION_CARGO_BUY_MAX_PER_TICK)
            o = market.submit_buy("SHIP_TONNAGE", qty, acc, self.store, self,
                                  tag=f"{self.name} cargo")
            if o:
                self._buy_orders.append(o)

        # --- 5. construction warehouse ----------------------------------
        if self.construction is not None:
            self.construction.submit_orders(market)

    # ------------------------------------------------------------------
    # PHASE 2: settle finances, then produce
    # ------------------------------------------------------------------
    def post_market(self, world):
        sector = self.sector
        market = sector.market

        revenue = sum(o.value for o in self._sell_orders)
        costs = sum(o.value for o in self._buy_orders)
        if self.construction is not None:
            self.construction.settle()
            costs += sum(o.value for o in self.construction.orders)

        acc = self.wallet.account(market.currency)

        # --- property tax (doc 3.3 "Operational Costs") -----------------
        self.property_tax_last = 0
        ctrl = self.controller
        if ctrl != sector.owner and sector.property_tax_bp > 0:
            base = config.PROPERTY_TAX_BASE_PER_LEVEL * max(1, self.level)
            due = apply_bp(base, sector.property_tax_bp)
            lord = world.factions.get(sector.owner)
            if lord is not None and due > 0:
                paid = self.ledger.transfer(acc, lord.wallet.account(acc.currency),
                                            due, "tax:property")
                self.property_tax_last = paid
                costs += paid
                sector.tax_collected_total += paid
                sector.last_tax_income += paid
                lord.tax_income_last += paid

        # --- loan interest (doc 3.3 "Capital Financing") ----------------
        self.interest_last = 0
        if self.loan is not None and self.loan.active:
            due = self.loan.interest_due() + self.loan.arrears
            lender = world.factions.get(self.loan.lender)
            if lender is not None and due > 0:
                dst = lender.wallet.account(acc.currency)
                if dst.currency == acc.currency:
                    paid = self.ledger.transfer(acc, dst, due, "interest")
                else:
                    paid = 0
                self.interest_last = paid
                self.loan.paid_total += paid
                self.loan.arrears = max(0, due - paid)
                costs += paid
                lender.interest_income_last += paid
                if self.loan.arrears >= config.ARREARS_SEIZE_THRESHOLD:
                    self._seize_by_lender(world)

        # --- maintenance consumption (regardless of production) ---------
        # Fractional per-level draw is accrued in basis points and whole units
        # are consumed as the accrual crosses them.
        maintained = True
        met_bp, due_bp = 0, 0
        upkeep = self.property_tax_last + self.interest_last
        for key, per_bp in config.MAINTENANCE_PER_LEVEL_BP.items():
            self.maint_accrual[key] += per_bp * max(1, self.level)
            due, self.maint_accrual[key] = divmod(self.maint_accrual[key],
                                                  config.BP)
            if due <= 0:
                continue
            got = self.ledger.consume(self.store, key, due, "maintenance")
            upkeep += got * market.spot(key)
            due_bp += due
            met_bp += got
            if got < due:
                maintained = False
                # Unmet obligations stay owed so the plant keeps trying to
                # catch up -- but the arrears are CAPPED. An uncapped backlog
                # grew to hundreds of units during a long shortage, and then
                # swallowed every unit of metal that arrived, so the station
                # could never actually get back to maintained status.
                owed = self.maint_accrual[key] + (due - got) * config.BP
                ceiling = (self.maintenance_per_tick(key) *
                           config.MAINTENANCE_BUFFER_TICKS * config.BP)
                self.maint_accrual[key] = min(owed, ceiling)
        if maintained:
            self.condition = min(config.CONDITION_MAX,
                                 self.condition + config.CONDITION_REPAIR_ON_MET)
        elif due_bp > 0 and met_bp > 0:
            # Partial upkeep buys partial recovery, scaled by the share of the
            # bill that was actually met, so a region starved of metal can
            # still claw its way back out instead of being pinned at zero.
            share = (met_bp * config.BP) // due_bp
            repair = (config.CONDITION_REPAIR_ON_MET * share) // config.BP
            decay = (config.CONDITION_DECAY_ON_MISSING *
                     (config.BP - share)) // config.BP
            self.condition = max(0, min(config.CONDITION_MAX,
                                        self.condition + repair - decay))
        else:
            self.condition = max(0, self.condition - config.CONDITION_DECAY_ON_MISSING)

        # --- MAINT_GOODS consumption (counter-based downgrade) -----------
        mg_need = config.MAINT_GOODS_PER_LEVEL * max(1, self.level)
        mg_got = self.ledger.consume(self.store, "MAINT_GOODS", mg_need,
                                     "maint_goods")
        mg_deficit = mg_need - mg_got
        if mg_deficit > 0:
            self.maint_missed += mg_deficit
        if self.maint_missed >= config.MAINT_MISSED_DOWNGRADE:
            self.maint_missed -= config.MAINT_MISSED_DOWNGRADE
            self._downgrade(world)

        # --- cash flow (for the finance screens) ------------------------
        self.revenue_last = revenue
        self.costs_last = costs
        self.cashflow_last = revenue - costs

        # --- throughput decision ----------------------------------------
        # NOTE: the clock is decided on the PRECEDING tick's economic margin
        # (doc 3.3), not on this tick's cash flow. The distinction matters
        # enormously: a healthy station restocking its six-tick input buffer
        # has sharply negative cash flow while being highly profitable. Judging
        # it on cash flow throttles it to 40% output, which cuts its revenue,
        # which keeps cash flow negative -- an absorbing trap that drags the
        # whole chain down with it.
        self.output_depleted = all(self.store.get(k) == 0
                                   for k in self.method.outputs) \
            if self.method.outputs else False
        self._decide_clock()

        # --- construction abandonment -----------------------------------
        if self.construction is not None and self.construction.is_stalled():
            missing = [k for k, v in self.construction.remaining().items()
                       if v > 0]
            world.log(f"{self.name} abandoned construction -- cannot source "
                      f"{', '.join(missing)} in {sector.name}")
            self.cancel_upgrade(world)

        # --- construction completion ------------------------------------
        if self.construction is not None and self.construction.is_complete():
            self.construction.consume_into_structure()
            world.log(f"{self.name} completed construction to level "
                      f"{self.construction.target_level}")
            self.construction = None
            self._apply_level_up()

        # --- production, and this tick's true profitability -------------
        out_value, in_value = self.produce(world)
        if self.ran_last_tick:
            self.idle_ticks = 0
        else:
            self.idle_ticks += 1
        self.margin_last = out_value - in_value - upkeep
        self.profit_last = self.margin_last
        self.profit_total += self.margin_last
        self.profit_log.append(self.margin_last)
        if len(self.profit_log) > 64:
            self.profit_log.pop(0)

    def _decide_clock(self):
        """Dynamic Throughput Tuning (doc 3.3).

        Judged on economic margin -- the value of goods produced less the value
        of inputs consumed and upkeep paid -- rather than on cash movements,
        and on a SMOOTHED margin with hysteresis, because the clock is a
        discrete switch and a twitchy one turns into a bang-bang controller
        whose oscillation propagates up the whole production chain.
        """
        # Exponential moving average of profitability.
        w = config.CLOCK_MARGIN_EMA_BP
        self.margin_ema = (self.margin_ema * (config.BP - w) +
                           self.margin_last * w) // config.BP

        threshold = config.OVERCLOCK_PROFIT_THRESHOLD * max(1, self.level)
        if self.margin_ema < 0:
            self.bad_ticks += 1
            self.good_ticks = 0
        elif self.margin_ema > threshold:
            self.good_ticks += 1
            self.bad_ticks = 0
        else:
            self.bad_ticks = 0
            self.good_ticks = 0

        if self.bad_ticks >= config.CLOCK_UNDERCLOCK_AFTER:
            self.clock = UNDERCLOCK
        elif (self.output_depleted and
              self.good_ticks >= config.CLOCK_OVERCLOCK_AFTER and
              self._has_input_headroom()):
            self.clock = OVERCLOCK
        else:
            self.clock = NORMAL

        self._decide_labor_mode()

    def _decide_labor_mode(self):
        lvl = max(1, self.level)
        labor_per = self.method.inputs.get("LABOR", 0)
        if labor_per <= 0:
            self.labor_mode = LABOR_NORMAL
            return
        labor_need = labor_per * lvl
        labor_stock = self.store.get("LABOR")
        if labor_stock < labor_need:
            self.labor_short_ticks += 1
            self.labor_surplus_ticks = 0
        elif labor_stock >= labor_need * config.LABOR_SURPLUS_STOCK_TICKS:
            self.labor_surplus_ticks += 1
            self.labor_short_ticks = 0
        else:
            self.labor_short_ticks = 0
            self.labor_surplus_ticks = 0

        if self.labor_short_ticks >= config.LABOR_MODE_SHORTAGE_TICKS:
            self.labor_mode = LABOR_EFFICIENT
        elif self.labor_surplus_ticks >= config.LABOR_MODE_SURPLUS_TICKS:
            self.labor_mode = LABOR_INEFFICIENT
        else:
            self.labor_mode = LABOR_NORMAL

    def _has_input_headroom(self) -> bool:
        lvl = max(1, self.level)
        for key, per in self.method.inputs.items():
            factor = config.OVERCLOCK_INPUT_PENALTY_BP
            if key == "LABOR" and self.labor_mode == LABOR_INEFFICIENT:
                factor = (factor * config.LABOR_INEFFICIENT_LABOR_BP) // config.BP
            need = (per * lvl * factor) // config.BP
            if self.store.get(key) < need * config.OVERCLOCK_MIN_INPUT_TICKS:
                return False
        return True

    # ------------------------------------------------------------------
    def produce(self, world):
        """Run the conversion formula for one tick, honouring all halts.

        Returns ``(output_value, input_value)`` at local spot prices, which is
        what the throughput tuner uses to judge real profitability.
        """
        self.ran_last_tick = False
        self.halt_reason = None

        if self.level <= 0:
            self.halt_reason = "under construction"
            return 0, 0
        lvl = self.level
        # Condition scales output rather than gating it (see config), so a
        # maintenance famine is survivable instead of terminal.
        cond_bp = max(config.CONDITION_MIN_EFFICIENCY_BP,
                      (self.condition * config.BP) // config.CONDITION_MAX)
        need = {k: max(0, (v * lvl * self.input_factor_bp(k)) // config.BP)
                for k, v in self.method.inputs.items()}
        plan = self.method.scaled_outputs(
            lvl, (self.output_factor_bp() * cond_bp) // config.BP)
        if not plan:
            return 0, 0

        # HALT: any required input entirely missing (doc 3.3 "Financial Halts").
        for key, qty in need.items():
            if qty > 0 and self.store.get(key) <= 0:
                self.halt_reason = f"no {key}"
                return 0, 0

        # HALT: output storage at absolute capacity.
        if all(self.store.space(k) <= 0 for k in plan):
            self.halt_reason = "output full"
            return 0, 0

        # Scale the run down to whatever the scarcest input allows.
        limit_bp = config.BP
        for key, qty in need.items():
            if qty <= 0:
                continue
            have = self.store.get(key)
            limit_bp = min(limit_bp, (have * config.BP) // qty)

        # ...and to whatever the output warehouse can accept.
        for key, qty in plan.items():
            if qty <= 0:
                continue
            space = self.store.space(key)
            limit_bp = min(limit_bp, (space * config.BP) // qty)

        if limit_bp <= 0:
            # Distinguish the two very different reasons a run can scale to
            # nothing: nowhere to put the output, versus nothing to make it
            # from. Reporting both as "no throughput" hid a large, ordinary
            # glut behind a label that reads like a fault.
            blocked_out = any(self.store.space(k) < q
                              for k, q in plan.items() if q > 0)
            self.halt_reason = "output full" if blocked_out else "no inputs"
            return 0, 0

        actual_in = {k: apply_bp(v, limit_bp) for k, v in need.items()}
        actual_out = {k: apply_bp(v, limit_bp) for k, v in plan.items()}

        # Extraction methods draw their output from the finite resource node
        # instead of conjuring it (doc 4 "Raw Harvestables").
        if self.is_extractor:
            node = self.sector.node(self.method.extracts)
            if node is None:
                self.halt_reason = "no deposit"
                return 0, 0
            key = self.method.extracts
            want = actual_out.get(key, 0)
            if want <= 0:
                self.halt_reason = "no throughput"
                return 0, 0
            got = node.extract(want)
            if got <= 0:
                self.halt_reason = "deposit exhausted"
                return 0, 0
            ratio_bp = (got * config.BP) // want if want else 0
            actual_out[key] = got
            actual_in = {k: apply_bp(v, ratio_bp) for k, v in actual_in.items()}

        if not any(v > 0 for v in actual_out.values()):
            self.halt_reason = "no throughput"
            return 0, 0

        # Consume inputs, then create outputs, valuing both at local prices.
        market = self.sector.market
        in_value = 0
        out_value = 0
        for key, qty in actual_in.items():
            if qty > 0:
                got = self.ledger.consume(self.store, key, qty, "input")
                in_value += got * market.spot(key)
        for key, qty in actual_out.items():
            if qty > 0:
                made = self.ledger.produce(self.store, key, qty, "output")
                out_value += made * market.spot(key)
        self.ran_last_tick = True
        return out_value, in_value

    # ------------------------------------------------------------------
    # corporate distributions
    # ------------------------------------------------------------------
    def operating_cost_per_tick(self) -> int:
        """Cash needed to buy one tick of inputs at current local prices."""
        market = self.sector.market
        lvl = max(1, self.level)
        return sum(per * lvl * market.spot(key)
                   for key, per in self.method.inputs.items())

    def working_capital(self) -> int:
        """Cash the station keeps on hand to fund its own operations."""
        market = self.sector.market
        lvl = max(1, self.level)
        need = 0
        for key, per in self.method.inputs.items():
            need += per * lvl * market.spot(key)
        for key in config.MAINTENANCE_PER_LEVEL_BP:
            need += self.maintenance_per_tick(key) * market.spot(key)
        if self.construction is not None:
            for key, qty in self.construction.remaining().items():
                need += qty * market.spot(key)
        return need * config.WORKING_CAPITAL_TICKS

    @property
    def is_habitation(self) -> bool:
        return self.method.key == config.HABITATION_METHOD_KEY

    def pay_dividends(self, world) -> int:
        if self.is_habitation:
            return 0
        from .market import MarketHub
        market = self.sector.market
        acc = self.wallet.account(market.currency)
        wc = self.working_capital()
        # Only distribute once the buffer is comfortably covered, so a price
        # swing cannot leave the plant unable to make payroll.
        if acc.balance * config.BP < wc * config.DIVIDEND_COVER_BP:
            return 0
        surplus = acc.balance - wc
        if surplus < config.DIVIDEND_MIN:
            return 0
        payout = apply_bp(surplus, config.DIVIDEND_PAYOUT_BP)
        if payout <= 0:
            return 0

        holders = [(k, v) for k, v in self.equity.items() if v > 0]
        if not holders:
            return 0
        amounts = MarketHub._split_money(payout, [v for _, v in holders])
        paid_total = 0
        for (holder, _), amount in zip(holders, amounts):
            fac = world.factions.get(holder)
            if fac is None or amount <= 0:
                continue
            paid_total += self.ledger.transfer(
                acc, fac.wallet.account(acc.currency), amount, "dividend")
        self.dividends_total += paid_total
        self.dividend_last = paid_total
        return paid_total

    # ------------------------------------------------------------------
    # construction / upgrades
    # ------------------------------------------------------------------
    def start_upgrade(self, world) -> bool:
        """Deploy a Construction Warehouse for the next level."""
        if self.construction is not None:
            return False
        if self.level >= config.STATION_MAX_LEVEL:
            return False
        self.construction = ConstructionWarehouse(self, self.level + 1,
                                                  self.ledger)
        world.log(f"{self.name} began construction to level {self.level + 1}")
        return True

    def cancel_upgrade(self, world):
        if self.construction is None:
            return False
        # Materials already delivered revert to station stores; nothing is lost.
        for key in list(self.construction.store.stock.keys()):
            qty = self.construction.store.get(key)
            if qty:
                self.ledger.move_goods(self.construction.store, self.store,
                                       key, qty, capped=False)
        self.construction = None
        world.log(f"{self.name} cancelled construction")
        return True

    def _seize_by_lender(self, world):
        lender = self.loan.lender
        ctrl = self.controller
        if ctrl == lender:
            return
        moved = self.transfer_shares(ctrl, lender, config.EQUITY_TOTAL_SHARES)
        if moved:
            world.log(f"{self.name} seized by {world.factions[lender].name} "
                      f"over unpaid debt")
            self.loan.arrears = 0

    # ------------------------------------------------------------------
    # valuation / FX / sovereignty
    # ------------------------------------------------------------------
    def book_value(self, world) -> int:
        market = self.sector.market
        cur = market.currency
        total = self.wallet.total_in(cur, world.fx)
        for key, qty in self.store.stock.items():
            if qty:
                total += qty * market.spot(key)
        if self.loan is not None:
            total -= max(0, self.loan.principal - self.loan.paid_total)
        return total

    def on_sovereignty_change(self, old_owner, new_owner, new_currency, world):
        """Doc 3.2: stations retain old cash but must trade into the new
        sovereign currency to meet tax and labour obligations."""
        self.wallet.set_operating(new_currency)

    def submit_fx(self, fx):
        from .currency import convert_legacy_cash
        convert_legacy_cash(self.wallet, fx, self)

    # ------------------------------------------------------------------
    # downgrade / destruction
    # ------------------------------------------------------------------
    def downsize(self, world):
        """Player-initiated downgrade: shed one level, never destroy."""
        if self.level <= 1:
            return
        self._downgrade(world)

    def _downgrade(self, world):
        """Shed one level; destroy the station if already at level 1."""
        if self.level <= 1:
            self._destroy(world)
            return
        self.level -= 1
        self._resize_storage()
        for key in list(self.store.stock.keys()):
            excess = self.store.get(key) - self.store.capacity.get(key, 0)
            if excess > 0:
                self.ledger.consume(self.store, key, excess, "downgrade_shed")
        world.log(f"{self.name} downgraded to level {self.level}")

    def _destroy(self, world):
        """Permanently remove this station, conserving all value."""
        owner = world.factions.get(self.sector.owner)
        if owner:
            for cur, acc in list(self.wallet.accounts.items()):
                if acc.balance > 0:
                    self.ledger.transfer(acc, owner.wallet.account(cur),
                                         acc.balance, "station_destroyed")
        for key in list(self.store.stock.keys()):
            qty = self.store.get(key)
            if qty > 0:
                self.ledger.consume(self.store, key, qty, "station_destroyed")
        if self in self.sector.stations:
            self.sector.stations.remove(self)
        if self in world.stations:
            world.stations.remove(self)
        world.log(f"{self.name} destroyed in {self.sector.name}")

    def should_auto_downgrade(self) -> bool:
        """Check if this station should shed a level due to poor performance."""
        if self.level <= 0 or self.construction is not None:
            return False
        threshold = config.STATION_DOWNGRADE_BAD_TICKS
        if self.bad_ticks >= threshold and self.clock == UNDERCLOCK:
            return True
        if self.idle_ticks >= threshold and self.level > 0:
            return True
        return False

    # ------------------------------------------------------------------
    def status_text(self) -> str:
        if self.construction is not None:
            return f"building L{self.construction.target_level} " \
                   f"({self.construction.progress_bp() // 100}%)"
        if self.ran_last_tick:
            parts = [self.clock]
            if self.labor_mode != LABOR_NORMAL:
                parts.append(self.labor_mode)
            return " ".join(parts)
        return self.halt_reason or "idle"

    def __repr__(self):
        return f"<Station {self.name} L{self.level} @{self.sector.name}>"

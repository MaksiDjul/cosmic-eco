"""
DEMOGRAPHICS & LABOUR (doc 3.5).

*   The Labor Pool -- sectors hold population density which provides Labour
    Tokens.
*   Population Entities -- pops are economic consumers with their own cash
    account and consumption warehouse. They generate Labour Tokens proportional
    to their size and sell them directly onto the local market WITHOUT
    requiring material inputs.
*   The Consumer Loop -- with their earned wages they pay local sector taxes
    and spend the remainder on consumer goods to sustain and grow.
"""

from . import config
from .commodities import COMMODITIES
from .fixed import apply_bp, clamp
from .ledger import Inventory, Wallet


class Pop:
    _seq = 0

    def __init__(self, sector, size, ledger, currency_code, name=None):
        Pop._seq += 1
        self.id = Pop._seq
        self.sector = sector
        self.size = int(size)
        self.name = name or f"Pop-{self.id}"

        self.wallet = Wallet(ledger, f"pop:{self.id}", currency_code)
        self.wallet.account(currency_code)
        self.ledger = ledger

        # Consumption warehouse (doc 3.5)
        self.store = Inventory(ledger, f"pop_store:{self.id}")
        self._resize_storage()

        # Per-tick telemetry
        self.last_income = 0
        self.last_tax = 0
        self.last_spend = 0
        self.public_income_last = 0
        self.luxury_consumed = 0
        self.satisfaction_bp = config.BP
        self.food_fill_bp = config.BP
        self.hunger_ticks = 0
        self.last_labor_offered = 0
        self.last_labor_sold = 0
        self.need_fill_bp = {}
        self._labor_order = None
        self._buy_orders = []

    # ------------------------------------------------------------------
    def _resize_storage(self):
        ticks = config.POP_CONSUMPTION_STORAGE_TICKS
        for key, per_1000 in config.SUSTENANCE_NEEDS.items():
            need = self.need_for(key)
            self.store.capacity[key] = max(config.STORAGE_MIN_CAPACITY,
                                           need * ticks)
        lab_cap = max(config.STORAGE_MIN_CAPACITY, self.labor_output() * 5)
        self.store.capacity["LABOR"] = lab_cap
        excess = self.store.get("LABOR") - lab_cap
        if excess > 0:
            self.ledger.consume(self.store, "LABOR", excess, "labor_trim")

    def need_for(self, key) -> int:
        per_1000 = config.SUSTENANCE_NEEDS.get(key, 0)
        return (self.size * per_1000) // 1000

    def labor_output(self) -> int:
        """Labour Tokens generated this tick, proportional to size."""
        return (self.size * config.POP_LABOR_PER_1000) // 1000

    @property
    def cash(self) -> int:
        return self.wallet.cash()

    # ------------------------------------------------------------------
    # PHASE 1: generate and offer labour, order consumer goods
    # ------------------------------------------------------------------
    def submit_orders(self, market, fx):
        self._resize_storage()
        acc = self.wallet.account(market.currency)

        # --- sell labour (no material inputs required) -------------------
        out = self.labor_output()
        self.last_labor_offered = out
        if out > 0:
            self.ledger.produce(self.store, "LABOR", out, "labor")
        have_labor = self.store.get("LABOR")
        self._labor_order = None
        if have_labor > 0:
            self._labor_order = market.submit_sell(
                "LABOR", have_labor, acc, self.store, self, tag=self.name)

        # --- buy sustenance goods --------------------------------------
        self._buy_orders = []
        spendable = acc.balance - apply_bp(acc.balance, config.POP_SAVINGS_FLOOR_BP)
        for key in config.SUSTENANCE_PRIORITY:
            if spendable <= 0:
                break
            # Replace this tick's consumption plus a fraction of any
            # shortfall, rather than demanding the whole larder at once --
            # see config.RESTOCK_SPREAD_TICKS.
            per_tick = self.need_for(key)
            target = per_tick * config.POP_CONSUMPTION_STORAGE_TICKS
            shortfall = target - self.store.get(key)
            if shortfall <= 0:
                continue
            short = min(shortfall,
                        per_tick + shortfall // config.RESTOCK_SPREAD_TICKS)
            if short <= 0:
                continue
            price = market.spot(key)
            if price <= 0:
                continue
            affordable = spendable // price
            qty = min(short, affordable, self.store.space(key))
            if qty <= 0:
                continue
            o = market.submit_buy(key, qty, acc, self.store, self, tag=self.name)
            if o:
                self._buy_orders.append(o)
                spendable -= o.qty * price

        # --- dynamic cash reserve ----------------------------------------
        # Hold back enough to cover upcoming food + habitation maintenance
        # so that luxury demand does not drain the pop's operating cash.
        food_price = market.spot("FOOD")
        food_reserve = food_price * self.need_for("FOOD") * config.POP_RESERVE_FOOD_TICKS
        hab_maint_per_tick = 0
        for st in self.sector.stations:
            if st.method.key == config.HABITATION_METHOD_KEY:
                hab_maint_per_tick += config.MAINT_GOODS_PER_LEVEL * max(1, st.level)
        maint_price = market.spot("MAINT_GOODS")
        maint_reserve = maint_price * hab_maint_per_tick * config.POP_RESERVE_MAINT_TICKS
        reserve = food_reserve + maint_reserve
        luxury_spendable = max(0, spendable - reserve)

        # --- elastic luxury demand (see config.POP_LUXURY_*) -------------
        self._luxury_order = None
        if luxury_spendable > 0:
            key = config.POP_LUXURY_KEY
            price = market.spot(key)
            base = self.need_for(key)
            if price > 0 and base > 0:
                budget = apply_bp(luxury_spendable, config.POP_LUXURY_SPEND_BP)
                qty = min(budget // price, base * config.POP_LUXURY_MAX_MULT)
                qty = min(qty, self.store.space(key))
                if qty > 0:
                    o = market.submit_buy(key, qty, acc, self.store, self,
                                          tag=f"{self.name} luxury")
                    if o:
                        self._buy_orders.append(o)
                        self._luxury_order = o

    # ------------------------------------------------------------------
    # PHASE 2: settle wages, pay tax, consume, grow
    # ------------------------------------------------------------------
    def post_market(self, world):
        sector = self.sector
        # Income realised from selling labour this tick.
        income = self._labor_order.value if self._labor_order else 0
        self.last_labor_sold = self._labor_order.filled if self._labor_order else 0
        self.last_income = income
        self.last_spend = sum(o.value for o in self._buy_orders)

        # --- local sector taxes (doc 3.5) -------------------------------
        self.last_tax = 0
        if income > 0 and sector.tax_bp > 0:
            tax = apply_bp(income, sector.tax_bp)
            if tax > 0:
                lord = world.factions.get(sector.owner)
                if lord is not None:
                    src = self.wallet.account(sector.market.currency)
                    dst = lord.wallet.account(src.currency)
                    paid = self.ledger.transfer(src, dst, tax, "tax:income")
                    self.last_tax = paid
                    sector.tax_collected_total += paid
                    sector.last_tax_income += paid
                    lord.tax_income_last += paid

        # --- consume the basket -----------------------------------------
        # Satisfaction is weighted toward the staple: being short of consumer
        # goods is a drag on quality of life, being short of food is fatal.
        weighted_need = 0
        weighted_got = 0
        self.need_fill_bp = {}
        for key in config.SUSTENANCE_NEEDS:
            need = self.need_for(key)
            if need <= 0:
                continue
            got = self.ledger.consume(self.store, key, need, "sustenance")
            self.need_fill_bp[key] = (got * config.BP) // need
            weight = config.SUSTENANCE_WEIGHT_BP.get(key, config.BP)
            weighted_need += need * weight
            weighted_got += got * weight

        self.satisfaction_bp = (weighted_got * config.BP) // weighted_need \
            if weighted_need else config.BP
        self.food_fill_bp = self.need_fill_bp.get("FOOD", config.BP)

        # --- growth / decline (tiered starvation) -----------------------
        tight_labor = sector.market.last_demand["LABOR"] > \
            sector.market.last_supply["LABOR"]

        starving = False
        for threshold_bp, mult in config.STARVATION_TIERS:
            if self.food_fill_bp < threshold_bp:
                self.size = max(config.POP_MIN_SIZE,
                                (self.size * mult) // config.BP)
                starving = True
                break

        if not starving and self.satisfaction_bp >= config.POP_GROWTH_SATISFACTION_BP:
            rate = config.POP_GROWTH_BP
            if tight_labor:
                rate += config.POP_MIGRATION_BP
            growth = apply_bp(self.size, rate)
            cap = config.POP_MAX_SIZE
            hab_cap = self._habitation_cap()
            if hab_cap is not None:
                cap = min(cap, hab_cap)
            self.size = min(cap, self.size + max(1, growth))

    def _habitation_cap(self):
        """Population cap imposed by habitation stations in this sector."""
        total = 0
        found = False
        for st in self.sector.stations:
            if st.method.key == config.HABITATION_METHOD_KEY:
                total += st.level * config.HABITATION_POP_PER_LEVEL
                found = True
        return total if found else None

    # ------------------------------------------------------------------
    # Sector Currency Transition hook (doc 3.2)
    # ------------------------------------------------------------------
    def on_sovereignty_change(self, old_owner, new_owner, new_currency, world):
        """Keep legacy cash, but start operating in the new sovereign money."""
        self.wallet.set_operating(new_currency)

    def submit_fx(self, fx):
        from .currency import convert_legacy_cash
        convert_legacy_cash(self.wallet, fx, self)

    # ------------------------------------------------------------------
    def status(self) -> str:
        if self.satisfaction_bp >= config.POP_GROWTH_SATISFACTION_BP:
            return "growing"
        if self.satisfaction_bp < config.POP_DECLINE_SATISFACTION_BP:
            return "starving"
        return "stable"

    def __repr__(self):
        return f"<Pop {self.name} size={self.size} sat={self.satisfaction_bp}>"

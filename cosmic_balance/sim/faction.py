"""
FACTION SOVEREIGNTY (doc 2) and faction-level finance.

"A faction represents a political entity that owns sectors, commands military
fleets, levies sector-wide taxes, and establishes legislative frameworks."

Also holds the diplomatic stance table and war exhaustion index (doc 6).
"""

from collections import defaultdict

from . import config
from .fixed import clamp
from .ledger import Wallet

PEACE = "peace"
WAR = "war"
ALLIANCE = "alliance"


class Loan:
    """Capital Financing (doc 3.3): a faction lends starting capital to a
    station at a defined interest rate. Interest obligations begin processing
    only once Level 1 construction completes."""

    __slots__ = ("lender", "principal", "interest_bp", "paid_total",
                 "arrears", "active", "station")

    def __init__(self, lender, principal, interest_bp, station):
        self.lender = lender              # faction key
        self.principal = int(principal)
        self.interest_bp = int(interest_bp)
        self.paid_total = 0
        self.arrears = 0
        self.active = False              # becomes True at level 1 completion
        self.station = station

    def interest_due(self) -> int:
        due = (self.principal * self.interest_bp) // config.BP
        if due < config.LOAN_MIN_PAYMENT and self.principal > 0:
            due = config.LOAN_MIN_PAYMENT
        return due


class Faction:
    def __init__(self, key, name, color, currency, ledger, is_player=False):
        self.key = key
        self.name = name
        self.color = color
        self.currency = currency
        self.ledger = ledger
        self.is_player = is_player

        self.wallet = Wallet(ledger, f"faction:{key}", currency.code)
        self.wallet.account(currency.code)

        # Diplomacy (doc 6 "State Relations")
        self.relations = {}               # other key -> PEACE/WAR/ALLIANCE
        self.opinion = defaultdict(int)   # other key -> -10000..10000
        self.exhaustion = 0               # war exhaustion index
        self.war_cooldown = defaultdict(int)

        # Legislative framework
        self.default_tax_bp = config.DEFAULT_SECTOR_TAX_BP
        self.default_tariff_bp = config.DEFAULT_TARIFF_BP

        # Telemetry
        self.loans = []
        self.tax_income_last = 0
        self.interest_income_last = 0
        self.military_spend_last = 0
        self.budget_last = 0
        self.strength_lost_total = 0
        self.sectors_lost_total = 0

    # ------------------------------------------------------------------
    @property
    def treasury(self) -> int:
        return self.wallet.cash(self.currency.code)

    def account(self):
        return self.wallet.account(self.currency.code)

    # ------------------------------------------------------------------
    def stance(self, other_key) -> str:
        if other_key == self.key:
            return ALLIANCE
        return self.relations.get(other_key, PEACE)

    def at_war_with(self, other_key) -> bool:
        return self.stance(other_key) == WAR

    def set_stance(self, other_key, stance):
        self.relations[other_key] = stance

    def adjust_opinion(self, other_key, delta):
        self.opinion[other_key] = clamp(self.opinion[other_key] + delta,
                                        config.DIPLO_OPINION_MIN,
                                        config.DIPLO_OPINION_MAX)

    # ------------------------------------------------------------------
    def add_exhaustion(self, amount):
        self.exhaustion = clamp(self.exhaustion + int(amount), 0,
                                config.EXHAUSTION_MAX)

    def decay_exhaustion(self):
        self.exhaustion = max(0, self.exhaustion -
                              config.EXHAUSTION_DECAY_AT_PEACE)

    # ------------------------------------------------------------------
    # FISCAL POLICY
    # ------------------------------------------------------------------
    def run_fiscal_policy(self, world) -> int:
        """Spend down the treasury surplus: fleets, industry, public works.

        This is not flavour -- it is a structural requirement of the hard-capped
        closed currency system (doc 3.1). Taxes, property taxes, tariffs,
        interest and dividends all flow INTO the treasury every single tick. If
        nothing flows back out, the state quietly absorbs the entire circulating
        money supply and the private economy seizes up for want of cash while
        remaining perfectly capable of producing. So the government runs a
        budget and returns the money to the firms and households it taxed.
        """
        from .fixed import apply_bp
        acc = self.account()
        income = (self.tax_income_last + self.interest_income_last + 1)
        reserve = max(income * config.STATE_RESERVE_TICKS,
                      config.STATE_MIN_TREASURY)
        surplus = acc.balance - reserve
        if surplus <= 0:
            # No discretionary room, but the working-capital facility still
            # runs: it is capped and floored, and letting solvent industry
            # seize up costs the state far more than it saves.
            self.budget_last = self._fund_industry(world, 0)
            return self.budget_last

        budget = apply_bp(surplus, config.STATE_SPEND_BP)
        if budget <= 0:
            self.budget_last = self._fund_industry(world, 0)
            return self.budget_last

        spent = 0
        spent += self._fund_military(
            world, apply_bp(budget, config.STATE_BUDGET_MILITARY_BP))
        spent += self._fund_industry(
            world, apply_bp(budget, config.STATE_BUDGET_INDUSTRY_BP))
        spent += self._fund_public_works(
            world, apply_bp(budget, config.STATE_BUDGET_PUBLIC_BP))
        self.budget_last = spent
        return spent

    def _fund_military(self, world, amount) -> int:
        """Keep the Logistical Bases solvent so they can crew and rearm."""
        if amount <= 0:
            return 0
        bases = [b for b in world.bases if b.owner == self.key]
        if not bases:
            return 0
        acc = self.account()
        spent = 0
        per = max(1, amount // len(bases))
        for b in bases:
            dst = b.wallet.account(acc.currency)
            if dst.currency != acc.currency:
                continue
            short = config.MILBASE_TARGET_CASH - dst.balance
            if short <= 0:
                continue
            spent += self.ledger.transfer(acc, dst, min(per, short),
                                          "state:military")
        self.military_spend_last += spent
        return spent

    def _fund_industry(self, world, amount) -> int:
        """Recapitalise controlled stations short of working capital.

        Runs in two passes. First a RESCUE pass guarantees every controlled
        station enough cash to buy a few ticks of inputs, outside the ordinary
        budget: a profitable plant that cannot make payroll produces nothing,
        earns nothing and can never recover on its own, and a galaxy full of
        those looks identical to a famine. Then the ordinary budget tops up
        whatever buffer is left short.
        """
        acc = self.account()
        from .fixed import apply_bp
        spent_rescue = 0
        rescue_budget = min(apply_bp(acc.balance, config.STATE_RESCUE_MAX_BP),
                            max(0, acc.balance - config.STATE_MIN_TREASURY))
        mine = world.stations_by_controller.get(self.key, ())
        for st in mine:
            if spent_rescue >= rescue_budget:
                break
            floor = st.operating_cost_per_tick() * config.STATE_RESCUE_TICKS
            if floor <= 0:
                continue
            dst = st.wallet.account(acc.currency)
            if dst.currency != acc.currency or dst.balance >= floor:
                continue
            give = min(floor - dst.balance, rescue_budget - spent_rescue)
            spent_rescue += self.ledger.transfer(acc, dst, give,
                                                 "state:rescue")
        if amount <= 0:
            return spent_rescue
        needy = []
        for st in mine:
            cash = st.wallet.cash(acc.currency)
            short = st.working_capital() - cash
            if short > 0:
                needy.append((st, short))
        if not needy:
            return 0
        # Neediest first, so the plants closest to seizing up get covered.
        needy.sort(key=lambda t: -t[1])
        spent = 0
        for st, short in needy:
            if spent >= amount:
                break
            give = min(short, amount - spent)
            dst = st.wallet.account(acc.currency)
            if dst.currency != acc.currency:
                continue
            spent += self.ledger.transfer(acc, dst, give, "state:industry")
        return spent + spent_rescue

    def _fund_public_works(self, world, amount) -> int:
        """Pay wages for public employment, distributed across the citizenry.

        Mechanically this is the valve that returns taxed money to the
        households that will spend it back into the commodity markets.
        """
        if amount <= 0:
            return 0
        from .market import MarketHub
        acc = self.account()
        pops = [p for s in world.galaxy if s.owner == self.key for p in s.pops]
        if not pops:
            return 0
        weights = [max(1, p.size) for p in pops]
        shares = MarketHub._split_money(amount, weights)
        spent = 0
        for p, share in zip(pops, shares):
            dst = p.wallet.account(acc.currency)
            if dst.currency != acc.currency or share <= 0:
                continue
            paid = self.ledger.transfer(acc, dst, share, "state:public_works")
            p.public_income_last = paid
            spent += paid
        return spent

    def submit_fx(self, fx):
        """Repatriate foreign currency received as taxes or dividends."""
        from .currency import convert_legacy_cash
        convert_legacy_cash(self.wallet, fx, self)

    # ------------------------------------------------------------------
    def net_worth(self, world) -> int:
        """Treasury plus equity in controlled stations plus fleet asset value,
        expressed in this faction's own currency."""
        total = self.wallet.total_in(self.currency.code, world.fx)
        for st in world.stations:
            share = st.equity.get(self.key, 0)
            if share:
                total += (st.book_value(world) * share) // config.EQUITY_TOTAL_SHARES
        for fl in world.fleets:
            if fl.owner == self.key:
                total += fl.asset_value(world)
        for ln in self.loans:
            total += ln.principal - ln.paid_total if ln.active else ln.principal
        return total

    def __repr__(self):
        return f"<Faction {self.key} {self.name}>"

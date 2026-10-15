"""
HARD-CAPPED CLOSED CURRENCY SYSTEM & THE GLOBAL FX EXCHANGE (doc 3.1).

*   No Infinite Capital -- every faction issues its own distinct currency and
    the entire supply of each is minted once, during scenario initialisation.
    ``Ledger`` proves nothing is created afterwards.
*   Dynamic Exchange Rates -- rates float on a market-driven global exchange.
    A currency that everybody is trying to offload depreciates; one everybody
    needs appreciates. Because the exchange is a pure order-matching clearing
    house (it keeps no reserves of its own), every conversion is a two-legged
    swap between real counterparties and both currencies stay conserved.

Rate representation: each currency carries an integer ``value`` scaled by
``FX_RATE_SCALE``, where the scale itself is parity. The price of X in terms
of Y is ``value[X] / value[Y]``.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from . import config
from .fixed import bounded_move, step_bp, apply_bp, clamp
from .ledger import Account
from .market import MarketHub


@dataclass
class Currency:
    code: str
    name: str
    symbol: str
    issuer: str                      # faction key
    value: int = config.FX_RATE_SCALE    # FX index; scale == parity
    history: list = field(default_factory=list)

    def __hash__(self):
        return hash(self.code)


class FXOrder:
    __slots__ = ("wallet", "src", "dst", "amount", "owner", "filled_src",
                 "filled_dst")

    def __init__(self, wallet, src, dst, amount, owner=None):
        self.wallet = wallet
        self.src = src          # currency code being sold
        self.dst = dst          # currency code being bought
        self.amount = int(amount)
        self.owner = owner
        self.filled_src = 0
        self.filled_dst = 0


class FXDesk:
    """The global exchange. Holds no capital between ticks."""

    def __init__(self, ledger):
        self.ledger = ledger
        self.currencies = {}
        self.orders = []
        self._escrow = {}
        # telemetry
        self.last_volume = defaultdict(int)
        self.last_sold = defaultdict(int)
        self.last_bought = defaultdict(int)
        self.unmatched = defaultdict(int)

    # ------------------------------------------------------------------
    def add_currency(self, cur: Currency):
        self.currencies[cur.code] = cur
        self.escrow(cur.code)
        return cur

    def escrow(self, code) -> Account:
        acc = self._escrow.get(code)
        if acc is None:
            acc = Account(self.ledger, "fx_escrow", code)
            self._escrow[code] = acc
            self.ledger.register_account(acc)
        return acc

    # ------------------------------------------------------------------
    def rate(self, src, dst) -> int:
        """Units of ``dst`` obtainable for one unit of ``src``, * FX_RATE_SCALE."""
        if src == dst:
            return config.FX_RATE_SCALE
        a = self.currencies[src].value
        b = self.currencies[dst].value
        if b <= 0:
            return config.FX_RATE_SCALE
        return (a * config.FX_RATE_SCALE) // b

    def convert(self, amount, src, dst) -> int:
        """Notional conversion (for valuation; moves no money)."""
        if src == dst or amount == 0:
            return amount
        return (amount * self.rate(src, dst)) // config.FX_RATE_SCALE

    def to_reference(self, amount, code) -> int:
        """Value in abstract reference units, for cross-currency comparison."""
        return (amount * self.currencies[code].value) // config.FX_RATE_SCALE

    # ------------------------------------------------------------------
    def submit(self, wallet, src, dst, amount, owner=None):
        """Queue a foreign-exchange order (doc 3.2: factories under new
        sovereignty must actively issue FX orders to swap legacy cash)."""
        if src == dst or amount < config.FX_MIN_ORDER_CENTS:
            return None
        if src not in self.currencies or dst not in self.currencies:
            return None
        have = wallet.cash(src)
        amount = min(amount, have)
        if amount < config.FX_MIN_ORDER_CENTS:
            return None
        o = FXOrder(wallet, src, dst, amount, owner)
        self.orders.append(o)
        return o

    # ------------------------------------------------------------------
    def clear(self):
        """Match all queued FX orders pairwise, then tick the rates."""
        self.last_volume.clear()
        self.last_sold.clear()
        self.last_bought.clear()
        self.unmatched.clear()

        # Bucket orders by unordered currency pair.
        pairs = defaultdict(lambda: ([], []))
        for o in self.orders:
            a, b = o.src, o.dst
            if a < b:
                pairs[(a, b)][0].append(o)
            else:
                pairs[(b, a)][1].append(o)

        for (a, b), (ab_orders, ba_orders) in pairs.items():
            # ab_orders sell A for B; ba_orders sell B for A.
            self._clear_pair(a, b, ab_orders, ba_orders)

        # Aggregate demand/supply pressure in reference units for rate ticking.
        for o in self.orders:
            residual = o.amount - o.filled_src
            if residual > 0:
                self.unmatched[o.src] += self.to_reference(residual, o.src)

        self._tick_rates()
        self.orders.clear()

    def _clear_pair(self, a, b, ab_orders, ba_orders):
        if not ab_orders or not ba_orders:
            # No counterparty: nothing trades. The rate still drifts via the
            # unmatched-pressure term below, bounded by FX_RATE_EMPTY_STEP_BP.
            return

        rate_ab = self.rate(a, b)       # B per A, scaled
        if rate_ab <= 0:
            return

        offered_a = sum(o.amount for o in ab_orders)
        offered_b = sum(o.amount for o in ba_orders)

        # What the A side is offering, expressed in B.
        offered_a_in_b = (offered_a * rate_ab) // config.FX_RATE_SCALE
        matched_b = min(offered_a_in_b, offered_b)
        if matched_b <= 0:
            return
        matched_a = (matched_b * config.FX_RATE_SCALE) // rate_ab
        if matched_a <= 0:
            return

        esc_a = self.escrow(a)
        esc_b = self.escrow(b)

        # --- A side pays A, is owed B --------------------------------------
        a_weights = [o.amount for o in ab_orders]
        a_pays = MarketHub._split_money(matched_a, a_weights)
        # Never ask an order to pay more than it offered.
        for i, o in enumerate(ab_orders):
            if a_pays[i] > o.amount:
                a_pays[i] = o.amount
        actual_a = sum(a_pays)
        b_gets = MarketHub._split_money(matched_b, a_pays) if actual_a else []

        # --- B side pays B, is owed A --------------------------------------
        b_weights = [o.amount for o in ba_orders]
        b_pays = MarketHub._split_money(matched_b, b_weights)
        for i, o in enumerate(ba_orders):
            if b_pays[i] > o.amount:
                b_pays[i] = o.amount
        actual_b = sum(b_pays)
        a_gets = MarketHub._split_money(matched_a, b_pays) if actual_b else []

        # Collect both legs into escrow.
        for o, pay in zip(ab_orders, a_pays):
            if pay > 0:
                moved = self.ledger.transfer(o.wallet.account(a), esc_a, pay, "fx")
                o.filled_src += moved
        for o, pay in zip(ba_orders, b_pays):
            if pay > 0:
                moved = self.ledger.transfer(o.wallet.account(b), esc_b, pay, "fx")
                o.filled_src += moved

        # Pay out: A side receives B, B side receives A. Distribute exactly
        # what actually arrived in escrow so both escrows end at zero.
        pot_b = esc_b.balance
        if pot_b > 0 and b_gets:
            share = MarketHub._split_money(pot_b, b_gets)
            for o, amt in zip(ab_orders, share):
                if amt > 0:
                    got = self.ledger.transfer(esc_b, o.wallet.account(b), amt, "fx")
                    o.filled_dst += got
        pot_a = esc_a.balance
        if pot_a > 0 and a_gets:
            share = MarketHub._split_money(pot_a, a_gets)
            for o, amt in zip(ba_orders, share):
                if amt > 0:
                    got = self.ledger.transfer(esc_a, o.wallet.account(a), amt, "fx")
                    o.filled_dst += got

        # Residual dust: hand it back so the desk holds nothing.
        if esc_a.balance > 0:
            self.ledger.transfer(esc_a, ab_orders[0].wallet.account(a),
                                 esc_a.balance, "fx_refund")
        if esc_b.balance > 0:
            self.ledger.transfer(esc_b, ba_orders[0].wallet.account(b),
                                 esc_b.balance, "fx_refund")

        self.last_volume[a] += actual_a
        self.last_volume[b] += actual_b
        self.last_sold[a] += self.to_reference(actual_a, a)
        self.last_sold[b] += self.to_reference(actual_b, b)
        self.last_bought[a] += self.to_reference(actual_a, a)
        self.last_bought[b] += self.to_reference(actual_b, b)

    # ------------------------------------------------------------------
    def _tick_rates(self):
        """Float each currency against aggregate pressure.

        A currency with unmatched sellers (nobody wants it) depreciates; one
        that is scarce relative to the orders chasing it appreciates. Moves are
        damped and hard-capped per tick (doc 3.1), and the whole range is
        clamped so no currency can hyperinflate to infinity or to zero.
        """
        for code, cur in self.currencies.items():
            pressure = self.unmatched[code]
            traded = self.last_volume[code]
            if pressure > 0:
                # Excess supply of this currency -> depreciate, at a bounded
                # creep rather than a collapse.
                severity = config.FX_RATE_EMPTY_STEP_BP
                if traded > 0:
                    # Partial match softens the move proportionally.
                    ref_traded = self.to_reference(traded, code)
                    denom = ref_traded + pressure
                    if denom > 0:
                        severity = max(
                            1, (config.FX_RATE_EMPTY_STEP_BP * pressure) // denom)
                cur.value = step_bp(cur.value, -severity,
                                    config.FX_RATE_MIN, config.FX_RATE_MAX)
            elif traded > 0:
                # Everything offered found a home: mild appreciation back
                # toward and above parity, damped.
                cur.value = bounded_move(
                    cur.value,
                    cur.value + apply_bp(cur.value, config.FX_RATE_EMPTY_STEP_BP),
                    config.FX_RATE_DAMPING_BP,
                    config.FX_RATE_MAX_MOVE_BP,
                    config.FX_RATE_MIN, config.FX_RATE_MAX)
            else:
                # Idle currency drifts gently back to parity.
                cur.value = bounded_move(
                    cur.value, config.FX_RATE_SCALE,
                    config.FX_RATE_DAMPING_BP // 4,
                    config.FX_RATE_MAX_MOVE_BP,
                    config.FX_RATE_MIN, config.FX_RATE_MAX)

        self._normalise_rates()

    def _normalise_rates(self):
        """Keep the basket centred on parity.

        An exchange rate is purely RELATIVE: only value[X]/value[Y] means
        anything. But the pressure term above pushes every currency that has
        unmatched sellers downward, and in a busy economy that is nearly all of
        them nearly all the time -- so the whole basket ratcheted down together
        until every currency sat pinned at FX_RATE_MIN. Relative rates were
        still roughly right, but with everything on the clamp the pinned
        currencies could no longer move against each other at all, and any
        currency that happened to be idle (the pirates', typically) looked
        fifty times stronger than every real economy.

        Rescaling the basket so its mean is parity removes that common-mode
        drift and leaves only the relative movements, which are the signal.
        """
        if not self.currencies:
            return
        values = [c.value for c in self.currencies.values()]
        mean = sum(values) // len(values)
        if mean <= 0 or mean == config.FX_RATE_SCALE:
            return
        for cur in self.currencies.values():
            scaled = (cur.value * config.FX_RATE_SCALE) // mean
            cur.value = clamp(scaled, config.FX_RATE_MIN, config.FX_RATE_MAX)

    # ------------------------------------------------------------------
    def supply_of(self, code) -> int:
        """Hard-capped total issuance of a currency (doc 3.1 "No Infinite
        Capital") -- read straight off the ledger's mint record."""
        return self.ledger.money_minted[code] - self.ledger.money_burned[code]

    def is_empty(self) -> bool:
        return all(a.balance == 0 for a in self._escrow.values())


def convert_legacy_cash(wallet, fx, owner=None):
    """Issue FX orders for stranded post-conquest balances (doc 3.2).

    Entities retain their old cash reserves but must actively swap them into
    the new sovereign currency to meet tax obligations and labour costs.
    """
    issued = []
    for cur, bal in wallet.foreign_balances():
        amount = apply_bp(bal, config.FX_LEGACY_CONVERSION_BP)
        if amount < config.FX_MIN_ORDER_CENTS:
            amount = bal           # sweep the dust in one go
        o = fx.submit(wallet, cur, wallet.operating, amount, owner)
        if o:
            issued.append(o)
    return issued

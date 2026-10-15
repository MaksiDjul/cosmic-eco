"""
THE LEDGER -- enforcement of "Conservation of Value" (doc 1.1).

Every cent and every commodity unit in the game lives inside an object that is
registered here. Nothing may be created or destroyed except through an
explicitly tracked ``mint``/``burn`` call, so at any moment the invariant

    sum(all account balances, currency C) == minted[C] - burned[C]
    sum(all inventory stocks, commodity K) == produced[K] - consumed[K]

must hold exactly. ``Ledger.audit()`` proves it, and the world runs that audit
periodically (and loudly fails if it ever breaks).

Why integers: Python ints are arbitrary precision. Combined with the
largest-remainder allocator in ``fixed.py``, no rounding residue can leak.
"""

from collections import defaultdict


class LedgerError(RuntimeError):
    """Raised when the conservation invariant is violated."""


class Account:
    """A cash balance in exactly one currency. Can never go negative."""

    __slots__ = ("owner_name", "currency", "balance", "ledger")

    def __init__(self, ledger, owner_name, currency, balance=0):
        self.ledger = ledger
        self.owner_name = owner_name
        self.currency = currency
        self.balance = 0
        if balance:
            ledger.mint(self, balance, "init")

    def __repr__(self):
        return f"<Account {self.owner_name} {self.currency} {self.balance}>"


class Wallet:
    """A set of accounts, one per currency an entity happens to hold.

    An entity has an *operating* currency (the sovereign currency of the sector
    it sits in). After a conquest it may hold stranded balances in a legacy
    currency, which it must swap on the FX market (doc 3.2 "Sector Currency
    Transition").
    """

    __slots__ = ("ledger", "owner_name", "accounts", "operating")

    def __init__(self, ledger, owner_name, operating_currency):
        self.ledger = ledger
        self.owner_name = owner_name
        self.accounts = {}
        self.operating = operating_currency

    def account(self, currency=None) -> Account:
        cur = currency or self.operating
        acc = self.accounts.get(cur)
        if acc is None:
            acc = Account(self.ledger, self.owner_name, cur)
            self.accounts[cur] = acc
            self.ledger.register_account(acc)
        return acc

    def cash(self, currency=None) -> int:
        cur = currency or self.operating
        acc = self.accounts.get(cur)
        return acc.balance if acc else 0

    def foreign_balances(self):
        """Stranded cash in any currency other than the operating one."""
        return [(cur, acc.balance) for cur, acc in self.accounts.items()
                if cur != self.operating and acc.balance > 0]

    def total_in(self, currency, fx) -> int:
        """Total wallet value expressed in ``currency`` at current FX rates."""
        total = 0
        for cur, acc in self.accounts.items():
            if acc.balance:
                total += fx.convert(acc.balance, cur, currency)
        return total

    def set_operating(self, currency):
        self.operating = currency
        self.account(currency)   # make sure it exists


class Inventory:
    """Per-commodity stock with per-commodity capacity.

    Capacity is advisory for *ordering* decisions; ``add`` hard-clamps so a
    station physically cannot exceed storage (doc 3.3 "Inventory Management").
    """

    __slots__ = ("ledger", "owner_name", "stock", "capacity", "default_capacity")

    def __init__(self, ledger, owner_name, default_capacity=0):
        self.ledger = ledger
        self.owner_name = owner_name
        self.stock = defaultdict(int)
        self.capacity = {}
        self.default_capacity = default_capacity
        ledger.register_inventory(self)

    def cap(self, key) -> int:
        return self.capacity.get(key, self.default_capacity)

    def get(self, key) -> int:
        return self.stock[key]

    def space(self, key) -> int:
        return max(0, self.cap(key) - self.stock[key])

    def add(self, key, qty) -> int:
        """Add up to ``qty``; returns the amount actually accepted."""
        if qty <= 0:
            return 0
        room = self.space(key)
        take = qty if qty <= room else room
        if take:
            self.stock[key] += take
        return take

    def add_uncapped(self, key, qty) -> int:
        """Add ignoring capacity (used for stolen hoards and transfers that
        must not silently destroy goods)."""
        if qty <= 0:
            return 0
        self.stock[key] += qty
        return qty

    def remove(self, key, qty) -> int:
        """Remove up to ``qty``; returns the amount actually removed."""
        if qty <= 0:
            return 0
        have = self.stock[key]
        take = qty if qty <= have else have
        if take:
            self.stock[key] -= take
        return take

    def total_volume(self, registry) -> int:
        return sum(registry[k].volume * v for k, v in self.stock.items() if v)

    def nonzero(self):
        return {k: v for k, v in self.stock.items() if v}


class Ledger:
    """Global registry + the only sanctioned creation/destruction gateway."""

    def __init__(self):
        self.accounts = []
        self.inventories = []
        self.money_minted = defaultdict(int)
        self.money_burned = defaultdict(int)
        self.goods_produced = defaultdict(int)
        self.goods_consumed = defaultdict(int)
        # Flow telemetry for the UI, reset every tick.
        self.flows = defaultdict(int)
        self.audit_failures = []

    # -- registration ----------------------------------------------------
    def register_account(self, acc):
        self.accounts.append(acc)

    def register_inventory(self, inv):
        self.inventories.append(inv)

    # -- money -----------------------------------------------------------
    def mint(self, account: Account, amount: int, reason: str):
        """Create money. Only legitimate during scenario initialisation."""
        if amount <= 0:
            return 0
        account.balance += amount
        self.money_minted[account.currency] += amount
        self.flows[f"mint:{reason}"] += amount
        return amount

    def burn(self, account: Account, amount: int, reason: str):
        if amount <= 0:
            return 0
        if amount > account.balance:
            amount = account.balance
        account.balance -= amount
        self.money_burned[account.currency] += amount
        self.flows[f"burn:{reason}"] += amount
        return amount

    def transfer(self, src: Account, dst: Account, amount: int, reason="") -> int:
        """Move money between accounts of the SAME currency.

        Returns the amount actually moved (clamped by the source balance), so
        a caller can never push an account negative (doc 3.3: "Cash accounts
        are never allowed to enter negative balances").
        """
        if amount <= 0:
            return 0
        if src.currency != dst.currency:
            raise LedgerError(
                f"cross-currency transfer {src.currency}->{dst.currency} must "
                f"go through the FX desk")
        if amount > src.balance:
            amount = src.balance
        if amount <= 0:
            return 0
        src.balance -= amount
        dst.balance += amount
        if reason:
            self.flows[reason] += amount
        return amount

    def swap(self, src: Account, dst: Account, src_amount: int,
             counter_src: Account, counter_dst: Account, dst_amount: int,
             reason="fx") -> bool:
        """An atomic two-legged FX swap between two counterparties.

        Party A pays ``src_amount`` of currency X from ``src`` into
        ``counter_dst``; party B pays ``dst_amount`` of currency Y from
        ``counter_src`` into ``dst``. Both currencies are conserved: nothing is
        minted, the FX desk holds nothing.
        """
        if src_amount <= 0 or dst_amount <= 0:
            return False
        if src.balance < src_amount or counter_src.balance < dst_amount:
            return False
        src.balance -= src_amount
        counter_dst.balance += src_amount
        counter_src.balance -= dst_amount
        dst.balance += dst_amount
        self.flows[reason] += src_amount
        return True

    # -- goods -----------------------------------------------------------
    def produce(self, inv: Inventory, key: str, qty: int, reason="production") -> int:
        """Create commodity units (mining, manufacturing, labour generation)."""
        if qty <= 0:
            return 0
        made = inv.add(key, qty)
        if made:
            self.goods_produced[key] += made
            self.flows[f"produce:{key}"] += made
        return made

    def produce_uncapped(self, inv: Inventory, key: str, qty: int,
                         reason="production") -> int:
        if qty <= 0:
            return 0
        inv.add_uncapped(key, qty)
        self.goods_produced[key] += qty
        self.flows[f"produce:{key}"] += qty
        return qty

    def consume(self, inv: Inventory, key: str, qty: int, reason="consumption") -> int:
        """Destroy commodity units (inputs, upkeep, spoilage, combat losses)."""
        if qty <= 0:
            return 0
        gone = inv.remove(key, qty)
        if gone:
            self.goods_consumed[key] += gone
            self.flows[f"consume:{key}"] += gone
        return gone

    def move_goods(self, src: Inventory, dst: Inventory, key: str, qty: int,
                   capped=True) -> int:
        """Conserving goods transfer. Anything the destination cannot store is
        left with the source rather than vanishing."""
        if qty <= 0:
            return 0
        avail = src.get(key)
        qty = min(qty, avail)
        if qty <= 0:
            return 0
        placed = dst.add(key, qty) if capped else dst.add_uncapped(key, qty)
        if placed:
            src.remove(key, placed)
        return placed

    # -- audit -----------------------------------------------------------
    def tick_reset_flows(self):
        self.flows = defaultdict(int)

    def money_totals(self):
        totals = defaultdict(int)
        for acc in self.accounts:
            totals[acc.currency] += acc.balance
        return totals

    def goods_totals(self):
        totals = defaultdict(int)
        for inv in self.inventories:
            for k, v in inv.stock.items():
                if v:
                    totals[k] += v
        return totals

    def audit(self, raise_on_fail=True):
        """Prove the conservation invariant. Returns a list of discrepancies."""
        problems = []

        actual_money = self.money_totals()
        currencies = set(actual_money) | set(self.money_minted) | set(self.money_burned)
        for cur in sorted(currencies):
            expected = self.money_minted[cur] - self.money_burned[cur]
            got = actual_money[cur]
            if got != expected:
                problems.append(
                    f"MONEY {cur}: expected {expected} got {got} "
                    f"(drift {got - expected:+d})")

        actual_goods = self.goods_totals()
        keys = set(actual_goods) | set(self.goods_produced) | set(self.goods_consumed)
        for key in sorted(keys):
            expected = self.goods_produced[key] - self.goods_consumed[key]
            got = actual_goods[key]
            if got != expected:
                problems.append(
                    f"GOODS {key}: expected {expected} got {got} "
                    f"(drift {got - expected:+d})")

        for acc in self.accounts:
            if acc.balance < 0:
                problems.append(f"NEGATIVE CASH {acc.owner_name} {acc.currency} "
                                f"{acc.balance}")

        if problems:
            self.audit_failures.extend(problems)
            if raise_on_fail:
                raise LedgerError("Conservation audit failed:\n  " +
                                  "\n  ".join(problems))
        return problems

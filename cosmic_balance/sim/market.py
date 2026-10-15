"""
THE LOCAL MARKET HUB (doc 3.2 "Localized Commodity Markets").

One of these lives in every sector. Design properties implemented here, each
mapped to the design document:

*   Localized Price Indexes -- prices are per-sector, computed locally, so the
    cost of a tick is O(orders in this sector) and the galaxy scales.
*   Order Book Processing -- entities submit quantity-only buy/sell orders.
*   Single-Clearing-Price Settlement -- Victoria-2 style: ONE spot price per
    commodity per tick; every fill in that tick happens at exactly that price.
*   Pro-Rata Allocation -- when the book is lopsided, volume is distributed
    proportionally to order size via the exact largest-remainder allocator,
    never sequentially.
*   Zero Storage Clearing -- the hub owns a float inventory and an escrow
    account purely as transit, and both are provably empty again by the end of
    every settlement.
*   Price Tick Ticker + Explosion Safeguard -- bounded incremental price moves,
    including when one side of the book is completely empty.
*   Sector Currency Transition -- the hub always settles in the native currency
    of the sector's current sovereign.
"""

from . import config
from .commodities import COMMODITIES
from .fixed import allocate_pro_rata, apply_bp, bounded_move, step_bp, clamp
from .ledger import Account, Inventory


class Order:
    """A quantity-only market order (doc 3.2 "Orders specify quantities only")."""

    __slots__ = ("side", "key", "qty", "account", "inventory", "owner",
                 "filled", "value", "tag")

    def __init__(self, side, key, qty, account, inventory, owner, tag=""):
        self.side = side            # 'buy' | 'sell'
        self.key = key
        self.qty = int(qty)
        self.account = account      # Account in the market's currency
        self.inventory = inventory
        self.owner = owner          # entity, for telemetry / callbacks
        self.filled = 0
        self.value = 0              # cash moved, in market currency cents
        self.tag = tag              # free-form purpose label for the UI

    def __repr__(self):
        return f"<{self.side} {self.qty}x{self.key} by {self.tag}>"


class Settlement:
    """Result record of one commodity clearing, kept for UI + piracy hooks."""

    __slots__ = ("key", "price", "traded", "demand", "supply", "buy_fills",
                 "sell_fills")

    def __init__(self, key, price, traded, demand, supply):
        self.key = key
        self.price = price
        self.traded = traded
        self.demand = demand
        self.supply = supply
        self.buy_fills = []    # list of (order, qty)
        self.sell_fills = []


class MarketHub:
    """A sector's clearing house. Holds no capital and no warehouses."""

    def __init__(self, sector, ledger, currency):
        self.sector = sector
        self.ledger = ledger
        self.currency = currency

        # price state
        self.price = {c.key: c.base_price for c in COMMODITIES}
        self.prev_price = dict(self.price)
        self.last_traded = {c.key: 0 for c in COMMODITIES}
        self.last_demand = {c.key: 0 for c in COMMODITIES}
        self.last_supply = {c.key: 0 for c in COMMODITIES}
        self.empty_streak = {c.key: 0 for c in COMMODITIES}

        self.buys = {c.key: [] for c in COMMODITIES}
        self.sells = {c.key: [] for c in COMMODITIES}
        self.settlements = {}

        # Transit-only facilities. Both are empty outside of ``clear_all``.
        self._float = Inventory(ledger, f"market_float:{sector.name}",
                                default_capacity=0)
        self._float.default_capacity = 10 ** 15     # transit, never a warehouse
        self._escrow = {}

    # ------------------------------------------------------------------
    # currency handling (doc 3.2 "Sector Currency Transition")
    # ------------------------------------------------------------------
    def escrow(self, currency=None) -> Account:
        cur = currency or self.currency
        acc = self._escrow.get(cur)
        if acc is None:
            acc = Account(self.ledger, f"market_escrow:{self.sector.name}", cur)
            self._escrow[cur] = acc
            self.ledger.register_account(acc)
        return acc

    def set_currency(self, currency):
        """Called when the sector changes hands. Orders already queued are
        discarded because they were denominated in the old sovereign's money."""
        self.currency = currency
        self.clear_books()
        self.escrow(currency)

    # ------------------------------------------------------------------
    # order submission
    # ------------------------------------------------------------------
    def submit_buy(self, key, qty, account, inventory, owner, tag=""):
        """Queue a buy order, pre-clamped to what the buyer can afford and
        physically store. Returns the accepted order or None."""
        if qty < config.MARKET_MIN_ORDER:
            return None
        price = self.price[key]
        budget = apply_bp(account.balance, config.MARKET_ORDER_CASH_SAFETY_BP)
        affordable = budget // price if price > 0 else 0
        qty = min(qty, affordable)
        if inventory is not None:
            qty = min(qty, inventory.space(key))
        if qty < config.MARKET_MIN_ORDER:
            return None
        o = Order("buy", key, qty, account, inventory, owner, tag)
        self.buys[key].append(o)
        return o

    def submit_sell(self, key, qty, account, inventory, owner, tag=""):
        """Queue a sell order, pre-clamped to physical stock on hand."""
        if qty < config.MARKET_MIN_ORDER:
            return None
        if inventory is not None:
            qty = min(qty, inventory.get(key))
        if qty < config.MARKET_MIN_ORDER:
            return None
        o = Order("sell", key, qty, account, inventory, owner, tag)
        self.sells[key].append(o)
        return o

    def clear_books(self):
        for k in self.buys:
            self.buys[k].clear()
            self.sells[k].clear()

    # ------------------------------------------------------------------
    # settlement
    # ------------------------------------------------------------------
    def clear_all(self):
        """Clear every commodity book, then tick prices for the next turn."""
        self.prev_price = dict(self.price)
        self.settlements = {}
        for c in COMMODITIES:
            self.settlements[c.key] = self._clear_commodity(c.key)
        self._tick_prices()
        self.clear_books()
        return self.settlements

    def _clear_commodity(self, key):
        price = self.price[key]
        buys = self.buys[key]
        sells = self.sells[key]

        # Re-validate every order against reality at the moment of settlement:
        # cash may have been spent and stock may have been consumed since the
        # order was queued. This is what guarantees no negative balances and
        # no phantom goods.
        live_buys = []
        for o in buys:
            q = o.qty
            if o.account.currency != self.currency:
                continue                     # stale, wrong-currency order
            affordable = o.account.balance // price if price > 0 else 0
            q = min(q, affordable)
            if o.inventory is not None:
                q = min(q, o.inventory.space(key))
            if q >= config.MARKET_MIN_ORDER:
                o.qty = q
                live_buys.append(o)

        live_sells = []
        for o in sells:
            q = o.qty
            if o.account.currency != self.currency:
                continue
            if o.inventory is not None:
                q = min(q, o.inventory.get(key))
            if q >= config.MARKET_MIN_ORDER:
                o.qty = q
                live_sells.append(o)

        demand = sum(o.qty for o in live_buys)
        supply = sum(o.qty for o in live_sells)
        traded = demand if demand < supply else supply

        st = Settlement(key, price, traded, demand, supply)
        self.last_demand[key] = demand
        self.last_supply[key] = supply
        self.last_traded[key] = traded

        if traded <= 0:
            return st

        # --- Pro-Rata Allocation (doc 3.2) --------------------------------
        buy_alloc = allocate_pro_rata(traded, [o.qty for o in live_buys])
        sell_alloc = allocate_pro_rata(traded, [o.qty for o in live_sells])
        # Both allocations sum to exactly `traded` by construction.

        escrow = self.escrow()
        float_inv = self._float

        # Sellers deliver goods into the float and are paid from escrow.
        for o, a in zip(live_sells, sell_alloc):
            if a <= 0:
                continue
            moved = self.ledger.move_goods(o.inventory, float_inv, key, a,
                                           capped=False)
            o.filled = moved
            st.sell_fills.append((o, moved))

        delivered = sum(q for _, q in st.sell_fills)

        # Buyers pay into escrow and receive goods from the float.
        if delivered != traded:
            # A seller's stock vanished between validation and settlement.
            # Re-ration the buy side against what actually arrived so the
            # float always empties.
            buy_alloc = allocate_pro_rata(delivered, [o.qty for o in live_buys])

        for o, a in zip(live_buys, buy_alloc):
            if a <= 0:
                continue
            got = self.ledger.move_goods(float_inv, o.inventory, key, a,
                                         capped=True)
            if got <= 0:
                continue
            cost = got * price
            paid = self.ledger.transfer(o.account, escrow, cost,
                                        f"buy:{key}")
            if paid < cost:
                # Should be impossible after validation, but stay conserving:
                # hand back the goods we could not pay for.
                refund_units = (cost - paid) // price
                if refund_units > 0:
                    self.ledger.move_goods(o.inventory, float_inv, key,
                                           refund_units, capped=False)
                    got -= refund_units
            o.filled = got
            o.value = got * price
            st.buy_fills.append((o, got))

        # Pay the sellers out of escrow, proportional to goods delivered.
        total_delivered = sum(q for _, q in st.buy_fills)
        revenue = escrow.balance
        if total_delivered > 0 and revenue > 0:
            # Note: goods use allocate_pro_rata (each share capped at its
            # request) but revenue must be paid out in full, so sellers are
            # settled with _split_money, where weights are pure ratios.
            weights = [q for _, q in st.sell_fills]
            payouts = self._split_money(revenue, weights)
            for (o, _), pay in zip(st.sell_fills, payouts):
                if pay > 0:
                    self.ledger.transfer(escrow, o.account, pay, f"sell:{key}")
                    o.value += pay

        # Anything left in the float had no solvent buyer: return it to the
        # sellers so no goods are destroyed by the clearing house.
        leftover = float_inv.get(key)
        if leftover > 0:
            for o, _ in st.sell_fills:
                if leftover <= 0:
                    break
                back = self.ledger.move_goods(float_inv, o.inventory, key,
                                              leftover, capped=False)
                leftover -= back
        # Any residual escrow dust goes back to the first paying buyer.
        if escrow.balance > 0 and st.buy_fills:
            self.ledger.transfer(escrow, st.buy_fills[0][0].account,
                                 escrow.balance, "escrow_refund")

        st.traded = total_delivered
        self.last_traded[key] = total_delivered
        return st

    @staticmethod
    def _split_money(total, weights):
        """Distribute the full ``total`` across ``weights`` exactly.

        Unlike ``allocate_pro_rata`` (which caps each share at its weight, the
        correct behaviour for goods), revenue must be entirely paid out, so the
        weights act purely as ratios.
        """
        n = len(weights)
        if n == 0:
            return []
        s = sum(weights)
        if s <= 0:
            out = [0] * n
            out[0] = total
            return out
        base = [(total * w) // s for w in weights]
        rem = total - sum(base)
        if rem:
            order = sorted(range(n),
                           key=lambda i: (-((total * weights[i]) % s), -weights[i], i))
            for i in order[:rem]:
                base[i] += 1
        return base

    # ------------------------------------------------------------------
    # Price Tick Ticker (doc 3.2)
    # ------------------------------------------------------------------
    def cost_anchor(self, c) -> int:
        """This commodity's local cost of production (see config).

        Labour is the only primary input in the economy, so a commodity's
        production cost is a fixed multiple of the prevailing local wage. The
        anchor is therefore its design-time base price rescaled by how far the
        local wage has drifted from the reference wage.
        """
        wage_key = config.MARKET_ANCHOR_WAGE_KEY
        if c.key == wage_key:
            return c.base_price
        wage = self.price[wage_key]
        wage_base = COMMODITIES[wage_key].base_price
        if wage_base <= 0:
            return c.base_price
        anchor = (c.base_price * wage) // wage_base
        return clamp(anchor, c.price_min, c.price_max)

    def _tick_prices(self):
        for c in COMMODITIES:
            key = c.key
            demand = self.last_demand[key]
            supply = self.last_supply[key]
            price = self.price[key]

            if demand > 0 and supply > 0:
                # Target price implied by the imbalance ratio, damped.
                # ratio > 1 -> excess demand -> price should rise.
                target = (price * demand) // supply
                self.price[key] = bounded_move(
                    price, target,
                    config.MARKET_PRICE_DAMPING_BP,
                    config.MARKET_MAX_MOVE_BP,
                    c.price_min, c.price_max)
                self.empty_streak[key] = 0

            elif demand > 0 and supply == 0:
                # EXPLOSION SAFEGUARD: unmet demand with an empty sell side.
                # Creep upward at a bounded pace instead of jumping to the cap.
                self.empty_streak[key] += 1
                self.price[key] = step_bp(price, config.MARKET_EMPTY_SIDE_STEP_BP,
                                          c.price_min, c.price_max)

            elif supply > 0 and demand == 0:
                self.empty_streak[key] += 1
                self.price[key] = step_bp(price, -config.MARKET_EMPTY_SIDE_STEP_BP,
                                          c.price_min, c.price_max)

            else:
                # Dead market: drift gently back toward the anchor price so a
                # dormant sector does not keep a stale extreme quote forever.
                self.empty_streak[key] += 1
                self.price[key] = bounded_move(
                    price, self.cost_anchor(c),
                    config.MARKET_EMPTY_BOTH_DECAY_BP,
                    config.MARKET_MAX_MOVE_BP,
                    c.price_min, c.price_max)

            # Cost anchoring: a weak, always-on pull toward the local cost of
            # production. Genuine scarcity still moves a price far from its
            # anchor, but a permanent order-book imbalance can no longer ratchet
            # it to the ceiling, and the chain's relative structure survives.
            if config.MARKET_ANCHOR_PULL_BP > 0:
                self.price[key] = bounded_move(
                    self.price[key], self.cost_anchor(c),
                    config.MARKET_ANCHOR_PULL_BP,
                    config.MARKET_MAX_MOVE_BP,
                    c.price_min, c.price_max)

    # ------------------------------------------------------------------
    # read-only queries used by traders and the UI
    # ------------------------------------------------------------------
    def spot(self, key) -> int:
        return self.price[key]

    def pressure_bp(self, key) -> int:
        """Order-book imbalance as basis points: >10000 means excess demand."""
        d, s = self.last_demand[key], self.last_supply[key]
        if d == 0 and s == 0:
            return config.BP
        if s == 0:
            return config.BP * 4
        return clamp((d * config.BP) // s, 0, config.BP * 4)

    def is_float_empty(self) -> bool:
        """Zero Storage Clearing assertion helper."""
        return not any(v for v in self._float.stock.values()) and \
            all(a.balance == 0 for a in self._escrow.values())

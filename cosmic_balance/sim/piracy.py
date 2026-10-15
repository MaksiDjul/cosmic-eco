"""
PIRACY & THE BLACK MARKET ECONOMY (doc 5.3).

*   Sector Piracy Index -- an abstract percentage driven by raider presence
    versus patrolling security fleets.
*   Security Patrols -- fleets on patrol stance suppress piracy with an
    efficiency bonus; the resulting skirmishes have low casualty rates to model
    asymmetric guerrilla warfare.
*   Interception Events -- above a piracy threshold, random checks steal a
    portion of the commodities moving through legal market orders. The goods
    are TRANSFERRED to a hidden pirate base, never deleted, so conservation of
    value holds across the criminal economy too.
*   The Pirate Loop -- pirate stations sit outside sector governance, need no
    upkeep or labour, fence their plunder in adjacent legal markets, and spend
    the illicit proceeds on heavy military components to spawn more raiders.
"""

import random

from . import config
from .fixed import apply_bp, clamp
from .galaxy import hex_distance
from .ledger import Inventory, Wallet
from .military import Fleet, PATROL


class PirateBase:
    """A hidden black-market hub. No upkeep, no labour, no taxes."""

    _seq = 0

    def __init__(self, sector, ledger, currency_code, name=None, cash=0):
        PirateBase._seq += 1
        self.id = PirateBase._seq
        self.sector = sector
        self.ledger = ledger
        self.name = name or f"Hideout-{self.id}"

        self.wallet = Wallet(ledger, f"pirate:{self.id}", currency_code)
        self.wallet.account(currency_code)
        if cash:
            ledger.mint(self.wallet.account(currency_code), cash, "pirate_seed")

        # Plunder hoard: uncapped, these are caves not warehouses.
        self.hoard = Inventory(ledger, f"pirate_hoard:{self.id}",
                               default_capacity=10 ** 12)
        self.raiders = []
        self.stolen_total = 0
        self.fenced_total = 0
        self._orders = []
        sector.pirate_base = self

    # ------------------------------------------------------------------
    def legal_markets(self, world):
        """Adjacent legal sectors it can fence goods through."""
        return [s for s in world.galaxy.neighbors(self.sector)
                if s.pirate_base is None] or [self.sector]

    def submit_orders(self, world):
        self._orders = []
        markets = self.legal_markets(world)
        if not markets:
            return

        # --- fence the plunder ------------------------------------------
        for key, qty in list(self.hoard.nonzero().items()):
            if key in ("LABOR",):
                continue
            share = apply_bp(qty, config.PIRATE_SELL_SHARE_BP)
            if share <= 0:
                share = qty
            # Sell where the black market pays best.
            best = max(markets, key=lambda s: s.market.spot(key))
            acc = self.wallet.account(best.market.currency)
            o = best.market.submit_sell(key, share, acc, self.hoard, self,
                                        tag=f"{self.name} fence")
            if o:
                self._orders.append(o)

        # --- buy heavy military components to spawn raiders -------------
        if len(self.raiders) < config.PIRATE_MAX_RAIDERS_PER_BASE:
            best = min(markets, key=lambda s: s.market.spot("MIL_UNIT"))
            acc = self.wallet.account(best.market.currency)
            self.hoard.capacity["MIL_UNIT"] = 10 ** 12
            o = best.market.submit_buy(
                "MIL_UNIT", config.PIRATE_BUY_MAX_PER_TICK, acc, self.hoard,
                self, tag=f"{self.name} rearm")
            if o:
                self._orders.append(o)

    def submit_fx(self, fx):
        """Launder foreign receipts into the black-market currency."""
        from .currency import convert_legacy_cash
        convert_legacy_cash(self.wallet, fx, self)

    def post_market(self, world):
        self.fenced_total += sum(o.value for o in self._orders
                                 if o.side == "sell")
        # Spawn raiders from accumulated military tonnage.
        units = self.hoard.get("MIL_UNIT")
        while units >= config.PIRATE_RAIDER_COST_MIL_UNITS and \
                len(self.raiders) < config.PIRATE_MAX_RAIDERS_PER_BASE:
            self.ledger.consume(self.hoard, "MIL_UNIT",
                                config.PIRATE_RAIDER_COST_MIL_UNITS, "raider")
            units -= config.PIRATE_RAIDER_COST_MIL_UNITS
            target = world.rng.choice(
                world.galaxy.neighbors(self.sector) or [self.sector])
            raider = Fleet(world.pirate_key, target,
                           strength=config.PIRATE_RAIDER_STRENGTH,
                           name=f"Raider-{len(self.raiders) + 1}",
                           stance="raid")
            target.fleets.append(raider)
            world.fleets.append(raider)
            self.raiders.append(raider)
            world.log(f"{self.name} launched a raider into {target.name}")

        self.raiders = [r for r in self.raiders if r.strength > 0]

    def receive_plunder(self, inventory, key, qty) -> int:
        moved = self.ledger.move_goods(inventory, self.hoard, key, qty,
                                       capped=False)
        self.stolen_total += moved
        return moved

    def book_value(self, world) -> int:
        total = self.wallet.total_in(self.wallet.operating, world.fx)
        market = self.sector.market
        for key, qty in self.hoard.stock.items():
            if qty:
                total += qty * market.spot(key)
        return total

    def __repr__(self):
        return f"<PirateBase {self.name} raiders={len(self.raiders)}>"


# ---------------------------------------------------------------------------
# Sector piracy index
# ---------------------------------------------------------------------------

def update_piracy_index(sector, world):
    """Raider presence versus patrolling security fleets (doc 5.3)."""
    raider_strength = 0
    patrol_strength = 0
    for f in sector.fleets:
        if not f.is_present() or f.strength <= 0:
            continue
        if world.is_pirate(f.owner):
            raider_strength += f.effective_strength()
        elif f.stance == PATROL:
            # Patrol stance suppresses with an efficiency bonus.
            patrol_strength += apply_bp(f.effective_strength(),
                                        config.PIRACY_PATROL_EFFICIENCY_BP)
        else:
            patrol_strength += f.effective_strength() // 3

    target = config.PIRACY_BASE_BP
    target += raider_strength * config.PIRACY_PER_RAIDER_STRENGTH_BP
    target -= patrol_strength * config.PIRACY_PATROL_SUPPRESSION_BP
    if sector.pirate_base is not None:
        target += config.PIRACY_BASE_BP * 4
    target = clamp(target, 0, config.PIRACY_MAX_BP)

    # Move gradually toward the target so the index is a trend, not a flicker.
    cur = sector.piracy_bp
    if target > cur:
        cur = min(target, cur + max(1, (target - cur) // 6))
    else:
        cur = max(target, cur - max(1, config.PIRACY_NATURAL_DECAY_BP))
    sector.piracy_bp = clamp(cur, 0, config.PIRACY_MAX_BP)


# ---------------------------------------------------------------------------
# Interception events
# ---------------------------------------------------------------------------

def run_interceptions(sector, world, rng: random.Random):
    """Steal a share of the goods delivered through this sector's market.

    Runs AFTER settlement, operating on the actual fills, so the thieves take
    real goods out of real inventories and the ledger stays balanced.
    """
    if sector.piracy_bp < config.PIRACY_INTERCEPT_THRESHOLD_BP:
        return 0
    base = world.nearest_pirate_base(sector)
    if base is None:
        return 0

    chance_bp = (config.PIRACY_INTERCEPT_CHANCE_BP * sector.piracy_bp) \
        // config.PIRACY_MAX_BP
    stolen_units = 0

    for key, st in sector.market.settlements.items():
        if st.traded <= 0 or key == "LABOR":
            continue
        for order, qty in st.buy_fills:
            if qty <= 0:
                continue
            if rng.randint(1, config.BP) > chance_bp:
                continue
            share = apply_bp(qty, config.PIRACY_STEAL_SHARE_BP)
            if share <= 0:
                share = 1
            got = base.receive_plunder(order.inventory, key, share)
            stolen_units += got

            # Cargo assets may be destroyed outright in the raid.
            if got and rng.randint(1, config.BP) <= config.PIRACY_CARGO_DESTROY_CHANCE_BP:
                if order.inventory.get("CARGO_UNIT") > 0:
                    world.ledger.consume(order.inventory, "CARGO_UNIT", 1,
                                         "piracy_destroyed")

    if stolen_units:
        world.stolen_this_tick += stolen_units
    return stolen_units

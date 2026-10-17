"""
MILITARY, WARFARE & SECURITY LAYER (doc 5.1, 5.2).

*   Fleet Formations -- forces are abstracted as a numerical combat strength
    built by buying Military Tonnage from the open market, not spawned free.
*   Logistical Bases -- every fleet links to a physical Military Base Station
    which buys military assets AND labour units locally to crew the fleets. If
    the labour supply drops, the fleet suffers a severe efficiency penalty.
*   Strategic Deployment -- fleets hold a hex coordinate and move incrementally
    across sectors over multiple ticks.
*   Sustained Engagements -- combat applies a deliberately low attrition
    coefficient per tick so wars are campaigns, not instant wipes.
*   Sovereignty Capture -- a slow conquest meter that only advances under
    near-total naval supremacy.
"""

import random

from . import config
from .fixed import apply_bp, clamp
from .ledger import Inventory, Wallet

PATROL, DEFEND, ATTACK = "patrol", "defend", "attack"
MOTHBALLED = "mothballed"


class Fleet:
    _seq = 0

    def __init__(self, owner, sector, strength=0, base=None, name=None,
                 stance=DEFEND):
        Fleet._seq += 1
        self.id = Fleet._seq
        self.owner = owner
        self.sector = sector
        self.strength = int(strength)
        self.base = base
        self.name = name or f"Fleet-{self.id}"
        self.stance = stance
        self.mothballed = False

        # Strategic deployment state
        self.path = []                 # remaining sectors to traverse
        self.move_progress = 0         # ticks spent on the current jump
        self.destination = None

        self.strength_at_engagement_start = self.strength
        self.engaged_last_tick = False
        self.losses_total = 0

    # ------------------------------------------------------------------
    @property
    def coord(self):
        return self.sector.coord

    def is_present(self) -> bool:
        """A fleet mid-jump is not available for combat or patrol duty."""
        return not self.path

    def crew_bp(self) -> int:
        if self.base is None:
            return config.BP
        return self.base.crew_bp

    def effective_strength(self) -> int:
        if self.strength <= 0:
            return 0
        bp = self.crew_bp()
        if bp < config.FLEET_UNCREWED_EFFICIENCY_BP:
            bp = config.FLEET_UNCREWED_EFFICIENCY_BP
        result = max(0, apply_bp(self.strength, bp))
        if self.mothballed:
            result = apply_bp(result, config.MOTHBALL_COMBAT_BP)
        return result

    def asset_value(self, world) -> int:
        units = self.strength // max(1, config.FLEET_STRENGTH_PER_TONNAGE)
        price = self.sector.market.spot("SHIP_TONNAGE")
        return units * price

    def can_mothball(self) -> bool:
        return (self.base is not None and
                self.base.sector is self.sector and
                self.is_present() and
                not self.mothballed)

    def set_mothball(self, state: bool):
        self.mothballed = state
        if state:
            self.path = []
            self.destination = None

    # ------------------------------------------------------------------
    def order_move(self, world, target_sector):
        """Plot an incremental multi-tick course to another sector."""
        if target_sector is self.sector:
            self.path = []
            self.destination = None
            return False
        path = world.galaxy.path(self.sector, target_sector)
        if not path or len(path) < 2:
            return False
        self.path = path[1:]
        self.destination = target_sector
        self.move_progress = 0
        return True

    def advance(self, world):
        """Spend a tick travelling (doc 5.1 "Strategic Deployment")."""
        if not self.path:
            return
        self.move_progress += 1
        if self.move_progress >= config.FLEET_TICKS_PER_JUMP:
            self.move_progress = 0
            nxt = self.path.pop(0)
            self.relocate(nxt)
            if not self.path:
                self.destination = None

    def relocate(self, sector):
        if self.sector is sector:
            return
        if self in self.sector.fleets:
            self.sector.fleets.remove(self)
        self.sector = sector
        sector.fleets.append(self)

    # ------------------------------------------------------------------
    def take_losses(self, amount, world):
        amount = max(0, int(amount))
        if amount <= 0:
            return 0
        amount = min(amount, self.strength)
        self.strength -= amount
        self.losses_total += amount
        fac = world.factions.get(self.owner)
        if fac is not None:
            fac.strength_lost_total += amount
            # War exhaustion is driven by fleet casualties (doc 6).
            fac.add_exhaustion(amount * config.EXHAUSTION_PER_STRENGTH_LOST
                               // 10)
        return amount

    def __repr__(self):
        return f"<Fleet {self.name} {self.owner} str={self.strength}>"


class MilitaryBase:
    """A fleet base station with goods storage, crewing and ship commissioning.

    One fleet per base. Fleet spawns at strength 0 and is built up by
    purchasing SHIP_TONNAGE. Level sets fleet capacity. Like habitats, money
    comes from the owning faction — no dividends.
    """

    _seq = 0

    def __init__(self, sector, owner, ledger, currency_code, level=1,
                 name=None, cash=0):
        MilitaryBase._seq += 1
        self.id = MilitaryBase._seq
        self.sector = sector
        self.owner = owner
        self.ledger = ledger
        self.level = level
        self.name = name or f"Base-{self.id}"

        self.wallet = Wallet(ledger, f"base:{self.id}", currency_code)
        self.wallet.account(currency_code)
        if cash:
            ledger.mint(self.wallet.account(currency_code), cash, "base_seed")

        self.store = Inventory(ledger, f"base_store:{self.id}",
                               default_capacity=400)
        self.fleets = []
        self._resize_storage()
        self.crew_bp = config.BP
        self.labor_shortfall = 0
        self.spend_last = 0
        self.maint_missed = 0
        self._orders = []

    def _resize_storage(self):
        lvl = max(1, self.level)
        cap = self.fleet_capacity()
        labor_need = cap * config.FLEET_CREW_LABOR_PER_STRENGTH
        self.store.capacity["LABOR"] = max(40,
            labor_need * config.MILBASE_LABOR_BUFFER_TICKS)
        mg_need = self.maint_goods_need()
        self.store.capacity["MAINT_GOODS"] = max(40,
            mg_need * config.MILBASE_LABOR_BUFFER_TICKS)
        self.store.capacity["SHIP_TONNAGE"] = max(40, (1 + lvl) * 5)

    def fleet_capacity(self) -> int:
        return max(1, self.level) * config.MILBASE_FLEET_CAP_PER_LEVEL

    def maint_goods_need(self) -> int:
        strength = self.linked_strength()
        return max(0, (strength * config.MILBASE_MAINT_GOODS_PER_STRENGTH) // 100)

    # ------------------------------------------------------------------
    def linked_strength(self) -> int:
        return sum(f.strength for f in self.fleets)

    def labor_need(self) -> int:
        total = self.linked_strength() * config.FLEET_CREW_LABOR_PER_STRENGTH
        for f in self.fleets:
            if f.mothballed:
                saved = f.strength * config.FLEET_CREW_LABOR_PER_STRENGTH
                total -= saved - apply_bp(saved, config.MOTHBALL_COST_BP)
        return max(0, total)

    def downsize(self, world):
        if self.level <= 1:
            return
        self.level -= 1
        self._resize_storage()
        for key in list(self.store.stock.keys()):
            excess = self.store.get(key) - self.store.capacity.get(key, 0)
            if excess > 0:
                self.ledger.consume(self.store, key, excess, "base_downsize")
        cap = self.fleet_capacity()
        for f in self.fleets:
            if f.strength > cap:
                over = f.strength - cap
                f.strength = cap
                f.losses_total += over
        world.log(f"{self.name} downsized to level {self.level}")

    # ------------------------------------------------------------------
    def submit_orders(self, world):
        self._orders = []
        self._resize_storage()
        market = self.sector.market
        acc = self.wallet.account(market.currency)
        lvl = max(1, self.level)

        # Crew labour tokens
        need = self.labor_need()
        if need > 0:
            target = need * config.MILBASE_LABOR_BUFFER_TICKS
            short = max(0, target - self.store.get("LABOR"))
            order = need + short // config.RESTOCK_SPREAD_TICKS
            order = min(order, short) if short > 0 else 0
            if order > 0:
                o = market.submit_buy("LABOR", order, acc, self.store, self,
                                      tag=f"{self.name} crew")
                if o:
                    self._orders.append(o)

        # Maintenance goods
        mg_need = self.maint_goods_need()
        if mg_need > 0:
            mg_target = mg_need * config.MILBASE_LABOR_BUFFER_TICKS
            mg_short = max(0, mg_target - self.store.get("MAINT_GOODS"))
            mg_order = mg_need + mg_short // config.RESTOCK_SPREAD_TICKS
            mg_order = min(mg_order, mg_short) if mg_short > 0 else 0
            if mg_order > 0:
                o = market.submit_buy("MAINT_GOODS", mg_order, acc,
                                      self.store, self,
                                      tag=f"{self.name} maint")
                if o:
                    self._orders.append(o)

        # Ship tonnage to commission into fleet strength
        tonnage_per_tick = 1 + lvl
        if world.wants_military(self.owner):
            o = market.submit_buy("SHIP_TONNAGE", tonnage_per_tick, acc,
                                  self.store, self,
                                  tag=f"{self.name} rearm")
            if o:
                self._orders.append(o)

    # ------------------------------------------------------------------
    def post_market(self, world):
        self.spend_last = sum(o.value for o in self._orders)
        fac = world.factions.get(self.owner)
        if fac is not None:
            fac.military_spend_last += self.spend_last

        # --- crew the fleets ------------------------------------------
        need = self.labor_need()
        if need > 0:
            got = self.ledger.consume(self.store, "LABOR", need, "crew")
            self.crew_bp = clamp((got * config.BP) // need, 0, config.BP)
            self.labor_shortfall = need - got
        else:
            self.crew_bp = config.BP
            self.labor_shortfall = 0

        # --- consume maintenance goods --------------------------------
        mg_need = self.maint_goods_need()
        if mg_need > 0:
            mg_got = self.ledger.consume(self.store, "MAINT_GOODS", mg_need,
                                         "base_maint")
            if mg_got < mg_need:
                self.maint_missed += mg_need - mg_got
            if self.maint_missed >= config.MAINT_MISSED_DOWNGRADE:
                self.maint_missed -= config.MAINT_MISSED_DOWNGRADE
                self.downsize(world)

        # --- commission ship tonnage into fleet strength ----------
        units = self.store.get("SHIP_TONNAGE")
        cap = self.fleet_capacity()
        if units > 0 and self.fleets:
            self.ledger.consume(self.store, "SHIP_TONNAGE", units, "commission")
            gained = units * config.FLEET_STRENGTH_PER_TONNAGE
            target = min(self.fleets, key=lambda f: f.strength)
            target.strength = min(cap, target.strength + gained)

    # ------------------------------------------------------------------
    def on_sovereignty_change(self, old_owner, new_owner, new_currency, world):
        self.wallet.set_operating(new_currency)

    def submit_fx(self, fx):
        from .currency import convert_legacy_cash
        convert_legacy_cash(self.wallet, fx, self)

    def __repr__(self):
        return f"<MilitaryBase {self.name} {self.owner}>"


# ---------------------------------------------------------------------------
# COMBAT RESOLUTION & SECTOR CONQUEST (doc 5.2)
# ---------------------------------------------------------------------------

def resolve_sector_combat(sector, world, rng: random.Random):
    """Sustained engagement: hostile fleets in the same sector grind down.

    Attrition is proportional to the ENEMY's effective strength and scaled by
    a deliberately small coefficient, so an engagement lasts many ticks.
    """
    present = [f for f in sector.fleets if f.is_present() and f.strength > 0]
    if len(present) < 2:
        for f in present:
            f.engaged_last_tick = False
        return None

    # Group by owner and find mutually hostile pairs.
    sides = {}
    for f in present:
        sides.setdefault(f.owner, []).append(f)
    owners = list(sides)
    hostile_pairs = []
    for i, a in enumerate(owners):
        for b in owners[i + 1:]:
            if world.are_hostile(a, b):
                hostile_pairs.append((a, b))
    if not hostile_pairs:
        for f in present:
            f.engaged_last_tick = False
        return None

    strengths = {o: sum(f.effective_strength() for f in sides[o]) for o in owners}
    report = []

    for a, b in hostile_pairs:
        sa, sb = strengths[a], strengths[b]
        if sa <= 0 or sb <= 0:
            continue
        pirate_fight = world.is_pirate(a) or world.is_pirate(b)
        coeff = config.PIRACY_COMBAT_CASUALTY_BP if pirate_fight \
            else config.COMBAT_ATTRITION_BP
        jitter = config.COMBAT_ATTRITION_JITTER_BP

        loss_to_a = apply_bp(sb, coeff + rng.randint(-jitter, jitter))
        loss_to_b = apply_bp(sa, coeff + rng.randint(-jitter, jitter))
        loss_to_a = max(config.COMBAT_MIN_LOSS, loss_to_a)
        loss_to_b = max(config.COMBAT_MIN_LOSS, loss_to_b)

        _distribute_losses(sides[a], loss_to_a, world)
        _distribute_losses(sides[b], loss_to_b, world)
        report.append((a, b, loss_to_a, loss_to_b))

        for f in sides[a] + sides[b]:
            f.engaged_last_tick = True

    # Rout check: a shattered fleet withdraws toward friendly space.
    for f in present:
        if f.strength <= 0:
            continue
        start = max(1, f.strength_at_engagement_start)
        if f.engaged_last_tick and \
                (f.strength * config.BP) // start < config.COMBAT_ROUT_THRESHOLD_BP:
            _retreat(f, world)

    return report


def _distribute_losses(fleets, total_loss, world):
    """Spread casualties across a side proportional to fleet size."""
    live = [f for f in fleets if f.strength > 0]
    if not live or total_loss <= 0:
        return
    from .fixed import allocate_pro_rata
    weights = [f.strength for f in live]
    shares = allocate_pro_rata(min(total_loss, sum(weights)), weights)
    for f, s in zip(live, shares):
        f.take_losses(s, world)


def _retreat(fleet, world):
    """Withdraw a routed fleet to the nearest friendly or neutral sector."""
    options = world.galaxy.neighbors(fleet.sector)
    friendly = [s for s in options if s.owner == fleet.owner]
    if not friendly:
        friendly = [s for s in options
                    if not world.are_hostile(fleet.owner, s.owner)]
    if friendly:
        fleet.order_move(world, friendly[0])
        fleet.strength_at_engagement_start = fleet.strength


def update_conquest(sector, world):
    """Sovereignty Capture (doc 5.2): slow, deliberate, supremacy-gated."""
    present = [f for f in sector.fleets if f.is_present() and f.strength > 0
               and not world.is_pirate(f.owner)]
    if not present:
        _decay_conquest(sector)
        return

    by_owner = {}
    for f in present:
        by_owner[f.owner] = by_owner.get(f.owner, 0) + f.effective_strength()
    total = sum(by_owner.values())
    if total <= 0:
        _decay_conquest(sector)
        return

    leader = max(by_owner, key=lambda k: by_owner[k])
    share_bp = (by_owner[leader] * config.BP) // total

    if leader == sector.owner or share_bp < config.CONQUEST_SUPREMACY_BP or \
            not world.are_hostile(leader, sector.owner):
        _decay_conquest(sector)
        return

    if sector.conquest_claimant != leader:
        sector.conquest_claimant = leader
        sector.conquest_progress = 0

    sector.conquest_progress = min(
        config.CONQUEST_PROGRESS_MAX,
        sector.conquest_progress + config.CONQUEST_PROGRESS_PER_TICK)

    if sector.conquest_progress >= config.CONQUEST_PROGRESS_MAX:
        loser = sector.owner
        sector.transfer_sovereignty(leader, world)
        fac = world.factions.get(loser)
        if fac is not None:
            fac.sectors_lost_total += 1
            fac.add_exhaustion(config.EXHAUSTION_PER_SECTOR_LOST)


def _decay_conquest(sector):
    if sector.conquest_progress > 0:
        sector.conquest_progress = max(
            0, sector.conquest_progress - config.CONQUEST_DECAY_PER_TICK)
        if sector.conquest_progress == 0:
            sector.conquest_claimant = None

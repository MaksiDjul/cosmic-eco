"""
SECTOR PROPERTIES (doc 2 "Sector Properties").

"Every sector tracks its current owner, a local tax rate, property tax values,
a piracy index, and localized labor demographics."

Also hosts the sector's Local Market Hub and its finite resource nodes
(doc 4 "Raw Harvestables").
"""

from . import config
from .fixed import apply_bp, clamp
from .market import MarketHub


class ResourceNode:
    """A finite, slowly replenishing deposit.

    "Extraction difficulty should scale inversely with the node's remaining
    volume" (doc 4): a drained node yields only a fraction of what a fresh one
    does, which is what eventually pushes industry to migrate.
    """

    __slots__ = ("kind", "capacity", "remaining", "extracted_total")

    def __init__(self, kind, capacity=None):
        self.kind = kind
        self.capacity = capacity or config.NODE_DEFAULT_CAPACITY
        self.remaining = self.capacity
        self.extracted_total = 0

    @property
    def fill_bp(self) -> int:
        if self.capacity <= 0:
            return 0
        return clamp((self.remaining * config.BP) // self.capacity, 0, config.BP)

    @property
    def yield_bp(self) -> int:
        """Effective extraction efficiency at the current depletion level."""
        floor = config.NODE_YIELD_DEPLETION_FLOOR_BP
        return floor + ((config.BP - floor) * self.fill_bp) // config.BP

    def extract(self, requested: int) -> int:
        """Attempt to pull ``requested`` units. Returns what actually came out."""
        if requested <= 0 or self.remaining <= 0:
            return 0
        got = apply_bp(requested, self.yield_bp)
        if got <= 0:
            got = 1 if self.remaining > 0 else 0
        if got > self.remaining:
            got = self.remaining
        self.remaining -= got
        self.extracted_total += got
        return got

    def replenish(self):
        if self.remaining >= self.capacity:
            return
        gain = apply_bp(self.capacity, config.NODE_REPLENISH_BP)
        if gain < 1:
            gain = 1
        self.remaining = min(self.capacity, self.remaining + gain)


class Sector:
    def __init__(self, coord, name, owner, ledger, currency_code):
        self.coord = coord
        self.name = name
        self.owner = owner                     # faction key (sovereign)
        self.ledger = ledger

        # Faction Sovereignty levers (doc 2)
        self.tax_bp = config.DEFAULT_SECTOR_TAX_BP
        self.property_tax_bp = config.DEFAULT_PROPERTY_TAX_BP
        self.tariff_bp = config.DEFAULT_TARIFF_BP

        # Security
        self.piracy_bp = config.PIRACY_BASE_BP
        self.conquest_progress = 0
        self.conquest_claimant = None

        # Contents
        self.nodes = {}                        # kind -> ResourceNode
        self.pops = []
        self.stations = []
        self.fleets = []
        self.pirate_base = None

        self.market = MarketHub(self, ledger, currency_code)

        # Telemetry
        self.tax_collected_total = 0
        self.last_tax_income = 0
        self.ownership_changes = 0

    # ------------------------------------------------------------------
    def add_node(self, kind, capacity=None):
        node = ResourceNode(kind, capacity)
        self.nodes[kind] = node
        return node

    def node(self, kind):
        return self.nodes.get(kind)

    # ------------------------------------------------------------------
    @property
    def population(self) -> int:
        return sum(p.size for p in self.pops)

    @property
    def labor_supply(self) -> int:
        """Localized labour demographics (doc 2 / doc 3.5)."""
        return sum(p.labor_output() for p in self.pops)

    def station_count(self, faction_key=None):
        if faction_key is None:
            return len(self.stations)
        return sum(1 for s in self.stations if s.controller == faction_key)

    def fleet_strength(self, faction_key):
        return sum(f.effective_strength() for f in self.fleets
                   if f.owner == faction_key and f.is_present())

    def present_factions(self):
        return {f.owner for f in self.fleets if f.is_present()}

    # ------------------------------------------------------------------
    def replenish_nodes(self):
        for n in self.nodes.values():
            n.replenish()

    def set_tax(self, bp):
        self.tax_bp = clamp(int(bp), 0, config.MAX_SECTOR_TAX_BP)

    def set_property_tax(self, bp):
        self.property_tax_bp = clamp(int(bp), 0, config.MAX_PROPERTY_TAX_BP)

    def set_tariff(self, bp):
        self.tariff_bp = clamp(int(bp), 0, config.MAX_TARIFF_BP)

    # ------------------------------------------------------------------
    def transfer_sovereignty(self, new_owner, world):
        """Flip ownership and run the Sector Currency Transition protocol
        (doc 3.2).

        Local entities keep their old cash reserves, but their operating
        currency becomes the new sovereign's. From the next tick they start
        issuing FX orders to swap the legacy balances so they can pay taxes
        and labour costs in the new money.
        """
        if new_owner == self.owner:
            return False
        old_owner = self.owner
        self.owner = new_owner
        self.ownership_changes += 1
        self.conquest_progress = 0
        self.conquest_claimant = None

        new_currency = world.factions[new_owner].currency.code
        self.market.set_currency(new_currency)

        # Event protocol hook into every local economic entity.
        for ent in list(self.stations) + list(self.pops):
            ent.on_sovereignty_change(old_owner, new_owner, new_currency, world)

        world.log(f"{self.name} annexed by {world.factions[new_owner].name} "
                  f"(was {world.factions[old_owner].name}); market now trades "
                  f"in {new_currency}")
        return True

    def __repr__(self):
        return f"<Sector {self.name} {self.coord} owner={self.owner}>"

"""
Commodity registry (doc 4 "Broad Commodity Classification").

The commodity table itself lives in ``config.py``; this module turns it into an
indexed registry and provides the extensible production matrix used by
stations (doc 3.3 "Production Methods").
"""

from dataclasses import dataclass, field

from . import config


@dataclass(frozen=True)
class Commodity:
    key: str
    cls: str
    name: str
    base_price: int
    price_min: int
    price_max: int
    storable: bool
    volume: int
    index: int


class CommodityRegistry:
    """Immutable, index-addressable commodity set."""

    def __init__(self, defs):
        self.all = []
        self.by_key = {}
        for i, (key, cls, name, base, pmin, pmax, storable, vol) in enumerate(defs):
            c = Commodity(key, cls, name, base, pmin, pmax, storable, vol, i)
            self.all.append(c)
            self.by_key[key] = c
        self.keys = [c.key for c in self.all]
        self.storable_keys = [c.key for c in self.all if c.storable]
        self.tradable_keys = list(self.keys)

    def __iter__(self):
        return iter(self.all)

    def __len__(self):
        return len(self.all)

    def __getitem__(self, key):
        return self.by_key[key]

    def by_class(self, cls):
        return [c for c in self.all if c.cls == cls]


COMMODITIES = CommodityRegistry(config.COMMODITY_DEFS)


# ---------------------------------------------------------------------------
# Production matrix (doc 3.3 "Production Methods")
# ---------------------------------------------------------------------------

@dataclass
class ProductionMethod:
    """An extensible input-to-output conversion formula per tick.

    ``inputs`` and ``outputs`` are per-level-per-tick base quantities. Station
    level multiplies both (doc 3.3 "Level Scaling": each level adds 100% of
    base throughput).

    ``extracts`` names a sector resource-node type when this method is an
    extraction method: raw harvestables are drawn from the node's finite
    volume rather than from an input commodity.
    """
    key: str
    name: str
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    extracts: str = None       # resource node type, for RAW harvesting
    short: str = ""            # 3-letter UI tag

    def scaled_inputs(self, level: int, factor_bp: int):
        """Input draw for a given level and throughput factor."""
        return {k: max(0, (v * level * factor_bp) // config.BP)
                for k, v in self.inputs.items()}

    def scaled_outputs(self, level: int, factor_bp: int):
        return {k: max(0, (v * level * factor_bp) // config.BP)
                for k, v in self.outputs.items()}


# The baseline industrial chain. This forms a genuinely closed loop:
#   pops sell LABOR -> miners buy LABOR and sell RAW -> refineries buy RAW+LABOR
#   and sell REFINED -> factories buy REFINED+LABOR and sell HITECH/SUSTENANCE
#   -> pops buy FOOD/GOODS with their wages.
PRODUCTION_METHODS = {
    m.key: m for m in [
        # --- Extraction (Raw Harvestables from finite nodes) ---
        ProductionMethod("ore_mine", "Ore Mine",
                         inputs={"LABOR": 7}, outputs={"ORE": 16},
                         extracts="ORE", short="ORE"),
        ProductionMethod("gas_collector", "Gas Collector",
                         inputs={"LABOR": 6}, outputs={"GAS": 15},
                         extracts="GAS", short="GAS"),
        ProductionMethod("ice_harvester", "Ice Harvester",
                         inputs={"LABOR": 5}, outputs={"ICE": 18},
                         extracts="ICE", short="ICE"),

        # --- Refining (Refined Elements) ---
        ProductionMethod("smelter", "Metal Smelter",
                         inputs={"ORE": 12, "LABOR": 8}, outputs={"METAL": 7},
                         short="MET"),
        ProductionMethod("fuel_refinery", "Fuel Refinery",
                         inputs={"GAS": 12, "LABOR": 7}, outputs={"FUEL": 7},
                         short="FUE"),
        ProductionMethod("chem_plant", "Polymer Plant",
                         inputs={"GAS": 6, "ICE": 8, "LABOR": 7},
                         outputs={"POLYMER": 8}, short="POL"),

        # --- High-Tech Infrastructure Components ---
        ProductionMethod("electronics_fab", "Electronics Fab",
                         inputs={"METAL": 4, "POLYMER": 5, "LABOR": 11},
                         outputs={"ELECTRONICS": 4}, short="ELE"),
        ProductionMethod("hull_works", "Hull Works",
                         inputs={"METAL": 6, "POLYMER": 3, "LABOR": 10},
                         outputs={"HULLPARTS": 4}, short="HUL"),

        # --- Sustenance Goods ---
        ProductionMethod("hydro_farm", "Hydroponics Farm",
                         inputs={"ICE": 9, "LABOR": 8}, outputs={"FOOD": 26},
                         short="FOO"),
        ProductionMethod("goods_factory", "Consumer Goods Plant",
                         inputs={"POLYMER": 6, "METAL": 2, "LABOR": 9},
                         outputs={"GOODS": 9}, short="GDS"),

        # --- Maintenance ---
        ProductionMethod("maint_workshop", "Maintenance Workshop",
                         inputs={"METAL": 3, "POLYMER": 2, "LABOR": 8},
                         outputs={"MAINT_GOODS": 6}, short="MNT"),

        # --- Habitation (population capacity infrastructure) ---
        ProductionMethod("habitation", "Habitation Complex",
                         inputs={"LABOR": 4}, outputs={},
                         short="HAB"),

        # --- Ship Tonnage (single unified shipyard) ---
        ProductionMethod("shipyard", "Shipyard",
                         inputs={"HULLPARTS": 5, "ELECTRONICS": 3, "FUEL": 4,
                                 "LABOR": 13},
                         outputs={"SHIP_TONNAGE": 3}, short="SHP"),
    ]
}

# Which methods are extraction methods, keyed by node type.
EXTRACTION_BY_NODE = {}
for _m in PRODUCTION_METHODS.values():
    if _m.extracts:
        EXTRACTION_BY_NODE.setdefault(_m.extracts, []).append(_m.key)

RESOURCE_NODE_TYPES = ("ORE", "GAS", "ICE")

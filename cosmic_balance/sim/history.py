"""
Historical data recording for the analytical dashboards (doc 7).

"The UI must provide historical data visualization screens, including commodity
price charts, faction net-worth graphs, and factory profit logs."

Pure data collection: fixed-length ring buffers so memory is bounded no matter
how long a game runs.
"""

from collections import deque

from . import config


class Series:
    """A bounded time series of (tick, value) samples."""

    __slots__ = ("label", "points", "vmin", "vmax")

    def __init__(self, label, length=None):
        self.label = label
        self.points = deque(maxlen=length or config.HISTORY_LENGTH)
        self.vmin = None
        self.vmax = None

    def add(self, tick, value):
        self.points.append((tick, value))
        # Recompute bounds lazily but cheaply.
        if self.vmin is None or value < self.vmin or self.vmax is None or \
                value > self.vmax:
            self._recompute()

    def _recompute(self):
        if not self.points:
            self.vmin = self.vmax = None
            return
        vals = [v for _, v in self.points]
        self.vmin = min(vals)
        self.vmax = max(vals)

    def bounds(self):
        if self.vmin is None:
            self._recompute()
        if not self.points:
            return 0, 1
        lo, hi = self.vmin, self.vmax
        if lo == hi:
            return lo - 1, hi + 1
        return lo, hi

    def last(self, default=0):
        return self.points[-1][1] if self.points else default

    def values(self):
        return [v for _, v in self.points]

    def __len__(self):
        return len(self.points)


class History:
    """All recorded series, grouped by subject."""

    def __init__(self):
        self.prices = {}            # (sector_name, commodity) -> Series
        self.volumes = {}           # (sector_name, commodity) -> Series
        self.net_worth = {}         # faction key -> Series
        self.treasury = {}          # faction key -> Series
        self.fx = {}                # currency code -> Series
        self.station_profit = {}    # station id -> Series
        self.population = {}        # faction key -> Series
        self.money_supply = {}      # currency code -> Series
        self.strength = {}          # faction key -> Series
        self.piracy = {}            # sector name -> Series
        self.global_series = {}     # label -> Series

    # ------------------------------------------------------------------
    def _get(self, bucket, key, label):
        s = bucket.get(key)
        if s is None:
            s = Series(label)
            bucket[key] = s
        return s

    def price(self, sector_name, commodity):
        return self._get(self.prices, (sector_name, commodity),
                         f"{commodity} @ {sector_name}")

    def volume(self, sector_name, commodity):
        return self._get(self.volumes, (sector_name, commodity),
                         f"{commodity} vol @ {sector_name}")

    def worth(self, key, name=None):
        return self._get(self.net_worth, key, name or key)

    def treas(self, key, name=None):
        return self._get(self.treasury, key, name or key)

    def rate(self, code):
        return self._get(self.fx, code, code)

    def profit(self, station_id, name=None):
        return self._get(self.station_profit, station_id, name or str(station_id))

    def pop(self, key, name=None):
        return self._get(self.population, key, name or key)

    def supply(self, code):
        return self._get(self.money_supply, code, f"{code} supply")

    def mil(self, key, name=None):
        return self._get(self.strength, key, name or key)

    def pir(self, sector_name):
        return self._get(self.piracy, sector_name, sector_name)

    def glob(self, label):
        return self._get(self.global_series, label, label)

    # ------------------------------------------------------------------
    def record(self, world):
        """Sample the whole world state. Called on the sampling interval."""
        t = world.tick

        for sector in world.galaxy:
            for c in world.commodities:
                self.price(sector.name, c.key).add(t, sector.market.spot(c.key))
                self.volume(sector.name, c.key).add(
                    t, sector.market.last_traded[c.key])
            self.pir(sector.name).add(t, sector.piracy_bp)

        for key, fac in world.factions.items():
            self.worth(key, fac.name).add(t, fac.net_worth(world))
            self.treas(key, fac.name).add(t, fac.treasury)
            self.pop(key, fac.name).add(t, world.faction_population(key))
            self.mil(key, fac.name).add(t, world.faction_strength(key))
            self.supply(fac.currency.code).add(
                t, world.fx.supply_of(fac.currency.code))
            self.rate(fac.currency.code).add(t, fac.currency.value)

        for st in world.stations:
            self.profit(st.id, st.name).add(t, st.profit_last)

        self.glob("Total population").add(t, world.total_population())
        self.glob("Stations").add(t, len(world.stations))
        self.glob("Trade routes").add(t, sum(len(f.routes) for f in world.firms))
        self.glob("Goods stolen").add(t, world.stolen_this_tick)

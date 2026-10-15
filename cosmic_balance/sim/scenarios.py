"""
EXTENSIBLE SANDBOX SCENARIOS (doc 6).

"The system must support at least two baseline initialisation states:
  1. A small, tightly contained sandbox map with minimal entities to verify
     microeconomic loops, currency exchange stabilization, and market order
     matching.
  2. A massive, multi-faction galactic map with complex trade networks and
     geopolitical tension."

Scenarios are declarative builders. This is also the ONLY place in the entire
engine where money is minted (doc 3.1 "No Infinite Capital"): once a scenario
has been built, the money supply of every currency is fixed forever and the
ledger audit will prove it.
"""

from . import config
from .balance import balance_scenario
from .currency import Currency
from .faction import Faction, PEACE, WAR
from .galaxy import hex_spiral, hex_distance
from .military import Fleet, MilitaryBase, PATROL, DEFEND, ATTACK
from .piracy import PirateBase
from .pop import Pop
from .sector import Sector
from .station import Station
from .trader import MerchantFirm
from .world import World

# The full industrial chain, in dependency order.
CHAIN = [
    "ore_mine", "gas_collector", "ice_harvester",
    "smelter", "fuel_refinery", "chem_plant",
    "electronics_fab", "hull_works",
    "hydro_farm", "goods_factory",
    "cargo_yard", "mil_yard",
]

GREEK = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta",
         "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi", "Omicron", "Pi", "Rho",
         "Sigma", "Tau", "Upsilon", "Phi", "Chi", "Psi", "Omega"]

SUFFIX = ["Reach", "Expanse", "Verge", "Drift", "Gate", "Cluster", "Shoal",
          "Rift", "Span", "Hollow", "Crown", "Marches"]


# ---------------------------------------------------------------------------
# builder helpers
# ---------------------------------------------------------------------------

def _add_faction(world, key, name, color, code, cur_name, symbol,
                 player=False, treasury=None):
    cur = Currency(code, cur_name, symbol, key)
    world.fx.add_currency(cur)
    fac = Faction(key, name, color, cur, world.ledger, is_player=player)
    world.factions[key] = fac
    # The one and only mint event for this currency.
    world.ledger.mint(fac.wallet.account(code),
                      treasury if treasury is not None
                      else config.FACTION_TREASURY_START,
                      f"treasury:{key}")
    if player:
        world.player_key = key
    return fac


def _add_sector(world, coord, name, owner_key):
    cur = world.factions[owner_key].currency.code
    s = Sector(coord, name, owner_key, world.ledger, cur)
    world.galaxy.add(s)
    return s


def _add_nodes(world, sector, kinds, capacity=None):
    for kind in kinds:
        sector.add_node(kind, capacity)


def _add_pop(world, sector, size):
    cur = sector.market.currency
    p = Pop(sector, size, world.ledger, cur)
    world.ledger.mint(p.wallet.account(cur), config.POP_CASH_START,
                      "pop_seed")
    sector.pops.append(p)
    world.pops.append(p)
    return p


def _add_station(world, sector, method_key, owner, level=1, loan=False,
                 cash=None):
    cur = sector.market.currency
    st = Station(sector, method_key, world.ledger, cur, owner, level=level,
                 cash=cash if cash is not None else config.STATION_CASH_START)
    st.seed_stock()
    sector.stations.append(st)
    world.stations.append(st)
    if loan:
        # A real loan: money moves out of the treasury, and the station owes
        # interest every tick from now on.
        world.lend_capital(owner, st, config.LOAN_PRINCIPAL_PER_LEVEL)
    return st


def _add_firm(world, sector, owner):
    cur = sector.market.currency
    f = MerchantFirm(sector, world.ledger, cur, owner,
                     cash=config.TRADER_CASH_START)
    world.firms.append(f)
    return f


def _add_base(world, sector, owner, fleets=1, strength=60):
    cur = sector.market.currency
    b = MilitaryBase(sector, owner, world.ledger, cur,
                     cash=config.MILBASE_CASH_START)
    world.bases.append(b)
    for i in range(fleets):
        fl = Fleet(owner, sector, strength=strength, base=b,
                   name=f"{world.factions[owner].name} {i + 1}th Flotilla",
                   stance=PATROL if i == 0 else DEFEND)
        b.fleets.append(fl)
        sector.fleets.append(fl)
        world.fleets.append(fl)
    return b


def _add_pirates(world, sector):
    pb = PirateBase(sector, world.ledger,
                    world.factions[world.pirate_key].currency.code,
                    cash=config.PIRATE_CASH_START)
    world.pirate_bases.append(pb)
    return pb


# ---------------------------------------------------------------------------
# SCENARIO 1 -- the verification sandbox
# ---------------------------------------------------------------------------

def build_sandbox(seed=None) -> World:
    """Small, tightly contained map with minimal entities.

    Purpose (doc 6): verify the microeconomic loops, currency exchange
    stabilisation and market order matching. Everything is deliberately
    hand-placed and small enough to read every number in the UI.
    """
    world = World(seed)
    world.scenario_name = "Sandbox: Twin Reach"

    _add_faction(world, "TERRA", "Terran Compact", (92, 160, 232),
                 "TCR", "Terran Credit", "T$", player=True)
    _add_faction(world, "VEGA", "Vegan Syndicate", (232, 136, 92),
                 "VSD", "Syndicate Dinar", "V$")
    _add_faction(world, "FREE", "Free Worlds", (150, 150, 158),
                 "FWM", "Free Mark", "F$",
                 treasury=config.FACTION_TREASURY_START // 4)
    _add_faction(world, World.PIRATE_KEY, "Corsairs", (196, 72, 96),
                 "BLK", "Black Script", "B$",
                 treasury=config.PIRATE_CASH_START)

    coords = list(hex_spiral(1))        # 7 sectors: centre + one ring
    names = ["Verge Station", "Terra Prime", "Terra Forge", "Terra Fields",
             "Vega Prime", "Vega Foundry", "Vega Shoal"]
    owners = ["FREE", "TERRA", "TERRA", "TERRA", "VEGA", "VEGA", "VEGA"]

    sectors = {}
    for coord, name, owner in zip(coords, names, owners):
        sectors[name] = _add_sector(world, coord, name, owner)

    hub = sectors["Verge Station"]

    # --- Terran industry: a complete chain -----------------------------
    tp, tf, tfi = (sectors["Terra Prime"], sectors["Terra Forge"],
                   sectors["Terra Fields"])
    _add_nodes(world, tp, ["ORE", "GAS"])
    _add_nodes(world, tf, ["ORE"])
    _add_nodes(world, tfi, ["ICE", "GAS"])

    _add_pop(world, tp, 9_000)
    _add_pop(world, tf, 7_000)
    _add_pop(world, tfi, 6_000)

    # Levels are sized so each stage can roughly feed the next; the markets
    # and the autonomous industrial AI take it from there.
    _add_station(world, tp, "ore_mine", "TERRA", level=2)
    _add_station(world, tp, "gas_collector", "TERRA", level=1)
    _add_station(world, tp, "smelter", "TERRA", level=3, loan=True)
    _add_station(world, tf, "ore_mine", "TERRA", level=1)
    _add_station(world, tf, "smelter", "TERRA", level=2)
    _add_station(world, tf, "hull_works", "TERRA", level=2)
    _add_station(world, tf, "electronics_fab", "TERRA", level=2, loan=True)
    _add_station(world, tfi, "ice_harvester", "TERRA", level=3)
    _add_station(world, tfi, "gas_collector", "TERRA", level=1)
    _add_station(world, tfi, "chem_plant", "TERRA", level=2)
    _add_station(world, tfi, "hydro_farm", "TERRA", level=3)
    _add_station(world, tf, "goods_factory", "TERRA", level=2)
    _add_station(world, tp, "fuel_refinery", "TERRA", level=1)
    _add_station(world, tf, "cargo_yard", "TERRA", level=1)
    _add_station(world, tp, "mil_yard", "TERRA", level=1, loan=True)

    # --- Vegan industry: deliberately lopsided so trade must happen -----
    vp, vf, vs = (sectors["Vega Prime"], sectors["Vega Foundry"],
                  sectors["Vega Shoal"])
    _add_nodes(world, vp, ["GAS", "ICE"])
    _add_nodes(world, vf, ["ORE"], capacity=config.NODE_DEFAULT_CAPACITY * 2)
    _add_nodes(world, vs, ["ICE"])

    _add_pop(world, vp, 8_000)
    _add_pop(world, vf, 7_500)
    _add_pop(world, vs, 5_000)

    _add_station(world, vp, "gas_collector", "VEGA", level=2)
    _add_station(world, vp, "ice_harvester", "VEGA", level=1)
    _add_station(world, vp, "fuel_refinery", "VEGA", level=2, loan=True)
    _add_station(world, vp, "chem_plant", "VEGA", level=3)
    _add_station(world, vf, "ore_mine", "VEGA", level=3)
    _add_station(world, vf, "smelter", "VEGA", level=3)
    _add_station(world, vf, "hull_works", "VEGA", level=2)
    _add_station(world, vf, "mil_yard", "VEGA", level=1, loan=True)
    _add_station(world, vs, "ice_harvester", "VEGA", level=3)
    _add_station(world, vs, "hydro_farm", "VEGA", level=3)
    _add_station(world, vs, "goods_factory", "VEGA", level=2)
    _add_station(world, vp, "electronics_fab", "VEGA", level=2)
    _add_station(world, vf, "cargo_yard", "VEGA", level=1)

    # --- the neutral hub: pops, a market, and the smugglers -------------
    _add_nodes(world, hub, ["ORE", "ICE"])
    _add_pop(world, hub, 4_500)
    _add_station(world, hub, "hydro_farm", "FREE", level=2)
    _add_station(world, hub, "ice_harvester", "FREE", level=1)
    _add_station(world, hub, "ore_mine", "FREE", level=1)

    # --- merchants ------------------------------------------------------
    _add_firm(world, hub, "FREE")
    _add_firm(world, tp, "TERRA")
    _add_firm(world, vf, "VEGA")

    # --- militaries -----------------------------------------------------
    _add_base(world, tp, "TERRA", fleets=2, strength=70)
    _add_base(world, vf, "VEGA", fleets=2, strength=70)

    # --- piracy ---------------------------------------------------------
    _add_pirates(world, hub)

    _balance_scenario(world)
    _seed_relations(world)
    _seed_prices(world)
    world.log("Sandbox initialised: 7 sectors, 2 powers, 1 free hub.")
    return world


# ---------------------------------------------------------------------------
# SCENARIO 2 -- the full galaxy
# ---------------------------------------------------------------------------

GALAXY_POWERS = [
    ("TERRA", "Terran Compact",   (92, 160, 232), "TCR", "Terran Credit", "T$"),
    ("VEGA",  "Vegan Syndicate",  (232, 136, 92), "VSD", "Syndicate Dinar", "V$"),
    ("ORION", "Orion Hegemony",   (126, 206, 132), "OHS", "Hegemony Scrip", "O$"),
    ("CYGNI", "Cygni Directorate", (198, 150, 238), "CYD", "Directorate Unit", "C$"),
    ("RIGEL", "Rigel Combine",    (228, 206, 108), "RGC", "Combine Note", "R$"),
]


def build_galaxy(seed=None) -> World:
    """A massive multi-faction galactic map with complex trade networks and
    geopolitical tension (doc 6)."""
    world = World(seed)
    world.scenario_name = "Campaign: The Contested Arm"
    rng = world.rng

    for i, (key, name, color, code, cname, sym) in enumerate(GALAXY_POWERS):
        _add_faction(world, key, name, color, code, cname, sym,
                     player=(i == 0))
    _add_faction(world, "FREE", "Free Worlds", (150, 150, 158),
                 "FWM", "Free Mark", "F$",
                 treasury=config.FACTION_TREASURY_START // 2)
    _add_faction(world, World.PIRATE_KEY, "Corsairs", (196, 72, 96),
                 "BLK", "Black Script", "B$",
                 treasury=config.PIRATE_CASH_START * 3)

    radius = config.GALAXY_MAP_RADIUS
    coords = list(hex_spiral(radius))

    # Capitals are spread around the rim so every power has a home region.
    capitals = {}
    rim = [c for c in coords if hex_distance(c, (0, 0)) == radius - 1]
    step = max(1, len(rim) // len(GALAXY_POWERS))
    for i, (key, *_rest) in enumerate(GALAXY_POWERS):
        capitals[key] = rim[(i * step) % len(rim)]

    # Assign each sector to its nearest capital; the deep core stays free.
    used_names = set()
    for coord in coords:
        best_key, best_d = "FREE", 99
        for key, cap in capitals.items():
            d = hex_distance(coord, cap)
            if d < best_d:
                best_key, best_d = key, d
        if hex_distance(coord, (0, 0)) <= 1:
            best_key = "FREE"          # neutral core: the galactic crossroads
        elif best_d > radius - 1:
            best_key = "FREE"

        name = _make_name(rng, used_names)
        sector = _add_sector(world, coord, name, best_key)

        # Procedural resource nodes with varied richness.
        kinds = []
        for kind, chance in (("ORE", 55), ("GAS", 45), ("ICE", 50)):
            if rng.randint(1, 100) <= chance:
                kinds.append(kind)
        if not kinds:
            kinds.append(rng.choice(["ORE", "GAS", "ICE"]))
        for kind in kinds:
            cap = int(config.NODE_DEFAULT_CAPACITY *
                      rng.uniform(0.45, 2.2))
            sector.add_node(kind, cap)

        # Tax policy varies by regime, giving traders real tariff arbitrage.
        sector.set_tax(rng.randint(900, 2_200))
        sector.set_tariff(rng.randint(150, 900))
        sector.set_property_tax(rng.randint(600, 1_600))

    # --- populate and industrialise -------------------------------------
    for sector in world.galaxy:
        is_capital = sector.coord in capitals.values()
        pops = 2 if is_capital else (1 if rng.randint(1, 100) <= 78 else 0)
        for _ in range(pops):
            _add_pop(world, sector, rng.randint(3_500, 11_000)
                     if not is_capital else rng.randint(9_000, 15_000))

        if sector.owner == "FREE" and rng.randint(1, 100) > 55:
            continue

        # Extractors follow the geology; processors cluster at capitals.
        for kind in sector.nodes:
            method = {"ORE": "ore_mine", "GAS": "gas_collector",
                      "ICE": "ice_harvester"}[kind]
            if rng.randint(1, 100) <= (92 if is_capital else 64):
                _add_station(world, sector, method, sector.owner,
                             level=rng.randint(1, 3) if is_capital else 1)

        processors = ["smelter", "fuel_refinery", "chem_plant", "hydro_farm",
                      "goods_factory"]
        advanced = ["electronics_fab", "hull_works"]
        yards = ["cargo_yard", "mil_yard"]

        count = 4 if is_capital else rng.randint(0, 2)
        for _ in range(count):
            _add_station(world, sector, rng.choice(processors), sector.owner,
                         level=rng.randint(1, 2),
                         loan=rng.randint(1, 100) <= 30)
        if is_capital:
            for m in advanced:
                _add_station(world, sector, m, sector.owner, level=2, loan=True)
            for m in yards:
                _add_station(world, sector, m, sector.owner, level=1)
        elif rng.randint(1, 100) <= 22:
            _add_station(world, sector, rng.choice(advanced), sector.owner,
                         level=1, loan=True)

        # A merchant presence almost everywhere. Markets are per-sector, so
        # the trade network IS the galaxy's circulatory system -- too few firms
        # and specialised sectors simply starve beside full warehouses.
        if rng.randint(1, 100) <= (100 if is_capital else 70):
            _add_firm(world, sector, sector.owner)

    # --- militaries at the capitals and a few border garrisons ----------
    for key, cap in capitals.items():
        sector = world.galaxy.get(cap)
        _add_base(world, sector, key, fleets=3, strength=rng.randint(70, 110))
    for sector in world.galaxy:
        if sector.owner not in capitals or sector.coord in capitals.values():
            continue
        if rng.randint(1, 100) <= 14:
            _add_base(world, sector, sector.owner, fleets=1,
                      strength=rng.randint(40, 70))

    # --- pirate hideouts in the lawless deep ----------------------------
    free_sectors = [s for s in world.galaxy if s.owner == "FREE"]
    rng.shuffle(free_sectors)
    for sector in free_sectors[:max(2, len(free_sectors) // 4)]:
        _add_pirates(world, sector)

    _balance_scenario(world)
    _seed_relations(world)
    _seed_prices(world)

    # Geopolitical tension from turn one: one live war, one cold front.
    keys = [k for k, *_ in GALAXY_POWERS]
    from . import diplomacy
    diplomacy.declare_war(world, keys[1], keys[2])
    world.factions[keys[0]].adjust_opinion(keys[3], -4_500)
    world.factions[keys[3]].adjust_opinion(keys[0], -4_500)

    world.log(f"Campaign initialised: {len(world.galaxy)} sectors, "
              f"{len(GALAXY_POWERS)} powers, {len(world.stations)} stations.")
    return world


def _balance_scenario(world):
    """Delegate to the scenario self-consistency solver (see balance.py)."""
    return balance_scenario(world, _add_station, _add_pop)


def _make_name(rng, used):
    for _ in range(400):
        name = f"{rng.choice(GREEK)} {rng.choice(SUFFIX)}"
        if name not in used:
            used.add(name)
            return name
    name = f"Sector {len(used) + 1}"
    used.add(name)
    return name


def _seed_relations(world):
    keys = [k for k in world.factions if not world.is_pirate(k)]
    for a in keys:
        fa = world.factions[a]
        for b in keys:
            if a != b:
                fa.set_stance(b, PEACE)
                fa.opinion[b] = 0
    # Everyone is permanently hostile to the Corsairs.
    for a in keys:
        world.factions[a].set_stance(world.pirate_key, WAR)
    world.factions[world.pirate_key].relations = {
        k: WAR for k in keys}


def _seed_prices(world):
    """Give every local hub a slightly different starting quote so arbitrage
    exists from tick one instead of having to be discovered from a flat
    surface."""
    rng = world.rng
    for sector in world.galaxy:
        for c in world.commodities:
            jitter = rng.uniform(0.82, 1.22)
            price = int(c.base_price * jitter)
            sector.market.price[c.key] = max(c.price_min,
                                             min(c.price_max, price))
        sector.market.prev_price = dict(sector.market.price)


SCENARIOS = {
    "sandbox": ("Sandbox: Twin Reach",
                "7 sectors, 2 powers, 1 free hub. Minimal entity count for "
                "verifying market matching, the consumer loop and FX.",
                build_sandbox),
    "galaxy": ("Campaign: The Contested Arm",
               "61 sectors, 5 powers, deep trade networks, live wars and "
               "pirate havens in the neutral core.",
               build_galaxy),
}


def build(name="sandbox", seed=None) -> World:
    return SCENARIOS[name][2](seed)

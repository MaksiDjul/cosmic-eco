"""
SCENARIO SELF-CONSISTENCY SOLVER.

Hand-writing station levels and pop counts that happen to form a closed,
solvent production chain is hopeless -- and getting it wrong does not produce a
mildly suboptimal economy, it produces total collapse. A single structural
deficit anywhere upstream cascades:

    short gas -> polymer plants starve -> electronics fabs starve -> no station
    can buy maintenance -> the whole industrial base falls into disrepair ->
    production halts -> demand for labour vanishes -> wages hit the floor ->
    the population starves to death.

So a scenario solves its own economy rather than trusting its author.

TWO CONSTRAINTS, TWO DIFFERENT SCOPES
-------------------------------------
The subtle part is that not every commodity balances at the same scale:

*   LABOUR is an Abstract Utility Unit (doc 4): non-storable, and therefore
    untradeable -- no merchant can haul it, because it evaporates at the end
    of the tick it was offered in. Labour must therefore balance SECTOR BY
    SECTOR. A sector whose industry draws more labour than its own citizens
    supply will bid its local wage to the ceiling, which makes every factory
    in it unprofitable regardless of how much slack the rest of the galaxy has.

*   FOOD is storable and tradeable, but markets are local and a sector that
    imports its entire food supply starves the instant a route is cut. That is
    a fine late-game crisis and a miserable opening position, so scenarios
    start every populated sector able to feed itself, and let trade handle
    surpluses and industrial goods.

*   Everything else balances GALAXY-WIDE, because merchant firms can move it.
    Sectors stay deliberately specialised so that inter-sector trade still has
    to carry the economy -- which is the entire point of the Market Hub and
    merchant firm design.

The solver iterates: sizing demographics to local labour demand adds food
demand, which adds farms, which adds labour demand, and so on to a fixed
point.
"""

from collections import defaultdict

from . import config
from .commodities import COMMODITIES, PRODUCTION_METHODS

# Which production method supplies each commodity.
SUPPLIER = {}
for _key, _m in PRODUCTION_METHODS.items():
    for _out in _m.outputs:
        SUPPLIER.setdefault(_out, _key)

MAX_STATIONS_PER_SECTOR = 18
# Slots the galaxy-wide filler may NOT consume, reserved for the two things a
# sector must be able to do for itself: feed its people and maintain its
# plants. Without this reservation the filler packed sectors to the cap in an
# early pass, and a sector left holding smelters but no ore mine could then
# never be given one -- so it made no metal, no plant in it could buy upkeep,
# every station decayed to zero condition and the whole region ran at its
# minimum efficiency floor forever.
ESSENTIAL_RESERVE_SLOTS = 6
# How strongly placement prefers a sector that already makes a method's inputs.
INPUT_COLOCATION_BONUS = 60_000
# Build the chain with slack rather than exactly to demand. Zero-slack
# capacity means any hiccup -- a throttled plant, a depleted node, an
# overclocking customer drawing 160% inputs -- turns straight into a shortage.
CAPACITY_HEADROOM_BP = 12_500
# Food gets more slack than anything else. Sized exactly to consumption it can
# never build the buffer households try to hold, so every market reads as
# permanently short, and any local maldistribution becomes a famine.
FOOD_HEADROOM_BP = 15_000
# Score penalty per station already in a candidate sector, so the solver
# spreads industry out instead of stacking one monstrous hub.
CROWDING_PENALTY = 4_000


def station_in(sector, method_key):
    for st in sector.stations:
        if st.method.key == method_key:
            return st
    return None


def sector_labor_demand(world, sector) -> int:
    demand = sum(st.method.inputs.get("LABOR", 0) * max(1, st.level)
                 for st in sector.stations)
    for b in world.bases:
        if b.sector is sector:
            demand += b.labor_need()
    return demand


def sector_food_capacity(sector) -> int:
    return sum(st.method.outputs.get("FOOD", 0) * max(1, st.level)
               for st in sector.stations)


def census(world):
    """Steady-state per-tick production and consumption of every commodity."""
    made = defaultdict(int)
    used = defaultdict(int)
    maint_bp = defaultdict(int)

    for st in world.stations:
        lvl = max(1, st.level)
        for k, v in st.method.outputs.items():
            made[k] += v * lvl
        for k, v in st.method.inputs.items():
            used[k] += v * lvl
        for k, per_bp in config.MAINTENANCE_PER_LEVEL_BP.items():
            maint_bp[k] += per_bp * lvl

    # Maintenance is a fractional per-level draw; aggregate in basis points and
    # only then reduce to whole units.
    for k, bp_total in maint_bp.items():
        used[k] += bp_total // config.BP

    # MAINT_GOODS consumption: every station consumes per level per tick.
    total_levels = sum(max(1, st.level) for st in world.stations)
    used["MAINT_GOODS"] += total_levels * config.MAINT_GOODS_PER_LEVEL

    for p in world.pops:
        made["LABOR"] += p.labor_output()
        for k, per in config.SUSTENANCE_NEEDS.items():
            qty = (p.size * per) // 1000
            if k == config.POP_LUXURY_KEY:
                # Households spend surplus income on consumer goods, so plan
                # the industry for prosperity, not just survival. Stored in
                # tenths to keep the loop gain below one (see below).
                qty = (qty * config.POP_LUXURY_PLANNING_MULT) // 10
            used[k] += qty

    for b in world.bases:
        used["LABOR"] += b.labor_need()
        # Bases rearm until they reach their strength target, so budget for
        # roughly half the maximum purchase rate in the steady state.
        used["MIL_UNIT"] += max(1, config.MILBASE_MIL_BUY_MAX_PER_TICK // 2)

    # Cargo assets are a stock rather than a flow: only attrition and new
    # firms create ongoing demand.
    used["CARGO_UNIT"] += len(world.firms)

    return made, used


def local_output(sector, key) -> int:
    """Units of ``key`` this sector can produce for itself each tick."""
    return sum(st.method.outputs.get(key, 0) * max(1, st.level)
               for st in sector.stations)


def host_sector_for(world, method_key, deficit_key):
    """Choose where to add capacity.

    Scored on the shortage being felt locally, biased against crowded sectors,
    and -- importantly -- strongly biased toward sectors that already produce
    the method's OWN inputs.

    That last term matters because markets are local and the merchant fleet is
    finite. Placing a polymer plant in a sector with no gas supply only looks
    sensible on a galaxy-wide balance sheet; in play it sits idle waiting for a
    trade route that never has the capacity to feed it, while its inputs pile
    up unsold three jumps away. Industry clusters near its feedstock for the
    same reason real industry does.
    """
    method = PRODUCTION_METHODS[method_key]
    best, best_score = None, None
    for sector in world.galaxy:
        if sector.owner == world.pirate_key:
            continue
        if len(sector.stations) >= MAX_STATIONS_PER_SECTOR - ESSENTIAL_RESERVE_SLOTS:
            continue
        score = sector.market.spot(deficit_key)
        score -= CROWDING_PENALTY * len(sector.stations)
        if method.extracts:
            node = sector.node(method.extracts)
            if node is None:
                continue
            score += node.remaining // 1000      # richer deposits host better
        for key, per in method.inputs.items():
            if key == "LABOR":
                continue
            have = local_output(sector, key)
            if have >= per:
                score += INPUT_COLOCATION_BONUS
            else:
                score -= INPUT_COLOCATION_BONUS
        existing = station_in(sector, method_key)
        if existing is not None and existing.level < config.STATION_MAX_LEVEL:
            score += 20_000                      # upgrading beats founding
        if best_score is None or score > best_score:
            best, best_score = sector, score
    return best


def raise_capacity(world, add_station, sector, method_key, output_key,
                   needed) -> bool:
    """Make ``sector`` able to produce ``needed`` units/tick locally.

    A single plant caps out at STATION_MAX_LEVEL, so a populous sector needs
    several of them -- this keeps adding plants until the requirement is met or
    the sector is physically full.
    """
    method = PRODUCTION_METHODS[method_key]
    per_level = method.outputs.get(output_key, 0)
    if per_level <= 0 or needed <= 0:
        return False

    want_levels = max(1, -(-needed // per_level))
    have_levels = sum(max(1, st.level) for st in sector.stations
                      if st.method.key == method_key)
    if have_levels >= want_levels:
        return False
    if method.extracts and sector.node(method.extracts) is None:
        sector.add_node(method.extracts)

    changed = False
    guard = 0
    while have_levels < want_levels and guard < 40:
        guard += 1
        plant = None
        for st in sector.stations:
            if st.method.key == method_key and st.level < config.STATION_MAX_LEVEL:
                plant = st
                break
        if plant is not None:
            plant._apply_level_up()
            plant.seed_stock()
        elif len(sector.stations) < MAX_STATIONS_PER_SECTOR:
            add_station(world, sector, method_key, sector.owner, level=1)
        else:
            break                     # sector is full; trade will have to cover
        have_levels += 1
        changed = True
    return changed


def add_capacity(world, add_station, deficit_key, deficit) -> bool:
    """Close a galaxy-wide structural shortfall in one commodity."""
    method_key = SUPPLIER.get(deficit_key)
    if method_key is None:
        return False
    method = PRODUCTION_METHODS[method_key]
    per_level = method.outputs.get(deficit_key, 0)
    if per_level <= 0:
        return False

    remaining = max(1, -(-deficit // per_level))
    changed = False

    # PASS 1 -- deepen plants that already exist, richest market first.
    #
    # This ordering matters more than it appears. Site selection penalises
    # crowded sectors, which is right for spreading industry around but wrong
    # as the primary growth mechanism: it founded a level-1 plant in every
    # sector in the galaxy, filled all of them to their station cap, and then
    # had nowhere left to build -- leaving dozens of level-1 factories and a
    # structural shortfall it could no longer close. Raising an existing plant
    # costs no site at all, so capacity goes deep before it goes wide.
    existing = [st for st in world.stations
                if st.method.key == method_key
                and st.level < config.STATION_MAX_LEVEL
                and st.sector.owner != world.pirate_key]
    existing.sort(key=lambda st: -st.sector.market.spot(deficit_key))
    for st in existing:
        if remaining <= 0:
            break
        grew = False
        while remaining > 0 and st.level < config.STATION_MAX_LEVEL:
            st._apply_level_up()
            remaining -= 1
            grew = True
        if grew:
            st.seed_stock()
            changed = True

    # PASS 2 -- open new sites for whatever is still missing.
    guard = 0
    while remaining > 0 and guard < 120:
        guard += 1
        sector = host_sector_for(world, method_key, deficit_key)
        if sector is None:
            break
        if method.extracts and sector.node(method.extracts) is None:
            sector.add_node(method.extracts)
        level = min(remaining, config.STATION_MAX_LEVEL)
        add_station(world, sector, method_key, sector.owner, level=level)
        remaining -= level
        changed = True
    return changed


def balance_scenario(world, add_station, add_pop, labor_slack_bp=11_500,
                     iterations=30):
    """Solve the scenario's economy to a fixed point.

    THE LOOP GAIN MUST STAY BELOW ONE. Sizing population to industry's labour
    demand means every new plant adds workers, and those workers need food and
    consumer goods, which needs more plants, which needs more workers. Summing
    that chain through the whole recipe tree, one unit of labour demand induces
    roughly 0.75 units of further labour demand at these settings -- so the
    series converges to about 4x the seed industry.

    With a 40% labour slack margin and consumer goods planned at 2x
    subsistence, the same sum came to ~1.06: supercritical. The solver then
    grew the economy without bound until it ran out of building sites, and
    stopped with a permanent, unfixable consumer-goods deficit baked in --
    a seven-sector sandbox carrying two hundred thousand citizens it could
    never feed. The slack and planning multipliers above are chosen to keep
    that sum comfortably under one.
    """
    """Iterate census-and-fix until the scenario's economy closes."""
    food_per_1000 = config.SUSTENANCE_NEEDS.get("FOOD", 0)
    ice_per_farm = PRODUCTION_METHODS["hydro_farm"].inputs.get("ICE", 0)

    for _ in range(iterations):
        changed = False

        # --- A. LOCAL labour: size each sector's demographics -----------
        for sector in world.galaxy:
            demand = sector_labor_demand(world, sector)
            if demand <= 0:
                continue
            target = (demand * 1000 * labor_slack_bp) // (
                config.POP_LABOR_PER_1000 * config.BP)
            if not sector.pops:
                add_pop(world, sector, max(config.POP_MIN_SIZE, target))
                changed = True
                continue
            weights = [max(1, p.size) for p in sector.pops]
            total = sum(weights)
            for p, w in zip(sector.pops, weights):
                p.size = max(config.POP_MIN_SIZE, (target * w) // total)
                p._resize_storage()

        # --- B. LOCAL food self-sufficiency -----------------------------
        for sector in world.galaxy:
            pop = sector.population
            if pop <= 0 or food_per_1000 <= 0:
                continue
            need = (pop * food_per_1000 * FOOD_HEADROOM_BP) // (
                1000 * config.BP)
            if need <= 0 or sector_food_capacity(sector) >= need:
                continue
            if raise_capacity(world, add_station, sector, "hydro_farm",
                              "FOOD", need):
                changed = True
            farm_levels = sum(max(1, st.level) for st in sector.stations
                              if st.method.key == "hydro_farm")
            if raise_capacity(world, add_station, sector, "ice_harvester",
                              "ICE", ice_per_farm * farm_levels):
                changed = True

        # --- B1b. LOCAL consumer-goods capacity -------------------------
        # Households live in every sector, so consumer goods are demanded
        # everywhere -- but they are bulky and the merchant fleet is finite,
        # so concentrating the industry in a few clusters leaves most sectors
        # with nothing for their citizens to buy. Those households then bank
        # their wages instead of spending them, and the money drains out of
        # circulation (see config.POP_LUXURY_*). Every populated sector gets
        # at least its own subsistence capacity; trade supplies the luxuries.
        lux_key = config.POP_LUXURY_KEY
        lux_per_1000 = config.SUSTENANCE_NEEDS.get(lux_key, 0)
        if lux_per_1000 > 0:
            for sector in world.galaxy:
                pop = sector.population
                if pop <= 0:
                    continue
                need = (pop * lux_per_1000) // 1000
                if need <= 0:
                    continue
                have = local_output(sector, lux_key)
                if have >= need:
                    continue
                if raise_capacity(world, add_station, sector, "goods_factory",
                                  lux_key, need):
                    changed = True

        # --- B2. LOCAL upkeep self-sufficiency --------------------------
        # Maintenance keeps a station alive, so like food it must not hang on
        # a trade route holding up. Every sector that hosts industry gets the
        # smelting (and the ore feeding it) to cover its own upkeep bill.
        for sector in world.galaxy:
            if not sector.stations:
                continue
            upkeep_bp = sum(per_bp * max(1, st.level)
                            for st in sector.stations
                            for key, per_bp in config.MAINTENANCE_PER_LEVEL_BP.items())
            metal_need = -(-upkeep_bp // config.BP)
            metal_need = (metal_need * CAPACITY_HEADROOM_BP) // config.BP
            if metal_need <= 0:
                continue
            if raise_capacity(world, add_station, sector, "smelter",
                              "METAL", metal_need):
                changed = True
            smelter_levels = sum(max(1, st.level) for st in sector.stations
                                 if st.method.key == "smelter")
            ore_need = PRODUCTION_METHODS["smelter"].inputs.get("ORE", 0) *                 smelter_levels
            if ore_need > 0 and raise_capacity(world, add_station, sector,
                                               "ore_mine", "ORE", ore_need):
                changed = True

        # --- B2b. LOCAL MAINT_GOODS self-sufficiency ----------------------
        for sector in world.galaxy:
            if not sector.stations:
                continue
            total_levels = sum(max(1, st.level) for st in sector.stations)
            mg_need = total_levels * config.MAINT_GOODS_PER_LEVEL
            mg_need = (mg_need * CAPACITY_HEADROOM_BP) // config.BP
            if mg_need <= 0:
                continue
            mg_have = local_output(sector, "MAINT_GOODS")
            if mg_have >= mg_need:
                continue
            if raise_capacity(world, add_station, sector, "maint_workshop",
                              "MAINT_GOODS", mg_need):
                changed = True

        # --- B3. HABITATION for every populated sector --------------------
        for sector in world.galaxy:
            pop = sector.population
            if pop <= 0:
                continue
            hab_cap = sum(max(1, st.level) * config.HABITATION_POP_PER_LEVEL
                          for st in sector.stations
                          if st.method.key == config.HABITATION_METHOD_KEY)
            if hab_cap >= pop:
                continue
            levels_need = -(-pop // config.HABITATION_POP_PER_LEVEL)
            if raise_capacity(world, add_station, sector, "habitation",
                              "LABOR", levels_need):
                changed = True

        # --- C. GALAXY-WIDE balance for everything tradeable ------------
        made, used = census(world)
        for c in COMMODITIES:
            # FOOD was already handled locally above, but if a sector was
            # too full to feed itself the galaxy-wide pass still has to close
            # the gap somewhere so trade can carry it.
            if c.key == "LABOR" or not c.storable:
                continue
            headroom = (FOOD_HEADROOM_BP if c.key == "FOOD"
                        else CAPACITY_HEADROOM_BP)
            want = (used[c.key] * headroom) // config.BP
            deficit = want - made[c.key]
            if deficit > 0 and add_capacity(world, add_station, c.key, deficit):
                changed = True

        if not changed:
            break

    reconcile_population(world)
    return report(world)


def reconcile_population(world):
    """Final safety net: size the population to the industry that actually
    got built.

    A map can simply run out of room -- every sector at its station cap, every
    plant at max level -- leaving the solver unable to close a deficit. Left
    alone that becomes a permanent famine the player can do nothing about. It
    is far better to start with a smaller population and some idle industrial
    capacity than with a large one and a structural food or goods shortfall.
    """
    made, _ = census(world)
    ceilings = []
    for key, per_1000 in config.SUSTENANCE_NEEDS.items():
        if per_1000 <= 0:
            continue
        need = per_1000
        if key == config.POP_LUXURY_KEY:
            need = (need * config.POP_LUXURY_PLANNING_MULT) // 10
        if need > 0:
            ceilings.append((made[key] * 1000) // need)
    if not ceilings:
        return
    ceiling = min(ceilings)
    current = sum(p.size for p in world.pops)
    if current <= ceiling or ceiling <= 0:
        return
    for p in world.pops:
        p.size = max(config.POP_MIN_SIZE, (p.size * ceiling) // current)
        p._resize_storage()
    world.log(f"Population reconciled to installed capacity: "
              f"{sum(p.size for p in world.pops):,} citizens.")


def report(world):
    made, used = census(world)
    shortfalls = {c.key: used[c.key] - made[c.key] for c in COMMODITIES
                  if used[c.key] > made[c.key]}

    # Per-sector labour check -- the constraint that actually bites.
    tight = []
    for sector in world.galaxy:
        demand = sector_labor_demand(world, sector)
        supply = sector.labor_supply
        if demand > supply:
            tight.append(f"{sector.name}({demand}>{supply})")

    world.log(f"Scenario balanced: {sum(p.size for p in world.pops):,} "
              f"citizens, {len(world.stations)} stations" +
              (f"; shortfalls {shortfalls}" if shortfalls else "") +
              (f"; labour-tight {tight}" if tight else ""))
    return made, used

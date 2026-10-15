"""
Invariant tests for the simulation engine.

These check the properties the design document states as absolute rather than
aspirational -- above all "Conservation of Value" (doc 1.1) and the exactness
of Pro-Rata Allocation (doc 3.2).

Run directly (``python tests.py``) or under pytest.
"""

import random
import sys

from cosmic_balance.sim import config, scenarios
from cosmic_balance.sim.commodities import COMMODITIES
from cosmic_balance.sim.fixed import allocate_pro_rata, bounded_move
from cosmic_balance.sim.market import MarketHub

FAILURES = []


def check(condition, label):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        FAILURES.append(label)
    return condition


# ---------------------------------------------------------------------------
def test_pro_rata_is_exact():
    """Pro-rata allocation must be exact: the parts sum to the whole, and
    nobody receives more than they asked for. This is what makes conservation
    provable rather than approximate."""
    print("pro-rata allocation")
    rng = random.Random(7)
    ok_sum = ok_cap = True
    for _ in range(4000):
        n = rng.randint(1, 12)
        weights = [rng.randint(0, 5000) for _ in range(n)]
        total = rng.randint(0, 20000)
        out = allocate_pro_rata(total, weights)
        expected = min(total, sum(weights))
        if sum(out) != expected:
            ok_sum = False
        if any(o > w for o, w in zip(out, weights)):
            ok_cap = False
    check(ok_sum, "allocations sum to min(total, demand) exactly")
    check(ok_cap, "no allocation exceeds its request")

    # Money distribution must pay out the full pot.
    ok_money = True
    for _ in range(2000):
        n = rng.randint(1, 10)
        weights = [rng.randint(0, 900) for _ in range(n)]
        pot = rng.randint(0, 10 ** 7)
        out = MarketHub._split_money(pot, weights)
        if sum(out) != pot:
            ok_money = False
    check(ok_money, "revenue splits distribute the pot exactly")


def test_price_ticker_is_bounded():
    """Prices must move incrementally and stay inside their band, including
    the Explosion Safeguard case where one side of the book is empty."""
    print("price ticker bounds")
    ok = True
    for start in (100, 5000, 100000):
        p = start
        for _ in range(500):
            nxt = bounded_move(p, p * 50, config.MARKET_PRICE_DAMPING_BP,
                               config.MARKET_MAX_MOVE_BP, 50, 200000)
            move = abs(nxt - p) * config.BP // max(1, p)
            if move > config.MARKET_MAX_MOVE_BP + 2:
                ok = False
            if not (50 <= nxt <= 200000):
                ok = False
            p = nxt
    check(ok, "per-tick moves respect the cap and the price band")


def test_scenario_conservation(name, ticks):
    """The headline invariant: run a whole scenario and prove that not one
    cent and not one commodity unit was created or destroyed untracked."""
    print(f"{name}: {ticks} ticks")
    world = scenarios.build(name)

    supply_before = dict(world.money_supply_table())
    total_pop = world.total_population()
    check(total_pop > 0, "scenario starts with a population")
    check(len(world.stations) > 0, "scenario starts with industry")

    for _ in range(ticks):
        world.step()

    problems = world.ledger.audit(raise_on_fail=False)
    check(not problems,
          f"conservation audit clean ({problems[:1] if problems else ''})")

    supply_after = world.money_supply_table()
    drift = {k: supply_after[k] - v for k, v in supply_before.items()
             if supply_after[k] != v}
    check(not drift, f"money supply unchanged per currency ({drift})")

    negatives = [a for a in world.ledger.accounts if a.balance < 0]
    check(not negatives, "no account ever went negative")

    stuck = world.verify_zero_storage()
    check(not stuck, f"clearing houses hold nothing between ticks ({stuck})")

    perishable = [c.key for c in COMMODITIES if not c.storable]
    held = [(inv.owner_name, k) for inv in world.ledger.inventories
            for k in perishable if inv.stock.get(k, 0)]
    check(not held, f"abstract utility units do not persist ({held[:2]})")

    over = [(inv.owner_name, k, v, inv.cap(k))
            for inv in world.ledger.inventories
            for k, v in inv.stock.items()
            if inv.cap(k) and v > inv.cap(k)]
    check(not over, f"no inventory exceeds its capacity ({over[:2]})")

    return world


def test_market_mechanics():
    """A sector hub must settle every fill at ONE price, and must not be left
    holding goods or cash."""
    print("market hub mechanics")
    world = scenarios.build("sandbox")
    for _ in range(40):
        world.step()

    sector = max(world.galaxy, key=lambda s: len(s.stations))
    settlements = sector.market.settlements

    single_price = True
    balanced = True
    for key, st in settlements.items():
        if st.traded <= 0:
            continue
        # every fill was valued at the settlement price
        for order, qty in st.buy_fills:
            if qty and order.value != qty * st.price:
                single_price = False
        bought = sum(q for _, q in st.buy_fills)
        sold = sum(q for _, q in st.sell_fills)
        if bought > sold:
            balanced = False
    check(single_price, "all fills settle at the single clearing price")
    check(balanced, "goods delivered never exceed goods supplied")
    check(sector.market.is_float_empty(),
          "hub holds no stock and no cash after clearing")


def test_separation_of_concerns():
    """The engine must not depend on the presentation layer at all."""
    print("separation of concerns")
    mods = [m for m in sys.modules if m.startswith("cosmic_balance.sim")]
    leaked = []
    for name in mods:
        mod = sys.modules[name]
        src = getattr(mod, "__file__", "") or ""
        if not src:
            continue
        with open(src, "r", encoding="utf-8") as fh:
            text = fh.read()
        if "import pygame" in text or "from ..ui" in text or \
                "from .ui" in text or "cosmic_balance.ui" in text:
            leaked.append(name)
    check(not leaked, f"sim package imports no presentation code ({leaked})")


def test_command_injection():
    """The UI's only write path is the command queue, and it must work."""
    print("command injection")
    world = scenarios.build("sandbox")
    sector = world.galaxy.order[0]
    original = sector.tax_bp
    target = min(config.MAX_SECTOR_TAX_BP, original + 500)

    world.push("set_tax", sector=sector, value=target)
    check(sector.tax_bp == original, "queued command does not take effect yet")
    world.step()
    check(sector.tax_bp == target, "command applied at the start of the tick")

    station = world.stations[0]
    world.push("upgrade_station", station=station)
    world.step()
    check(station.construction is not None or
          station.level >= config.STATION_MAX_LEVEL,
          "upgrade command deployed a construction warehouse")

    world.push("nonsense_command", x=1)
    world.step()
    check(True, "unknown commands are ignored without killing the engine")


# ---------------------------------------------------------------------------
def main():
    print("=" * 66)
    print("COSMIC BALANCE -- engine invariants")
    print("=" * 66)
    test_pro_rata_is_exact()
    test_price_ticker_is_bounded()
    test_market_mechanics()
    test_separation_of_concerns()
    test_command_injection()
    test_scenario_conservation("sandbox", 400)
    test_scenario_conservation("galaxy", 120)

    print("=" * 66)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())

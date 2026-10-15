"""
Headless simulation harness -- runs the engine with no presentation layer.

This is both a balancing tool and the proof of doc 1.1's separation of
concerns: the entire simulation runs, and can be audited, without pygame ever
being imported.

Usage:
    python headless.py [sandbox|galaxy] [ticks]
"""

import sys
import time

from cosmic_balance.sim import config, scenarios
from cosmic_balance.sim.commodities import COMMODITIES
from cosmic_balance.sim.fixed import money_short, bp_str


def run(scenario="sandbox", ticks=400, verbose=True):
    t0 = time.perf_counter()
    world = scenarios.build(scenario)
    build_time = time.perf_counter() - t0

    print(f"=== {world.scenario_name} ===")
    print(f"built in {build_time * 1000:.0f} ms")
    print(f"sectors={len(world.galaxy)} stations={len(world.stations)} "
          f"pops={len(world.pops)} firms={len(world.firms)} "
          f"bases={len(world.bases)} fleets={len(world.fleets)} "
          f"pirate_bases={len(world.pirate_bases)}")

    supply0 = dict(world.money_supply_table())
    print("\nmoney supply at init (hard cap, never changes again):")
    for code, amount in supply0.items():
        print(f"  {code}: {money_short(amount)}")

    # Baseline audit before anything moves.
    problems = world.ledger.audit(raise_on_fail=False)
    print(f"\ninitial audit: {'CLEAN' if not problems else problems}")

    t0 = time.perf_counter()
    for i in range(ticks):
        world.step()
        if verbose and (i + 1) % max(1, ticks // 8) == 0:
            _snapshot(world)
    elapsed = time.perf_counter() - t0

    print(f"\n--- ran {ticks} ticks in {elapsed:.2f}s "
          f"({elapsed / ticks * 1000:.1f} ms/tick) ---")

    # ------------------------------------------------------------------
    # Conservation proof
    # ------------------------------------------------------------------
    print("\n=== CONSERVATION AUDIT ===")
    problems = world.ledger.audit(raise_on_fail=False)
    if problems:
        print("FAILED:")
        for p in problems:
            print("  " + p)
    else:
        print("PASSED: every cent and every commodity unit accounted for.")

    supply1 = world.money_supply_table()
    print("\nmoney supply drift (must be exactly zero):")
    for code in supply0:
        drift = supply1[code] - supply0[code]
        print(f"  {code}: {drift:+d}")

    stuck = world.verify_zero_storage()
    print(f"\nzero-storage clearing: "
          f"{'all hubs empty' if not stuck else 'RESIDUE IN ' + str(stuck)}")

    _final_report(world)
    return world


def _snapshot(world):
    s = world.galaxy.order[0]
    pops = world.total_population()
    print(f"  t={world.tick:5d}  pop={pops:>9,}  "
          f"stations={len(world.stations):3d}  "
          f"routes={sum(len(f.routes) for f in world.firms):3d}  "
          f"{s.name}: FOOD={s.market.spot('FOOD'):6d} "
          f"LABOR={s.market.spot('LABOR'):5d} "
          f"METAL={s.market.spot('METAL'):6d}  "
          f"piracy={bp_str(s.piracy_bp)}")


def _final_report(world):
    print("\n=== FACTIONS ===")
    for key, fac in world.factions.items():
        sectors = len(world.faction_sectors(key))
        print(f"  {fac.name:<20} treasury={money_short(fac.treasury):>12} "
              f"sectors={sectors:3d} pop={world.faction_population(key):>9,} "
              f"mil={world.faction_strength(key):>6} "
              f"exh={bp_str(fac.exhaustion)} "
              f"fx={fac.currency.value / config.FX_RATE_SCALE:.3f}")

    print("\n=== MARKET (first sector) ===")
    s = world.galaxy.order[0]
    print(f"  {s.name}  owner={s.owner}  currency={s.market.currency}")
    print(f"  {'commodity':<14}{'price':>9}{'demand':>9}{'supply':>9}"
          f"{'traded':>9}")
    for c in COMMODITIES:
        m = s.market
        print(f"  {c.key:<14}{m.spot(c.key):>9}{m.last_demand[c.key]:>9}"
              f"{m.last_supply[c.key]:>9}{m.last_traded[c.key]:>9}")

    print("\n=== STATIONS (sample) ===")
    rows = sorted(world.stations, key=lambda x: -x.profit_total)[:10]
    for st in rows:
        print(f"  {st.name:<12} L{st.level} {st.sector.name:<16} "
              f"{st.status_text():<16} "
              f"cash={money_short(st.wallet.cash()):>11} "
              f"profit/t={money_short(st.profit_last):>10} "
              f"total={money_short(st.profit_total):>12}")

    halted = {}
    for st in world.stations:
        if not st.ran_last_tick:
            halted[st.halt_reason] = halted.get(st.halt_reason, 0) + 1
    print(f"\n  running: {sum(1 for s in world.stations if s.ran_last_tick)}"
          f"/{len(world.stations)}   halts: {halted}")

    print("\n=== POPULATION ===")
    starving = sum(1 for p in world.pops if p.status() == "starving")
    growing = sum(1 for p in world.pops if p.status() == "growing")
    print(f"  total={world.total_population():,}  pops={len(world.pops)}  "
          f"growing={growing}  starving={starving}")

    print("\n=== TRADE ===")
    routes = [(f, r) for f in world.firms for r in f.routes]
    print(f"  firms={len(world.firms)} active routes={len(routes)}")
    for f, r in routes[:8]:
        print(f"    {f.name:<10} {r.label():<40} "
              f"profit={money_short(r.profit_total):>12}")

    print("\n=== PIRACY ===")
    for pb in world.pirate_bases[:5]:
        print(f"  {pb.name} @ {pb.sector.name}: raiders={len(pb.raiders)} "
              f"stolen={pb.stolen_total} fenced={money_short(pb.fenced_total)}")

    print("\n=== RECENT EVENTS ===")
    for tick, text in world.events[-12:]:
        print(f"  t{tick}: {text}")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "sandbox"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    run(name, n)

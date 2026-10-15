"""Balancing diagnostic: where is the economy actually jamming?"""
import sys
from collections import defaultdict

from cosmic_balance.sim import config, scenarios
from cosmic_balance.sim.commodities import COMMODITIES
from cosmic_balance.sim.fixed import money_short, bp_str


def main(scenario="sandbox", ticks=120):
    world = scenarios.build(scenario)
    for _ in range(ticks):
        world.step()

    print(f"tick {world.tick}  pop {world.total_population():,}\n")

    # Global physical balance: production vs consumption per commodity.
    print(f"{'commodity':<13}{'made/t':>9}{'used/t':>9}{'stock':>10}"
          f"{'avgprice':>10}{'base':>8}{'ratio':>7}")
    made = defaultdict(int)
    used = defaultdict(int)
    for st in world.stations:
        lvl = max(1, st.level)
        for k, v in st.method.outputs.items():
            made[k] += v * lvl
        for k, v in st.method.inputs.items():
            used[k] += v * lvl
    for p in world.pops:
        made["LABOR"] += p.labor_output()
        for k, per in config.SUSTENANCE_NEEDS.items():
            used[k] += (p.size * per) // 1000

    totals = world.ledger.goods_totals()
    for c in COMMODITIES:
        prices = [s.market.spot(c.key) for s in world.galaxy]
        avg = sum(prices) // len(prices)
        ratio = avg / c.base_price
        print(f"{c.key:<13}{made[c.key]:>9}{used[c.key]:>9}"
              f"{totals.get(c.key, 0):>10}{avg:>10}{c.base_price:>8}"
              f"{ratio:>7.2f}")

    print("\n--- sectors ---")
    for s in world.galaxy:
        pop = s.population
        running = sum(1 for st in s.stations if st.ran_last_tick)
        print(f"  {s.name:<16} own={s.owner:<6} pop={pop:>7,} "
              f"st={len(s.stations):2d} run={running:2d} "
              f"food={s.market.spot('FOOD'):>7} labor={s.market.spot('LABOR'):>7} "
              f"pir={bp_str(s.piracy_bp)}")

    print("\n--- halts ---")
    halts = defaultdict(int)
    for st in world.stations:
        if not st.ran_last_tick:
            halts[st.halt_reason] += 1
    for k, v in sorted(halts.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")

    print("\n--- pops ---")
    for p in world.pops:
        print(f"  {p.name:<9} @{p.sector.name:<16} size={p.size:>7,} "
              f"sat={bp_str(p.satisfaction_bp):>8} "
              f"cash={money_short(p.cash):>10} "
              f"inc={money_short(p.last_income):>10} "
              f"spend={money_short(p.last_spend):>10} "
              f"fills={ {k: bp_str(v) for k, v in p.need_fill_bp.items()} }")

    print("\n--- station P&L by method ---")
    by_method = defaultdict(lambda: [0, 0, 0])
    for st in world.stations:
        row = by_method[st.method.key]
        row[0] += 1
        row[1] += st.profit_last
        row[2] += 1 if st.ran_last_tick else 0
    for m, (n, profit, run) in sorted(by_method.items(),
                                      key=lambda kv: kv[1][1]):
        print(f"  {m:<17} n={n:2d} running={run:2d} "
              f"profit/t={money_short(profit):>12}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sandbox",
         int(sys.argv[2]) if len(sys.argv) > 2 else 120)

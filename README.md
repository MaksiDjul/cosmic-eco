# Project Cosmic Balance

A playable grand-strategy economic simulation: a hexagonal galaxy of sectors,
each with its own **Local Market Hub**, joined by merchant trade routes, fought
over by fleets, and run on a **hard-capped closed currency system** in which no
unit of money or cargo is ever created from nothing.

Built from `cosmic_balance_design_master(1).md`. Python 3.11 + Pygame.

```bash
python -m pip install pygame
python main.py                  # Sandbox: 7 sectors, 2 powers
python main.py galaxy           # Campaign: 61 sectors, 5 powers
python main.py sandbox 12345    # specific world seed
```

Press `?` in game for controls. `SPACE` pauses, `1`-`6` set speed, `TAB`
cycles screens, `N` single-steps while paused.

### Without a display

```bash
python headless.py sandbox 500   # run the engine, print a full economic report
python diagnose.py galaxy 200    # per-commodity balance, halts, pop detail
python tests.py                  # invariant suite (conservation, pro-rata, ...)
python uitest.py sandbox 150     # render every screen to screenshots/
```

---

## Architecture

**Strict separation of concerns (doc 1.1).** `cosmic_balance/sim/` is the
engine; `cosmic_balance/ui/` is the presentation layer. The engine imports
nothing from the UI — `tests.py` asserts this. The UI reads state and may only
write through one channel:

```python
world.push("set_tax", sector=sector, value=2000)   # queued
world.step()                                       # drained, then applied
```

```
cosmic_balance/
  sim/
    config.py       every tunable number in the game (doc 8)
    fixed.py        integer maths; the exact pro-rata allocator
    ledger.py       accounts, inventories, and the conservation audit
    commodities.py  commodity registry + the production matrix
    market.py       the Local Market Hub: order book, clearing, price ticker
    currency.py     closed currencies and the global FX desk
    galaxy.py       hex grid, adjacency, range-limited pathfinding
    sector.py       sector properties, resource nodes, sovereignty transfer
    faction.py      sovereignty, loans, diplomacy state, fiscal policy
    station.py      production, construction, equity, debt, throughput tuning
    pop.py          demographics, labour, the consumer loop
    trader.py       merchant firms and inter-market trade routes
    military.py     fleets, logistical bases, combat, conquest
    piracy.py       piracy index, interception, the black-market loop
    diplomacy.py    the AI relation matrix and war exhaustion
    balance.py      scenario self-consistency solver
    scenarios.py    the two baseline initialisation states
    history.py      bounded time series for the dashboards
    world.py        the tick pipeline and the command queue
  ui/
    theme.py, widgets.py, app.py, views/*
```

---

## How the design document maps to the code

| Design doc | Implementation |
|---|---|
| 1.1 Separation of concerns | `ui` → `sim` only; `World.commands` is the sole write path |
| 1.1 Conservation of value | `ledger.py`; `Ledger.audit()` runs every 25 ticks |
| 2 Galactic board / sovereignty | `galaxy.py`, `sector.py`, `faction.py` |
| 3.1 Hard-capped closed currency | all minting happens in `scenarios.py`; nowhere else |
| 3.1 Dynamic exchange rates | `currency.FXDesk` — pure counterparty matching, no reserves |
| 3.2 Single clearing price | `MarketHub._clear_commodity` |
| 3.2 Pro-rata allocation | `fixed.allocate_pro_rata` (largest remainder, exact) |
| 3.2 Explosion safeguard | `MarketHub._tick_prices`, empty-side branches |
| 3.2 Zero storage clearing | hub float + escrow, proven empty by `verify_zero_storage()` |
| 3.2 Sector currency transition | `Sector.transfer_sovereignty` → `convert_legacy_cash` |
| 3.3 Production methods / levels | `commodities.ProductionMethod`, `Station.produce` |
| 3.3 Physical construction sinks | `ConstructionWarehouse` buys real goods on the real market |
| 3.3 Over/underclocking | `Station._decide_clock` (smoothed margin + hysteresis) |
| 3.3 Financial halts | `Station.produce` halt branches; transfers clamp at zero |
| 3.3 Maintenance / property tax | `Station.post_market` |
| 3.3 Equity shares / control | `Station.equity`, exactly 100 shares |
| 3.3 Capital financing | `faction.Loan`, `World.lend_capital` |
| 3.4 Abstract shipping fleets | cargo assets gate each station's trade volume |
| 3.4 Merchant firms / routes | `trader.py` |
| 3.4 Distance sinks / pathfinding | `TradeRoute.distance_penalty_bp`, `ROUTE_JUMP_RANGE` |
| 3.4 Route recalibration | `MerchantFirm.recalibrate` |
| 3.4 Hazard avoidance | `MerchantFirm.sector_is_hazardous` |
| 3.5 Labour pool / pops | `pop.py` |
| 4 Commodity classification | `config.COMMODITY_DEFS` (6 classes) |
| 4 Finite nodes, inverse yield | `sector.ResourceNode` |
| 5.1 Fleets and logistical bases | `military.Fleet`, `military.MilitaryBase` |
| 5.2 Sustained engagements | `resolve_sector_combat`, low attrition coefficient |
| 5.2 Sovereignty capture | `update_conquest`, >90% supremacy over many ticks |
| 5.3 Piracy / interception / loop | `piracy.py` |
| 6 Diplomacy and exhaustion | `diplomacy.py` |
| 6 Two baseline scenarios | `scenarios.build_sandbox`, `build_galaxy` |
| 7 UI, 1920×1080, dark, flat | `ui/` — seven screens |
| 7 Analytical dashboards | `history.py` + Markets / Finance / Trade screens |
| 8 Fixed-point conservation | integers throughout; largest-remainder splits |
| 8 Global configuration hub | `sim/config.py` |

---

## The fixed-point convention

There are no floats in the simulation. Money is an `int` in **cents**,
quantities are `int` **units**, rates are `int` **basis points** (1 bp =
0.01%, so `2000` is 20%), and exchange rates are integers scaled by
`FX_RATE_SCALE`. Python integers are arbitrary precision, so the only way to
lose value would be a division that discards a remainder — which is exactly
what `allocate_pro_rata` exists to prevent. It splits a total across claimants
using the largest-remainder method, so the parts always sum to the whole.

`Ledger.audit()` then proves the result:

```
sum(all balances in currency C) == minted[C] - burned[C]
sum(all stock of commodity K)   == produced[K] - consumed[K]
```

Any violation raises. Across a 2,000-tick campaign the drift is exactly zero.

---

## Notes on making a closed economy actually work

Most of the engineering effort here went not into the features but into making
them stable together. A hard-capped money supply is unforgiving: anything that
accumulates money without spending it eventually owns all of it, and anything
that consumes a good faster than it is made will eventually take the whole
chain down with it. The failures were rarely where they appeared.

**Monetary sinks.** Any agent that takes in more than it pays out is a black
hole. Profitable stations, merchant firms and faction treasuries each had to be
given a way to return money to circulation — dividends across the 100 equity
shares, and a state budget that funds fleets, recapitalises industry and pays
for public works. Without them the economy seized up with full warehouses and
wealthy, starving citizens.

**Households earn more than their basket costs** — the difference being the
margin that accrues to capital. With a fixed per-capita basket they bank a
third of their income forever. That surplus has to go somewhere, so pops have
income-elastic demand for consumer goods, and the state taxes and recycles the
rest.

**Profitability is a margin, not a cash flow.** Judging a station by cash
movements meant a healthy plant restocking its input buffer looked
unprofitable, throttled itself to 40% output, earned less, and stayed
throttled — an absorbing trap. Throughput is tuned on the value of goods
produced less inputs consumed, smoothed, with hysteresis.

**Labour is local and non-storable**, so it must balance sector by sector —
no merchant can haul it. That also means a depopulated sector cannot restart
its own farms, which is why people migrate toward labour shortages.

**Demand must reflect flow, not restocking.** Buyers that order an entire
multi-tick buffer at once overstate demand several-fold, which drives prices to
their ceiling even when supply covers consumption. Restocking is spread over
several ticks.

**Prices need a cost anchor.** Labour is the only primary input, so every
commodity's cost of production is a multiple of the prevailing wage. Prices are
pulled gently toward that anchor, which preserves the chain's relative
structure while leaving genuine scarcity free to move prices far from it.

**Maintenance must degrade, not gate** — and recovery must be *partial*. A
hard production floor made a maintenance shortage an absorbing state: nothing
could produce, so nothing could be repaired, forever. Condition scales
throughput instead, and partial upkeep buys partial repair, so a region
starved of metal can claw its way back out rather than being pinned at zero
while metal goes begging three jumps away.

**Renewable extraction sets the ceiling on everything.** Hydroponics run on
ice, so the entire food supply is downstream of ice deposits. With
replenishment sized below the galaxy's draw, the deposits drained, yields fell
toward the floor, and the map slid into a famine with no visible cause — every
intermediate symptom pointed at labour, or at the farms. Over-extraction still
bites locally; the galaxy-wide draw has to be sustainable.

**Demography must not be asymmetric.** Populations grew at 0.25%/tick and
starved at 0.7%. Every sector grew slowly up to its local food ceiling,
inevitably overshot it, then fell three times faster than it had climbed —
depopulating a healthy galaxy with no external shock at all. Growth now also
requires a stocked larder, so a population stops short of the ceiling instead
of discovering it by starving.

**Mobility needs both directions, and both need limits.** Migration toward
labour shortages alone built enormous permanently-starving industrial hubs,
because the biggest employers have the biggest labour deficits. Famine has to
push people out as well as jobs pulling them in — but refugees must never
strip a sector below the workforce its own farms need, and destinations are
judged on the food they could actually produce, or the first wave out of a
region guarantees no one can ever move back in.

Each of these is documented at its definition in `config.py`, with the failure
it prevents.

---

## Known limitations

- **Regional gluts persist.** Both maps run stably — the sandbox holds ~284k
  citizens and the campaign grows to ~2.7M over 3,000 ticks with no
  starvation and a clean audit — but distribution is still imperfect: some
  sectors sit on large unsold stockpiles (consumer goods especially) that the
  merchant fleet never quite hauls to where they are wanted. It is an
  efficiency loss rather than a failure, and arguably the point of a trade
  game, but a smarter routing layer would close it.
- **Performance** is ~6 ms/tick for the sandbox and ~50 ms/tick for the
  campaign (≈800 stations). At speeds above 10× the campaign falls behind
  rather than stuttering: the tick budget per frame is capped.
- **Pirates** raid and fence effectively, but their own AI is simple — raiders
  drift toward rich, lightly patrolled markets rather than planning.
- **Fleet AI** garrisons, patrols and presses an existing war; it does not
  plan multi-front campaigns.

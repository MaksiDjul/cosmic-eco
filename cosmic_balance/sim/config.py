"""
COSMIC BALANCE -- GLOBAL CONFIGURATION HUB
==========================================
Design doc Section 8: "All numerical parameters ... must be externalised into a
single, well-commented configuration file for easy balancing."

Every tunable number in the simulation lives here. Nothing else in the `sim`
package should contain a balancing literal.

UNITS AND FIXED-POINT CONVENTION (doc 8, "Floating-Point Conservation")
-----------------------------------------------------------------------
*   MONEY is stored as a Python ``int`` in CENTS (1/100 of a credit). Python
    ints are arbitrary precision, so there is no float drift whatsoever.
*   COMMODITY QUANTITY is stored as an ``int`` in whole units.
*   PRICE is stored as an ``int`` in cents-per-unit.
*   RATES / PERCENTAGES are stored as ``int`` BASIS POINTS (1 bp = 0.01%),
    so 1500 == 15.00%. Arithmetic is ``value * bp // BP`` which truncates
    deterministically and can never manufacture value.
*   EXCHANGE RATES are stored as an ``int`` numerator over ``FX_RATE_SCALE``.

Any place where a quantity must be split across several recipients uses the
largest-remainder allocator in ``fixed.py``, which is exact by construction.
"""

# ---------------------------------------------------------------------------
# 0. FIXED-POINT SCALES
# ---------------------------------------------------------------------------

CENTS_PER_CREDIT = 100          # money display divisor
BP = 10_000                     # basis-point denominator (100% == 10000 bp)
FX_RATE_SCALE = 1_000_000       # exchange rates are stored * this scale


# ---------------------------------------------------------------------------
# 1. SIMULATION CLOCK
# ---------------------------------------------------------------------------

DEFAULT_SPEED_INDEX = 2         # index into SPEED_STEPS at startup
SPEED_STEPS = [0.0, 1.0, 2.0, 4.0, 10.0, 30.0]  # selectable ticks per second

HISTORY_SAMPLE_INTERVAL = 4     # record a history datapoint every N ticks
HISTORY_LENGTH = 1200           # ring-buffer length for every tracked series

AUDIT_INTERVAL = 25             # run the full conservation audit every N ticks
AUDIT_RAISE_ON_FAIL = True      # abort loudly if value is created from nothing


# ---------------------------------------------------------------------------
# 2. COMMODITY MATRIX  (doc 4: "Broad Commodity Classification")
# ---------------------------------------------------------------------------
# Classes:
#   RAW        - Raw Harvestables, extracted from finite sector resource nodes
#   REFINED    - Refined Elements, processed from raws
#   HITECH     - High-Tech Infrastructure Components, for upgrades/maintenance
#   LOGISTIC   - Logistical & Military Asset Units, built only at shipyards
#   SUSTENANCE - Sustenance Goods, drained continuously by populations
#   UTILITY    - Abstract Utility Units (Labour); non-storable market assets
#
# Fields per commodity:
#   cls        - classification above
#   name       - UI label
#   base       - reference spot price in CENTS (scenario seed + price anchor)
#   pmin/pmax  - hard price band in CENTS (anti-explosion clamp, doc 3.2)
#   storable   - False for UTILITY units: unsold volume evaporates each tick
#   volume     - cargo volume per unit; drives shipping capacity consumption

#
# CALIBRATION NOTE -- these anchors are not arbitrary. Labour is the only
# primary input in the whole game (raw harvestables are drawn from nodes using
# only labour), so every price is ultimately a multiple of the wage. The base
# prices below were derived bottom-up through the production chain in
# ``commodities.py`` at a reference wage of 1900, allowing roughly a 15-20%
# gross margin per processing stage. Keeping these ratios consistent is what
# lets the economy find an equilibrium instead of spiralling.
#
COMMODITY_DEFS = [
    # key            cls           name                   base     pmin       pmax  store vol
    ("ORE",         "RAW",        "Metallic Ore",        1_000,     200,    12_000, True,  1),
    ("GAS",         "RAW",        "Hydrogen Gas",          900,     180,    11_000, True,  1),
    ("ICE",         "RAW",        "Volatile Ice",          650,     130,     9_000, True,  1),

    ("METAL",       "REFINED",    "Refined Metal",       4_600,     900,    52_000, True,  1),
    ("FUEL",        "REFINED",    "Reactor Fuel",        4_100,     800,    46_000, True,  1),
    ("POLYMER",     "REFINED",    "Industrial Polymer",  3_550,     700,    40_000, True,  1),

    ("ELECTRONICS", "HITECH",     "Electronics",        17_000,   3_200,   190_000, True,  2),
    ("HULLPARTS",   "HITECH",     "Hull Components",    17_000,   3_200,   190_000, True,  2),

    ("CARGO_UNIT",  "LOGISTIC",   "Cargo Tonnage",      80_000,  15_000,   900_000, True,  3),
    ("MIL_UNIT",    "LOGISTIC",   "Military Tonnage",  115_000,  22_000, 1_300_000, True,  3),

    ("FOOD",        "SUSTENANCE", "Food Rations",          960,     190,    12_000, True,  1),
    ("GOODS",       "SUSTENANCE", "Consumer Goods",      6_300,   1_200,    70_000, True,  1),

    ("MAINT_GOODS", "REFINED",    "Maintenance Supplies", 5_000,   1_000,    55_000, True,  1),

    ("LABOR",       "UTILITY",    "Labour Units",        1_900,     350,    22_000, False, 0),
]

# Commodities accepted by a Construction Warehouse (doc 3.3 "Physical
# Construction Sinks"). Building a station level never debits an abstract
# lump of credits: the warehouse buys these goods on the open market.
CONSTRUCTION_GOODS = ("METAL", "ELECTRONICS", "HULLPARTS")

# Goods a population entity must consume (doc 3.5 "The Consumer Loop").
# Values are units required each tick per 1000 citizens.
#
# CALIBRATION NOTE: the basket must embody materially LESS labour than a pop
# supplies, because the difference is exactly the margin that accrues to
# station owners and to the state as tax. At POP_LABOR_PER_1000 = 95 this
# basket embodies ~48 labour units per 1000 citizens through the full chain,
# i.e. about half of what the pop sells -- which is what keeps populations
# solvent while still leaving capital a return.
SUSTENANCE_NEEDS = {
    "FOOD": 60,     # staple: starvation driver
    "GOODS": 10,    # discretionary: growth / contentment driver
}
SUSTENANCE_PRIORITY = ("FOOD", "GOODS")   # purchase order when cash is short
# Weight of each good in the satisfaction index. Food dominates: a shortage of
# consumer goods is a drag on contentment, a shortage of food is fatal, and
# blending them equally meant a well-fed population could still be judged to be
# starving because the polymer plants were down.
SUSTENANCE_WEIGHT_BP = {
    "FOOD": 10_000,
    "GOODS": 3_000,
}


# ---------------------------------------------------------------------------
# 3. LOCALISED COMMODITY MARKETS  (doc 3.2)
# ---------------------------------------------------------------------------

# Single-clearing-price settlement. The price for the NEXT tick is nudged by
# the imbalance ratio of the order book that just cleared.
MARKET_PRICE_DAMPING_BP = 900     # move 9% of the way toward the imbalance
MARKET_MAX_MOVE_BP = 600          # price may never move more than 6% per tick

# Explosion Safeguard (doc 3.2): when one side of the book is EMPTY the price
# must not jump to the clamp instantly -- it creeps at a bounded pace.
MARKET_EMPTY_SIDE_STEP_BP = 150   # 1.5% per tick while a side stays empty
MARKET_EMPTY_BOTH_DECAY_BP = 60   # 0.6%/tick pull toward the base anchor when
                                  # nobody trades at all

# COST ANCHORING.
#
# A price ticker driven purely by the order book has no anchor, and that turns
# out to be fatal. Buyers top up safety buffers, so measured demand sits
# persistently above supply whenever supply is merely adequate; the ratio stays
# above 1 forever and the price climbs to its ceiling even though consumption
# is fully met. Worse, it climbs at DIFFERENT rates for different goods, so the
# relative price structure drifts apart -- raw materials at 10x anchor while
# the goods made from them sit at 4x -- and every refinery in the galaxy is
# structurally loss-making through no fault of its own.
#
# The fix is to pull each price gently toward its cost of production. Labour is
# the only primary input in the game (raw harvestables are drawn from nodes
# using nothing but labour), so every commodity's cost of production is a fixed
# multiple of the prevailing wage. Anchoring to `base_price * wage / wage_base`
# therefore preserves the chain's relative structure -- keeping each processing
# stage viable -- while letting the absolute price level float with wages and
# letting genuine local scarcity push prices well away from the anchor.
MARKET_ANCHOR_PULL_BP = 250       # 2.5%/tick pull toward the cost-of-production
MARKET_ANCHOR_WAGE_KEY = "LABOR"

# Order-book hygiene
MARKET_MIN_ORDER = 1              # orders below this are dropped
MARKET_ORDER_CASH_SAFETY_BP = 9_700   # a buyer only commits 97% of its cash so
                                      # a price uptick can never overdraw it


# ---------------------------------------------------------------------------
# 4. CLOSED CURRENCY SYSTEM & FX  (doc 3.1)
# ---------------------------------------------------------------------------
# The FX desk is a pure order-matching clearing house: it holds no reserves,
# so every swap is a conserving transfer between two counterparties.

FX_RATE_DAMPING_BP = 900          # 9% move toward implied imbalance per tick
FX_RATE_MAX_MOVE_BP = 500         # hard 5% per-tick cap on any pair
FX_RATE_EMPTY_STEP_BP = 120       # 1.2% creep when one side has no counterparty
FX_RATE_MIN = FX_RATE_SCALE // 50     # a currency can fall to 1/50 of peg
FX_RATE_MAX = FX_RATE_SCALE * 50      # ...or rise to 50x
FX_MIN_ORDER_CENTS = 500          # ignore dust conversion orders

# After a conquest, local entities must swap their legacy cash (doc 3.2
# "Sector Currency Transition"). They convert this share of the stranded
# balance per tick so the FX market absorbs the flow gradually.
FX_LEGACY_CONVERSION_BP = 2_500   # 25% of the stranded balance per tick


# ---------------------------------------------------------------------------
# 5. STATIONS, PRODUCTION & GOVERNANCE  (doc 3.3)
# ---------------------------------------------------------------------------

STATION_MAX_LEVEL = 20
# Each level multiplies the base recipe throughput by 100% (doc 3.3).
LEVEL_THROUGHPUT_MULT_BP = 10_000   # +100% of base per level

# Inventory sizing: capacity scales with level so a big station buffers more.
STORAGE_PER_LEVEL_INPUT_TICKS = 14    # ticks of input demand held at capacity
STORAGE_PER_LEVEL_OUTPUT_TICKS = 14   # ticks of output production held
STORAGE_MIN_CAPACITY = 40             # floor so tiny recipes still have room

# Input safety buffer (doc 3.3 "Inventory Management")
INPUT_BUFFER_TICKS = 6                # aim to hold 6 ticks of every input
#
# Restocking is SPREAD over this many ticks rather than ordered all at once,
# and this matters far more than it looks. An order book is the only signal the
# price ticker has. If every buyer demands its entire multi-tick buffer in a
# single tick, measured demand is a large multiple of actual consumption, the
# imbalance ratio is wildly overstated, and the price climbs to its ceiling
# even though physical supply comfortably covers real usage. The resulting
# false prices then whipsaw the throughput tuner between overclock and
# underclock, and the oscillation tears the production chain apart.
#
# Ordering "one tick of consumption, plus a fraction of any shortfall" keeps
# steady-state demand equal to steady-state flow, which is what makes the
# single-clearing price mean something.
RESTOCK_SPREAD_TICKS = 6

# Output selling policy
OUTPUT_SELL_RESERVE_TICKS = 0         # sell everything not needed internally

# Dynamic Throughput Tuning (doc 3.3)
NORMAL_BP = 10_000                    # 100% baseline
OVERCLOCK_THROUGHPUT_BP = 13_000      # 130% output when overclocked
OVERCLOCK_INPUT_PENALTY_BP = 16_000   # ...but 160% input draw (disproportionate)
OVERCLOCK_PROFIT_THRESHOLD = 15_000   # need >150.00 credits margin per LEVEL
UNDERCLOCK_THROUGHPUT_BP = 6_000      # 60% output when throttled
UNDERCLOCK_INPUT_BP = 6_000           # inputs scale down with it
#
# Hysteresis on the throughput controller. The clock is a discrete three-way
# switch, so reacting to a single tick's margin makes it a bang-bang
# controller: it overshoots, flips, overshoots the other way, and the
# oscillation propagates up the production chain as alternating gluts and
# famines. Requiring a sustained signal, and judging it on a smoothed margin,
# turns that into a stable control loop.
CLOCK_MARGIN_EMA_BP = 3_000           # weight of the newest tick in the EMA
CLOCK_UNDERCLOCK_AFTER = 3            # consecutive bad ticks before throttling
CLOCK_OVERCLOCK_AFTER = 3             # consecutive good ticks before pushing
# Never overclock into an input shortage: the extra draw would simply starve
# the plant and deepen the upstream scarcity it is reacting to.
OVERCLOCK_MIN_INPUT_TICKS = 2

# Maintenance (doc 3.3 "Operational Costs") -- a fixed baseline of maintenance
# commodities purchased and consumed per LEVEL every tick, regardless of
# whether production actually ran.
#
# Expressed in BASIS POINTS of a unit per level per tick, because a whole unit
# of Electronics per station per tick would demand far more high-tech output
# than the galaxy's fabs can produce, and the entire industrial base would
# decay. The station accrues the fractional draw and consumes whole units as
# the accrual crosses them -- a continuous per-tick drain expressed in the
# fixed-point convention this engine uses everywhere else.
# Only Refined Metal. Electronics were originally part of the upkeep basket,
# which looked reasonable and was quietly catastrophic: electronics sit four
# stages deep in the chain, so a sector that could not import them watched its
# ENTIRE industrial base decay -- including the smelters and harvesters whose
# output the rest of the galaxy needed to make more electronics. Upkeep is
# existential, so it must depend only on a good every sector can plausibly make
# for itself: metal needs nothing but ore and labour. Electronics remain
# essential for CONSTRUCTION and military hardware, where a shortage is a
# strategic setback rather than a death sentence.
MAINTENANCE_PER_LEVEL_BP = {
    "METAL": 2_500,         # 0.25 units per level per tick
}
MAINTENANCE_BUFFER_TICKS = 12
# Missing maintenance does not instantly destroy a station; it degrades
# condition, and a station at low condition cannot produce.
CONDITION_MAX = 10_000                # 100.00%
CONDITION_DECAY_ON_MISSING = 120      # -1.2%/tick while maintenance is unmet
CONDITION_REPAIR_ON_MET = 260         # +2.6%/tick while maintenance is met
#
# Condition scales THROUGHPUT; it does not gate it. This distinction decides
# whether the economy is recoverable at all. With a hard production floor, a
# maintenance shortage is an absorbing state: stations stop producing, so the
# electronics and metal that maintenance needs stop being produced, so nothing
# can ever be repaired, and a fully capable industrial base sits dead forever.
# Degrading output instead means a maintenance famine hurts -- badly -- but the
# chain keeps turning over and can climb back out.
CONDITION_MIN_EFFICIENCY_BP = 5_000   # a wrecked station still manages 50%
# Condition recovers in PROPORTION to the maintenance actually delivered.
# All-or-nothing recovery created a trap a region could not climb out of: a
# sector short of metal ran everything at the efficiency floor, so its own ore
# mine produced too little to feed its smelter, so maintenance stayed unmet
# and condition stayed pinned at zero -- permanently, even with metal going
# begging three jumps away. Partial upkeep now buys partial recovery.
CONDITION_PARTIAL_REPAIR = True

# MAINTENANCE GOODS system: stations consume MAINT_GOODS to stay operational.
# Missed deliveries accumulate and eventually force a downgrade.
MAINT_GOODS_PER_LEVEL = 1            # units of MAINT_GOODS consumed per level/tick
MAINT_MISSED_DOWNGRADE = 100         # accumulated misses that trigger downgrade
MAINT_BUFFER_TICKS = 12              # ordering look-ahead for MAINT_GOODS

# STATION AUTO-DOWNGRADE: every N ticks a station checks whether to shed a level.
STATION_DOWNGRADE_INTERVAL = 5       # ticks between downgrade reviews
STATION_DOWNGRADE_BAD_TICKS = 5      # required bad ticks before downgrade

# HABITATION: population capacity is bounded by habitation infrastructure.
HABITATION_POP_PER_LEVEL = 1_000     # max citizens per habitation-station level
HABITATION_METHOD_KEY = "habitation"  # the production method key for habitation

# TIERED STARVATION: population shrinks when food satisfaction drops below
# these thresholds, at the given multiplier per tick (in BP).
STARVATION_TIERS = [
    (2_500, 9_600),    # <25% food → multiply by 0.96
    (5_000, 9_800),    # <50% food → multiply by 0.98
    (7_500, 9_900),    # <75% food → multiply by 0.99
]

# Property tax (doc 3.3): paid to the sector lord when the station's
# controlling owner is NOT the sovereign of the sector it sits in.
PROPERTY_TAX_BASE_PER_LEVEL = 4_000   # 40.00 credits per level per tick, scaled
                                      # by the sector's own property tax rate

# Corporate Governance (doc 3.3)
EQUITY_TOTAL_SHARES = 100             # exactly 100 fractional shares
EQUITY_CONTROL_THRESHOLD = 51         # strict majority assumes operations

# Capital Financing (doc 3.3)
DEFAULT_LOAN_INTEREST_BP = 45         # 0.45% of principal per tick
LOAN_PRINCIPAL_PER_LEVEL = 1_500_000  # 15,000.00 credits lent per planned level
# Interest only begins once Level 1 construction completes (doc 3.3).
LOAN_MIN_PAYMENT = 1                  # never round a live debt down to zero
# If a station cannot service interest it accrues arrears rather than going
# negative (cash accounts may never be negative, doc 3.3 "Financial Halts").
ARREARS_SEIZE_THRESHOLD = 3_000_000   # lender seizes control above 30,000 cr

# Construction Warehouse (doc 3.3 "Physical Construction Sinks")
CONSTRUCTION_COST_PER_LEVEL = {
    "METAL": 60,
    "ELECTRONICS": 18,
    "HULLPARTS": 22,
}
CONSTRUCTION_COST_LEVEL_SCALING_BP = 11_000  # each level costs 110% of the last
CONSTRUCTION_BUFFER_TICKS = 10        # warehouse order aggressiveness
CONSTRUCTION_MAX_ORDER_PER_TICK = 40  # throttle so one build cannot corner a market
# Abandon a project that cannot get its materials. A construction warehouse
# sited where its bill of materials simply does not exist locally -- and that
# trade cannot reach -- will sit at partial progress forever. Each one holds a
# slot in its owner's build programme, so a handful of them permanently froze
# all industrial expansion while the economy around them changed. Delivered
# materials revert to the station; the debt, correctly, does not.
CONSTRUCTION_ABANDON_TICKS = 150      # ticks of zero progress before giving up

# Station shipping capability (doc 3.4 "Abstract Shipping Fleets")
# Cargo units are an ASSET, not a consumable: they are only lost to piracy.
STATION_CARGO_PER_LEVEL = 2           # target cargo units held per level
CARGO_VOLUME_PER_UNIT = 400           # commodity volume movable per cargo unit
STATION_CARGO_BUY_MAX_PER_TICK = 1
# A bootstrap allowance so a brand-new station with zero cargo assets can
# still trade (and therefore buy its first cargo unit) instead of deadlocking.
STATION_BASE_TRADE_VOLUME = 300
# Share of a station's shipping capacity usable for OUTBOUND sales, so
# exports cannot crowd out the freight it needs to keep running.
SELL_VOLUME_SHARE_BP = 6_000

# Trader (merchant firm) specifics (doc 3.4 "Independent Trader Stations")
TRADER_CARGO_TARGET = 20
TRADER_CARGO_BUY_MAX_PER_TICK = 2


# Corporate distributions.
#
# WHY THIS EXISTS: in a hard-capped closed currency system, any agent that
# takes in more than it pays out is a monetary black hole. Profitable stations
# accumulate cash forever, which drains the circulating medium out of the rest
# of the economy until nobody can afford to transact -- a deflationary spiral
# that kills a perfectly productive galaxy. Real firms return capital to their
# owners, so these stations do too, pro-rata across the 100 equity shares.
# The buffer must be generous and the payout slow. An aggressive dividend is
# indistinguishable from asset-stripping: paying out a quarter of the surplus
# every tick drained every station down to its working-capital line within a
# dozen ticks, and the first time input prices rose the whole industrial base
# was left PROFITABLE BUT ILLIQUID -- unable to afford even one tick of labour,
# therefore unable to produce, therefore unable to earn its way back. A
# liquidity trap looks exactly like a famine from the outside.
WORKING_CAPITAL_TICKS = 25        # ticks of input costs kept as a buffer
DIVIDEND_PAYOUT_BP = 500          # 5% of surplus cash distributed per tick
DIVIDEND_MIN = 20_000             # don't bother below 200.00 credits
DIVIDEND_COVER_BP = 20_000        # need 2x working capital before paying out

# State fiscal policy (doc 2: factions levy taxes and set policy).
#
# The same monetary argument applies to the treasury, with interest: taxes and
# loan interest flow INTO it every tick and nothing flows out. A government
# that only accumulates is a sink, so factions run a budget: they fund their
# fleets, recapitalise their industry and spend on public works, which returns
# the money to the households and firms it was taxed from.
STATE_RESERVE_TICKS = 220         # treasury kept as a strategic war chest
STATE_SPEND_BP = 3_000            # 30% of the surplus above reserve per tick
STATE_BUDGET_MILITARY_BP = 3_000  # share of the budget funding military bases
STATE_BUDGET_INDUSTRY_BP = 4_500  # share recapitalising controlled stations
STATE_BUDGET_PUBLIC_BP = 2_500    # share spent on public works (paid to pops)
# A working-capital facility: the controlling faction always covers at least
# this many ticks of inputs for a station that would otherwise be too illiquid
# to trade at all. This is the circuit breaker against the liquidity trap
# described above -- a solvent, profitable plant should never be unable to
# make payroll.
# A merchant firm turns its entire stock over every tick, so it needs a far
# thinner buffer than a factory with a six-tick input pipeline. Giving firms
# the factory-sized reserve meant their dividend test never triggered at all
# and they resumed hoarding the money supply.
TRADER_WORKING_CAPITAL_TICKS = 4
TRADER_DIVIDEND_PAYOUT_BP = 4_000     # 40% of surplus per tick
# Firms pay out as soon as their buffer is covered; unlike a factory a
# trader can rebuild working capital in a single tick, and the stricter
# 2x cover test let them quietly re-accumulate a third of the money supply.
TRADER_DIVIDEND_COVER_BP = 10_000

STATE_RESCUE_TICKS = 3
# ...but the facility is CAPPED. Left unbounded it is a blank cheque: a power
# with hundreds of marginal plants transferred its entire treasury into them
# in a few ticks, hit zero, and could then fund neither rescues nor public
# works nor its fleets -- so the state itself became the bottleneck. A state
# that cannot afford to rescue all its industry should see some of it fail.
STATE_RESCUE_MAX_BP = 1_500       # at most 15% of the treasury per tick
STATE_MIN_TREASURY = 20_000_000   # hard floor: 200k credits never spent
MILBASE_TARGET_CASH = 20_000_000  # base cash the state tops up toward

# Autonomous industrial expansion.
#
# Station levels are otherwise static, so a sustained shortage would never be
# answered by new capacity and the economy could not grow with its population.
AI_EXPAND_INTERVAL = 25           # ticks between expansion reviews
AI_EXPAND_MIN_TREASURY = 40_000_000
AI_EXPAND_PRICE_TRIGGER_BP = 13_000   # build when a good trades >1.3x anchor
AI_EXPAND_MAX_CONCURRENT = 3      # builds in progress per faction
# Subsistence shortages outrank every other kind, and get their own, larger
# build allowance. A commodity glut is an inefficiency; a food shortage
# compounds -- hungry households spend their entire budget on food, so demand
# for everything else collapses, the industry making it idles, and the labour
# that industry employed stops being paid. Famine is the one shortage that
# takes the rest of the economy down with it.
AI_SUBSISTENCE_PRIORITY = 4       # score multiplier for food/goods scarcity
AI_SUBSISTENCE_CONCURRENT = 4     # extra concurrent subsistence builds


# ---------------------------------------------------------------------------
# 6. TRADE ROUTES  (doc 3.4)
# ---------------------------------------------------------------------------

ROUTE_JUMP_RANGE = 3                  # adjustable pathfinding radius (perf cap)
ROUTE_MAX_PER_FIRM = 10
ROUTE_VOLUME_PER_LEVEL = 70           # commodity units moved per route level
ROUTE_MAX_LEVEL = 5
# Distance Sink (doc 3.4): a percentage volume malus per hop beyond the home
# market, applied per route level.
ROUTE_DISTANCE_PENALTY_BP = 1_400     # -14% throughput per extra hop
ROUTE_MIN_MARGIN_BP = 250             # need a 2.5% gross margin to open a route
ROUTE_EVALUATION_INTERVAL = 6         # re-scan markets every N ticks
ROUTE_LOSS_THROTTLE_BP = 3_000        # loss-making route drops to 30% volume
ROUTE_DISSOLVE_AFTER_LOSS_TICKS = 12  # then dissolves if still unprofitable
ROUTE_LEVEL_UP_PROFIT = 120_000       # 1200 cr cumulative profit to level up

# Hostile Hazard Avoidance (doc 3.4)
ROUTE_PIRACY_ABORT_BP = 4_500         # refuse to route through >45% piracy
# Bonus applied to a candidate route's score when the destination market had
# genuinely unfilled demand last tick. Without this, firms chase whichever
# commodity has the widest speculative spread and ignore actual shortages --
# which in practice means they let factory sectors starve while hauling metal.
ROUTE_SHORTAGE_BONUS_BP = 2_500


# ---------------------------------------------------------------------------
# 7. DEMOGRAPHICS & LABOUR  (doc 3.5)
# ---------------------------------------------------------------------------

POP_LABOR_PER_1000 = 95               # labour units produced per 1000 citizens
# Demographics move SLOWLY relative to industry. This is not timidity: at
# 1.6%/tick a population doubles in 44 ticks, which no amount of construction
# can keep pace with, so every scenario ran the same classic overshoot --
# a population boom on a fixed food supply, then a famine and a crash to the
# floor. Industry must be able to out-build demography, not the reverse.
POP_GROWTH_BP = 25                    # +0.25%/tick when fully fed and content
# Decline must not massively outpace growth. At -0.7% against +0.25% the
# system ratchets downward: a sector grows slowly up to its local food
# ceiling, inevitably overshoots it, and then falls three times faster than it
# climbed. Repeat that across a galaxy and a healthy economy depopulates
# itself without any external shock at all.
POP_DECLINE_BP = 30                   # -0.3%/tick while starving
# ...and only after sustained hunger. Food supply naturally oscillates (a
# cobweb cycle between farm throughput and price), so a single hungry tick is
# noise, not famine.
POP_DECLINE_AFTER = 4                 # consecutive hungry ticks before decline
# Growth needs a stocked larder as well as a full plate, so a population stops
# expanding BEFORE it hits the ceiling of what its market can supply rather
# than discovering the ceiling by starving.
POP_GROWTH_RESERVE_TICKS = 2          # ticks of food in store required to grow
POP_MIN_SIZE = 50
POP_MAX_SIZE = 250_000
# Satisfaction is the fraction of its need basket a pop actually consumed.
POP_GROWTH_SATISFACTION_BP = 9_000    # >=90% satisfied -> grow
# Extra growth while the local labour market is short of workers: wages are
# good, so migrants arrive. This is the feedback that lets demography follow
# industry instead of permanently lagging it.
POP_MIGRATION_BP = 60                 # +0.6%/tick on top of natural growth

# INTER-SECTOR MIGRATION.
#
# Natural growth alone cannot fix a regional labour shortage, because a sector
# that has lost its workforce is in an absorbing trap: too few workers to run
# its own farms, so no food, so no growth, so too few workers -- forever. Its
# idle industry sits next to a neighbour with surplus labour and neither can
# help the other, because labour is the one commodity no merchant can haul.
#
# People, however, can walk. Migration moves workers from labour-surplus
# sectors toward labour-scarce ones, carrying a proportional share of their
# savings with them (which keeps money conserved), and that is what lets a
# depopulated region actually recover.
MIGRATION_INTERVAL = 8                # ticks between migration waves
MIGRATION_RATE_BP = 600               # at most 6% of a source pop per wave
MIGRATION_MAX_HOPS = 3                # how far people will relocate
MIGRATION_MIN_GAP = 20                # ignore trivial labour imbalances
# Famine emigration. A sector below this food-fill sheds refugees toward
# anywhere with food to spare, independently of labour demand -- otherwise an
# overgrown industrial hub, which is starving AND labour-short, can only ever
# attract more mouths and never shrink to a size it can feed.
# Deliberately SEVERE and slow. An early, generous trigger made nearly every
# sector a source at once, everyone piled into the few with spare food, and the
# emptied regions could no longer work their own farms -- turning a shortage
# into a galaxy-wide depopulation spiral. Refugees are a last resort, not a
# routine response to a thin harvest.
MIGRATION_FAMINE_BP = 4_000           # below 40% fed, people start leaving
MIGRATION_REFUGEE_BP = 250            # up to 2.5% of a starving pop per wave
POP_DECLINE_SATISFACTION_BP = 6_000   # <60% satisfied -> shrink
# A pop keeps a cash war-chest rather than spending to zero every tick.
POP_SAVINGS_FLOOR_BP = 1_200          # retain 12% of cash as savings
POP_CONSUMPTION_STORAGE_TICKS = 4     # consumption warehouse depth

# ELASTIC (LUXURY) DEMAND -- and this one is not a nicety, it is what closes
# the monetary circuit.
#
# With a FIXED per-capita basket the arithmetic is inescapable: a pop sells
# ~95 labour units per 1000 citizens but its basket costs only ~64 of them at
# cost-anchored prices, because the difference is the margin that accrues to
# capital. Households therefore bank roughly a third of their income EVERY
# TICK, forever. In a hard-capped closed currency system that is a slow-motion
# catastrophe: eventually households hold almost the entire money supply, the
# firms that pay their wages cannot recover the cash, factories cannot make
# payroll, and production stops in an economy with full warehouses and
# wealthy, starving citizens.
#
# Real households spend more when they earn more. Giving pops an income-
# elastic appetite for consumer goods above their survival needs returns that
# surplus to the firms, and the circuit closes.
POP_LUXURY_SPEND_BP = 2_500           # 25% of surplus cash spent on luxuries
POP_LUXURY_MAX_MULT = 3               # at most 3x the base need in a tick
POP_LUXURY_KEY = "GOODS"              # what prosperity is spent on
# Consumer-goods industry must be sized to real consumer demand, not to bare
# subsistence. If the only goods factories a scenario builds are the ones
# needed to meet survival needs, prosperous households have nothing to spend
# their surplus on, and the elastic demand above cannot actually clear -- so
# the money piles up in household accounts exactly as before.
POP_LUXURY_PLANNING_MULT = 12         # tenths: plan goods at 1.2x base need

# Taxation: sector tax is levied on realised pop income (doc 2, doc 3.5)
# A substantial income tax is load-bearing, not flavour. Households earn more
# than their consumption basket costs (the difference being capital's margin),
# so SOMETHING has to recycle that surplus or it accumulates in household
# accounts until the firms that pay the wages run out of cash. Taxation does
# it directly: the state collects the surplus and spends it on fleets,
# industrial recapitalisation and public works. The alternative -- absorbing
# it all through consumer demand -- would need a consumer-goods industry
# several times larger than the rest of the economy combined.
DEFAULT_SECTOR_TAX_BP = 2_000         # 20% income tax
MAX_SECTOR_TAX_BP = 6_000
DEFAULT_PROPERTY_TAX_BP = 1_000       # 10% of PROPERTY_TAX_BASE_PER_LEVEL
MAX_PROPERTY_TAX_BP = 5_000
DEFAULT_TARIFF_BP = 400               # 4% levied on cross-border route value
MAX_TARIFF_BP = 3_000


# ---------------------------------------------------------------------------
# 8. RESOURCE NODES  (doc 4 "Raw Harvestables")
# ---------------------------------------------------------------------------
# Extraction difficulty scales INVERSELY with remaining volume: a depleted node
# yields a fraction of a fresh one.
NODE_YIELD_DEPLETION_FLOOR_BP = 1_500 # a fully drained node still yields 15%
#
# REPLENISHMENT SETS THE CEILING ON THE WHOLE ECONOMY, and it has to be sized
# against what the galaxy actually draws. At 0.12%/tick each deposit renewed
# 480 units per tick, so 61 ice fields sustained ~29k/tick -- against an ice
# draw of ~64k/tick, because hydroponics (the entire food supply) run on ice.
# The deposits drained, yields fell toward the floor, the farms starved of
# feedstock and the galaxy slid into a famine with no visible cause: every
# intermediate symptom pointed at labour or at the farms themselves.
#
# Over-extraction must still bite -- a sector that outruns its own deposits
# watches its yield fall -- but the GALAXY-wide draw has to be sustainable, or
# the whole map is on a timer.
NODE_REPLENISH_BP = 35                # +0.35% of capacity per tick
NODE_DEFAULT_CAPACITY = 400_000


# ---------------------------------------------------------------------------
# 9. MILITARY & COMBAT  (doc 5.1, 5.2)
# ---------------------------------------------------------------------------

FLEET_STRENGTH_PER_MIL_UNIT = 10      # abstract combat strength per tonne
FLEET_CREW_LABOR_PER_STRENGTH = 2     # labour units/tick to keep crews aboard
FLEET_UNCREWED_EFFICIENCY_BP = 3_500  # 35% effectiveness when unmanned
MILBASE_MIL_BUY_MAX_PER_TICK = 3
MILBASE_LABOR_BUFFER_TICKS = 1        # labour cannot be stockpiled

# Sustained Engagements (doc 5.2): long strategic campaigns, not instant wipes.
COMBAT_ATTRITION_BP = 220             # 2.2% of the enemy's effective strength
COMBAT_ATTRITION_JITTER_BP = 80       # +/- randomisation band
COMBAT_MIN_LOSS = 1
COMBAT_ROUT_THRESHOLD_BP = 1_500      # retreat at 15% of starting strength

# Sovereignty Capture (doc 5.2)
CONQUEST_SUPREMACY_BP = 9_000         # need >90% of sector strength to progress
CONQUEST_PROGRESS_PER_TICK = 140      # out of CONQUEST_PROGRESS_MAX
CONQUEST_PROGRESS_MAX = 10_000
CONQUEST_DECAY_PER_TICK = 220         # meter bleeds back when supremacy lapses

# Fleet movement
FLEET_TICKS_PER_JUMP = 4              # incremental hex-to-hex travel


# ---------------------------------------------------------------------------
# 10. PIRACY & BLACK MARKET  (doc 5.3)
# ---------------------------------------------------------------------------

PIRACY_MAX_BP = 10_000
PIRACY_PER_RAIDER_STRENGTH_BP = 9     # each point of raider strength adds 0.09%
PIRACY_PATROL_SUPPRESSION_BP = 16     # each point of patrol strength removes
PIRACY_PATROL_EFFICIENCY_BP = 15_000  # patrol stance is 150% effective
PIRACY_NATURAL_DECAY_BP = 40          # piracy decays 0.4%/tick on its own
PIRACY_BASE_BP = 150                  # ambient lawlessness floor

# Interception Events (doc 5.3): stolen goods leave the legal economy and are
# transferred to the hidden pirate base -- value is conserved, not deleted.
PIRACY_INTERCEPT_THRESHOLD_BP = 1_000 # checks begin above 10% piracy
PIRACY_INTERCEPT_CHANCE_BP = 5_500    # scaled by the piracy index
PIRACY_STEAL_SHARE_BP = 1_800         # up to 18% of an intercepted order
PIRACY_CARGO_DESTROY_CHANCE_BP = 1_100  # chance a cargo asset is destroyed
PIRACY_COMBAT_CASUALTY_BP = 45        # asymmetric guerrilla warfare: low losses

# The Pirate Loop (doc 5.3)
PIRATE_SELL_SHARE_BP = 3_500          # fences 35% of its hoard per tick
PIRATE_RAIDER_COST_MIL_UNITS = 4      # mil tonnage required to spawn a raider
PIRATE_RAIDER_STRENGTH = 28
PIRATE_MAX_RAIDERS_PER_BASE = 14
PIRATE_BUY_MAX_PER_TICK = 2


# ---------------------------------------------------------------------------
# 11. DIPLOMACY  (doc 6)
# ---------------------------------------------------------------------------

DIPLO_EVAL_INTERVAL = 20              # AI relation matrix re-evaluation period
DIPLO_OPINION_MIN = -10_000
DIPLO_OPINION_MAX = 10_000
DIPLO_OPINION_DRIFT = 15              # opinion decays toward neutral per eval
DIPLO_WAR_OPINION = -6_000            # below this the AI considers war
DIPLO_ALLY_OPINION = 6_500            # above this the AI considers an alliance
DIPLO_WAR_STRENGTH_RATIO_BP = 13_000  # only attack at a 1.3:1 strength edge
DIPLO_BORDER_FRICTION = 90            # shared borders push opinion down per eval
DIPLO_WAR_DECLARE_COOLDOWN = 120

# War Exhaustion (doc 6)
EXHAUSTION_MAX = 10_000
EXHAUSTION_PER_STRENGTH_LOST = 7
EXHAUSTION_PER_SECTOR_LOST = 900
EXHAUSTION_PER_TICK_AT_WAR = 6
EXHAUSTION_DECAY_AT_PEACE = 20
EXHAUSTION_PEACE_THRESHOLD = 6_500    # sue for peace above 65% exhaustion


# ---------------------------------------------------------------------------
# 12. SCENARIO SHAPES  (doc 6 "Extensible Sandbox Scenarios")
# ---------------------------------------------------------------------------

# Starting capital minted per faction at init (doc 3.1: finite, hard-capped).
FACTION_TREASURY_START = 240_000_000      # 2.4M credits
STATION_CASH_START = 9_000_000            # 90k credits
TRADER_CASH_START = 7_000_000
MILBASE_CASH_START = 12_000_000
POP_CASH_START = 900_000                  # 9k credits
PIRATE_CASH_START = 3_000_000

SANDBOX_MAP_RADIUS = 2                    # small verification map
GALAXY_MAP_RADIUS = 4                     # large multi-faction map (61 sectors)

RANDOM_SEED = 20261015

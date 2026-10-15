# DESIGN ARCHITECTURE MASTER PLAN: PROJECT COSMIC BALANCE

## 1. VISION & CORE PHILOSOPHY
Project **Cosmic Balance** is a playable Grand Strategy Economic Simulation game set within a procedurally generated or scenario-driven galaxy map consisting of hexagonal sectors. 
The core design concept is a deep synthesis of the production-loop infrastructure found in **X4** with the realistic, closed-loop macroeconomic systems of **Victoria 2**. 

### 1.1 Core Directives for the AI Coding Agent
* **Strict Separation of Concerns:** The simulation engine must operate independently of the presentation layer. The user interface (UI) must act strictly as a read-only viewer and an action-command injector. It must never contain or alter simulation state variables directly.
* **Conservation of Value:** Money and resources must never appear from or vanish into nothingness (except via explicit, tracked loops like resource mining, consumption, or combat destruction). Every unit of currency and commodity must be accounted for at all times.
* **Agent Autonomy:** This document defines the functional scope, behavioral boundaries, and mathematical constraints of the system. The architectural implementation, class inheritance structures, data pipelines, and design patterns are left entirely to the discretion of the agent.

---

## 2. MAP & FACTION STRUCTURE
* **The Galactic Board:** The playable area is a galaxy grid composed of hexagonal sectors, visually inspired by the structural connectivity of *X4*.
* **Faction Sovereignty:** A faction represents a political entity that owns sectors, commands military fleets, levies sector-wide taxes, and establishes legislative frameworks.
* **Sector Properties:** Every sector tracks its current owner, a local tax rate, property tax values, a piracy index, and localized labor demographics.

---

## 3. THE CLOSED ECONOMIC ENGINE

### 3.1 Hard-Capped Closed Currency System
* **No Infinite Capital:** Money is globally finite. Each faction issues its own distinct currency.
* **Dynamic Exchange Rates:** Currencies are traded dynamically on a market-driven global exchange. Exchange rates fluctuate based on trade volumes, inflation, and deflation caused by faction actions.

### 3.2 Localized Commodity Markets (The Market Hub)
* **Localized Price Indexes:** Every sector contains a distinct *Local Market Hub*. Prices are calculated locally at the sector level to maintain extreme algorithmic performance.
* **Order Book Processing:** Economic entities (stations, populations) submit buy and sell orders to their local market. Orders specify quantities only.
* **Single-Clearing Price Settlement:** Inspired by *Victoria 2*, the market maintains a single, clearing-house style spot price for each commodity. All successful transactions within a tick occur at this exact price.
* **Price Tick Ticker:** Prices adjust incrementally every turn based on the ratio between buy and sell demand.
  * *Explosion Safeguard:* If one side of the order book is completely empty (e.g., 0 sell orders), the price must not explode instantly. It must continue to tick upward or downward at a controlled, bounded pace.
* **Pro-Rata Allocation (Market Clearing):** When demand exceeds supply (or vice versa), matching orders are not fulfilled sequentially. Instead, the available volume is distributed proportionally based on the requested size of each order.
* **Zero Storage Clearing:** The Market Hub itself possesses no warehouses and holds no capital reserves; it serves purely as a clearing house executing transfers between buyers and sellers.
* **Sector Currency Transition:** The market always operates in the native currency of the sector's current political owner. When a sector changes ownership due to conquest:
  * An event protocol hooks into local entities.
  * Factories and stations retain their old cash reserves but must actively issue foreign exchange orders to swap their old currency into the new sovereign currency on the global market to meet tax obligations and labor costs.

### 3.3 Factory & Station Infrastructure
* **Production Methods:** Every station requires an extensible production matrix defining an input-to-output conversion formula per tick (e.g., combining raw materials and labor units to create refined components).
* **Level Scaling:** Stations utilize a leveling system. Each level multiplies the base production recipe throughput by 100%.
* **Physical Construction Sinks:** Upgrading or building a station never consumes an abstract lump-sum of credits out of thin air. Instead, it deploys a physical *Construction Warehouse* that places buy orders on the local market for actual construction commodities (similar to *X4* station modules).
* **Inventory Management:** Stations maintain distinct storage capacities for each required input and output commodity. They dynamically attempt to maintain a safety buffer of input resources sufficient for multiple upcoming production ticks.
* **Dynamic Throughput Tuning:**
  * **Overclocking:** Activated if the output warehouse is completely depleted post-market tick and the station's net profitability exceeds a specific threshold. It increases output volume at a disproportionate penalty to input efficiency.
  * **Underclocking:** Activated if net profitability falls below zero in the preceding tick. It throttles production to a fraction of base output to minimize losses.
  * *Financial Halts:* A factory is strictly forbidden from running if any required input resource is entirely missing, if its output storage is at absolute capacity, or if its cash balance is insufficient to clear its input buy orders. Cash accounts are never allowed to enter negative balances.
* **Operational Costs:**
  * **Maintenance:** A fixed baseline of maintenance commodities must be purchased and consumed per level every tick, regardless of production status.
  * **Property Taxes:** If a station's owner is different from the sovereign sector owner, the station must pay a property tax per tick to the sector lord.
* **Corporate Governance & Corporate Debt:**
  * **Equity Shares:** Every station's ownership is split into exactly 100 fractional equity shares (each representing 1%). The entity holding the strict majority of shares assumes operational control.
  * **Capital Financing:** Factions can finance station construction by lending starting capital at a defined interest rate. Stations must service these interest debts to their lenders every tick. Interest obligations begin processing only once Level 1 construction completes.

### 3.4 Abstract Logistical Shippings (The Merchant Firms)
* **Abstract Shipping Fleets:** Stations maintain an internal inventory of abstract cargo assets representing their transport capability. Stations attempt to keep this capacity fully supplied by purchasing shipping units. Cargo units are not consumed during standard trade, but can be destroyed via piracy.
* **Independent Trader Stations:** Merchant firms operate as cheap, low-cost commercial stations. They hold an inventory of commercial cargo units that dictates their total trade capacity.
* **Inter-Market Trade Routes:** Merchant firms establish macroeconomic trade routes that act as pipelines transporting commodity volume between distinct sector markets (inspired by *Victoria 3* trade loops).
* **Algorithmic Pathfinding & Distance Sinks:**
  * To maximize processor efficiency across large galaxies, merchant routing calculation is restricted by an adjustable jump-range radius.
  * Distance is simulated via an automatic volume penalty: each sector hop beyond the home market introduces a percentage-based throughput penalty per route level.
  * Traders scan connected markets, compute price deltas, factor in the distance volume malus, account for local tariffs/tariffs of transit sectors, and execute the highest-margin route.
* **Route Recalibration:** If trade capacity drops, firms down-level or dissolve their lowest-performing routes. Routes making a net loss are immediately throttled.
* **Hostile Hazard Avoidance:** Traders dynamically calculate sector risk. They will automatically reroute or terminate operations if a sector belongs to a faction at war with their owner, or if the piracy value exceeds a safe threshold.

### 3.5 Demographics & Labor
* **The Labor Pool:** Sektors possess a fixed or dynamically scaling population density providing *Labor Units*.
* **Population Entities:** Pop entities act as economic consumers. They own a cash account and a consumption warehouse. They generate Labor Units proportional to their size and sell them directly onto the local market without requiring material inputs.
* **The Consumer Loop:** With their earned wages, the population pays local sector taxes, and utilizes the remaining cash balance to issue buy orders for consumer goods to sustain and grow their population size.

---

## 4. BROAD COMMODITY CLASSIFICATION
To allow the coding agent complete architectural freedom, commodities are categorized broadly rather than by rigid names. The agent should implement an extensible asset matrix matching these profiles:
* **Raw Harvestables:** Extracted from sector resource nodes that possess finite capacities and dynamic replenishment rates. Extraction difficulty should scale inversely with the node's remaining volume.
* **Refined Elements:** Intermediate industrial components processed directly from raw harvestables.
* **High-Tech Infrastructure Components:** Complex components required for advanced station upgrades and maintenance.
* **Logistical & Military Asset Units:** Abstracted commercial and military shipping components produced exclusively at specialized shipyards. They are bought, sold, and stocked like standard commodities.
* **Sustenance Goods:** Consumer products purchased and drained continuously by population entities to maintain labor output.
* **Abstract Utility Units:** Intangible market assets (e.g., Labor Units) generated by populations and purchased by factories to drive production.

---

## 5. MILITARY, WARFARE, & SECURITY LAYER

### 5.1 Abstracted Military Fleets
* **Fleet Formations:** Factions maintain military forces abstracted as a numerical value of combat fleet strength, built directly by buying military shipping units from markets.
* **Logistical Bases:** Every fleet links to a physical Military Base Station. This base buys military assets and purchases labor units from the local market to crew the fleets. If the labor supply drops, the corresponding fleet suffers a severe combat efficiency penalty.
* **Strategic Deployment:** Fleets possess a coordinate location on the hex grid and move incrementally across sectors over multiple simulation ticks.

### 5.2 Combat Resolution & Sector Conquest
* **Sustained Engagements:** When hostile fleets occupy the same sector, engagement triggers automatically. Combat calculates total fleet strengths and applies a highly tuned, low-impact attrition coefficient per tick to ensure battles are long, strategic campaigns rather than instant wipes.
* **Sovereignty Capture:** Sector conquest is a deliberate, slow process. If an invading fleet establishes absolute naval supremacy in a sector (>90% strength vs defenders), a conquest progression meter advances incrementally over a high number of ticks until ownership flips.

### 5.3 Piracy & The Black Market Economy
* **Sector Piracy Index:** Every sector tracks an abstract piracy percentage based on the presence of raiders versus patrolling security fleets.
* **Security Patrols:** Fleets assigned to defensive patrol modifiers suppress the local piracy index with an efficiency bonus. Piracy combat features low casualty rates to simulate asymmetric guerrilla warfare.
* **Interception Events:** High piracy values trigger random cargo interception checks. When triggered, a portion of legal market order commodities are stolen from active trades and transferred directly to a hidden *Pirate Base Base*.
* **The Pirate Loop:** Pirate stations exist as hidden entities outside standard sector governance. They require no upkeep or labor. They act as black-market hubs, creating sell orders for their plundered goods in adjacent legal sectors, and using the resulting illicit cash to place buy orders for heavy military components to spawn further raiders.

---

## 6. DIPLOMACY & SCENARIO FRAMEWORKS
* **State Relations:** Factions track diplomatic stances (Peace, Alliance, War) managed by an automated AI evaluation matrix.
* **War Exhaustion:** Conflicts track a progressive war exhaustion index driven by fleet casualties, economic stagnation, and sector losses, forcing factions to negotiate peace deals.
* **Extensible Sandbox Scenarios:** The game initializes via pre-configured scenario files. The system must support at least two baseline initialization states:
  1. A small, tightly contained sandbox map with minimal entities to verify microeconomic loops, currency exchange stabilization, and market order matching.
  2. A massive, multi-faction galactic map with complex trade networks and geopolitical tension.

---

## 7. USER INTERFACE & PRESENTATION SPECIFICATIONS
* **Tech Stack Boundaries:** Engineered in Python using **Pygame**. 
* **Target Resolution:** Designed natively for full-screen display at **1920x1080** resolution.
* **Visual Aesthetic:** Clean, scannable dark-mode strategy interface utilizing flat stylized iconography, crisp text layouts, and non-animated layout structures.
* **Analytical Dashboards:** The UI must provide historical data visualization screens, including commodity price charts, faction net-worth graphs, and factory profit logs.

---

## 8. STABILITY & BALANCING PROTOCOLS
* **Floating-Point Conservation:** The agent must implement a precision rounding system (e.g., standard fixed-point decimals) to guarantee that micro-fractions generated during pro-rata distribution or currency conversions never cause systemic capital drift or create wealth out of nothing.
* **Global Configuration Hub:** All numerical parameters—including base production formulas, combat attrition variables, piracy scaling constants, and market damping variables—must be externalized into a single, well-commented configuration file for easy balancing.

# Alerts and agent

How PlanGraph finds problems, prices them in dollars, answers questions about them, and recommends what to do.

## 1. The alert engine

The nightly pipeline ends with the alert engine (`src/plangraph/alerts/`). It has three parts:

| File | Job |
|---|---|
| `rules.py` | Decide **what** is a problem. Plain functions on DataFrames, no database, easy to test. |
| `impact.py` | Decide **how much it matters**: walk the graph to get dollars at risk. |
| `engine.py` | Run every rule, price each hit, write `Alert` nodes. |

### The six rules

| Alert type | Fires when | Threshold and why |
|---|---|---|
| `late_po` | An open PO is past its due date | 3 days or more. One or two days late is normal and would be noise. |
| `po_at_risk` | An open PO is not late yet, but the model expects it to be | `late_risk` ≥ 0.40, twice the typical late rate of 20%. |
| `stockout` | A purchased item's projected stock goes below zero | Within the 6-week horizon. |
| `demand_surge` | A product group sold far more than forecast last month | Actual above the P90 forecast, at least 1.5× the forecast, and at least 50 extra units for the group. |
| `scrap_spike` | A work centre scraps far more than usual | At least 5% of units in the last 21 days and at least 3× its own normal rate. |
| `target_gap` | A territory is projected to miss its sales target | Projection more than 15% under target. |

All thresholds are constants at the top of `rules.py`.

**Why 15% for targets.** Sales reps in this dataset reach about 84% of quota on average. A 5% tolerance would flag every territory every quarter, and an alert that always fires gets ignored.

**Why demand surges are grouped.** Twenty touring bike models surging at once is one event. The rule groups products by subcategory, so the planner gets one alert, not twenty.

**Why scrap needs a "likely source".** A work order passes through several work centres. When Paint scraps 37% of its frames, every work centre on the same route also shows a raised scrap rate. The rule picks the centre with the highest *rate × share of scrapped units* and lists the others as "also elevated".

### Severity
- **high:** $50,000 or more at risk, or urgent (stockout within 2 weeks, scrap spike, PO 14+ days late).
- **medium:** some money at risk, or a PO 7+ days late.
- **low:** worth knowing, nothing at risk yet.

## 2. Pricing an alert: the dollars-at-risk traversal

This is the centre of the project. A late PO only matters if it stops a customer order from shipping.

### Worked example: PO 2538

PO 2538 from Custom Frames, Inc. is 9 days late. It contains five metal sheet parts.

**Step 1: which products use these parts?** The graph walks up the bill of materials from each part to every product that has open orders:

```cypher
MATCH (part:Product) WHERE part.uid IN $part_uids
MATCH path = (fg:Product)-[:HAS_COMPONENT*0..4]->(part)
WHERE EXISTS { (:SalesOrder {status: 'Open'})-[:CONTAINS]->(fg) }
```

Metal sheet is in tubes, tubes are in frames, frames are in bikes. Open orders for all these products add up to **$3,857,641**. This is the revenue **exposed**.

**Step 2: can finished stock cover the orders?** For each product: `short = open order units − finished units in stock`. Most bikes have enough stock. Only the short units need building.

**Step 3: are there enough parts to build the short units?** `part need = short units × parts per unit`, summed over all products. In the scenario, stock of each metal sheet covers only 25% of that need. So 75% of the short units cannot be built.

**Step 4: turn that into money.** For each product, `blocked share = (short units ÷ open units) × (1 − share the part can cover)`. Multiply each open order line by its product's blocked share and add up.

Result: **$319,813 at risk**, across 33 products and all 10 territories. The first affected customer order is due in 12 days: that is the early warning.

```python
usage["short_qty"] = (usage["open_qty"] - usage["fg_on_hand"]).clip(lower=0)
usage["part_need"] = usage["short_qty"] * usage["qty_per_unit"]
covered = (usage["part_on_hand"] / total_need).clip(upper=1)
usage["blocked"] = (usage["short_qty"] / usage["open_qty"]) * (1 - covered)
```

**Why both numbers matter.** Four other POs are also late. Each "exposes" between $0.3M and $3.3M, because their parts are in many products. But their parts are in stock, so each has **$0 at risk**. Ranking by exposure would send a planner chasing five vendors. Ranking by revenue at risk points at one.

### From alert to financial target
Each at-risk order was sold by a sales rep, and each rep has a target for the current period. The alert gets a `THREATENS` relationship to each of those targets, carrying the dollars at risk. That is the full chain:

```
(Alert)-[:ABOUT]->(PurchaseOrder)-[:FROM]->(Vendor)
(Alert)-[:ABOUT]->(Product)                       the products that cannot ship
(Alert)-[:THREATENS {usd_at_risk}]->(Target)<-[:HAS_TARGET]-(SalesPerson)-[:COVERS]->(Territory)
```

### No double counting
Several alerts can point at the same blocked orders (the late PO, the at-risk PO and six stockout alerts all describe the Custom Frames problem). For the target tracker, revenue at risk is computed **once** for all problem parts together and stored on each `Target`. Adding up the alerts' own figures would count the same $320k eight times.

### Alert lifecycle
Alerts have stable IDs such as `Alert:late_po:2538`. Each run upserts them: an alert that fires again is updated, one that no longer fires is marked `cleared` and kept. Decisions stay attached.

### Current output (planning date 2026-11-14)
25 open alerts: 7 stockout, 6 PO at risk, 5 late PO, 5 target gap, 1 demand surge, 1 scrap spike. Twelve belong to the four injected scenarios. The other 13 are discussed in [EVALUATION.md](EVALUATION.md).

## 3. The agent

The Ask page sends a question to `POST /ask`. What happens next depends on whether a Gemini API key is set.

### 3.1 The tools
A tool is a Python function the LLM can ask to run. Each one executes **one fixed Cypher template** in a read-only session (`src/plangraph/agent/tools.py`).

| Tool | Answers |
|---|---|
| `open_alerts` | What is going wrong? Filter by type or territory. |
| `late_po_exposure` | Which late POs expose the most open-order revenue? |
| `where_used` | Which finished goods use this part? |
| `alternate_vendors` | Who else can supply this part, at what lead time and price? |
| `target_variance` | Target vs shipped, open and supply-blocked revenue per territory. |
| `scrap_by_location` | Scrap by work centre and reason. |
| `stock_cover` | Stock, open POs, weekly demand and stockout week for one product. |
| `search_documents` | Vector search over policies, procedures and emails. |
| `graph_query` | Last resort: the LLM writes a new read-only query (Text2Cypher). |

```python
@tool(response_format="content_and_artifact")
def alternate_vendors(product: str):
    """Vendors that can supply a part, with lead time, price, credit rating and late history.
    Use for re-sourcing questions. product: a part name, SKU or uid."""
    return run_template(queries.ALTERNATE_VENDORS, product=product, limit=ROW_LIMIT)
```

The docstring is what the LLM reads to decide when to use the tool, so it is written as an instruction. Each tool returns two things: the rows as text for the LLM, and an **artifact** (the Cypher, parameters and rows) that the UI shows to the user.

### 3.2 Why templates first
The LLM chooses *which* query to run and with *what* parameters. It does not write the query. Reasons:

- A template has been tested and returns the same shape every time.
- The business logic (what counts as late, how BOM quantities multiply) is written once, by a person.
- A parameter such as a territory name is passed as a query parameter, so it cannot change the query.

Text2Cypher exists for questions no template covers, such as "which components go directly into this bike?".

### 3.3 The loop
`src/plangraph/agent/graph.py` defines the agent as a LangGraph state machine with two steps:

```
START → model → (did the model ask for tools?) → tools → model → ... → END
```

```python
graph = StateGraph(AgentState)
graph.add_node("model", call_model)            # the LLM reads the conversation
graph.add_node("tools", ToolNode(tools))       # runs the tools it asked for
graph.add_edge(START, "model")
graph.add_conditional_edges("model", tools_condition)   # tool calls → "tools", else END
graph.add_edge("tools", "model")
```

A hard limit of 12 steps stops a confused model from looping forever.

### 3.4 A traced example
Question: *"Why is Canada tracking under quota? Is it supply or demand?"* The intended path:

1. **model** → asks for `target_variance(territory="Canada")`.
2. **tools** → target $1,220,000, shipped $487,472, open $571,392, blocked by supply $55,222. Projection $1,003,642, a gap of $216,358.
3. **model** → asks for `open_alerts(territory="Canada")` to find what blocks the $55k.
4. **tools** → the late Custom Frames PO and the metal sheet stockouts.
5. **model** → answers: Canada is projected 18% under target. About a quarter of the gap ($55k) is supply: metal sheet from Custom Frames is 9 days late. The rest is demand: even if every open order ships, Canada reaches $1.06M of $1.22M.

The response carries the answer, every step with its Cypher and rows, and the `uid`s of the evidence nodes.

**Not verified live.** This environment has no Gemini key, so the path above is the designed behaviour, not a recorded run. The loop itself is tested with a scripted fake model (`tests/test_agent.py`).

### 3.5 The system prompt
The prompt (`SYSTEM_PROMPT` in `graph.py`) gives the model its role and its working rules: get every number from a tool, use the most specific tool first, check both supply and demand before answering a "why" question, keep "at risk" and "exposed" apart, and say so plainly when the tools return nothing.

### 3.6 Offline mode
Without an API key, `src/plangraph/agent/offline.py` answers with keyword routing: words in the question pick one tool ("alternate" → `alternate_vendors`, "quota" → `target_variance`), product and territory names are matched against the graph, and the rows are shown as the answer. It cannot chain steps or write prose. It keeps the app usable for free, and it is the "no LLM" baseline in the evaluation.

## 4. Guardrails for LLM-written Cypher

A query written by an LLM is untrusted input. Four layers protect the database (`src/plangraph/agent/guardrails.py`):

| Layer | What it does | What it stops |
|---|---|---|
| 1. Static validation | Rejects write and admin keywords (`CREATE`, `MERGE`, `DELETE`, `SET`, `DROP`, `CALL`, `LOAD CSV`...), multiple statements, and queries that do not start with `MATCH`/`WITH`/`UNWIND`/`RETURN` | Obvious write attempts, before they reach the database |
| 2. Read-only session | The query runs with `default_access_mode=READ` | Any write that slips past layer 1. Neo4j itself refuses it. |
| 3. Timeout | 10 seconds | A runaway query that would hold the database |
| 4. Row limit | Adds `LIMIT 50` if missing, caps larger limits, trims long text, drops embeddings | Flooding the LLM's context or the UI |

Example: the model produces `MATCH (n) DETACH DELETE n`.
Layer 1 raises `UnsafeCypherError("Keyword not allowed: DETACH")`. The error is sent back to the model once so it can try again. If layer 1 were bypassed, layer 2 would answer `Writing in read access mode not allowed`. Both behaviours are covered by tests.

Keywords inside quoted strings are ignored, so a product called "Seat Set" does not trip the `SET` rule.

Layer 1 is deliberately strict: it blocks all `CALL`, including harmless ones. A wrongly rejected query costs one retry. A wrongly accepted one could cost data.

## 5. Decision automation

Selecting an alert in the inbox shows ranked actions from `src/plangraph/agent/actions.py`.

| Alert type | Actions offered |
|---|---|
| Late PO, PO at risk | Re-source each open part to the fastest alternate vendor within the 10% price rule; expedite the PO |
| Stockout | Place a rush PO for 4 weeks of demand; check other locations for stock |
| Scrap spike | Quality hold on the work centre; re-plan work orders |
| Demand surge | Review the forecast (one-off or new run rate); check component stock |
| Target gap | Prioritise the territory in allocation; raise at S&OP |

Each action has a rationale, an estimated cost, who must approve it, and a draft message to the vendor. The numbers come from the policy documents: premium freight costs about 10% of the PO value, and a planner may approve up to $5,000.

*Example:* for PO 2538 the metal sheets have no alternate vendor, so the list says "No alternate vendor available (single source)" and offers "Expedite PO 2538" at an estimated $2,432, within the planner's limit, protecting $319,813.

### Human in the loop
Nothing is sent or changed automatically. Clicking **Approve** calls `POST /actions/approve`, which writes:

```cypher
MATCH (a:Alert {uid: $alert_uid})
CREATE (d:Decision)-[:RESOLVES]->(a)
SET d = $decision, a.resolution = $label
```

The `Decision` node records the action, the approver, the time, the estimated cost and a note. Decisions survive the nightly reload, so the graph keeps a history of who decided what about which alert.

In a real deployment the approval would also trigger the action in the ERP or send the email. Here it stops at the record.

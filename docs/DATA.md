# Data

What data the project uses, what each table represents in a real company, how each one is used, and which parts are real and which are simulated.

## 1. The dataset: AdventureWorks

**What it is.** AdventureWorks is a sample database published by Microsoft. It describes a fictional company, Adventure Works Cycles, that builds bicycles and sells them to resellers (bike shops) and online customers. It ships as plain CSV files in Microsoft's public GitHub repository, under the MIT licence.

**Why this dataset.** Most public supply chain datasets cover one area only: an orders file, or a deliveries file. Planning problems live *between* areas. AdventureWorks is useful because one consistent set of IDs runs through sales, purchasing, production, inventory and sales quotas. Product 782 is the same product in a sales order, a work order, a stock record and a bill of materials. That is what lets a late purchase order be traced to a sales target.

**What it is not.** It is sample data. The numbers were generated to look plausible, not recorded from a real business, and some parts do not agree with each other (section 4 explains what was done about that).

**How it gets here.** `src/plangraph/etl/download.py` downloads 18 CSV files into `data/raw/` on the first pipeline run. The files have no header row and are tab-separated, so `src/plangraph/etl/tables.py` lists every column name and which columns are kept.

## 2. Five simulated source systems

A real manufacturer runs separate software for each function. To imitate that, each group of tables is loaded into its own Postgres schema. Nothing joins them until the staging step, exactly the problem a real data team faces.

| Postgres schema | Real-world system it imitates | Who uses it |
|---|---|---|
| `sales` | Order management system (OMS) | Sales, customer service |
| `purchasing` | Purchasing module of the ERP | Buyers, supply planners |
| `production` | Manufacturing execution system (MES) and product master | Plant, production planners |
| `inventory` | Warehouse management system (WMS) | Warehouse |
| `finance` | Planning tool of the finance team (FP&A) | Finance, S&OP lead |

Row counts below are after preparation, with the planning date at 2026-11-14.

### 2.1 Sales system

#### `sales.sales_order_header` (28,342 rows)
**Represents:** one customer order.

| sales_order_id | order_date | due_date | ship_date | status | online_order_flag | sales_person_id | territory_id | sub_total |
|---|---|---|---|---|---|---|---|---|
| 43659 | 2023-12-14 | 2023-12-26 | 2023-12-21 | Shipped | 0 | 279 | 5 | 20,565.62 |

- `online_order_flag` splits the business in two: `0` is a reseller order placed through a sales rep (large, arrives in one batch at each month end), `1` is a small online order with no sales rep.
- `status` is `Open` or `Shipped`. 650 orders are open, worth $4.06M.
- `sub_total` is the order value before tax and freight. This is the revenue figure used everywhere.

**Becomes in the graph:** a `SalesOrder` node, linked `IN_TERRITORY` to a `Territory` and `SOLD_BY` to a `SalesPerson`.
**Used for:** open orders are the revenue that supply problems put at risk. Shipped plus open orders per sales rep are compared with targets. The full history trains the demand forecast.

#### `sales.sales_order_detail` (113,687 rows)
**Represents:** one line of an order: a product and a quantity.

| sales_order_id | sales_order_detail_id | order_qty | product_id | unit_price | line_total |
|---|---|---|---|---|---|
| 43659 | 1 | 1 | 776 | 2,024.99 | 2,024.99 |

**Becomes:** a `(SalesOrder)-[:CONTAINS {qty, value}]->(Product)` relationship.
**Used for:** this is the link from money to products. Summed by product and month it is the demand history the forecast learns from.

#### `sales.sales_territory` (10 rows)
**Represents:** a sales region: Northwest, Northeast, Central, Southwest, Southeast, Canada, France, Germany, Australia, United Kingdom.
**Becomes:** a `Territory` node. **Used for:** grouping revenue, risk and targets the way managers ask about them.

### 2.2 Purchasing system

#### `purchasing.vendor` (104 rows)
**Represents:** a supplier. `credit_rating` runs from 1 (best) to 5 (worst).
**Becomes:** a `Vendor` node. **Used for:** vendor reliability features in the late-PO model, and judging alternate vendors.

#### `purchasing.product_vendor` (460 rows)
**Represents:** an approved source: "this vendor may supply this product, with this lead time and price".

| product_id | vendor_id | average_lead_time | standard_price |
|---|---|---|---|
| 1 | 1580 | 17 | 47.87 |

**Becomes:** `(Vendor)-[:SUPPLIES {lead_days, price}]->(Product)`.
**Used for:** the "is there an alternate vendor?" question. Of 265 purchased products, 113 have one vendor (single source), 109 have two and 43 have three.

#### `purchasing.purchase_order_header` (2,820 rows)
**Represents:** one order sent to a vendor. Status is `Open`, `Received` or `Rejected`.
**Becomes:** a `PurchaseOrder` node with `FROM` to its `Vendor`.

#### `purchasing.purchase_order_detail` (6,281 rows)
**Represents:** one line of a PO: a part, a quantity and a due date.

| purchase_order_id | due_date | order_qty | product_id | unit_price | received_qty | received_date |
|---|---|---|---|---|---|---|
| 1 | 2023-11-13 | 4 | 1 | 50.26 | 3 | 2023-11-11 |

**Becomes:** `(PurchaseOrder)-[:CONTAINS {qty, due_date, is_open, days_late}]->(Product)`.
**Used for:** an open line past its due date is a late PO. Open lines are incoming supply in the inventory projection. Past lines are the training data for the late-PO model.
**Caveat:** `received_date` does not exist in AdventureWorks. It is simulated (section 4.3).

### 2.3 Production system

#### `production.product` (504 rows), `product_subcategory`, `product_category`
**Represents:** the product master: every item the company makes, buys or sells, from "Chainring Bolts" to "Mountain-200 Black, 42".

Two flags describe each product:
- `make_flag`: 1 = built in the plant, 0 = bought from a vendor.
- `finished_goods_flag`: 1 = can be sold to customers.

| make_flag | finished_goods_flag | Count | Example |
|---|---|---|---|
| 1 | 1 | 212 | Bikes and frames, built and sold |
| 0 | 1 | 83 | Helmets, tyres, jerseys: bought and resold |
| 1 | 0 | 27 | Subassemblies such as forks and tubes |
| 0 | 0 | 182 | Purchased components such as metal sheet and bolts |

**Becomes:** a `Product` node. Finished goods and components share one label on purpose, because the BOM links products to products.

#### `production.bill_of_materials` (2,679 rows, 2,383 current)
**Represents:** one "parent contains child" link of the recipe.

| product_assembly_id | component_id | bom_level | per_assembly_qty | end_date |
|---|---|---|---|---|
| 749 (a bike) | 807 (a headset) | 1 | 1.0 | |

- Rows with an `end_date` are old versions and are left out.
- Rows with no `product_assembly_id` only mark top-level products and are left out.

**Becomes:** `(Product)-[:HAS_COMPONENT {qty}]->(Product)`.
**Used for:** this is the most important relationship in the project. It carries a part shortage up to the finished goods that need it, and carries a finished goods forecast down to the parts it will consume. Example path: `Mountain-200 Black, 42` → `HL Mountain Frame - Black, 42` → `Top Tube` → `Metal Sheet 2`.

#### `production.work_order` (69,070 rows) and `work_order_routing` (63,603 rows)
**Represents:** a work order is a production job for one product. Its routing rows list the work centres it passes through.

| work_order_id | product_id | order_qty | scrapped_qty | start_date | end_date | scrap_reason_id |
|---|---|---|---|---|---|---|
| 1 | 722 | 8 | 0 | 2023-12-17 | 2023-12-27 | |

**Becomes:** a `WorkOrder` node with `PRODUCES` to a `Product`, `ROUTED_THROUGH` to each `Location`, and `SCRAPPED_FOR` to a `ScrapReason` when units were scrapped.
**Used for:** the scrap spike alert and the operations manager's questions.

#### `production.scrap_reason` (16 rows)
**Represents:** the list of reasons a unit can be scrapped, such as "Paint process failed" or "Stress test failed". **Becomes:** a `ScrapReason` node.

### 2.4 Inventory system

#### `inventory.product_inventory` (1,069 rows) and `inventory.location` (14 rows)
**Represents:** how many units of each product sit in each location. Locations are both storage areas (Finished Goods Storage, Miscellaneous Storage) and work centres (Frame Welding, Paint, Final Assembly).

| product_id | location_id | quantity |
|---|---|---|
| 1 | 1 (Tool Crib) | 3,628 |

**Becomes:** `(Product)-[:STOCKED_AT {on_hand}]->(Location)`. The product's total is also stored on the node as `on_hand` to keep queries short.
**Used for:** deciding whether a late part matters. If stock covers the open orders, a late PO costs nothing.

### 2.5 Finance system

#### `finance.sales_person` (17 rows)
**Represents:** a sales rep and their home territory. Three managers have no territory in the sample; staging assigns each the territory where they sold the most.
**Becomes:** a `SalesPerson` node with `COVERS` to a `Territory`. The sample's name table is not loaded, so reps appear as "Sales rep 279".

#### `finance.sales_person_quota_history` (163 rows)
**Represents:** the sales quota of one rep for one quota period.

| sales_person_id | quota_date | sales_quota |
|---|---|---|
| 274 | 2023-12-14 | 28,000 |

- A quota period starts on its `quota_date` and ends the day before the next one, about 91 days.
- The current period is 2026-09-14 to 2026-12-13, with $10.36M of quota across 17 reps.

**Becomes:** a `Target` node, linked from its rep with `HAS_TARGET`.
**Used for:** the financial end of every chain. An alert that blocks an open order `THREATENS` the target of the rep who sold it.
**Good to know:** over the 12 periods in the sample, reps reached about 84% of quota on average. Quotas in this dataset are simply set high. The target alert allows for this (see [ALERTS_AND_AGENT.md](ALERTS_AND_AGENT.md)).

### 2.6 Documents (`data/docs/`, 20 markdown files)

**Represents:** the unstructured knowledge of a company: 6 policies, 6 procedures (SOPs), 5 emails, a finance memo, vendor notes and a glossary. They were written for this project.
**Why they exist:** some questions are answered by text, not by data: "What is our expedite policy?", "Why is Custom Frames late?". The emails also give the *reason* behind a pattern the data only shows.
**Becomes:** `DocChunk` nodes with an embedding, linked with `MENTIONS` to any `Vendor` named in the text.

### 2.7 Tables in AdventureWorks that are not used

Customers, people, addresses, stores, special offers, transaction history, HR and product descriptions are skipped. None of them is needed for the planning questions, and leaving them out keeps the model small enough to explain. Adding customer names would be the natural next step.

## 3. From sample to "a company that is live today"

The sample is a finished history from 2022 to 2025: every order shipped, every PO complete. A planning tool needs a present moment with unfinished business. `src/plangraph/etl/prepare.py` creates one in six steps, each a small function.

### 3.1 Pick a planning date
The planning date is **30 April 2025 in sample time**. It was chosen because:
- it is the day the last large batch of reseller orders was placed, so there are fresh open orders,
- it is two months into the quota period that started on 28 February 2025, so the target question is live,
- reseller orders stop after this date in the sample.

Everything created after the planning date is dropped: to the company it has not happened yet.

### 3.2 Open sales orders
An order whose ship date is after the planning date has not shipped yet, so its status becomes `Open` and its ship date is cleared. This gives 650 open orders. Nothing is invented here: it is the sample's own data, viewed from a date in the middle.

### 3.3 Open work orders
The same logic: a work order that ends after the planning date is in progress.

### 3.4 Shift all dates to today
Every date column is moved forward by the same number of days, so that the planning date lands on the day the pipeline runs (or on `AS_OF` if you set it). With the planning date at 2026-11-14 the shift is 563 days.

```python
def shift_dates(frames, days):
    delta = pd.Timedelta(days=days)
    ...
    frame[column] = frame[column] + delta
```

Shifting everything by one constant keeps every gap between dates unchanged: lead times, days late and order-to-ship times are exactly as before.

The two synthetic steps (3.5 and 3.6 below, described in section 4) run before the shift.

## 4. What is real and what is synthetic

"Real" below means "as published in the AdventureWorks sample", which is itself fictional. "Synthetic" means generated or changed by this project. A fixed random seed (42) makes every run produce the same data.

| Data | Status | Detail |
|---|---|---|
| Products, categories, bill of materials | Real | Unchanged |
| Vendors, approved sources, lead times, prices | Real | Unchanged |
| Sales orders and lines up to the planning date | Real | Only the status is derived |
| Purchase orders, lines, due dates, quantities | Real | Unchanged |
| Work orders, routings, scrap reasons | Real | Except the injected scrap spike |
| Sales territories, reps, quotas | Real | Unchanged |
| All dates | Shifted | Same constant added to every date |
| **PO receipt dates** | **Synthetic** | Section 4.3 |
| **Stock of purchased items** | **Synthetic** | Section 4.4 |
| **Four disruptions** | **Synthetic** | Section 5 |
| **Company documents** | **Synthetic** | Written for this project |

### 4.3 Why PO receipt dates are simulated
AdventureWorks records when a PO line is *due* but not when it *arrived*. Without arrival dates there is no "late", and nothing for a late-PO model to learn.

`add_po_receipts` simulates it:
1. Each vendor gets its own chance of delivering late, between 3% and 60%. A worse credit rating raises it.
2. Each PO is late with that probability. A late PO arrives about 6 days after its due date on average. An on-time PO arrives 0 to 2 days early.
3. A PO whose simulated arrival is after the planning date has not arrived: it is `Open`.

About 20% of delivered POs end up late. **Consequence:** the late-PO model's score measures how well it recovers this simulation. It demonstrates the method, not a finding about real vendors.

### 4.4 Why stock of purchased items is calibrated
The sample's stock quantities were not generated to match its order volumes. Taken as they are, most parts look weeks short of demand, so every part would raise an alert and a real problem could not be told from noise.

`calibrate_stock` sets the stock of each purchased item to between 8 and 12 weeks of its recent demand. Demand for a part is worked out by pushing three months of product sales down through the BOM. Stock of manufactured products is left as published.

This gives a healthy baseline. The disruptions in the next section then break specific items on purpose.

## 5. The four injected disruptions

A planning tool is only useful if its alerts catch real problems. To measure that, four known problems are written into the data by `src/plangraph/etl/scenarios.py`, and each one is recorded in the table `meta.scenario_truth`. The evaluation later checks that an alert of the expected type points at the recorded entities.

| Scenario | What is changed | Why this scenario | Alert that should catch it |
|---|---|---|---|
| **Vendor slip** | Custom Frames, Inc. delivers 14 days late for its last 6 weeks of orders. Two of its POs are overdue. Stock of its 6 metal sheet parts has run down to 25% of what open orders need. | The flagship case: a supply delay that reaches revenue through the BOM | `late_po` on PO 2538 or 2617 |
| **Demand surge** | Open reseller orders for touring bikes in the Southwest are multiplied by 3 | Demand, not supply, moves away from plan | `demand_surge` on touring bike products |
| **Stock shortfall** | Stock of Lock Ring, the busiest purchased part with nothing on order, is cut to one week of demand | A shortage with no late PO to point at | `stockout` on Lock Ring |
| **Scrap spike** | Work orders through the Paint work centre scrap 25% to 40% of their units for 3 weeks | A production quality problem | `scrap_spike` on Paint |

The vendor for the first scenario is not hard-coded. The code picks the vendor whose parts feed the most open-order revenue that finished stock cannot cover, so the scenario is always one that matters.

Several documents in `data/docs/` tell the same stories in words (the Custom Frames delay email, the Southwest promotion email, the Paint supervisor's email), so the agent can find the *reason* as well as the numbers.

## 6. Staging: the first place the systems meet

`src/plangraph/etl/staging.sql` builds a `stg` schema of clean tables by joining across the five source schemas. For example `stg.product` combines the product master (production), whether any vendor supplies it (purchasing) and total stock (inventory):

```sql
CREATE TABLE stg.product AS
SELECT p.product_id, p.product_number AS sku, p.name, ...
       EXISTS (SELECT 1 FROM purchasing.product_vendor pv
               WHERE pv.product_id = p.product_id) AS is_purchased,
       COALESCE(i.on_hand, 0)::int AS on_hand
FROM production.product p
LEFT JOIN (SELECT product_id, SUM(quantity) AS on_hand
           FROM inventory.product_inventory GROUP BY 1) i USING (product_id);
```

Staging also derives the fields the rest of the project relies on: `is_open` and `days_late` for PO lines, `is_current` for targets, and the home territory of each sales rep.

## 7. Data quality checks

Before anything is loaded into the graph, `src/plangraph/etl/quality.py` runs 17 checks. Each one is a SQL query that counts bad rows, so every check should return zero. If one fails, the pipeline stops.

| Kind of check | Examples |
|---|---|
| Row counts | Staging has the same number of sales orders, order lines, PO lines and work orders as the sources |
| Keys | Product, sales order and target IDs are unique |
| Orphans | Every order line has a product, every PO has a vendor, every BOM component exists |
| Business rules | No negative stock, no order dated in the future, open orders have no ship date, exactly one current quota period, no product that contains itself |

All 17 pass on the current data.

## 8. Other tables the pipeline creates

| Table | Content |
|---|---|
| `meta.run_info` | The planning date (`as_of`), the date shift in days and the random seed |
| `meta.scenario_truth` | The four injected disruptions |
| `ml.demand_backtest`, `ml.demand_forecast` | Forecast test results and the forecast for the next 3 months |
| `ml.po_risk` | Late-risk score for each open PO |
| `ml.inventory_projection` | Stock projection for each purchased item |
| `ml.metrics` | Model quality metrics from the last run |

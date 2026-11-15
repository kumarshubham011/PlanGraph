# Graph model

How the planning data is modelled as a graph, why, and what each query template does.

## 1. Why a graph for this problem

The questions planners ask are chains:

> late purchase order → part → (bill of materials, several levels) → finished product → open sales orders → sales rep → sales target

Each arrow crosses into a different source system. In SQL each arrow is a join, and the BOM step is a recursive join because its depth is not fixed. In a graph each arrow is a relationship you follow.

The graph is the "current picture" layer. It holds master data (products, vendors, BOM), one year of orders, and what the models and alert engine produce. Older history stays in Postgres, where the models read it for training.

## 2. Nodes

Every node has a `uid` such as `Product:782`, made of its label and its source ID. A uniqueness constraint on `uid` exists for every label (`src/plangraph/graph/schema.cypher`). The constraint also creates an index, which makes loading and lookups fast.

| Label | One node is... | Main properties | Count |
|---|---|---|---|
| `Product` | An item that is made, bought or sold | `name`, `sku`, `category`, `list_price`, `is_finished_good`, `is_manufactured`, `is_purchased`, `on_hand`, and from the models `weekly_demand`, `weeks_of_cover`, `stockout_week` | 504 |
| `Vendor` | A supplier | `name`, `rating` (1 best to 5 worst), `is_active` | 104 |
| `Location` | A storage area or work centre | `name` | 14 |
| `PurchaseOrder` | An order to a vendor | `status`, `due_date`, `days_late`, `is_late`, `total_value`, and from the model `late_risk` | 2,484 |
| `SalesOrder` | A customer order | `status`, `order_date`, `due_date`, `is_online`, `total_value` | 21,213 |
| `Territory` | A sales region | `name`, `region` | 10 |
| `SalesPerson` | A sales rep | `name` | 17 |
| `Target` | One rep's quota for one period | `period_start`, `period_end`, `amount`, `is_current`, `usd_at_risk` | 163 |
| `WorkOrder` | A production job | `status`, `order_qty`, `scrapped_qty`, `end_date` | 34,470 |
| `ScrapReason` | A reason for scrap | `name` | 16 |
| `Forecast` | Forecast demand for one product in one month | `period_start`, `period_end`, `qty`, `p10`, `p90` | 549 |
| `Alert` | A problem found by the alert engine | `type`, `severity`, `title`, `summary`, `usd_at_risk`, `usd_exposed`, `status` | 25 open |
| `Decision` | An action a planner approved | `label`, `approved_by`, `ts`, `note` | grows with use |
| `DocChunk` | A piece of a company document | `title`, `text`, `embedding` | 20 |

## 3. Relationships

| Relationship | Meaning | Properties |
|---|---|---|
| `(Product)-[:HAS_COMPONENT]->(Product)` | Parent contains child (the BOM) | `qty` per parent unit |
| `(Vendor)-[:SUPPLIES]->(Product)` | Approved source | `lead_days`, `price` |
| `(PurchaseOrder)-[:FROM]->(Vendor)` | Who the PO was sent to | |
| `(PurchaseOrder)-[:CONTAINS]->(Product)` | A PO line | `qty`, `received_qty`, `due_date`, `is_open`, `days_late` |
| `(SalesOrder)-[:CONTAINS]->(Product)` | An order line | `qty`, `value` |
| `(SalesOrder)-[:IN_TERRITORY]->(Territory)` | Where the order was sold | |
| `(SalesOrder)-[:SOLD_BY]->(SalesPerson)` | Who gets quota credit (reseller orders only) | |
| `(SalesPerson)-[:COVERS]->(Territory)` | The rep's home territory | |
| `(SalesPerson)-[:HAS_TARGET]->(Target)` | The rep's quota for a period | |
| `(Product)-[:STOCKED_AT]->(Location)` | Stock in a location | `on_hand` |
| `(WorkOrder)-[:PRODUCES]->(Product)` | What the job builds | |
| `(WorkOrder)-[:ROUTED_THROUGH]->(Location)` | Work centres the job passes | |
| `(WorkOrder)-[:SCRAPPED_FOR]->(ScrapReason)` | Why units were scrapped | |
| `(Product)-[:HAS_FORECAST]->(Forecast)` | Model output | |
| `(Alert)-[:ABOUT]->(any node)` | What the alert points at | |
| `(Alert)-[:THREATENS]->(Target)` | Targets it puts at risk | `usd_at_risk` |
| `(Decision)-[:RESOLVES]->(Alert)` | Audit trail | |
| `(DocChunk)-[:MENTIONS]->(Vendor)` | Text linked to data | |

## 4. Modelling decisions

**Order lines are relationships, not nodes.** A line connects exactly one order to one product, so `CONTAINS` with `qty` and `value` on it says everything. This keeps the core query two hops shorter. The cost: a line cannot have relationships of its own. If lines needed to link to shipments or invoices, they would become nodes.

**One `Product` label for finished goods and components.** The BOM connects products to products at any level, and some items are both sold and used as components. Separate labels would force every BOM query to handle both. The `is_finished_good` flag is used when the difference matters.

**Targets belong to sales reps, as in the source data.** Quotas are set per rep, and reps roll up to territories through `COVERS`. An order counts towards the target of the rep who sold it (`SOLD_BY`), which is how the business measures it.

**Model outputs are written back into the graph.** `late_risk` on a PO, `stockout_week` on a product and `Forecast` nodes are stored like any other fact. A question about predictions is then answered the same way as a question about history.

**Alerts and decisions are nodes.** An alert linked to its PO, vendor, products and targets *is* its own explanation: the impact graph screen simply draws that neighbourhood. Decisions linked to alerts give an audit trail that survives the nightly reload.

**Totals are also stored on nodes.** `Product.on_hand` duplicates the sum of its `STOCKED_AT` relationships. This is deliberate: it removes an aggregation from almost every query.

## 5. Loading the graph

`src/plangraph/graph/loader.py` uses one pattern for everything: read rows from staging with a SELECT, send them to Neo4j in batches of 5,000, and create one item per row.

```cypher
UNWIND $rows AS row
MERGE (n:Product {uid: row.uid})
SET n += row
```

- `UNWIND` turns the list of rows into one row at a time, inside the database. One network call handles 5,000 rows.
- `MERGE` on the unique `uid` creates the node or reuses it, so re-running the load is safe.
- `SET n += row` copies every column onto the node.

Relationships work the same way: match the two end nodes by `uid`, then `MERGE` the relationship. Order lines carry a `key` (the line ID), because one order can have two lines for the same product.

A full reload takes about 20 seconds. It deletes everything except `Alert` and `Decision` nodes, then verifies that every node and relationship count in Neo4j equals the row count sent from staging. A mismatch stops the pipeline.

## 6. The query templates

All templates live in `src/plangraph/graph/queries.py`. The alert engine, the API and the agent's tools all run these same queries.

### 6.1 Core query: revenue exposed to late purchase orders

```cypher
MATCH (po:PurchaseOrder {is_late: true})-[l:CONTAINS]->(part:Product)
WHERE l.is_open
MATCH (po)-[:FROM]->(v:Vendor)
MATCH (fg:Product)-[:HAS_COMPONENT*0..4]->(part)
WITH DISTINCT po, v, fg
MATCH (so:SalesOrder {status: 'Open'})-[s:CONTAINS]->(fg)
MATCH (so)-[:IN_TERRITORY]->(t:Territory)
WHERE $territory IS NULL OR toLower(t.name) = toLower($territory)
RETURN po.id AS late_po, v.name AS vendor, po.days_late AS days_late,
       t.name AS territory, count(DISTINCT fg) AS products_hit,
       round(sum(s.value)) AS revenue_exposed
ORDER BY revenue_exposed DESC LIMIT $limit
```

Line by line:

1. Start from late POs and the parts on their lines.
2. Keep only lines that have not been received.
3. Fetch the vendor, for display.
4. Walk **up** the BOM: find every product `fg` that contains the part, 0 to 4 levels above it. Zero levels means the part itself, which matters because some purchased items (helmets, tyres) are sold directly.
5. `DISTINCT` removes duplicates: a bike can reach the same part by several BOM paths, and must be counted once.
6. Find open sales orders that contain those products.
7. Attach the territory.
8. Optional territory filter. `$territory` is a parameter, never pasted into the query text.
9. Sum the order value per PO and territory.

**The same question in SQL** needs a recursive common table expression across four source schemas:

```sql
WITH RECURSIVE where_used AS (
    SELECT l.purchase_order_id, l.product_id AS part_id, l.product_id AS product_id, 0 AS depth
    FROM stg.purchase_order_line l
    JOIN stg.purchase_order o USING (purchase_order_id)
    WHERE o.is_late AND l.is_open
  UNION ALL
    SELECT w.purchase_order_id, w.part_id, b.assembly_id, w.depth + 1
    FROM where_used w
    JOIN stg.bom b ON b.component_id = w.product_id
    WHERE w.depth < 4
)
SELECT u.purchase_order_id, t.name, SUM(sl.value)
FROM (SELECT DISTINCT purchase_order_id, product_id FROM where_used) u
JOIN stg.sales_order_line sl ON sl.product_id = u.product_id
JOIN stg.sales_order so ON so.sales_order_id = sl.sales_order_id AND so.status = 'Open'
JOIN stg.territory t ON t.territory_id = so.territory_id
GROUP BY 1, 2;
```

It works, and for this data size it is fast enough. The graph version is shorter, reads in the order a planner thinks, and stays the same shape as more hops are added (sales rep, target, alternate vendor).

**Exposed is not at risk.** This query returns an upper bound: it counts every open order for every product that uses the part. PO 2538 "exposes" $3.86M. The alert engine goes further and nets off stock, which leaves $320k really at risk. See [ALERTS_AND_AGENT.md](ALERTS_AND_AGENT.md), section 2.

### 6.2 Where-used

```cypher
MATCH path = (fg:Product)-[:HAS_COMPONENT*1..4]->(part)
WHERE fg.is_finished_good
WITH part, fg, sum(reduce(q = 1.0, r IN relationships(path) | q * r.qty)) AS qty_per_unit
```

`reduce` multiplies the quantities along one path: 1 frame per bike × 2 fork ends per fork × 1 sheet per fork end. `sum` then adds the paths together, because a bike can use the same part in several places. The result is the number of units of the part in one finished product.

### 6.3 Alternate vendors
Follows `SUPPLIES` into a product from every vendor, and counts each vendor's past POs and how many were late. Sorted by lead time, then price. One row back means single source.

### 6.4 Target tracker

```cypher
MATCH (t:Territory)<-[:COVERS]-(sp:SalesPerson)-[:HAS_TARGET]->(tg:Target {is_current: true})
OPTIONAL MATCH (so:SalesOrder)-[:SOLD_BY]->(sp)
WHERE so.order_date >= tg.period_start AND so.order_date <= tg.period_end
```

For each territory: its reps, their current targets, and their orders inside the quota period. `OPTIONAL MATCH` keeps a rep who sold nothing. The query returns target, shipped, open and at-risk revenue. The projection is `shipped + open - at_risk`.

### 6.5 Scrap by location
Recent work orders with scrap, grouped by work centre and scrap reason, with example products.

### 6.6 Stock cover
One product's stock by location, its open PO lines, and the model's weekly demand, weeks of cover and stockout week.

### 6.7 Document search

```cypher
CALL db.index.vector.queryNodes('doc_chunks', $k, $embedding) YIELD node, score
OPTIONAL MATCH (node)-[:MENTIONS]->(v:Vendor)
```

Finds the `k` chunks whose embedding is closest to the question's embedding, and returns the vendors each chunk mentions. That last hop is what connects a paragraph of text to the structured graph.

## 7. Try it yourself

Open the Neo4j browser at http://localhost:7474 (user `neo4j`, password `plangraph-dev`) and run:

```cypher
// The evidence chain behind the biggest alert
MATCH (a:Alert {uid: 'Alert:late_po:2538'})-[r]->(n)
RETURN a, r, n
```

```cypher
// Every path from a bike down to metal sheet
MATCH p = (:Product {name: 'Mountain-200 Black, 42'})-[:HAS_COMPONENT*1..4]->(m:Product)
WHERE m.name STARTS WITH 'Metal Sheet'
RETURN p
```

# Concepts

Every term used in this project, in plain words, with an example from the simulated bicycle manufacturer. Read this first if supply chain planning or GraphRAG is new to you.

## Part 1: Supply chain terms

### Sales order
A customer's order. It has a header (who, when, which territory) and lines (which product, how many, at what price).

*Example:* sales order 43659 was placed by a reseller in the Southeast territory. Its first line is 1 unit of product 776 at $2,024.99.

An order is **Open** until it ships, then **Shipped**. Revenue counts when it ships, so open orders are revenue that can still be lost.

### Purchase order (PO)
An order the company sends to a **vendor** (supplier) for parts. Each line has a **due date**. A PO is **late** when the due date has passed and the parts have not arrived.

*Example:* PO 2538 to Custom Frames, Inc. for metal sheets was due 9 days ago and nothing has been received.

### Lead time
The number of days between placing a PO and receiving the parts. Custom Frames quotes 15 days for metal sheets.

### Bill of materials (BOM)
The recipe for a product: which components go into it and how many. It has levels: a bike contains a frame, the frame contains tubes, the tubes are cut from metal sheet.

*Example:* `Mountain-200 Black, 42` → `HL Mountain Frame - Black, 42` → `Top Tube` → `Metal Sheet 2`. Three levels down from the bike.

This is why a late delivery of metal sheet can stop a bike from shipping, even though no bike order mentions metal sheet.

### Where-used
The BOM read upwards: "which finished products use this part?" It is the question a planner asks when a part is late.

### Finished good, component, purchased item
- A **finished good** is something customers buy (a bike, a helmet, a frame sold as a spare).
- A **component** is used to build something else.
- A **purchased item** is bought from a vendor, not made in the plant. Some purchased items are components (metal sheet), others are sold directly (helmets).

### Work order
An instruction to the plant: "build 8 units of product 722". It passes through **work centres** such as Frame Welding, Paint and Final Assembly. In the data these are called locations.

### Scrap
Units that fail a quality check during production and cannot be sold. Each scrapped work order records a **scrap reason**, for example "Paint process failed". Normal scrap here is well under 1% of units.

### Inventory, on hand, weeks of cover
**On hand** is the number of units physically in stock. **Weeks of cover** is on hand divided by weekly demand.

*Example:* 808 units of Metal Sheet 2 on hand and demand of 4,552 per week gives 0.2 weeks of cover. That is one day.

### Stockout
Running out. The inventory projection predicts it: stock next week = stock now + deliveries − demand.

### Safety stock
Extra stock held on purpose to absorb late deliveries and demand spikes.

### Single source
A part with only one approved vendor. If that vendor is late there is nobody to switch to. Metal sheets (Custom Frames) and Lock Ring (Bergeron Off-Roads) are single-sourced here.

### Expedite
Paying extra, usually for premium freight, to get a late PO faster.

### Territory, sales quota, target
The company sells through sales reps, each covering a **territory** (Southwest, Canada, France...). Each rep has a **quota** for each quota period of about one quarter. A territory's **target** is the sum of its reps' quotas.

### S&OP (sales and operations planning)
The monthly process that lines up three plans that usually live in different teams: what sales expects to sell, what supply can deliver, and what finance promised. PlanGraph does this reconciliation continuously instead of monthly.

### Revenue at risk vs revenue exposed
Two different numbers, both shown on every alert:
- **Exposed:** the value of all open orders for products that use the affected part. An upper bound.
- **At risk:** the part of that value that stock cannot cover. This is the real problem.

*Example:* PO 2538 is late. $3,857,641 of open orders use metal sheet somewhere in their BOM (exposed). But most of those orders can ship from finished stock. Only $319,813 cannot be built (at risk).

## Part 2: Data and AI terms

### ELT and staging
ELT means extract, load, transform: copy the raw data into a database first, then clean it with SQL inside the database. The **staging** layer is the cleaned result: one tidy table per business entity, joined across the source systems.

### Knowledge graph
A database that stores things (**nodes**) and the connections between them (**relationships**) as first-class data.

*Example:* `(Vendor: Custom Frames)-[:SUPPLIES]->(Product: Metal Sheet 2)`. The vendor and the product are nodes. `SUPPLIES` is the relationship, and it carries its own properties: lead time 15 days, price $8.34.

A relational database stores the same facts in tables and needs joins to connect them. A graph stores the connection itself, so following a chain of five connections is one short query.

### Cypher
Neo4j's query language. You draw the pattern you are looking for:

```cypher
MATCH (v:Vendor)-[s:SUPPLIES]->(p:Product {name: 'Metal Sheet 2'})
RETURN v.name, s.lead_days
```

Round brackets are nodes, square brackets are relationships, arrows give direction.

### Variable-length path
`-[:HAS_COMPONENT*1..4]->` means "follow HAS_COMPONENT between 1 and 4 times". It walks a BOM to any depth without knowing the depth in advance. In SQL this needs a recursive query.

### MERGE and idempotent loading
`MERGE` means "create this node if it does not exist, otherwise reuse it". Loading with MERGE is **idempotent**: running the load twice gives the same graph as running it once, so a failed nightly job can simply be re-run.

### Embedding
A list of numbers (here 768 of them) that represents the meaning of a piece of text. Texts with similar meaning get similar numbers, so "late delivery" lands near "delayed shipment".

### Vector search
Finding the stored texts whose embeddings are closest to the question's embedding. Neo4j has a **vector index** for this on the `DocChunk` nodes.

### Chunk
A document is split into smaller pieces (chunks) before embedding, because a small piece about one topic matches a question better than a whole document.

### RAG (retrieval-augmented generation)
Instead of asking an LLM to answer from memory, first **retrieve** relevant text, then let the LLM **generate** an answer from it. Plain RAG retrieves by vector search over documents.

*Where it works:* "What is our expedite policy?" The answer is written in one document.

*Where it fails:* "Which open orders does the late Custom Frames PO block?" No document says this. The answer is a join across purchasing, the BOM, inventory and sales.

### GraphRAG
RAG where retrieval is a graph query: follow relationships from the entity in the question to the facts needed. It answers questions whose answer is a chain of connections.

This project uses both. The evaluation shows they are complementary: documents-only search gets 100% of policy questions and 0% of BOM questions; graph queries do the reverse.

### LLM agent and tool calling
An **agent** is an LLM that can call functions (**tools**) and decide what to do next based on the results. The loop: the LLM reads the question → asks for a tool → reads the result → asks for another tool or answers.

*Example:* "Why is Canada under quota?" The agent calls `target_variance('Canada')`, sees $55k blocked by supply out of a $216k gap, calls `open_alerts(territory='Canada')` to find what blocks it, and answers: mostly a demand gap, partly the late Custom Frames PO.

### Templated tools vs Text2Cypher
- A **templated tool** runs a fixed, tested Cypher query. The LLM only chooses the tool and fills in parameters such as a territory name.
- **Text2Cypher** lets the LLM write the Cypher itself. It is flexible, and also the most common source of wrong answers and unsafe queries.

This project gives the agent 8 templated tools and uses Text2Cypher only as a guarded last resort.

### Guardrails
Checks that stop an LLM-written query from doing harm: reject write keywords, force a row limit, run in a read-only session with a timeout. See [ALERTS_AND_AGENT.md](ALERTS_AND_AGENT.md).

### LangGraph
A library for writing an agent as an explicit state machine: named steps ("model", "tools") and the arrows between them. The loop is visible in about 15 lines of code in `src/plangraph/agent/graph.py`.

### Human in the loop
The system recommends, a person decides. Actions here are drafts until a planner clicks Approve, and each approval is stored as a `Decision` node.

## Part 3: Machine learning terms

### Backtest
Testing a forecast on the past: pretend it is six months ago, forecast the next month, compare with what happened, move forward one month, repeat.

### WAPE (weighted absolute percentage error)
Total absolute error divided by total actual demand.

*Example:* actual demand was 100 and 100, forecasts were 90 and 120. Errors are 10 and 20, so WAPE = 30 / 200 = 15%.

It is preferred over a plain average of percentage errors because slow-moving products with tiny numbers do not dominate it.

### Baseline
The simplest method worth comparing against. A model is only useful if it beats its baseline. Here: "same as last month" for the forecast, "the vendor's past late rate" for the late-PO model.

### Quantile forecast (P10, P90)
A range instead of one number. P90 is the level demand should stay under 90% of the time. Demand above P90 is a signal that something changed.

### AUC
A score for a yes/no model between 0.5 (guessing) and 1.0 (perfect). It is the chance that the model gives a higher risk score to a randomly chosen late PO than to a randomly chosen on-time PO.

### Leakage
Accidentally training a model on information it would not have at prediction time. The usual cause is using data from the future. Every feature in this project only looks backwards from the row's own date, and both models are tested on a later time period than they were trained on.

### Ground truth
The known right answer you grade against. The injected scenarios are the ground truth for the alerts. The `truth` queries in `golden_questions.yaml` are the ground truth for the question answering.

# Architecture

How the pieces fit together, why each choice was made, and what would change for a production system.

## 1. The seven layers

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 1 SOURCES     sales │ purchasing │ production │ inventory │ finance   + docs │
│               (one Postgres schema per "system")                 (markdown)  │
└──────────────────────────────────────────────────────────────────────────────┘
        │ etl/prepare.py + etl/scenarios.py: planning date, date shift, disruptions
        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 2 STAGING     stg.* clean, joined, typed tables       etl/staging.sql        │
│               17 data quality checks                  etl/quality.py         │
└──────────────────────────────────────────────────────────────────────────────┘
        │ graph/loader.py: batched UNWIND + MERGE, counts verified
        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 3 GRAPH       Neo4j: entities, relationships, vector index on DocChunk       │
└──────────────────────────────────────────────────────────────────────────────┘
        ▲ write predictions back          │ read history (from Postgres)
        │                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 4 INTELLIGENCE  demand forecast │ late-PO risk │ inventory projection        │
│                 alert engine: rules + impact traversal → Alert nodes         │
└──────────────────────────────────────────────────────────────────────────────┘
        │ tools
        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 5 AI LAYER    LangGraph agent │ 8 templated tools │ guarded Text2Cypher      │
│               vector search │ offline router │ action recommender            │
└──────────────────────────────────────────────────────────────────────────────┘
        │ REST
        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 6 API         FastAPI: /health /alerts /ask /graph /targets /actions/approve │
│               /decisions /metrics                                            │
└──────────────────────────────────────────────────────────────────────────────┘
        │ HTTP
        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ 7 FRONTEND    Streamlit: Home │ Alert inbox │ Ask │ Impact graph │ Targets   │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 2. Two flows

### Nightly batch (`python -m plangraph.pipeline`, about 75 seconds)

| Step | What happens | Time |
|---|---|---|
| Download raw files | 18 CSVs, skipped if already present | 0 s after the first run |
| Load source systems | Prepare, inject scenarios, write 5 schemas | 34 s |
| Build staging | Run `staging.sql` | 1 to 3 s |
| Quality checks | 17 checks; stop on failure | under 1 s |
| Load graph | 59,000 nodes, 180,000 relationships; verify counts | 21 to 27 s |
| Load documents | Chunk, embed, build vector index | 1 s |
| Models | Forecast, late-PO risk, inventory projection | 9 s |
| Write-back | Model outputs onto the graph | under 1 s |
| Alert engine | Rules, impact traversal, Alert nodes | 9 s |

Every step can be re-run safely. Alerts and decisions survive a reload.

### Interactive (per request)
The UI calls the API. The API reads the graph. For `/ask` the agent calls tools, each of which runs one read-only query. For `/actions/approve` one `Decision` node is written. Nothing interactive touches Postgres except the health and metrics endpoints.

## 3. Design decisions

Each entry: the choice, the reason, and the cost.

### Postgres for sources and staging, Neo4j for the connected picture
**Why.** Each database does what it is good at. Postgres holds full history in tables, which is what model training wants. Neo4j holds the relationships, which is what multi-hop questions want.
**Cost.** Two databases to run, and the graph is a copy that must be refreshed.
**Not chosen.** Postgres alone with recursive SQL would work at this data size. The graph earns its place as chains get longer and as questions become exploratory.

### One Postgres schema per source system
**Why.** It reproduces the real difficulty: five systems that do not join themselves. Staging is where they first meet, which makes the integration work visible.
**Cost.** None beyond naming.

### Staging in Postgres (the spec suggested DuckDB as an option)
**Why.** The sources already live in Postgres, so staging is plain `CREATE TABLE AS SELECT` with no extra engine or data movement.

### Plain SQL file for staging (the spec listed dbt as optional)
**Why.** 14 tables in one readable file. dbt adds a project structure, a profile and a CLI that would be more to explain than the SQL itself.
**When to switch.** When there are many models, several developers, or a need for built-in lineage and tests.

### The graph holds one year of orders; history stays in Postgres
**Why.** Questions are about the present. A smaller graph loads in 20 seconds and keeps queries fast.
**Cost.** A question about orders from two years ago cannot be answered from the graph.

### Full reload of the graph every night
**Why.** Simple and always consistent with staging. 20 seconds is acceptable.
**When to switch.** At millions of orders, load only what changed since the last run.

### Templated tools first, Text2Cypher as a guarded fallback
**Why.** This is the main reliability decision. A template is tested, returns a fixed shape, and holds business logic in one reviewed place. LLM-written Cypher is the usual source of wrong numbers and unsafe queries.
**Cost.** Every new kind of question needs a new template. The fallback covers the gap, with less reliability.

### No `neo4j-graphrag` dependency (the spec listed it)
**Why.** The parts used here are small: a vector query is 4 lines of Cypher, and Text2Cypher is one prompt plus validation. Writing them directly keeps every step visible and puts the guardrails exactly where the query is produced.
**When to switch.** For hybrid retrievers or entity extraction pipelines, the library saves real work.

### LangGraph with an explicit two-node graph
**Why.** The loop is 6 lines and each step has a name. `create_react_agent` would hide the same loop in one call, which is shorter and harder to explain.

### One LLM wrapper
**Why.** `get_llm()` in `agent/llm.py` is the only place that names Gemini. Changing provider means editing that function.
**Cost.** Provider-specific features are not used.

### Offline mode
**Why.** The app, the tests and the alert pipeline all work without an API key, so reviewers can run the project for free and CI needs no secret.
**Cost.** A second, simpler answer path to maintain.

### Model outputs and alerts stored in the graph
**Why.** One place to ask. A question about a forecast or an alert is a graph query like any other, and an alert's neighbourhood is its explanation.
**Cost.** The graph mixes facts with predictions. They are kept apart by label and property name.

### Python does the arithmetic, Cypher does the traversal
**Why.** The dollars-at-risk netting is a few lines of pandas that a unit test can check with hand-made numbers. The same logic in Cypher would be long and hard to test.

### Deterministic simulation
**Why.** A fixed seed means the same company on every run, so results can be reproduced and documented.

### Streamlit (the spec proposed Next.js)
**Why.** Your choice: all Python, far less code. The UI only calls the API, so a different frontend could replace it without touching the backend.
**Cost.** Less control over layout, and the whole page re-runs on each interaction.

### A cron line for scheduling (the spec offered Prefect)
**Why.** The job is one command with clear logs. A scheduler would add a service to run.
**When to switch.** When steps need retries, alerts on failure, or to run in parallel.

## 4. Security and safety

| Concern | Handling |
|---|---|
| Secrets | Only in `.env`, which is git-ignored. `.env.example` has placeholders. |
| LLM-written queries | Four layers: static validation, read-only session, timeout, row limit |
| Query parameters | Always passed as parameters, never built into query text |
| Actions | Drafts only; a person approves; every approval is recorded |
| Input size | Questions are limited to 500 characters by the API schema |

**Known gaps.** The API has no authentication, and the approver's name is typed in, not verified. The default database passwords are for local use only.

## 5. What would change for production

| Area | Now | Production |
|---|---|---|
| Data | Sample CSVs reloaded in full | Change data capture from the real ERP; incremental loads |
| Identity | Typed-in approver name | Single sign-on; role-based access; a read-only database user for the agent |
| Actions | Recorded only | Write back to the ERP or send the email, with an approval workflow |
| Scheduling | Cron | An orchestrator with retries and failure alerts |
| Models | Retrained every night | Versioned models, drift monitoring, scheduled retraining |
| LLM | One provider, no caching | Response caching, rate limits, cost budgets, tracing of every tool call |
| Evaluation | 40 questions, 4 scenarios, one date | A larger question set from real users, run on every change |
| Alerts | One list | Grouping of alerts with one root cause, routing to owners, feedback on usefulness |
| Graph | Full nightly reload | Incremental updates, backups, a managed cluster |
| UI | Streamlit | A product frontend with deep links and notifications |

## 6. Code map

| Path | Responsibility |
|---|---|
| `src/plangraph/config.py` | All settings, from environment or `.env` |
| `src/plangraph/db.py` | Connections; `run_read` (read-only) and `run_write` |
| `src/plangraph/bom.py` | BOM explosion shared by ETL, scenarios and models |
| `src/plangraph/etl/` | `download`, `tables`, `prepare`, `scenarios`, `load_sources`, `staging`, `quality` |
| `src/plangraph/graph/` | `schema.cypher`, `loader`, `queries` (templates), `documents` |
| `src/plangraph/embeddings.py` | Gemini embeddings, or local hashing without a key |
| `src/plangraph/models/` | `forecast`, `late_po`, `inventory`, `writeback`, `store` |
| `src/plangraph/alerts/` | `rules`, `impact`, `engine` |
| `src/plangraph/agent/` | `llm`, `tools`, `text2cypher`, `guardrails`, `graph`, `offline`, `actions` |
| `src/plangraph/api/` | `main` (endpoints), `schemas`, `subgraph` |
| `src/plangraph/eval/` | `scenario_recall`, `golden_questions.yaml`, `run_eval` |
| `src/plangraph/pipeline.py` | The nightly job |
| `ui/` | Streamlit pages and the API client |

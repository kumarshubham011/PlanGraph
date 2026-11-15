# Build log

A record of what was built, in order: what, why, which files, how it was verified, and the problems met along the way. For how things work, follow the links to the other documents.

Environment: a cloud development container, Python 3.13, Postgres 16 and Neo4j 5 running in Docker. Planning date for all figures: 2026-11-14.

## Decisions agreed before starting

| Question | Choice |
|---|---|
| LLM provider | Google Gemini, behind one wrapper |
| Frontend | Streamlit |
| Agent framework | LangGraph |
| Scope | Everything, running locally. No cloud deployment, no demo video. |
| Documentation | Written as a learning guide with examples, not only a log |

## Step 1: Data foundation

**Built.** Download of 18 AdventureWorks CSVs, loading into five Postgres schemas, preparation into a "live today" company, four injected disruptions, a staging layer and 17 quality checks.

**Files.** `src/plangraph/etl/` (`download.py`, `tables.py`, `prepare.py`, `scenarios.py`, `load_sources.py`, `staging.sql`, `staging.py`, `quality.py`), `src/plangraph/bom.py`.

**Verified.** Row counts logged per table. All 17 checks pass. Exactly one current quota period.

**What profiling the data showed, and what was done.**

| Finding | Consequence |
|---|---|
| CSVs have no header row | Column names listed by hand in `tables.py`, taken from Microsoft's table definitions |
| Reseller orders arrive in one batch at each month end, and stop on 30 April 2025 | That date became the planning date. Planning months follow the sample's calendar. |
| Every order in the sample is already shipped | Orders that ship after the planning date are treated as open (650 orders) |
| Purchase orders have due dates and no receipt dates | Receipt dates are simulated per vendor, and documented as synthetic |
| Stock quantities do not match order volumes | Stock of purchased items is calibrated to 8 to 12 weeks of demand |
| Some purchased items are sold directly (helmets, tyres) | Where-used traversals start at depth 0, not 1 |
| Reps reach about 84% of quota in every period | The target alert tolerance was set to 15% |
| Three sales managers have no territory | Their home territory is where they sold most |

**Problems.**
- Docker Hub rate-limited the `postgres:16-alpine` image (HTTP 429). The `postgres:16` tag downloaded, so the compose file uses it.

Details: [DATA.md](DATA.md).

## Step 2: Knowledge graph

**Built.** Constraints and indexes, a generic batched loader for 10 node types and 13 relationship types, and a count check against staging.

**Files.** `src/plangraph/graph/schema.cypher`, `loader.py`.

**Verified.** Every node and relationship count equals its staging row count. Loading twice gives identical counts (idempotent). A `CREATE` sent through the read helper is refused by Neo4j.

**Decisions.** Order lines are relationships. One `Product` label. One year of orders in the graph. Alerts and decisions survive reloads.

**Problems.**
- The first read helper passed a timeout in a way the Neo4j driver rejected. Rewritten to use the driver's `Query` object.

Details: [GRAPH_MODEL.md](GRAPH_MODEL.md).

## Step 3: Models

**Built.** Demand forecast with P10/P90 range and walk-forward backtest; late-PO risk classifier; week-by-week inventory projection; write-back to the graph.

**Files.** `src/plangraph/models/` (`forecast.py`, `late_po.py`, `inventory.py`, `writeback.py`, `store.py`).

**Verified.** Forecast WAPE 18.3% against 25.8% for the last-month baseline. Late-PO AUC 0.640 against 0.635 for the vendor-rate baseline. Inventory projection flags exactly the 7 items the scenarios broke.

**Problems.**
- The first late-PO model, a gradient-boosted tree with 9 features, scored AUC 0.60, *below* the 0.635 baseline. Several variants were compared. A regularised logistic regression on 4 features with a smoothed vendor rate reached 0.640 and was kept. The reason the ceiling is low: simulated lateness depends on the vendor only.

Details: [MODELS.md](MODELS.md).

## Step 4: Alert engine

**Built.** Six rules, the dollars-at-risk traversal, alert nodes linked to what they are about and the targets they threaten, and the pipeline entry point.

**Files.** `src/plangraph/alerts/` (`rules.py`, `impact.py`, `engine.py`), `src/plangraph/graph/queries.py`, `src/plangraph/pipeline.py`.

**Verified.** All four injected scenarios are caught. The slipping vendor's PO shows $319,813 at risk of $3,857,641 exposed; four unrelated late POs show $0 at risk. The SQL and Cypher versions of the core exposure query return the same total ($23,944,500 summed over PO and territory rows).

**Problems and tuning.** The first run raised 35 alerts.

| Symptom | Cause | Change |
|---|---|---|
| Scrap alerts on five work centres | Work orders pass through several centres, so one bad centre raises them all | Added a "likely source" rule; one alert, others listed as "also elevated" |
| Scrap scenario at Frame Welding could not be told from Frame Forming | Both centres process exactly the same work orders | Moved the scenario to Paint, which is distinguishable |
| Nine of ten territories flagged under target | Quotas in the sample are structurally about 16% above sales | Tolerance raised from 5% to 15% |
| Three demand surge alerts, two of them trivial | Single products a few units over forecast | Added a 50-unit minimum per product group |
| A PO 8 days late with $0 at risk marked "high" | Severity treated any 7-day delay as urgent | "High" now needs money at risk or 14+ days; 7+ days is "medium" |

After tuning: 25 alerts. This tuning used the same scenarios the evaluation measures, which is stated as a limit in [EVALUATION.md](EVALUATION.md).

Details: [ALERTS_AND_AGENT.md](ALERTS_AND_AGENT.md).

## Step 5: Documents and vector search

**Built.** 20 company documents, chunking, embeddings, a Neo4j vector index, and `MENTIONS` links from chunks to vendors.

**Files.** `data/docs/*.md`, `src/plangraph/embeddings.py`, `src/plangraph/graph/documents.py`.

**Verified.** With the offline hashing embedder, "what is our expedite policy?" returns `policy_expedite.md` first, and "how much scrap is normal" returns `sop_scrap_review.md` first.

**Not verified.** Gemini embeddings (no key available).

## Step 6: Agent

**Built.** LLM wrapper, 8 templated tools plus a Text2Cypher fallback, four-layer guardrails, the LangGraph loop, offline keyword routing, and the action recommender with approvals.

**Files.** `src/plangraph/agent/` (`llm.py`, `tools.py`, `text2cypher.py`, `guardrails.py`, `graph.py`, `offline.py`, `actions.py`).

**Verified.** The loop runs end to end with a scripted fake model: tool call, tool result, final answer, steps and evidence IDs collected. Guardrails reject 9 kinds of unsafe query and accept safe ones. Offline routing picks the right tool for six sample questions against the live graph. The recommender returns sensible actions for late-PO and stockout alerts.

**Not verified.** Any call to Gemini: real tool selection, answer quality, Text2Cypher generation.

**Found along the way.** The parts in the vendor scenario (metal sheets) and the stock scenario (Lock Ring) are single-sourced, so "switch vendor" is not available for them. The recommender says so and offers expediting. Re-sourcing does appear for other alerts, such as PO 2589.

## Step 7: API

**Built.** FastAPI app with 9 endpoints, request validation, logging, and the subgraph builder for the impact view.

**Files.** `src/plangraph/api/` (`main.py`, `schemas.py`, `subgraph.py`).

**Verified.** Every endpoint called with curl and returns 200. An approval for "Expedite PO 2538" returned a decision, and the `Decision` node with its `RESOLVES` link was confirmed in Neo4j. An unknown action returns 404. An empty question returns 422.

## Step 8: Frontend

**Built.** Streamlit app: Home, Alert inbox, Ask, Impact graph, Target tracker. The UI only calls the API.

**Files.** `ui/Home.py`, `ui/api_client.py`, `ui/pages/*.py`, `.streamlit/config.toml`.

**Verified.** All five pages opened in headless Chromium with Playwright: no exceptions, no page errors. Screenshots are in `docs/img/`. The chart colours were run through a palette validator for colour-blind safety, and the chart has a table view beneath it.

**Problems.**
- The first screenshots showed a broken legend on the graph page, a green up-arrow next to a negative target gap, and too many gridlines. Fixed and re-shot.
- A later re-shoot showed the graph page unchanged: an automated text replacement had failed silently after the formatter re-wrapped the lines. Fixed by hand and checked in a new screenshot.

## Step 9: Evaluation

**Built.** Scenario recall report, 40 golden questions with truth queries, and a runner that compares documents-only, routed graph query and the full agent.

**Files.** `src/plangraph/eval/` (`scenario_recall.py`, `golden_questions.yaml`, `run_eval.py`).

**Verified.** Scenario recall 4 of 4. Without an LLM: documents-only 35%, routed graph query 55%.

**Not verified.** The agent row.

**Problems.**
- Cypher in YAML broke the parser because of colons. Queries are now block scalars.
- Two truth queries sorted by an aggregate without declaring it first, which Neo4j rejects. Fixed with an explicit `WITH`.
- Scrap queries used the real `date()`, which is wrong when `AS_OF` is set. They now take the planning date as a parameter.

Details: [EVALUATION.md](EVALUATION.md).

## Step 10: Quality, packaging and docs

**Built.** 51 tests, `ruff` configuration, GitHub Actions workflow, Makefile, Dockerfile, Docker Compose file, README and nine documents.

**Verified.** `ruff check` and `ruff format --check` clean. 47 unit tests and 4 integration tests pass. A full pipeline run from an empty database completed in about 75 seconds, twice.

**Not verified.**
- **Docker image build.** The build environment's network proxy blocks package downloads inside image builds, and three attempts failed at the `apt-get` step. Postgres and Neo4j did run from Compose. On a normal machine `docker compose up --build` is expected to work. If the `libgomp1` install fails for you, that single line in the `Dockerfile` is the place to look.
- **GitHub Actions.** The workflow has not run yet.

## Summary of what is real, simulated and unverified

| Item | Status |
|---|---|
| Schema, products, BOM, vendors, orders, quotas | AdventureWorks sample, unchanged |
| PO receipt dates, purchased-item stock, four disruptions, documents | Simulated by this project |
| Pipeline, graph, models, alerts, API, UI, offline Ask | Built and run |
| Gemini agent, Gemini embeddings, Text2Cypher, agent evaluation | Built and unit-tested with a fake model; not run live |
| Docker image build, GitHub Actions | Written; not run |

## Suggested next steps

1. Add a Gemini key, re-run the pipeline, and run `python -m plangraph.eval.run_eval` to fill in the agent row.
2. Run `docker compose up --build` on your machine and fix anything the build shows.
3. Write two or three new disruption scenarios the rules have not seen, for a fairer recall figure.
4. Group alerts that share a root cause (the eight Custom Frames alerts) into one case.
5. Add customer names and a simulated daily feed so alerts change from night to night.

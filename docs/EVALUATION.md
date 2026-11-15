# Evaluation

What was measured, how, the results, and what the numbers do and do not show. All figures are from the run with planning date 2026-11-14 and seed 42.

## 1. Did the alerts catch the injected disruptions?

**Method.** Four disruptions are written into the data and recorded in `meta.scenario_truth` (see [DATA.md](DATA.md), section 5). `python -m plangraph.eval.scenario_recall` checks, for each one, whether an open alert of the expected type points at one of the recorded entities.

**Result: 4 of 4 caught.**

| Scenario | Expected alert | Caught | $ at risk | Days of warning |
|---|---|---|---|---|
| Vendor slip (Custom Frames) | `late_po` | Yes | $319,813 | 12 |
| Demand surge (touring bikes, Southwest) | `demand_surge` | Yes | $0 | - |
| Stock shortfall (Lock Ring) | `stockout` | Yes | $0 | - |
| Scrap spike (Paint) | `scrap_spike` | Yes | $0 | - |

"Days of warning" is the time until the first affected customer order is due.

Three scenarios show $0 at risk. That is the netting logic at work, not a miss: finished stock covers the tripled touring bike orders, Lock Ring's remaining week of stock covers today's open orders, and the scrapped frames are not yet short. They are still raised as alerts because each will become a revenue problem if left alone.

### The other alerts
25 alerts are open. 12 belong to an injected scenario. The other 13:

| Type | Count | What they are |
|---|---|---|
| `target_gap` | 5 | Territories more than 15% under target. A real finding in the sample data, not noise. |
| `late_po` | 4 | POs late by chance in the simulated receipts. All show $0 at risk and low or medium severity. |
| `po_at_risk` | 4 | Vendors with a poor simulated record. All $0 at risk. |

So a planner sees 8 supply alerts that need no action today. They are easy to tell apart because they carry $0 and sit at the bottom of the inbox.

### Limits of this result
- **The thresholds were tuned on the same four scenarios.** The first version raised 35 alerts, including a scrap alert for five work centres and three surge alerts. The rules were adjusted until the output was clean. A fair test needs new scenarios the rules have never seen. Recall of 4 of 4 is therefore an upper estimate.
- **Four scenarios is a small sample**, and each is large and obvious. Subtler cases (a vendor slipping 3 days, a 20% surge) were not tried.
- **One planning date.** False alert rates over many nights were not measured.

## 2. Model quality

Details and method are in [MODELS.md](MODELS.md).

| Model | Metric | Model | Best baseline |
|---|---|---|---|
| Demand forecast | WAPE, 6-month walk-forward backtest | 18.3% | 25.8% (same as last month) |
| Late-PO risk | AUC, newest 25% of POs as test set | 0.640 | 0.635 (vendor late rate alone) |

- The forecast is a real improvement over its baseline on the sample data.
- The late-PO model is not better than its baseline in any meaningful way. Its labels are simulated from the vendor alone, so there is nothing more to find. The number is reported because hiding a flat result would be misleading.

## 3. Question answering: 40 golden questions

### The questions
`src/plangraph/eval/golden_questions.yaml` holds 40 questions in 7 categories: supply (7), sourcing (5), bill of materials (4), stock (5), targets (8), scrap (3) and documents (8).

The expected answer is not typed in by hand. Each graph question has a `truth` Cypher query that fetches the right value when the evaluation runs:

```yaml
- id: v1
  category: sourcing
  question: Who supplies Metal Sheet 2?
  truth: >-
    MATCH (v:Vendor)-[:SUPPLIES]->(:Product {name: 'Metal Sheet 2'}) RETURN v.name AS expected
```

Document questions have fixed keywords, since the documents do not change.

### Grading
An answer is correct when it contains the expected values. Text is matched as a case-insensitive substring. Numbers match within 2%, and "$320k", "319,813" and "0.32 million" are all read as numbers. By default all expected values must appear; a few "name one..." questions accept any.

### The three approaches

| Approach | How it retrieves | Needs an LLM |
|---|---|---|
| `docs_only` (plain RAG) | Vector search over the 20 documents | To write the answer |
| `graph_router` (single-shot GraphRAG) | Keyword routing picks one templated graph query | To write the answer |
| `agent` | LangGraph agent: several tools, documents and graph, Text2Cypher fallback | Yes |

### Results: no-LLM mode
No Gemini key was available, so the first two approaches were graded on the **retrieved text itself**: was the right fact fetched? This measures retrieval, the part that decides whether a correct answer is possible at all.

| Approach | Accuracy | Median time |
|---|---|---|
| Documents only | 35% (14 of 40) | 0.01 s |
| One routed graph query | 55% (22 of 40) | 0.07 s |
| Agent with Gemini | **not yet run** | |

By category:

| Category | Documents only | Routed graph query |
|---|---|---|
| Documents (policies, emails) | 100% | 0% |
| Stock | 0% | 100% |
| Scrap | 67% | 100% |
| Targets | 12% | 75% |
| Sourcing | 40% | 60% |
| Bill of materials | 0% | 50% |
| Supply | 14% | 43% |

### What this shows
- **The two retrieval methods are complementary.** Documents answer every policy question and no stock or BOM question. The graph does the reverse. Neither alone is enough, which is the case for an agent that can use both.
- **Plain RAG wins where the answer is written down.** "Up to what cost can a planner approve an expedite?" is one sentence in one document. A graph query cannot answer it.
- **One routed query is not enough for the graph either.** It fails when the question needs a tool the keywords did not pick, a fact from an alert's details, or a query no template covers ("which components go directly into this bike?").

### Limits
- **Grading on retrieved text is lenient.** A raw result of 15 rows contains many names and numbers, so a match can happen by luck. Documents-only scores 67% on scrap partly because an email happens to name the same reason. LLM-written answers are shorter and would be graded more strictly.
- **The routed-query score moved between 52.5% and 55% across two runs** on the same data. One sourcing question depends on the order of rows that tie. The honest figure is "about half".
- **I wrote both the questions and the tools**, so the questions favour what the tools can do. Questions from real planners would be a harder test.
- **The agent row is empty.** It is the result the project is built for, and it has not been measured.

### Completing the evaluation
With a key in `.env`:

```bash
python -m plangraph.eval.run_eval
```

This runs all three approaches with the LLM writing the answers, and reports accuracy, median latency, average tokens per question, and the share of LLM-written Cypher that ran without error. Results are saved to `artifacts/eval_results.json`. Token counts can be turned into cost with the current Gemini price list.

## 4. Software checks

| Check | Result |
|---|---|
| Data quality checks on staging | 17 of 17 pass |
| Graph counts vs staging | 10 node types and 13 relationship types match exactly |
| Unit tests (no services needed) | 47 pass |
| Integration tests (live Postgres and Neo4j) | 4 pass |
| Lint and format (`ruff`) | Clean |
| API endpoints, including one approval | All respond; `Decision` node confirmed in the graph |
| UI pages (Playwright) | 5 pages load with no errors |

## 5. Not verified in this build

- **Anything that calls Gemini:** agent answers, Gemini embeddings (document search ran on the local hashing fallback), Text2Cypher generation, the agent evaluation.
- **The Docker image build.** `docker compose up --build` could not be completed in the build environment, whose network proxy blocks package downloads inside image builds. Postgres and Neo4j ran in Docker; the pipeline, API and UI ran directly from the virtual environment.
- **GitHub Actions.** The workflow file runs the same lint and test commands that pass locally, and has not yet run on GitHub.

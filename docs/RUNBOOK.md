# Runbook

How to set up, run, configure and troubleshoot PlanGraph.

## 1. Requirements

- Docker with Docker Compose
- Python 3.11 or newer (local development only)
- About 4 GB of free memory (Neo4j is given a 1 GB heap)
- Internet access on the first run, to download the AdventureWorks CSV files (about 45 MB)
- Optional: a Gemini API key from Google AI Studio

## 2. Run everything in Docker

```bash
cp .env.example .env          # optional: set GEMINI_API_KEY
docker compose up --build
```

What starts, in order:

1. `postgres` and `neo4j`
2. `pipeline`, which runs the nightly job once and exits
3. `api` on http://localhost:8000 (interactive docs at `/docs`)
4. `ui` on http://localhost:8501

The Neo4j browser is at http://localhost:7474 (user `neo4j`, password `plangraph-dev`).

Note: this full Docker route was not verified in the environment the project was built in (see [BUILD_LOG.md](BUILD_LOG.md)). The local route below was.

## 3. Local development

```bash
make setup                     # creates .venv and installs the package with dev tools
source .venv/bin/activate
make db                        # Postgres and Neo4j in Docker
make pipeline                  # about 75 seconds
make api                       # terminal 1
make ui                        # terminal 2
```

| Command | What it does |
|---|---|
| `make pipeline` | Rebuild everything: sources, staging, graph, models, alerts |
| `make api` | Start the API with auto-reload |
| `make ui` | Start the Streamlit app |
| `make test` | Unit tests. No databases needed. |
| `pytest -m integration` | Integration tests. Need the databases and a completed pipeline run. |
| `make lint` | `ruff` lint and format check |
| `make eval` | Scenario recall and the golden question evaluation |
| `make down` | Stop the databases. Add `-v` to delete their data. |

## 4. Configuration

All settings are environment variables, read from `.env` (`src/plangraph/config.py`).

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_URL` | `postgresql+psycopg://plangraph:plangraph@localhost:5432/plangraph` | Postgres connection |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection |
| `NEO4J_USER`, `NEO4J_PASSWORD` | `neo4j`, `plangraph-dev` | Neo4j login |
| `GEMINI_API_KEY` | empty | Empty means offline mode |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Chat model. Change it if Google has retired this name. |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | Embedding model |
| `AS_OF` | empty | The simulated "today", as `YYYY-MM-DD`. Empty means the real today. |
| `API_URL` | `http://localhost:8000` | Where the UI finds the API |

### Turning on the LLM agent
1. Put your key in `.env`: `GEMINI_API_KEY=...`
2. Re-run `make pipeline`. Documents must be re-embedded with Gemini, because a question and the documents must be embedded by the same method.
3. Restart the API. `/health` should show `"llm_enabled": true`.
4. Run `python -m plangraph.eval.run_eval` to fill in the agent row of the evaluation.

### Reproducing the documented numbers
The figures in the docs come from `AS_OF=2026-11-14`. Set that in `.env` and run the pipeline to get the same dates. Dollar figures and counts are the same for any date, because every date shifts together.

## 5. Running it every night

Add a cron entry on the machine that hosts the project:

```cron
0 2 * * * cd /path/to/PlanGraph && .venv/bin/python -m plangraph.pipeline >> pipeline.log 2>&1
```

With `AS_OF` empty, each run moves the simulated company to the new day. Since the underlying sample does not change, the same alerts reappear with the same figures. In a real deployment the sources would change daily and so would the alerts.

## 6. API reference

| Method and path | Purpose |
|---|---|
| `GET /health` | Status, planning date, graph size, whether the LLM is on |
| `GET /alerts?status=open&type=&severity=` | List alerts, biggest dollars at risk first |
| `GET /alerts/{uid}` | One alert with what it is about, targets it threatens, recommended actions and decisions |
| `POST /ask` | Body `{"question": "..."}`. Returns the answer, each step with its Cypher and rows, and evidence node IDs |
| `GET /graph/{uid}` | Nodes and edges around any node, for the impact graph |
| `GET /targets` | Target, shipped, open, at-risk, projected and gap per territory |
| `POST /actions/approve` | Body `{"alert_uid", "action_id", "approved_by", "note"}`. Writes a Decision. |
| `GET /decisions` | The audit trail |
| `GET /metrics` | Model metrics from the last run and the injected scenarios |

Example:

```bash
curl -s -X POST localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "Is there an alternate vendor for Metal Sheet 2?"}'
```

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| UI shows "Cannot reach the PlanGraph API" | API not running, or `API_URL` wrong | Start the API; check the variable |
| API returns 500 on `/health` | Pipeline has never run, so `meta.run_info` does not exist | `make pipeline` |
| Pipeline stops with "Data quality checks failed" | A check found bad rows | The log names the check; the SQL is in `etl/quality.py` |
| Pipeline stops with "Graph counts do not match staging" | A load was interrupted, or a row has a missing key | Re-run; the log shows which label or relationship differs |
| `ServiceUnavailable` from Neo4j | Neo4j still starting (takes 20 to 30 seconds) | Wait and retry |
| `docker pull` fails with 429 | Docker Hub rate limit | Wait a few minutes or log in to Docker Hub |
| Ask page says offline mode although a key is set | API started before the key was added | Restart the API |
| Gemini returns "model not found" | The default model name has been retired | Set `GEMINI_MODEL` to a current model |
| Document search returns odd results after adding a key | Documents still embedded with the offline method | Re-run the pipeline |
| Download fails on the first run | No internet access, or GitHub blocked | Place the 18 CSV files in `data/raw/` by hand (names in `etl/tables.py`) |

### Useful checks

```bash
# How many of each node type?
docker exec plangraph-neo4j-1 cypher-shell -u neo4j -p plangraph-dev \
  "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC"

# What did the last run decide the date was?
docker exec plangraph-postgres-1 psql -U plangraph -c "SELECT * FROM meta.run_info"
```

### Starting again from nothing

```bash
docker compose down -v      # deletes both databases, including Decisions
make db && make pipeline
```

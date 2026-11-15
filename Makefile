# Common commands. Run `make help` to list them.
.PHONY: help setup db pipeline api ui test lint eval up down

help:
	@grep -E '^[a-z]+:.*##' Makefile | sed 's/:.*##/ -/'

setup: ## Create a virtual environment and install everything
	python -m venv .venv && .venv/bin/pip install -e ".[dev]"

db: ## Start Postgres and Neo4j in Docker
	docker compose up -d postgres neo4j

pipeline: ## Run the nightly job: sources -> staging -> graph -> models -> alerts
	python -m plangraph.pipeline

api: ## Start the API on http://localhost:8000
	uvicorn plangraph.api.main:app --reload

ui: ## Start the Streamlit app on http://localhost:8501
	streamlit run ui/Home.py

test: ## Unit tests only (no databases needed)
	pytest -m "not integration"

lint: ## Check code style
	ruff check . && ruff format --check .

eval: ## Scenario recall + golden question evaluation (needs the pipeline to have run)
	python -m plangraph.eval.scenario_recall && python -m plangraph.eval.run_eval

up: ## Build and run everything in Docker
	docker compose up --build

down: ## Stop Docker services (add -v to also delete the data)
	docker compose down

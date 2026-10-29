# Raccourcis BIFlow. Sous Windows sans `make` : utiliser scripts/tasks.ps1 (mêmes cibles).
ifeq ($(OS),Windows_NT)
PY ?= .venv/Scripts/python
else
PY ?= .venv/bin/python
endif

.PHONY: setup up down init-db ingest ingest-small run evaluate dashboard demo test

setup:          ## venv + dépendances + .env
	python -m venv .venv
	$(PY) -m pip install -e ".[dev]"
	test -f .env || cp .env.example .env

up:             ## PostgreSQL + Adminer, puis création des tables
	docker compose up -d --wait
	$(PY) -m biflow.db.init_db

down:
	docker compose down

init-db:
	$(PY) -m biflow.db.init_db

ingest:         ## 12 derniers mois, max 200 000 lignes
	$(PY) -m biflow.ingestion $(ARGS)

ingest-small:   ## extrait de test (5 000 lignes)
	$(PY) -m biflow.ingestion --max-rows 5000

run:            ## pipeline complet
	$(PY) -m biflow.run $(ARGS)

evaluate:       ## évaluation du MVP sur données synthétiques
	$(PY) -m biflow.evaluation $(ARGS)

dashboard:      ## dashboard du dernier run publié
	$(PY) -m streamlit run src/biflow/dashboard/app.py

demo:           ## run sur l'extrait 2023-2026 puis dashboard (LLM=ollama pour le modèle local)
	BIFLOW_LLM_PROVIDER=$(or $(LLM),none) $(PY) -m biflow.run --no-db 		--file data/raw/nyc311_2023-01_2026-09_echantillon.parquet 		--question "Quel borough a le délai médian de résolution le plus long ?" 		--question "Pourquoi les délais sont-ils plus longs à Staten Island ?"
	$(PY) -m streamlit run src/biflow/dashboard/app.py

test:
	$(PY) -m pytest

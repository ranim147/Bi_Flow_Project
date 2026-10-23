# Raccourcis BIFlow. Sous Windows sans `make` : utiliser scripts/tasks.ps1 (mêmes cibles).
ifeq ($(OS),Windows_NT)
PY ?= .venv/Scripts/python
else
PY ?= .venv/bin/python
endif

.PHONY: setup up down init-db ingest ingest-small run test

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

test:
	$(PY) -m pytest

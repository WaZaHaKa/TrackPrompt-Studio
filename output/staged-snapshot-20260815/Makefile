PYTHON ?= python
NPM ?= npm
TRACKPROMPT_DATA_DIR ?= $(abspath .trackprompt-data)
MODEL_CACHE_DIR ?= $(TRACKPROMPT_DATA_DIR)/models

.PHONY: help setup dev-backend dev-frontend fixtures test lint typecheck build e2e check compose-config compose-up compose-down

help:
	@echo "TrackPrompt Studio targets:"
	@echo "  setup           Install backend and frontend development dependencies"
	@echo "  dev-backend     Run FastAPI on http://localhost:8000"
	@echo "  dev-frontend    Run Vite on http://localhost:5173"
	@echo "  fixtures        Generate local synthetic audio fixtures"
	@echo "  test            Run backend and frontend unit tests"
	@echo "  lint            Run Python and TypeScript linting"
	@echo "  typecheck       Run Python and TypeScript type checks"
	@echo "  build           Build the production frontend"
	@echo "  e2e             Run the browser end-to-end tests"
	@echo "  check           Run the required non-E2E checks"
	@echo "  compose-config  Validate the Compose model"
	@echo "  compose-up      Build and start the local Docker application"
	@echo "  compose-down    Stop the Docker application"

setup:
	cd backend && $(PYTHON) -m pip install -e ".[dev]"
	cd frontend && $(NPM) ci

dev-backend:
	cd backend && TRACKPROMPT_DATA_DIR="$(TRACKPROMPT_DATA_DIR)" MODEL_CACHE_DIR="$(MODEL_CACHE_DIR)" $(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

dev-frontend:
	cd frontend && $(NPM) run dev

fixtures:
	$(PYTHON) tools/generate_test_audio.py

test:
	cd backend && $(PYTHON) -m pytest
	cd frontend && $(NPM) test -- --run

lint:
	cd backend && $(PYTHON) -m ruff check .
	cd frontend && $(NPM) run lint

typecheck:
	cd backend && $(PYTHON) -m mypy app
	cd frontend && $(NPM) run typecheck

build:
	cd frontend && $(NPM) run build

e2e:
	cd frontend && $(NPM) run test:e2e

compose-config:
	docker compose config

check: test lint typecheck build compose-config

compose-up:
	docker compose up --build

compose-down:
	docker compose down

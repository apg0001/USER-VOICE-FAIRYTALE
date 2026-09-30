.PHONY: install test lint format run-api run-web compose-up compose-down

install:
	python -m pip install -e "./backend[dev]"
	cd frontend && npm ci

test:
	cd backend && python -m pytest

lint:
	cd backend && python -m ruff check app tests && python -m mypy app
	cd frontend && npm run lint && npm run build

format:
	cd backend && python -m ruff format app tests && python -m ruff check --fix app tests

run-api:
	cd backend && uvicorn app.main:app --reload

run-web:
	cd frontend && npm run dev

compose-up:
	docker compose up --build

compose-down:
	docker compose down


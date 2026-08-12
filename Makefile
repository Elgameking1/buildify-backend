.PHONY: up down logs shell migrate seed test lint reset

up:            ## Start api + mysql + adminer
	docker compose up -d --build

down:          ## Stop everything (keeps data)
	docker compose down

reset:         ## Stop and DELETE the database volume
	docker compose down -v

logs:
	docker compose logs -f api

shell:
	docker compose exec api bash

migrate:       ## Apply migrations
	docker compose exec api alembic upgrade head

seed:          ## Load demo data
	docker compose exec api python -m app.seeds.seed

test:
	docker compose exec api pytest -v

lint:
	docker compose exec api ruff check app tests

bootstrap: up migrate seed  ## Everything needed for a fresh demo
	@echo "Ready: http://localhost:8000/docs"

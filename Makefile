## Used only for Unix
## Windows will use the PowerShell script `scripts/dev.ps1`

.PHONY: dev dev-backend dev-frontend db-up db-down lint

dev:      ## Run backend+frontend and Postgres
	bash scripts/dev.sh all

dev-backend:
	bash scripts/dev.sh backend

dev-frontend:
	bash scripts/dev.sh frontend

db-up:
	docker compose up -d db

db-down:
	docker compose down

lint:
	bash scripts/lint-all.sh

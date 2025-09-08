Awesome combo. Here’s a clean, “everything runs from the repo root” monorepo layout that plays nicely with Git, works on Windows/macOS/Linux, and keeps Node/Flask/Postgres tidy.

# Directory layout

```
your-project/
├─ README.md
├─ .gitignore
├─ .editorconfig
├─ .pre-commit-config.yaml          # optional but recommended
├─ .env.example                     # all shared env vars (no secrets)
├─ .env                             # local overrides (gitignored)
├─ compose.yml                      # docker compose for Postgres (and pgAdmin optional)
├─ Makefile                         # convenience tasks (Unix); see scripts/win for Windows
├─ package.json                     # root scripts to orchestrate dev tasks
├─ scripts/
│  ├─ dev.ps1                       # Windows “run both servers”
│  ├─ dev.sh                        # *nix “run both servers”
│  ├─ db_wait.sh                    # wait-for-Postgres helper
│  └─ lint-all.sh
├─ infra/
│  ├─ sql/                          # raw SQL (bootstrap/seed)
│  └─ migrations/                   # Alembic versions (generated)
├─ backend/
│  ├─ pyproject.toml                # Poetry/UV/PDM (pick one) OR requirements.txt
│  ├─ requirements.txt              # (if not using pyproject.toml)
│  ├─ .env.example                  # backend-only vars (FLASK_*, DB_*)
│  ├─ app/
│  │  ├─ __init__.py
│  │  ├─ main.py                    # create_app(), register_blueprints()
│  │  ├─ routes/
│  │  │  └─ api.py
│  │  ├─ services/
│  │  └─ models/
│  ├─ wsgi.py                       # for production servers (gunicorn/uwsgi)
│  └─ alembic.ini
├─ frontend/
│  ├─ package.json
│  ├─ vite.config.ts                # or webpack
│  ├─ src/
│  │  ├─ main.tsx
│  │  ├─ app/
│  │  │  ├─ api.ts                  # API base URL from env
│  │  │  └─ ...
│  │  └─ components/
│  └─ public/
└─ tests/
   ├─ backend/
   └─ frontend/
```

# Git housekeeping

**.gitignore** (top-level)

```
# Python
.venv/
__pycache__/
*.pyc
*.pyo
*.pyd
.coverage
.pytest_cache/

# Node
node_modules/
pnpm-lock.yaml
npm-debug.log*
dist/
build/

# Env / local
.env
backend/.env
frontend/.env.local

# OS/editor
.DS_Store
Thumbs.db
.vscode/
.idea/
```

# One-command local DB

**compose.yml**

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: ${DB_USER:-app}
      POSTGRES_PASSWORD: ${DB_PASSWORD:-app}
      POSTGRES_DB: ${DB_NAME:-app}
    ports:
      - "${DB_PORT:-5432}:5432"
    volumes:
      - dbdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER:-app} -d ${DB_NAME:-app}"]
      interval: 3s
      timeout: 2s
      retries: 20

  # optional: web GUI
  pgadmin:
    image: dpage/pgadmin4
    environment:
      PGADMIN_DEFAULT_EMAIL: ${PGADMIN_EMAIL:-admin@example.com}
      PGADMIN_DEFAULT_PASSWORD: ${PGADMIN_PASSWORD:-admin}
    ports:
      - "8081:80"
    depends_on:
      - db

volumes:
  dbdata:
```

**.env.example** (root)

```
# Shared
NODE_ENV=development

# Backend Flask
FLASK_DEBUG=1
FLASK_APP=app/main.py
BACKEND_HOST=127.0.0.1
BACKEND_PORT=5000

# DB
DB_USER=app
DB_PASSWORD=app
DB_NAME=app
DB_HOST=127.0.0.1
DB_PORT=5432
DATABASE_URL=postgresql+psycopg://app:app@127.0.0.1:5432/app

# Frontend
VITE_API_BASE=http://127.0.0.1:5000
```

# Backend (Flask)

**backend/pyproject.toml** (choose Poetry/UV/PDM; here’s Poetry)

```toml
[tool.poetry]
name = "your-backend"
version = "0.1.0"
packages = [{ include = "app" }]

[tool.poetry.dependencies]
python = "^3.11"
flask = "^3.0"
flask-cors = "^4.0"
sqlalchemy = "^2.0"
psycopg = {version="^3.1", extras=["binary"]}
alembic = "^1.13"
python-dotenv = "^1.0"

[tool.poetry.group.dev.dependencies]
pytest = "^8.0"
black = "^24.0"
ruff = "^0.6"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

**backend/app/main.py**

```python
from flask import Flask, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, text
import os

def create_app():
    app = Flask(__name__)
    CORS(app)
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg://app:app@127.0.0.1:5432/app")
    engine = create_engine(db_url, pool_pre_ping=True, future=True)

    @app.get("/api/health")
    def health():
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return jsonify(ok=True)

    return app

app = create_app()
```

**backend/wsgi.py**

```python
from app.main import app as application
```

Alembic quick-start (already in `pyproject`):

```
cd backend
poetry install
poetry run alembic init infra  # if you prefer infra/migrations at repo root, adjust
```

(Or keep Alembic under `infra/` and point `script_location` in `alembic.ini` there.)

# Frontend (Node + Vite + React)

**frontend/package.json**

```json
{
  "name": "your-frontend",
  "private": true,
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview --port 5173"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "typescript": "^5.5.4",
    "vite": "^5.4.0",
    "@types/react": "^18.2.66",
    "@types/react-dom": "^18.2.22"
  }
}
```

**frontend/src/app/api.ts**

```ts
export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000";
export async function ping() {
  const res = await fetch(`${API_BASE}/api/health`);
  return res.json();
}
```

# Root orchestration

**package.json** (repo root; use it as a tiny task runner so “one command from git dir” works)

```json
{
  "name": "your-project",
  "private": true,
  "workspaces": ["frontend"],
  "scripts": {
    "setup": "npm run -w frontend install && node -e \"console.log('Frontend deps installed')\"",
    "db:up": "docker compose up -d db",
    "db:down": "docker compose down",
    "dev:backend": "bash scripts/dev.sh backend",
    "dev:frontend": "bash scripts/dev.sh frontend",
    "dev": "bash scripts/dev.sh all",
    "lint": "bash scripts/lint-all.sh"
  }
}
```

**scripts/dev.sh** (Unix/macOS; Windows uses `scripts/dev.ps1`)

```bash
#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-all}"

if [[ "$TARGET" == "backend" || "$TARGET" == "all" ]]; then
  # Ensure venv & deps
  if [[ ! -d ".venv" ]]; then python -m venv .venv; fi
  source .venv/bin/activate
  python -m pip install -U pip
  if [[ -f backend/pyproject.toml ]]; then
    python -m pip install poetry
    (cd backend && poetry install)
  else
    (cd backend && pip install -r requirements.txt)
  fi
fi

if [[ "$TARGET" == "frontend" || "$TARGET" == "all" ]]; then
  (cd frontend && npm install)
fi

# Start DB
docker compose up -d db

if [[ "$TARGET" == "backend" ]]; then
  (cd backend && FLASK_APP=app/main.py flask --app app/main run --host 127.0.0.1 --port 5000 --debug)
elif [[ "$TARGET" == "frontend" ]]; then
  (cd frontend && npm run dev)
else
  # run both with tmux/gnu-screen fallback
  (cd backend && FLASK_APP=app/main.py flask --app app/main run --host 127.0.0.1 --port 5000 --debug) &
  BACK_PID=$!
  (cd frontend && npm run dev) &
  FRONT_PID=$!
  wait $BACK_PID $FRONT_PID
fi
```

**scripts/dev.ps1** (Windows PowerShell)

```powershell
param([string]$Target="all")
$ErrorActionPreference = "Stop"

# Python venv
if ($Target -eq "backend" -or $Target -eq "all") {
  if (-not (Test-Path ".\.venv")) { py -3 -m venv .venv }
  .\.venv\Scripts\python -m pip install --upgrade pip
  if (Test-Path ".\backend\pyproject.toml") {
    .\.venv\Scripts\python -m pip install poetry
    Push-Location .\backend
    ..\.\.venv\Scripts\poetry install
    Pop-Location
  } else {
    Push-Location .\backend
    ..\.\.venv\Scripts\pip install -r requirements.txt
    Pop-Location
  }
}

# Frontend deps
if ($Target -eq "frontend" -or $Target -eq "all") {
  Push-Location .\frontend
  npm install
  Pop-Location
}

# DB
docker compose up -d db

if ($Target -eq "backend") {
  Push-Location .\backend
  $env:FLASK_APP="app/main.py"
  ..\.\.venv\Scripts\python -m flask --app app/main run --host 127.0.0.1 --port 5000 --debug
  Pop-Location
} elseif ($Target -eq "frontend") {
  Push-Location .\frontend
  npm run dev
  Pop-Location
} else {
  Start-Process -NoNewWindow powershell -ArgumentList "Push-Location .\backend; `$env:FLASK_APP='app/main.py'; ..\.\.venv\Scripts\python -m flask --app app/main run --host 127.0.0.1 --port 5000 --debug"
  Start-Process -NoNewWindow powershell -ArgumentList "Push-Location .\frontend; npm run dev"
  Write-Host "Backend on http://127.0.0.1:5000 , Frontend on http://127.0.0.1:5173"
}
```

**Makefile** (Unix/macOS convenience; Windows uses PowerShell):

```makefile
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
```

# Typical workflow (from the git dir)

1. First-time setup

```
cp .env.example .env
docker compose up -d db
```

2. Run everything (Unix/macOS):

```
make dev
```

Windows PowerShell:

```
scripts\dev.ps1
```

3. Visit:

* Frontend: [http://127.0.0.1:5173](http://127.0.0.1:5173)
* API: [http://127.0.0.1:5000/api/health](http://127.0.0.1:5000/api/health)

# Migrations

* Configure **Alembic** to use `DATABASE_URL` from `.env`.
* Create revision: `cd backend && poetry run alembic revision -m "init" --autogenerate`
* Apply: `poetry run alembic upgrade head`

# Pre-commit (optional but nice)

**.pre-commit-config.yaml**

```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 24.8.0
    hooks: [{id: black}]
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks: [{id: ruff}]
  - repo: https://github.com/pre-commit/mirrors-prettier
    rev: v4.0.0-alpha.8
    hooks: [{id: prettier}]
```

Enable with `pre-commit install`.

---

If you want, I can tailor this to **Poetry vs. requirements.txt**, **Vite vs. Next.js**, or swap in **Dockerized backend/frontend** so literally everything runs under `docker compose up`.

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

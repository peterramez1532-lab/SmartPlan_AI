$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Error "Python 3.11+ is required. Install it from https://www.python.org/downloads/ and enable Add Python to PATH."
}
$pyVersion = python -c "import sys; print(sys.version_info.major*100+sys.version_info.minor)"
if ([int]$pyVersion -lt 311) { Write-Error "Python 3.11 or newer is required." }
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if (-not (Test-Path "data")) { New-Item -ItemType Directory -Path "data" | Out-Null }
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) { Copy-Item ".env.example" ".env" }
Write-Host "Starting SmartPlan AI at http://127.0.0.1:8000 (Ctrl+C to stop)" -ForegroundColor Cyan
& ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

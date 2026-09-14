# Start the pp-project development server on Windows.
# Usage:  .\run.ps1  [port]
param([int]$Port = 8000)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Error "Virtualenv missing. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

Set-Location $root
& $python manage.py migrate --no-input
& $python manage.py runserver "127.0.0.1:$Port"

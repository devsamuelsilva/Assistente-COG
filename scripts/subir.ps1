$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
Set-Location (Join-Path $PSScriptRoot "..")
$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Ambiente ainda nao existe. Na pasta do projeto: python -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements.txt"
}
& $python -m app servir

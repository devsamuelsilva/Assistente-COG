$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\web")
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error "Instale o Node.js para ter o comando npm."
}
if (-not (Test-Path "node_modules")) {
    npm install
}
npm run dev

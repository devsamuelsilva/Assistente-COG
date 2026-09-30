$ErrorActionPreference = "Stop"
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Error "Instale o cloudflared e deixe o comando disponivel no PATH. https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
}
$config = Join-Path $PSScriptRoot "cloudflared.yml"
if (-not (Test-Path $config)) {
    Write-Error "Copie scripts\cloudflared.config.example.yml para scripts\cloudflared.yml e preencha o hostname estavel."
}
Write-Host "Abrindo o tunel para http://127.0.0.1:8080. A API local precisa estar no ar (scripts\subir.ps1)."
cloudflared tunnel --config $config run

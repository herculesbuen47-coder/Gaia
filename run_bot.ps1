# Execute este script no PowerShell para manter o bot ativo.
# Ele ativa o ambiente virtual, executa bot.py e reinicia automaticamente se o processo cair.

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvActivate = Join-Path $scriptDir ".venv\Scripts\Activate.ps1"
$botScript = Join-Path $scriptDir "bot.py"

while ($true) {
    Write-Host "[run_bot] Iniciando bot..." -ForegroundColor Green
    if (Test-Path $venvActivate) {
        & $venvActivate
    } else {
        Write-Host "[run_bot] AVISO: ambiente virtual não encontrado em .venv\Scripts\Activate.ps1" -ForegroundColor Yellow
    }

    python $botScript

    Write-Host "[run_bot] Bot finalizou. Reiniciando em 5 segundos..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}

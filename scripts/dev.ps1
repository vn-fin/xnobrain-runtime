$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$HermesPython = Join-Path $HOME ".local/lib/hermes-agent/venv/bin/python"
$Python = if (Test-Path $HermesPython) { $HermesPython } else { "python" }

$Frontend = Start-Process -FilePath "npm" -ArgumentList "run", "dev", "--", "--host", "127.0.0.1" -WorkingDirectory (Join-Path $ProjectDir "src") -PassThru
try {
    Push-Location $ProjectDir
    & $Python server.py
} finally {
    Pop-Location
    Stop-Process -Id $Frontend.Id -ErrorAction SilentlyContinue
}

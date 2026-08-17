$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$HermesPython = Join-Path $HOME ".local/lib/hermes-agent/venv/bin/python"
$Python = if (Test-Path $HermesPython) { $HermesPython } else { "python" }

Push-Location $ProjectDir
try {
    & $Python server.py
} finally {
    Pop-Location
}

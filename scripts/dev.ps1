$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$Frontend = Start-Process -FilePath "npm" -ArgumentList "run", "dev", "--", "--host", "127.0.0.1" -WorkingDirectory "$ProjectDir/frontend" -PassThru
try {
    Push-Location "$ProjectDir"
    go run cmd/main.go
} finally {
    Pop-Location
    Stop-Process -Id $Frontend.Id -ErrorAction SilentlyContinue
}

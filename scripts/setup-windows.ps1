# Install Docker Desktop, Docker Compose, and make for local use on Windows.
# Run from PowerShell as your normal user. Windows may request a restart for WSL.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw 'Windows Package Manager (winget) is required. Install App Installer from the Microsoft Store, then run this script again.'
}

if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing WSL 2. Windows may ask you to restart; rerun this script after the restart.'
    wsl --install --no-distribution
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    winget install --exact --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

if (-not (Get-Command make -ErrorAction SilentlyContinue)) {
    winget install --exact --id GnuWin32.Make --accept-package-agreements --accept-source-agreements
    $makePath = 'C:\Program Files (x86)\GnuWin32\bin'
    if (Test-Path $makePath) {
        $env:Path = "$makePath;$env:Path"
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if ($userPath -notlike "*$makePath*") {
            [Environment]::SetEnvironmentVariable('Path', "$makePath;$userPath", 'User')
        }
    }
}

$dockerDesktop = @(
    "$env:LOCALAPPDATA\Programs\Docker\Docker\Docker Desktop.exe",
    'C:\Program Files\Docker\Docker\Docker Desktop.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $dockerDesktop) {
    throw 'Docker Desktop was installed but its executable was not found. Start it from the Start menu, then rerun this script.'
}

Start-Process $dockerDesktop
Write-Host -NoNewline 'Waiting for Docker Desktop to start'
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if (docker info 2>$null) { break }
    Write-Host -NoNewline '.'
    Start-Sleep -Seconds 2
}
Write-Host ''

if (-not (docker info 2>$null)) {
    throw 'Docker Desktop did not become ready. Complete its first-run setup, then rerun this script.'
}

docker --version
docker compose version
make --version | Select-Object -First 1
Write-Host 'Installation complete. Start XNOBrain with: docker compose up -d --build'

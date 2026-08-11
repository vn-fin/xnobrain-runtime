$ErrorActionPreference = 'Stop'

if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -notin @('AMD64', 'x86')) {
  throw 'XNOBrain Windows packaging currently requires Windows x86-64.'
}

foreach ($command in @('node', 'npm', 'rustc', 'cargo')) {
  if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
    throw "$command is missing. Run make from app/ to install contributor dependencies."
  }
}

$hostTriple = (& rustc -vV | Select-String '^host:').ToString().Split(':', 2)[1].Trim()
if ($hostTriple -ne 'x86_64-pc-windows-msvc') {
  throw "Rust host $hostTriple is unsupported. Select stable-x86_64-pc-windows-msvc."
}

$vsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vsWhere)) { throw 'Visual Studio C++ Build Tools are missing.' }
$vcInstall = & $vsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ([string]::IsNullOrWhiteSpace(($vcInstall | Select-Object -First 1))) {
  throw 'Install the Visual Studio Desktop development with C++ workload.'
}

if ($env:XNOBRAIN_REQUIRE_SIGNING -eq '1') {
  $thumbprint = ($env:XNOBRAIN_WINDOWS_CERTIFICATE_THUMBPRINT -replace '\s', '').ToUpperInvariant()
  if ($thumbprint -notmatch '^[A-F0-9]{40}$') {
    throw 'XNOBRAIN_WINDOWS_CERTIFICATE_THUMBPRINT must be a 40-character certificate thumbprint.'
  }
  $certificate = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
    Where-Object { ($_.Thumbprint -replace '\s', '').ToUpperInvariant() -eq $thumbprint } |
    Select-Object -First 1
  if (-not $certificate) { throw "The Windows signing certificate $thumbprint is not installed." }
}

Write-Host 'Windows native build preflight passed.'

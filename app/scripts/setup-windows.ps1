$ErrorActionPreference = 'Stop'
$AppDir = Split-Path -Parent $PSScriptRoot
$VsConfig = Join-Path $AppDir 'config\tauri-windows.vsconfig'
$VsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'

function Install-WinGetPackage {
  param([Parameter(Mandatory = $true)][string]$Id)
  & winget install --exact --id $Id --accept-package-agreements --accept-source-agreements --disable-interactivity
  if ($LASTEXITCODE -ne 0) { throw "winget could not install $Id (exit $LASTEXITCODE)." }
}

function Test-VcTools {
  if (-not (Test-Path $VsWhere)) { return $false }
  $installation = & $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
  return -not [string]::IsNullOrWhiteSpace(($installation | Select-Object -First 1))
}

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
  throw 'Windows App Installer (winget) is required for contributor setup.'
}
if (-not (Get-Command make -ErrorAction SilentlyContinue)) {
  Install-WinGetPackage 'GnuWin32.Make'
  $env:Path = "${env:ProgramFiles(x86)}\GnuWin32\bin;$env:Path"
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Install-WinGetPackage 'OpenJS.NodeJS.LTS'
}
if (-not (Get-Command rustup -ErrorAction SilentlyContinue)) {
  Install-WinGetPackage 'Rustlang.Rustup'
}
if (-not (Test-VcTools)) {
  & winget install --exact --id Microsoft.VisualStudio.BuildTools --accept-package-agreements --accept-source-agreements --disable-interactivity --override "--passive --wait --norestart --config `"$VsConfig`""
  if ($LASTEXITCODE -ne 0) { throw "Visual Studio C++ Build Tools installation failed (exit $LASTEXITCODE)." }
}
if (-not (Test-VcTools)) {
  throw 'Microsoft Visual Studio C++ Build Tools are installed without the Desktop development with C++ workload. Rerun setup as Administrator.'
}
& winget list --exact --id Microsoft.EdgeWebView2Runtime --accept-source-agreements | Out-Null
if ($LASTEXITCODE -ne 0) {
  Install-WinGetPackage 'Microsoft.EdgeWebView2Runtime'
}
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:ProgramFiles\nodejs;$env:Path"
rustup toolchain install stable-x86_64-pc-windows-msvc --profile minimal --component clippy,rustfmt
rustup default stable-x86_64-pc-windows-msvc
npm --prefix $AppDir ci
& (Join-Path $PSScriptRoot 'preflight-windows.ps1')
Write-Host 'XNOBrain app development dependencies are ready.'
Write-Host 'Open a new terminal so make, Node, Rust, and Visual Studio environment changes are visible.'

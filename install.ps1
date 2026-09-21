# Grounded 1-Line Installer for Windows (PowerShell)
# Usage: irm https://raw.githubusercontent.com/gonisulaimann/Grounded/main/install.ps1 | iex
$ErrorActionPreference = "Stop"

$repo = "gonisulaimann/Grounded"
$binName = "grounded.exe"

function Write-Info ($msg) {
    Write-Host "==> " -ForegroundColor Cyan -NoNewline
    Write-Host $msg -ForegroundColor White
}

function Write-Success ($msg) {
    Write-Host "==> " -ForegroundColor Green -NoNewline
    Write-Host $msg -ForegroundColor White
}

function Write-WarningMsg ($msg) {
    Write-Host "Warning: $msg" -ForegroundColor Yellow
}

# 1. Check if uv is available
if (Get-Command "uv" -ErrorAction SilentlyContinue) {
    Write-Info "Installing Grounded via uv..."
    try {
        & uv tool install grounded-lint
        Write-Success "Grounded installed successfully via uv!"
        exit 0
    } catch {
        Write-WarningMsg "uv install failed, trying fallback..."
    }
}

# 2. Check if pip / python is available
$pythonCmd = $null
if (Get-Command "python" -ErrorAction SilentlyContinue) { $pythonCmd = "python" }
elseif (Get-Command "py" -ErrorAction SilentlyContinue) { $pythonCmd = "py" }

if ($pythonCmd) {
    Write-Info "Python detected. Installing Grounded via pip..."
    try {
        & $pythonCmd -m pip install --user grounded-lint
        Write-Success "Grounded installed successfully via pip!"
        exit 0
    } catch {
        Write-WarningMsg "pip install failed, falling back to standalone binary..."
    }
}

# 3. Zero-Python Fallback: Download standalone precompiled .exe from GitHub Releases
Write-Info "No Python environment found. Downloading standalone grounded.exe from GitHub Releases..."

$arch = if ([System.Environment]::Is64BitOperatingSystem) { "amd64" } else { "x86" }
$targetName = "grounded-windows-$arch.exe"
$url = "https://github.com/$repo/releases/latest/download/$targetName"

$installDir = Join-Path $env:LOCALAPPDATA "grounded"
if (!(Test-Path $installDir)) {
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
}

$dest = Join-Path $installDir $binName

Write-Info "Downloading $targetName to $dest..."
Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing

# Add to User PATH if not present
$userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$installDir*") {
    Write-Info "Adding $installDir to User PATH..."
    [System.Environment]::SetEnvironmentVariable("Path", "$userPath;$installDir", "User")
    $env:Path = "$env:Path;$installDir"
}

Write-Success "Grounded standalone binary installed successfully!"
Write-Host ""
Write-Host "Verification:" -ForegroundColor Cyan
& $dest --version

Write-Host ""
Write-Host "Grounded is ready to use!" -ForegroundColor Green
Write-Host "Run 'grounded scan .' in any repository to check for stale references."

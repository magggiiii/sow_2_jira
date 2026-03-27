# SOW-to-Jira Guided Windows Installer

# Utility: Confirm with user
function Confirm-Action {
    param([string]$Message)
    $choice = Read-Host "$Message (y/n)"
    return ($choice -eq 'y' -or $choice -eq 'Y')
}

# 1. Colors & Banner
$Blue = "`e[34m"
$Green = "`e[32m"
$Red = "`e[31m"
$Yellow = "`e[33m"
$Reset = "`e[0m"

if (Test-Path "art.md") {
    Write-Host "$Blue"
    Get-Content "art.md"
    Write-Host "$Reset"
} else {
    Write-Host "$Blue SOW TO JIRA INSTALLER $Reset"
}

Write-Host "Starting guided setup for your portable extraction engine...`n"

# 2. Check/Install Docker
if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "$Yellow [!] Docker is not installed.$Reset"
    if (Confirm-Action "Would you like me to install Docker Desktop for you?") {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "$Blue [INFO] Installing Docker Desktop via Winget...$Reset"
            winget install Docker.DockerDesktop
        } else {
            Write-Host "$Blue [INFO] Downloading Docker Desktop Installer...$Reset"
            $url = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
            $out = Join-Path $env:TEMP "DockerInstaller.exe"
            Invoke-WebRequest -Uri $url -OutFile $out
            Start-Process $out -Wait
        }
        Write-Host "$Yellow [ACTION] Please restart your computer after Docker installation completes, then run this script again.$Reset"
        exit 0
    } else {
        Write-Host "$Red [ERROR] Docker is required to run SOW-to-Jira.$Reset"
        exit 1
    }
}

# Check if docker is running
docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "$Yellow [!] Docker is installed but not running.$Reset"
    Write-Host "$Blue [INFO] Starting Docker Desktop...$Reset"
    & "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    Write-Host "Waiting for Docker to start..."
    until (docker info) { Start-Sleep -s 2 }
}

Write-Host "$Green [OK]$Reset Docker is ready."

# 3. Setup Persistence Folder
$SowHome = Join-Path $HOME ".sow_to_jira"
$SowData = Join-Path $SowHome "data"
if (!(Test-Path $SowData)) {
    New-Item -ItemType Directory -Force -Path $SowData | Out-Null
}
Write-Host "$Green [OK]$Reset Global data directory created at $SowHome"

# 4. Global Environment Scaffolding
$GlobalEnv = Join-Path $SowHome ".env"
if (!(Test-Path $GlobalEnv)) {
    Copy-Item ".env.example" $GlobalEnv
    Write-Host "$Green [OK]$Reset Initialized global .env at $GlobalEnv"
}

# 5. Native Command Registration (sjt)
$InstallDir = $PSScriptRoot
$ProfilePath = $PROFILE
if (!(Test-Path $ProfilePath)) { New-Item -ItemType File -Path $ProfilePath -Force | Out-Null }

$SjtFunction = @"

# SOW-to-Jira Terminal Function
function sjt {
    `$env:SOW_DATA_HOME = "$SowData"
    `$env:SOW_ENV_FILE = "$GlobalEnv"
    Set-Location "$InstallDir"
    docker-compose -f docker-compose.yml up -d
    Start-Process "http://localhost:8000"
}
"@

if ((Get-Content $ProfilePath | Select-String "function sjt {").Count -eq 0) {
    Add-Content $ProfilePath $SjtFunction
    Write-Host "$Green [OK]$Reset Added 'sjt' command to your profile."
}

# 6. Final Instructions
Write-Host "`n--------------------------------------------------"
Write-Host "$Green Configuration Complete! $Reset"
Write-Host "1. Restart your terminal."
Write-Host "2. Type $Blue sjt $Reset to launch."
Write-Host "3. Open $Blue Settings $Reset (gear icon) in the UI to add your API keys."
Write-Host "--------------------------------------------------`n"

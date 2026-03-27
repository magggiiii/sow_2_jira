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

# 1. Setup Persistence & Source Folders
$SowHome = Join-Path $HOME ".sow_to_jira"
$SowSource = Join-Path $SowHome "source"
$SowData = Join-Path $SowHome "data"
if (!(Test-Path $SowData)) { New-Item -ItemType Directory -Path $SowData -Force | Out-Null }

# 2. Bootstrap Repository
Write-Host "$Blue [INFO] Bootstrapping repository... $Reset"
if (Test-Path $SowSource) {
    Write-Host "$Blue [INFO] Updating existing source... $Reset"
    Set-Location $SowSource
    git pull origin main | Out-Null
} else {
    Write-Host "$Blue [INFO] Cloning repository to $SowSource... $Reset"
    git clone https://calib.dev/mageswaran/sow_2_jira.git $SowSource | Out-Null
    Set-Location $SowSource
}

# 3. Display Art
if (Test-Path "art.md") {
    Write-Host "$Blue"
    Get-Content "art.md"
    Write-Host "$Reset"
} else {
    Write-Host "$Blue SOW TO JIRA INSTALLER $Reset"
}

Write-Host "Starting guided setup for your portable extraction engine...`n"

# 4. Check/Install Docker
if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "$Yellow [!] Docker is not installed. $Reset"
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
        Write-Host "$Yellow [ACTION] Please restart your computer and run this script again. $Reset"
        exit 0
    } else {
        Write-Host "$Red [ERROR] Docker is required. $Reset"
        exit 1
    }
}

# Check if docker is running
docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "$Yellow [!] Docker is installed but not running.$Reset"
    if (Test-Path "C:\Program Files\Docker\Docker\Docker Desktop.exe") {
        Write-Host "$Blue [INFO] Starting Docker Desktop...$Reset"
        & "C:\Program Files\Docker\Docker\Docker Desktop.exe"
        until (docker info) { Start-Sleep -s 1 }
    } else {
        Write-Host "$Red [ERROR] Please start Docker Desktop and try again. $Reset"
        exit 1
    }
}

Write-Host "$Green [OK]$Reset Docker is ready. "

# 5. Global Environment Scaffolding
$GlobalEnv = Join-Path $SowHome ".env"
if (!(Test-Path $GlobalEnv)) {
    Copy-Item ".env.example" $GlobalEnv
    Write-Host "$Green [OK]$Reset Initialized global .env at $GlobalEnv"
}

# 6. Native Command Registration (sjt)
$ProfilePath = $PROFILE
if (!(Test-Path $ProfilePath)) { New-Item -ItemType File -Path $ProfilePath -Force | Out-Null }

$SjtFunction = @"

# SOW-to-Jira Terminal Function
function sjt {
    `$env:SOW_DATA_HOME = "$SowData"
    `$env:SOW_ENV_FILE = "$GlobalEnv"
    Set-Location "$SowSource"
    docker-compose -f docker-compose.yml up -d
    Start-Process "http://localhost:8000"
}
"@

if ((Get-Content $ProfilePath | Select-String "function sjt {").Count -eq 0) {
    Add-Content $ProfilePath $SjtFunction
    Write-Host "$Green [OK]$Reset Command 'sjt' registered in your profile."
}

# 7. Final Instructions
Write-Host "`n--------------------------------------------------"
Write-Host "$Green Configuration Complete! $Reset"
Write-Host "1. Restart your terminal."
Write-Host "2. Type $Blue sjt $Reset to launch."
Write-Host "3. Open $Blue Settings $Reset (gear icon) in the UI to add your API keys."
Write-Host "--------------------------------------------------`n"

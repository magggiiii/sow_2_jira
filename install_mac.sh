#!/bin/bash

# SOW-to-Jira Guided Installer for macOS
# Optimized for Homebrew and Apple Silicon / Intel

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Utility: Confirm with user (Pipe-safe for macOS)
confirm() {
    read -r -p "${1} [y/N] " response < /dev/tty
    case "$response" in
        [yY][eE][sS]|[yY]) true ;;
        *) false ;;
    esac
}

# 1. Setup Folders
SOW_HOME="$HOME/.sow_to_jira"
SOW_SOURCE="$SOW_HOME/source"
SOW_DATA="$SOW_HOME/data"
mkdir -p "$SOW_DATA"

# 2. Bootstrap Git
if ! command -v git &> /dev/null; then
    echo -e "${YELLOW}[!] Git is not installed.${NC}"
    if confirm "Would you like me to install Git via Homebrew?"; then
        if ! command -v brew &> /dev/null; then
            echo -e "${BLUE}[INFO] Installing Homebrew first...${NC}"
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
        brew install git
    else
        echo -e "${RED}[ERROR] Git is required.${NC}"
        exit 1
    fi
fi

# 3. Bootstrap Repository
echo -e "${BLUE}[INFO] Bootstrapping repository...${NC}"
if [ -d "$SOW_SOURCE" ]; then
    cd "$SOW_SOURCE" && git pull origin main &> /dev/null || true
else
    git clone https://calib.dev/mageswaran/sow_2_jira.git "$SOW_SOURCE" &> /dev/null
fi
cd "$SOW_SOURCE"

# Display Art
[ -f "art.md" ] && { echo -e "${BLUE}"; cat art.md; echo -e "${NC}"; }

# 4. Docker Setup
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}[!] Docker is not installed.${NC}"
    if confirm "Would you like me to install Docker Desktop via Homebrew?"; then
        if ! command -v brew &> /dev/null; then
            echo -e "${RED}[ERROR] Homebrew required.${NC}"
            exit 1
        fi
        brew install --cask docker
    else
        echo -e "${RED}[ERROR] Docker required.${NC}"
        exit 1
    fi
fi

if ! docker info &> /dev/null; then
    echo -e "${YELLOW}[!] Docker is not running.${NC}"
    echo -e "${BLUE}[INFO] Starting Docker Desktop...${NC}"
    open --background -a Docker
    echo -e "Waiting for Docker to start..."
    until docker info &> /dev/null; do sleep 2; done
fi

echo -e "${GREEN}[OK]${NC} Docker is ready."

# 5. Global Env & Alias
GLOBAL_ENV="$SOW_HOME/.env"
[ ! -f "$GLOBAL_ENV" ] && cp .env.example "$GLOBAL_ENV"

SHELL_RC="$HOME/.zshrc"
LAUNCH_CMD="alias sjt='export SOW_DATA_HOME=\"$SOW_DATA\" && export SOW_ENV_FILE=\"$GLOBAL_ENV\" && cd \"$SOW_SOURCE\" && docker-compose -f docker-compose.yml up -d && open http://localhost:8000'"

# Portable update of .zshrc
touch "$SHELL_RC"
grep -v "alias sjt=" "$SHELL_RC" > "$SHELL_RC.tmp" || true
echo -e "\n# SOW-to-Jira\n$LAUNCH_CMD" >> "$SHELL_RC.tmp"
mv "$SHELL_RC.tmp" "$SHELL_RC"

echo -e "\n${GREEN}Configuration Complete!${NC}"
echo -e "Reloading shell... Typing ${BLUE}sjt${NC} will now launch the app."
exec zsh

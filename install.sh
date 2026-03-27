#!/bin/bash

# SOW-to-Jira Guided Installer
# Inspired by Oh-My-Zsh & Homebrew.

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Utility: Confirm with user
confirm() {
    read -r -p "${1} [y/N] " response
    case "$response" in
        [yY][eE][sS]|[yY]) 
            true
            ;;
        *)
            false
            ;;
    esac
}

# 1. Display Art
if [ -f "art.md" ]; then
    echo -e "${BLUE}"
    cat art.md
    echo -e "${NC}"
else
    echo -e "${BLUE}SOW TO JIRA INSTALLER${NC}"
fi

echo -e "Starting guided setup for your portable extraction engine...\n"

# 2. Check/Install Docker
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}[!] Docker is not installed.${NC}"
    if confirm "Would you like me to install Docker for you?"; then
        OS_TYPE="$(uname)"
        if [ "$OS_TYPE" == "Darwin" ]; then
            if command -v brew &> /dev/null; then
                echo -e "${BLUE}[INFO] Installing Docker via Homebrew...${NC}"
                brew install --cask docker
            else
                echo -e "${RED}[ERROR] Homebrew not found.${NC} Please install Docker Desktop manually: https://www.docker.com/products/docker-desktop"
                exit 1
            fi
        elif [ "$OS_TYPE" == "Linux" ]; then
            echo -e "${BLUE}[INFO] Installing Docker via official script...${NC}"
            curl -fsSL https://get.docker.com | sh
            sudo usermod -aG docker "$USER"
            echo -e "${YELLOW}[ACTION] Please log out and back in for Docker group changes to take effect.${NC}"
        else
            echo -e "${RED}[ERROR] Unsupported OS for auto-install.${NC} Please install Docker manually."
            exit 1
        fi
    else
        echo -e "${RED}[ERROR] Docker is required to run SOW-to-Jira.${NC} Exiting."
        exit 1
    fi
fi

# Verify Docker is running
if ! docker info &> /dev/null; then
    echo -e "${YELLOW}[!] Docker is installed but not running.${NC}"
    if [[ "$(uname)" == "Darwin" ]]; then
        echo -e "${BLUE}[INFO] Attempting to start Docker Desktop...${NC}"
        open --background -a Docker
        echo -e "Waiting for Docker to start..."
        until docker info &> /dev/null; do sleep 1; done
    else
        echo -e "${RED}[ERROR] Please start the Docker service and try again.${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}[OK]${NC} Docker is ready."

# 3. Setup Persistence Folder
SOW_HOME="$HOME/.sow_to_jira"
if [ ! -d "$SOW_HOME" ]; then
    mkdir -p "$SOW_HOME/data"
    echo -e "${GREEN}[OK]${NC} Global data directory created at $SOW_HOME"
fi

# 4. Environment Scaffolding
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "${GREEN}[OK]${NC} Initialized .env from template."
fi

# 5. Native Command Registration (sjt)
INSTALL_DIR=$(pwd)
SHELL_RC_FILE=""

# Detect shell
if [[ "$SHELL" == *"zsh"* ]; then
    SHELL_RC_FILE="$HOME/.zshrc"
elif [[ "$SHELL" == *"bash"* ]]; then
    [ -f "$HOME/.bash_profile" ] && SHELL_RC_FILE="$HOME/.bash_profile" || SHELL_RC_FILE="$HOME/.bashrc"
fi

if [ -n "$SHELL_RC_FILE" ]; then
    LAUNCH_CMD="alias sjt='export SOW_DATA_HOME=\"$SOW_HOME/data\" && cd \"$INSTALL_DIR\" && docker-compose up -d && open http://localhost:8000'"
    
    if grep -q "alias sjt=" "$SHELL_RC_FILE"; then
        sed -i '' "s|alias sjt=.*|$LAUNCH_CMD|" "$SHELL_RC_FILE" 2>/dev/null || sed -i "s|alias sjt=.*|$LAUNCH_CMD|" "$SHELL_RC_FILE"
        echo -e "${GREEN}[OK]${NC} Updated 'sjt' command in $SHELL_RC_FILE"
    else
        echo -e "\n# SOW-to-Jira Alias\n$LAUNCH_CMD" >> "$SHELL_RC_FILE"
        echo -e "${GREEN}[OK]${NC} Added 'sjt' command to $SHELL_RC_FILE"
    fi
fi

# 6. Final Instructions
echo -e "\n--------------------------------------------------"
echo -e "${GREEN}Configuration Complete!${NC}"
echo -e "1. Edit your ${BLUE}.env${NC} file to add your API keys."
echo -e "2. Restart your terminal."
echo -e "3. Type ${BLUE}sjt${NC} to launch."
echo -e "--------------------------------------------------\n"

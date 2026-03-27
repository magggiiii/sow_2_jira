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

# 1. Setup Persistence & Source Folders
SOW_HOME="$HOME/.sow_to_jira"
SOW_SOURCE="$SOW_HOME/source"
SOW_DATA="$SOW_HOME/data"
mkdir -p "$SOW_DATA"

# 2. Bootstrap Repository
echo -e "${BLUE}[INFO] Bootstrapping repository...${NC}"
if [ -d "$SOW_SOURCE" ]; then
    echo -e "${BLUE}[INFO] Updating existing source...${NC}"
    cd "$SOW_SOURCE" && git pull origin main &> /dev/null || true
else
    echo -e "${BLUE}[INFO] Cloning repository to $SOW_SOURCE...${NC}"
    git clone https://calib.dev/mageswaran/sow_2_jira.git "$SOW_SOURCE" &> /dev/null || {
        echo -e "${RED}[ERROR] Failed to clone repository. Please check your internet connection.${NC}"
        exit 1
    }
fi

cd "$SOW_SOURCE"

# 3. Display Art
if [ -f "art.md" ]; then
    echo -e "${BLUE}"
    cat art.md
    echo -e "${NC}"
else
    echo -e "${BLUE}SOW TO JIRA INSTALLER${NC}"
fi

echo -e "Starting guided setup for your portable extraction engine...\n"

# 4. Check/Install Docker
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}[!] Docker is not installed.${NC}"
    if confirm "Would you like me to install Docker for you?"; then
        OS_TYPE="$(uname)"
        if [ "$OS_TYPE" == "Darwin" ]; then
            if command -v brew &> /dev/null; then
                echo -e "${BLUE}[INFO] Installing Docker via Homebrew...${NC}"
                brew install --cask docker
            else
                echo -e "${RED}[ERROR] Homebrew not found.${NC} Please install Docker Desktop manually."
                exit 1
            fi
        elif [ "$OS_TYPE" == "Linux" ]; then
            echo -e "${BLUE}[INFO] Installing Docker via official script (requires sudo)...${NC}"
            curl -fsSL https://get.docker.com | sh
            sudo usermod -aG docker "$USER"
            echo -e "${YELLOW}[ACTION] Please log out and back in for Docker group changes to take effect.${NC}"
        fi
    else
        echo -e "${RED}[ERROR] Docker is required.${NC} Exiting."
        exit 1
    fi
fi

# Verify Docker is running
if ! docker info &> /dev/null; then
    echo -e "${YELLOW}[!] Docker is installed but not running.${NC}"
    if [[ "$(uname)" == "Darwin" ]]; then
        echo -e "${BLUE}[INFO] Starting Docker Desktop...${NC}"
        open --background -a Docker
        until docker info &> /dev/null; do sleep 1; done
    else
        echo -e "${RED}[ERROR] Please start the Docker service (sudo systemctl start docker).${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}[OK]${NC} Docker is ready."

# 5. Global Environment Scaffolding
GLOBAL_ENV="$SOW_HOME/.env"
if [ ! -f "$GLOBAL_ENV" ]; then
    cp .env.example "$GLOBAL_ENV"
    echo -e "${GREEN}[OK]${NC} Initialized global .env at $GLOBAL_ENV"
fi

# 6. Native Command Registration (sjt)
SHELL_RC_FILE=""
if [[ "$SHELL" == *"zsh"* ]]; then
    SHELL_RC_FILE="$HOME/.zshrc"
elif [[ "$SHELL" == *"bash"* ]]; then
    [ -f "$HOME/.bash_profile" ] && SHELL_RC_FILE="$HOME/.bash_profile" || SHELL_RC_FILE="$HOME/.bashrc"
fi

if [ -n "$SHELL_RC_FILE" ]; then
    LAUNCH_CMD="alias sjt='export SOW_DATA_HOME=\"$SOW_DATA\" && export SOW_ENV_FILE=\"$GLOBAL_ENV\" && cd \"$SOW_SOURCE\" && docker-compose -f docker-compose.yml up -d && (open http://localhost:8000 || xdg-open http://localhost:8000 || echo \"Open http://localhost:8000 in your browser\")'"
    
    if grep -q "alias sjt=" "$SHELL_RC_FILE"; then
        sed -i '' "s|alias sjt=.*|$LAUNCH_CMD|" "$SHELL_RC_FILE" 2>/dev/null || sed -i "s|alias sjt=.*|$LAUNCH_CMD|" "$SHELL_RC_FILE"
    else
        echo -e "\n# SOW-to-Jira Alias\n$LAUNCH_CMD" >> "$SHELL_RC_FILE"
    fi
    echo -e "${GREEN}[OK]${NC} Command 'sjt' registered in $SHELL_RC_FILE"
fi

# 7. Final Instructions
echo -e "\n--------------------------------------------------"
echo -e "${GREEN}Configuration Complete!${NC}"
echo -e "1. Restart your terminal."
echo -e "2. Type ${BLUE}sjt${NC} to launch."
echo -e "3. Open ${BLUE}Settings${NC} (gear icon) in the UI to add your API keys."
echo -e "--------------------------------------------------\n"

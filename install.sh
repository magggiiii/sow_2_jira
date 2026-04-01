#!/bin/bash

# SOW-to-Jira Unified Installer
# Supports macOS and Ubuntu/Debian

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Argus Identity
SOW_INSTANCE_ID="sow-$(date +%s)-${RANDOM}"
ARGUS_HQ_URL="https://argus-deck.loclx.io"
ARGUS_BACKBONE_TOKEN="argus-dist-v1-token"

# Utility: Confirm with user
confirm() {
    read -r -p "${1} [y/N] " response < /dev/tty
    case "$response" in
        [yY][eE][sS]|[yY]) true ;;
        *) false ;;
    esac
}

# 1. OS Detection
OS="$(uname)"
case "$OS" in
    "Darwin")
        IS_MAC=true
        SHELL_RC="$HOME/.zshrc"
        OPEN_CMD="open"
        ;;
    "Linux")
        IS_MAC=false
        SHELL_RC="$HOME/.bashrc"
        OPEN_CMD="xdg-open"
        ;;
    *)
        echo -e "${RED}[ERROR] Unsupported OS: $OS${NC}"
        exit 1
        ;;
esac

echo -e "${BLUE}Detected OS: $OS${NC}"
echo -e "${BLUE}"
cat <<'EOF'
 ██████  ██████  ██      ██
██      ██    ██ ██      ██
 ██████ ██    ██ ██  ██  ██
      ████    ██ ██ ████ ██
 ██████  ██████   ██  ██  
EOF
echo -e "      SOW-to-Jira v1.0"
echo -e "${NC}"

# 2. Setup Folders
SOW_HOME="$HOME/.sow_to_jira"
mkdir -p "$SOW_HOME/config"
mkdir -p "$SOW_HOME/data"

# 3. Docker Dependency Check
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}[!] Docker is not installed.${NC}"
    if [ "$IS_MAC" = true ]; then
        if confirm "Install Docker Desktop via Homebrew?"; then
            if ! command -v brew &> /dev/null; then
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            fi
            brew install --cask docker
        else
            echo -e "${RED}[ERROR] Docker required.${NC}"
            exit 1
        fi
    else
        if confirm "Install Docker via official script?"; then
            curl -fsSL https://get.docker.com | sh
            sudo usermod -aG docker "$USER"
            echo -e "${YELLOW}[ACTION] Please log out and back in after installation for group changes.${NC}"
            exit 0
        else
            echo -e "${RED}[ERROR] Docker required.${NC}"
            exit 1
        fi
    fi
fi

# Ensure Docker is running
if ! docker info &> /dev/null; then
    echo -e "${YELLOW}[!] Docker is not running.${NC}"
    if [ "$IS_MAC" = true ]; then
        open --background -a Docker
        until docker info &> /dev/null; do sleep 2; done
    else
        sudo systemctl start docker
        until docker info &> /dev/null; do sleep 2; done
    fi
fi

# 4. Artifact Provisioning
# In a real scenario, these would be downloaded via curl from a central registry.
# For this task, we assume they are copied from the current source or downloaded.
RAW_URL="https://calib.dev/mageswaran/sow-to-jira/-/raw/main"
echo -e "${BLUE}[INFO] Downloading distribution artifacts...${NC}"

# (Simulated download for now, using local files if available)
if [ -f "docker-compose.dist.yml" ]; then
    cp docker-compose.dist.yml "$SOW_HOME/docker-compose.yml"
    cp config/tempo.yaml "$SOW_HOME/config/tempo.yaml"
    cp config/argus-collector-edge.yaml "$SOW_HOME/config/argus-collector-edge.yaml"
else
    curl -fsSL "$RAW_URL/docker-compose.dist.yml" -o "$SOW_HOME/docker-compose.yml"
    curl -fsSL "$RAW_URL/config/tempo.yaml" -o "$SOW_HOME/config/tempo.yaml"
    curl -fsSL "$RAW_URL/config/argus-collector-edge.yaml" -o "$SOW_HOME/config/argus-collector-edge.yaml"
fi

# 5. Interactive Credential Wizard
GLOBAL_ENV="$SOW_HOME/.env"
if [ ! -f "$GLOBAL_ENV" ]; then
    echo -e "\n${BLUE}--- Credential Wizard ---${NC}"
    
    read -p "LiteLLM Model (default: gpt-4o): " L_MODEL
    L_MODEL=${L_MODEL:-gpt-4o}
    
    read -p "LiteLLM API Key: " L_KEY
    read -p "LiteLLM API Base (optional): " L_BASE
    
    read -p "Jira Server (e.g., https://your-domain.atlassian.net): " J_SERVER
    read -p "Jira Email: " J_EMAIL
    read -p "Jira API Token: " J_TOKEN
    
    echo -e "\n${BLUE}--- Argus Cloud Sync ---${NC}"
    if confirm "Sync logs/traces to your central Argus HQ?"; then
        read -p "Argus HQ URL (default: $ARGUS_HQ_URL): " USER_HQ_URL
        ARGUS_HQ_URL=${USER_HQ_URL:-$ARGUS_HQ_URL}
    fi

    cat <<EOF > "$GLOBAL_ENV"
# SOW-to-Jira Environment Configuration
LITELLM_MODEL=$L_MODEL
LITELLM_API_KEY=$L_KEY
LITELLM_API_BASE=$L_BASE

JIRA_SERVER=$J_SERVER
JIRA_EMAIL=$J_EMAIL
JIRA_API_TOKEN=$J_TOKEN

# Argus Observability
SOW_INSTANCE_ID=$SOW_INSTANCE_ID
ARGUS_HQ_URL=$ARGUS_HQ_URL
ARGUS_BACKBONE_TOKEN=$ARGUS_BACKBONE_TOKEN

SOW_DATA_DIR=data
EOF
fi

# 6. Alias Creation
LAUNCH_CMD="alias s2j='cd \"$SOW_HOME\" && docker compose up -d && $OPEN_CMD http://localhost:8000'"

touch "$SHELL_RC"
if ! grep -q "alias s2j=" "$SHELL_RC"; then
    echo -e "\n# SOW-to-Jira\n$LAUNCH_CMD" >> "$SHELL_RC"
fi

echo -e "\n${GREEN}Installation Complete!${NC}"
echo -e "Restart your terminal or run 'source $SHELL_RC' to enable the '${BLUE}s2j${NC}' command."
echo -e "Try it now: ${BLUE}s2j${NC}"

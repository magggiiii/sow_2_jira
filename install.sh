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
ARGUS_HQ_URL="https://hz8nuthhmt.loclx.io"
ARGUS_BACKBONE_TOKEN="42e389e1f820e7f52c55aa35b8592552bf0d83ca5e82a62d"

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

# 3.5 Ollama Dependency Check & Configuration
echo -e "${BLUE}[INFO] Checking for Ollama (optional, but required for local models)...${NC}"
if ! command -v ollama &> /dev/null; then
    echo -e "${YELLOW}[!] Ollama is not installed.${NC}"
    if confirm "Install Ollama for local LLM support?"; then
        if [ "$IS_MAC" = true ]; then
            if ! command -v brew &> /dev/null; then
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            fi
            brew install --cask ollama
            echo -e "${GREEN}✓ Ollama installed via Homebrew.${NC}"
        else
            curl -fsSL https://ollama.com/install.sh | sh
            echo -e "${GREEN}✓ Ollama installed.${NC}"
        fi
    fi
fi

if command -v ollama &> /dev/null; then
    echo -e "${BLUE}[INFO] Configuring Ollama for Docker access...${NC}"
    if [ "$IS_MAC" = true ]; then
        launchctl setenv OLLAMA_HOST "0.0.0.0"
        echo -e "${YELLOW}[ACTION] Please ensure the Ollama app is running.${NC}"
        echo -e "${YELLOW}         If it was already running, please completely Quit it and restart it so the new OLLAMA_HOST setting takes effect.${NC}"
    else
        # For Linux, configure systemd override for OLLAMA_HOST
        if [ -d "/etc/systemd/system" ]; then
            sudo mkdir -p /etc/systemd/system/ollama.service.d
            echo -e "[Service]\nEnvironment=\"OLLAMA_HOST=0.0.0.0\"" | sudo tee /etc/systemd/system/ollama.service.d/environment.conf > /dev/null
            sudo systemctl daemon-reload
            sudo systemctl restart ollama
            echo -e "${GREEN}✓ Ollama systemd service configured to listen on 0.0.0.0.${NC}"
        fi
    fi
fi

# Ensure registry access
echo -e "${BLUE}[INFO] Verifying access to SOW-to-Jira images...${NC}"
# Attempt a manifest check instead of a full pull to verify visibility
if ! docker manifest inspect ghcr.io/magggiiii/sow_2_jira:v1.1 &> /dev/null; then
    echo -e "${YELLOW}[!] Note: Could not verify public image visibility.${NC}"
    echo -e "If the next step fails, please ensure the project registry at https://ghcr.io/magggiiii/sow_2_jira is set to 'Public'."
fi

# 4. Artifact Provisioning
# In a real scenario, these would be downloaded via curl from a central registry.
# For this task, we assume they are copied from the current source or downloaded.
RAW_URL="https://raw.githubusercontent.com/magggiiii/sow_2_jira/main"
echo -e "${BLUE}[INFO] Downloading distribution artifacts...${NC}"

# (Simulated download for now, using local files if available)
if [ -f "docker-compose.user.yml" ]; then
    cp docker-compose.user.yml "$SOW_HOME/docker-compose.user.yml"
    cp config/tempo.yaml "$SOW_HOME/config/tempo.yaml"
    cp config/argus-collector-edge.yaml "$SOW_HOME/config/argus-collector-edge.yaml"
else
    echo "  -> Fetching docker-compose.user.yml..."
    curl -# -fL "$RAW_URL/docker-compose.user.yml" -o "$SOW_HOME/docker-compose.user.yml"
    echo "  -> Fetching tempo.yaml..."
    curl -# -fL "$RAW_URL/config/tempo.yaml" -o "$SOW_HOME/config/tempo.yaml"
    echo "  -> Fetching argus-collector-edge.yaml..."
    curl -# -fL "$RAW_URL/config/argus-collector-edge.yaml" -o "$SOW_HOME/config/argus-collector-edge.yaml"
fi

# 5. Environment Setup (Wizard removed, now handled in UI)
GLOBAL_ENV="$SOW_HOME/.env"
if [ ! -f "$GLOBAL_ENV" ]; then
    cat <<EOF > "$GLOBAL_ENV"
# SOW-to-Jira Environment Configuration
# Use the Web UI at http://localhost:8000 to configure API keys.

# Argus Observability (Optional)
ARGUS_SYNC_ENABLED=false
SOW_INSTANCE_ID=$SOW_INSTANCE_ID
ARGUS_HQ_URL=$ARGUS_HQ_URL
ARGUS_BACKBONE_TOKEN=$ARGUS_BACKBONE_TOKEN

SOW_DATA_DIR=data
EOF
fi

# 6. Alias Creation
LAUNCH_CMD="alias s2j='cd \"$SOW_HOME\" && docker compose -f docker-compose.user.yml up -d && $OPEN_CMD http://localhost:8000'"

touch "$SHELL_RC"
# Remove old alias if it exists
sed -i.bak '/alias s2j=/d' "$SHELL_RC" 2>/dev/null || true
echo -e "\n# SOW-to-Jira\n$LAUNCH_CMD" >> "$SHELL_RC"

echo -e "\n${GREEN}Installation Complete!${NC}"
echo -e "Restart your terminal or run 'source $SHELL_RC' to enable the '${BLUE}s2j${NC}' command."
echo -e "Try it now: ${BLUE}s2j${NC}"

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
# otlp gRPC exporter expects host:port (no scheme)
ARGUS_HQ_URL="hz8nuthhmt.loclx.io:443"
ARGUS_BACKBONE_TOKEN="42e389e1f820e7f52c55aa35b8592552bf0d83ca5e82a62d"

# Utility: Confirm with user
confirm() {
    read -r -p "${1} [y/N] " response < /dev/tty
    case "$response" in
        [yY][eE][sS]|[yY]) true ;;
        *) false ;;
    esac
}

normalize_host_port() {
    local value="$1"
    value="${value#https://}"
    value="${value#http://}"
    if [[ "$value" != *:* ]]; then
        value="${value}:443"
    fi
    echo "$value"
}

upsert_env_var() {
    local file="$1"
    local key="$2"
    local value="$3"
    if grep -q "^${key}=" "$file"; then
        sed -i.bak "s|^${key}=.*|${key}=${value}|" "$file"
    else
        echo "${key}=${value}" >> "$file"
    fi
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
echo -e "      SOW-to-Jira Portable Engine"
echo -e "${NC}"

# 2. Setup Folders
SOW_HOME="$HOME/.sow_to_jira"
mkdir -p "$SOW_HOME/config/user"
mkdir -p "$SOW_HOME/config/admin"
mkdir -p "$SOW_HOME/data"
mkdir -p "$SOW_HOME/data/argus_storage"

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
    else
        # For Linux, configure systemd override for OLLAMA_HOST
        if [ -d "/etc/systemd/system" ]; then
            sudo mkdir -p /etc/systemd/system/ollama.service.d
            echo -e "[Service]\nEnvironment=\"OLLAMA_HOST=0.0.0.0\"" | sudo tee /etc/systemd/system/ollama.service.d/environment.conf > /dev/null
            sudo systemctl daemon-reload
            sudo systemctl restart ollama
        fi
    fi

    # Physical Connectivity Check
    echo -e "${BLUE}[INFO] Verifying Ollama network availability...${NC}"
    until curl -s http://localhost:11434/api/tags > /dev/null; do
        echo -e "${YELLOW}[!] Ollama is not responding on port 11434.${NC}"
        echo -e "${YELLOW}    If you just installed it or updated OLLAMA_HOST, you MUST:${NC}"
        echo -e "${YELLOW}    1. Find the Ollama icon in your menu bar/tray.${NC}"
        echo -e "${YELLOW}    2. Click 'Quit Ollama'.${NC}"
        echo -e "${YELLOW}    3. Restart Ollama from your Applications/Start menu.${NC}"
        echo -e "Waiting for Ollama to start... (Press Ctrl+C to skip if you don't plan to use local models)"
        sleep 5
    done
    echo -e "${GREEN}✓ Ollama is reachable!${NC}"
fi

# 3.7 Resolve Docker Host DNS/IP
if [ "$IS_MAC" = true ]; then
    DOCKER_HOST_INTERNAL="host.docker.internal"
else
    # Try to find the bridge IP for Linux
    DOCKER_HOST_INTERNAL=$(ip addr show docker0 2>/dev/null | grep -Po 'inet \K[\d.]+' | head -n 1)
    DOCKER_HOST_INTERNAL=${DOCKER_HOST_INTERNAL:-"host.docker.internal"}
fi

ARGUS_HQ_URL="$(normalize_host_port "$ARGUS_HQ_URL")"

# 3.8 Fetch Latest Version
if [ -f "VERSION" ]; then
    S2J_VERSION=$(cat VERSION | head -n 1 | tr -d '\r\n')
    echo -e "${BLUE}[INFO] Using local version: ${S2J_VERSION}${NC}"
else
    RAW_URL="https://raw.githubusercontent.com/magggiiii/sow_2_jira/main"
    echo -e "${BLUE}[INFO] Fetching latest version info from GitHub...${NC}"
    S2J_VERSION=$(curl -fsSL "$RAW_URL/VERSION" | head -n 1 | tr -d '\r\n')
fi
S2J_VERSION=${S2J_VERSION:-"latest"}
echo -e "${BLUE}[INFO] Target Version: ${S2J_VERSION}${NC}"

# Ensure registry access
echo -e "${BLUE}[INFO] Verifying access to SOW-to-Jira images...${NC}"
# Attempt a manifest check instead of a full pull to verify visibility
if ! docker manifest inspect ghcr.io/magggiiii/sow_2_jira:${S2J_VERSION} &> /dev/null; then
    echo -e "${YELLOW}[!] Note: Could not verify public image visibility for version ${S2J_VERSION}.${NC}"
    echo -e "If the next step fails, please ensure the project registry at https://ghcr.io/magggiiii/sow_2_jira is set to 'Public'."
fi

# 4. Artifact Provisioning
# In a real scenario, these would be downloaded via curl from a central registry.
# For this task, we assume they are copied from the current source or downloaded.
RAW_URL="https://raw.githubusercontent.com/magggiiii/sow_2_jira/main"
echo -e "${BLUE}[INFO] Downloading distribution artifacts...${NC}"

# (Simulated download for now, using local files if available)
# Ensure we don't have lingering Docker-created directories instead of files
rm -rf "$SOW_HOME/docker-compose.user.yml" "$SOW_HOME/config/user/tempo.yaml" "$SOW_HOME/config/user/argus-collector-edge.yaml" 2>/dev/null || true

if [ -f "infra/user/docker-compose.user.yml" ]; then
    cp infra/user/docker-compose.user.yml "$SOW_HOME/docker-compose.user.yml"
    cp config/user/tempo.yaml "$SOW_HOME/config/user/tempo.yaml"
    cp config/user/argus-collector-edge.yaml "$SOW_HOME/config/user/argus-collector-edge.yaml"
else
    echo "  -> Fetching docker-compose.user.yml..."
    curl -# -fL "$RAW_URL/infra/user/docker-compose.user.yml" -o "$SOW_HOME/docker-compose.user.yml"
    echo "  -> Fetching tempo.yaml..."
    curl -# -fL "$RAW_URL/config/user/tempo.yaml" -o "$SOW_HOME/config/user/tempo.yaml"
    echo "  -> Fetching argus-collector-edge.yaml..."
    curl -# -fL "$RAW_URL/config/user/argus-collector-edge.yaml" -o "$SOW_HOME/config/user/argus-collector-edge.yaml"
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

# Networking
DOCKER_HOST_INTERNAL=$DOCKER_HOST_INTERNAL
S2J_VERSION=$S2J_VERSION

SOW_DATA_DIR=data
EOF
fi

# Always refresh upgrade-sensitive runtime values on reinstall/update.
upsert_env_var "$GLOBAL_ENV" "DOCKER_HOST_INTERNAL" "$DOCKER_HOST_INTERNAL"
upsert_env_var "$GLOBAL_ENV" "S2J_VERSION" "$S2J_VERSION"
upsert_env_var "$GLOBAL_ENV" "ARGUS_HQ_URL" "$ARGUS_HQ_URL"

# 6. Shortcut Creation (Support for uninstall/update)
cat <<EOF > "$SOW_HOME/s2j.sh"
#!/bin/bash
SOW_HOME="\$HOME/.sow_to_jira"
case "\$1" in
    "uninstall")
        echo "⚠️  WARNING: This will delete ALL data, logs, and API configurations."
        read -p "Are you sure you want to completely remove SOW-to-Jira? [y/N] " confirm
        if [[ \$confirm == [yY] || \$confirm == [yY][eE][sS] ]]; then
            echo "Stopping containers..."
            cd "\$SOW_HOME" && docker compose -f docker-compose.user.yml down -v 2>/dev/null
            echo "Removing files..."
            rm -rf "\$SOW_HOME"
            echo "Removing shortcut from profile..."
            # Note: Removal from profile requires manual restart or sed
            sed -i.bak '/# SOW-to-Jira/,+1d' "\$HOME/.zshrc" "\$HOME/.bashrc" 2>/dev/null
            echo "✅ SOW-to-Jira has been completely removed."
        else
            echo "Uninstall cancelled."
        fi
        ;;
    ""|"up"|"start")
        cd "\$SOW_HOME" && docker compose -f docker-compose.user.yml up -d && $OPEN_CMD http://localhost:8000
        ;;
    "down"|"stop")
        cd "\$SOW_HOME" && docker compose -f docker-compose.user.yml down
        ;;
    "logs")
        docker logs -f s2j-user-app
        ;;
    *)
        echo "\"\$1\" unknown command."
        ;;
esac
EOF
chmod +x "$SOW_HOME/s2j.sh"

LAUNCH_CMD="alias s2j='\$HOME/.sow_to_jira/s2j.sh'"

# 7. Admin Shortcut Creation (Developer only)
# Only create s2j-admin if the admin compose file exists locally during install
if [ -f "infra/admin/docker-compose.admin.yml" ]; then
    cp infra/admin/docker-compose.admin.yml "$SOW_HOME/docker-compose.admin.yml"
    cp config/admin/bifrost.admin.yaml "$SOW_HOME/config/admin/bifrost.admin.yaml"
    cp config/admin/prometheus.admin.yml "$SOW_HOME/config/admin/prometheus.admin.yml"
    cp config/admin/argus-collector-admin.yaml "$SOW_HOME/config/admin/argus-collector-admin.yaml"
    cp config/admin/argus-dashboard.json "$SOW_HOME/config/admin/argus-dashboard.json"
    cp config/user/tempo.yaml "$SOW_HOME/config/admin/tempo.yaml" # Use user tempo if needed



    cat <<EOF > "$SOW_HOME/s2j-admin.sh"
#!/bin/bash
SOW_HOME="\$HOME/.sow_to_jira"
case "\$1" in
    "uninstall")
        echo "⚠️  WARNING: This will delete the Argus Admin HQ stack."
        read -p "Are you sure? [y/N] " confirm
        if [[ \$confirm == [yY] || \$confirm == [yY][eE][sS] ]]; then
            echo "Stopping Admin containers..."
            cd "\$SOW_HOME" && docker compose -f docker-compose.admin.yml down -v 2>/dev/null
            echo "✅ Admin stack stopped."
        else
            echo "Cancelled."
        fi
        ;;
    ""|"up"|"start")
        cd "\$SOW_HOME" && docker compose -f docker-compose.admin.yml up -d
        ;;
    "down"|"stop")
        cd "\$SOW_HOME" && docker compose -f docker-compose.admin.yml down
        ;;
    "logs")
        cd "\$SOW_HOME" && docker compose -f docker-compose.admin.yml logs -f
        ;;
    *)
        echo "\"\$1\" unknown command."
        ;;
esac
EOF
    chmod +x "$SOW_HOME/s2j-admin.sh"
    ADMIN_CMD="alias s2j-admin='\$HOME/.sow_to_jira/s2j-admin.sh'"
fi

touch "$SHELL_RC"
# Remove old aliases if they exist
sed -i.bak '/alias s2j=/d' "$SHELL_RC" 2>/dev/null || true
sed -i.bak '/alias s2j-admin=/d' "$SHELL_RC" 2>/dev/null || true

echo -e "\n# SOW-to-Jira\n$LAUNCH_CMD" >> "$SHELL_RC"
if [ -n "$ADMIN_CMD" ]; then
    echo "$ADMIN_CMD" >> "$SHELL_RC"
fi

echo -e "\n${GREEN}Installation Complete!${NC}"
echo -e "Restart your terminal or run 'source $SHELL_RC' to enable the '${BLUE}s2j${NC}' command."
echo -e "Try it now: ${BLUE}s2j${NC}"

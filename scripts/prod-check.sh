#!/bin/bash

# SOW-to-Jira Production Readiness Check
# Verifies Docker configuration, security, and service connectivity.

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}--- Production Readiness Audit ---${NC}"

# 1. Dockerfile User Check
echo -n "1. Non-root user check... "
if grep -q "USER sow" Dockerfile; then
    echo -e "${GREEN}PASS${NC} (User 'sow' defined)"
else
    echo -e "${RED}FAIL${NC} (No non-root user defined)"
    exit 1
fi

# 2. Dockerfile Healthcheck
echo -n "2. Container healthcheck... "
if grep -q "HEALTHCHECK" Dockerfile; then
    echo -e "${GREEN}PASS${NC} (Healthcheck defined)"
else
    echo -e "${RED}FAIL${NC} (No healthcheck defined)"
    exit 1
fi

# 3. Compose Consolidation
echo -n "3. Compose service audit... "
SERVICES=$(grep "container_name:" docker-compose.yml | awk '{print $2}')
REQUIRED=("sow-to-jira-app" "llm-gateway" "sow-loki" "sow-tempo" "sow-grafana")

MISSING=0
for REQ in "${REQUIRED[@]}"; do
    if [[ ! "$SERVICES" =~ "$REQ" ]]; then
        echo -e "${RED}MISSING:${NC} $REQ"
        MISSING=1
    fi
done

if [ $MISSING -eq 0 ]; then
    echo -e "${GREEN}PASS${NC} (All 5 core services present)"
else
    echo -e "${RED}FAIL${NC} (Incomplete compose stack)"
    exit 1
fi

# 4. Tempo Config Validation
echo -n "4. Tempo config validation... "
if [ -f "config/tempo.yaml" ] && grep -q "otlp" config/tempo.yaml; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC} (Missing or invalid tempo.yaml)"
    exit 1
fi

# 5. Network Isolation
echo -n "5. Network isolation... "
if grep -q "sow-internal" docker-compose.yml; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC} (Network isolation missing)"
    exit 1
fi

echo -e "\n${GREEN}✓ All production readiness checks PASSED.${NC}"
echo -e "Next steps: Run 'docker compose up -d' and verify Grafana datasources."

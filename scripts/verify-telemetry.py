import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent))

from pipeline.observability import logger, resolve_observability_endpoint, sync_telemetry
from pipeline.telemetry import TelemetryEmitter

def main():
    print("--- Observability Configuration Verification ---")
    
    gateway_url = os.environ.get("BIFROST_GATEWAY_URL", "UNSET (default: http://localhost:8080)")
    print(f"BIFROST_GATEWAY_URL: {gateway_url}")
    
    token = os.environ.get("BIFROST_BACKBONE_TOKEN", "UNSET")
    print(f"BIFROST_BACKBONE_TOKEN: {'[SET]' if token != 'UNSET' else 'UNSET'}")
    
    resolved = resolve_observability_endpoint()
    print(f"Resolved Endpoint: {resolved}")
    
    print("\n1. Testing Loguru -> Loki mapping...")
    logger.info("Test log for observability verification", agent="verify-script", run_id="test-run-123")
    
    print("\n2. Testing Telemetry Emitter...")
    emitter = TelemetryEmitter()
    emitter.emit("verification.ping", {"status": "ok", "run_id": "test-run-123"})
    
    print("\n3. Testing Buffer Sync...")
    sync_telemetry()
    
    print("\nVerification steps sent. Check your Grafana dashboard for 'verification.ping' and logs from 'verify-script'.")

if __name__ == "__main__":
    main()

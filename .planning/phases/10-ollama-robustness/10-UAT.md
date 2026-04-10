# Phase 10: User Acceptance Testing (UAT)

## Goal
Automate Ollama setup on host machines and use exponential backoff for all API calls to ensure extreme reliability under load.

## Success Criteria
1. `install.sh` detects and configures Ollama's host binding automatically.
2. `get_provider_models` uses `tenacity` retry logic to recover from connection blips.
3. `_execute_call` correctly retries extraction steps for any provider error.

## Test Cases

### Test 1: Ollama Automation in Installer
**Status**: NOT TESTED
- **Action**: Run `curl -fsSL "https://raw.githubusercontent.com/magggiiii/sow_2_jira/main/install.sh" | bash` on a machine where Ollama is either not installed or not configured with `OLLAMA_HOST=0.0.0.0`.
- **Expected Result**: Script detects the missing configuration/installation, performs the setup, and provides clear instructions to restart the Ollama app.

### Test 2: Model Discovery Retry Logic
**Status**: NOT TESTED
- **Action**: In the Web UI, attempt to select a provider (e.g., OpenAI) to fetch its models, while simulating a brief network interruption.
- **Expected Result**: The request shouldn't fail instantly. It should retry up to 3 times (with exponential backoff) before finally failing or succeeding if the network is restored.

### Test 3: LLM Extraction Retry Logic
**Status**: NOT TESTED
- **Action**: Run a SOW extraction. Interfere with the network connection during an LLM call.
- **Expected Result**: The terminal output logs `› LLM attempt X failed: ...` and automatically retries, eventually recovering or failing gracefully without crashing the main orchestrator loop.

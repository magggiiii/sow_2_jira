# Plan Summary - 01-01 Provider Consistency

## Objective
Centralize settings logic and model formatting to eliminate duplicated Fernet encryption logic and ensure consistent model string formatting across the system.

## Tasks Completed
- [x] **Task 0: Add pytest to dependencies**
  - Added `pytest>=8.0.0` to `requirements.txt`.
- [x] **Task 1: Create centralized SettingsManager**
  - Implemented `SettingsManager` in `config/settings.py`.
  - Moved Fernet key derivation, loading, encryption, and decryption logic from `ui/server.py`.
  - Implemented `load()` and `save()` with fast-fail logic on corrupted JSON.
- [x] **Task 2: Consolidate Registry and Formatting Logic**
  - Moved `PROVIDER_REGISTRY`, `resolve_provider_base`, and `build_litellm_model` to `config/settings.py`.
- [x] **Task 3: Add unit tests for settings**
  - Created `test_settings.py` with unit tests for `SettingsManager` and formatting functions.
  - Verified tests pass with `pytest`.

## Verification Results
- `python3.11 -m py_compile config/settings.py`: SUCCESS
- `./venv/bin/python -m pytest test_settings.py`: SUCCESS (4 passed)

## Changes
- `requirements.txt`: Added `pytest>=8.0.0`.
- `config/settings.py`: New module for settings and encryption management.
- `test_settings.py`: New unit tests.

## Decisions & Observations
- Pre-existing dependency conflict in `requirements.txt` between `openai` and `litellm` was noted but not modified to avoid scope creep, as it requires wider consideration.
- `SettingsManager` now handles `SOW_FERNET_KEY` environment variable and `data/.keyfile` fallback consistently.
- Malformed JSON in `settings.json` now correctly raises `RuntimeError` instead of silent failure.

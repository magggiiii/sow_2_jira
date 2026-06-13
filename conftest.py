# conftest.py
#
# Root conftest so the project root is on sys.path for test collection
# regardless of how pytest is launched (the `venv/bin/pytest` console
# script vs. `python -m pytest`). Tests import first-party packages by
# their top-level names (e.g. `config`, `models`, `core`), which requires
# the repo root to be importable. This is additive and does not change any
# runtime behavior.

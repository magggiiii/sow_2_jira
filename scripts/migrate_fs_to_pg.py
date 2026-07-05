"""One-shot migrator: filesystem session store -> DB (via the repository seam).

STREAM C. Moves the local-first, on-disk state produced by the running app into
the relational store behind the ``core.ports`` repository Protocols, for the
hosted/multi-tenant pivot:

    data/sessions/<run_id>/pipeline_output.json   ->  runs + tasks rows
    data/sessions/<run_id>/metadata.json          ->  run filename/legacy id
    data/settings.json (encrypted secrets)        ->  user_credentials rows

Design (mirrors the rest of the elevation):

* **Injected repos, no I/O at import.** :func:`migrate` takes the three repos
  (``run_repo`` / ``task_repo`` / ``credential_repo``) so tests can pass in-memory
  FAKES. Importing this module opens NO database connection and touches no
  network — the real DB wiring lives in ``__main__`` and only runs when the
  script is executed directly (that path is walled / untested offline).

* **Task.id parity + app-faithful derived fields.** Each ``ManagedTask`` is
  re-hydrated from the checkpoint and handed to ``task_repo.add`` unchanged, so
  ``Task.id == ManagedTask.id`` (never regenerated). The run's ``task_count`` and
  ``coverage_pct`` are reproduced EXACTLY the way the orchestrator computes them
  (``len(tasks)`` and ``coverage_report["coverage_pct"]``).

* **Never persist plaintext.** ``settings.json`` secrets (LLM ``api_key`` and
  ``jira_api_token``) are decrypted from their on-disk form and handed to the
  credential repo, which encrypts them at rest under ``APP_ENC_KEY`` (see
  ``config.crypto`` / ``integrations.repositories.CredentialRepository``). The
  plaintext never reaches the persisted row.

The migrator is async so it can await the concrete async repositories in
``integrations/repositories.py`` (whose methods are
``create(user_id, run_id, data)`` / ``add(user_id, run_id, task)`` /
``set(user_id, key, value)``). Fakes in tests satisfy the same async surface.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

from models.schemas import ManagedTask

__all__ = ["migrate", "load_run_records", "extract_credentials"]


# ── fs readers (pure, no DB) ──────────────────────────────────────────────────


def _read_json(path: Path) -> Optional[dict]:
    """Load a JSON object from ``path``; return None if missing/empty/corrupt."""
    if not path.exists():
        return None
    try:
        if path.stat().st_size == 0:
            return None
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def load_run_records(fs_root: Path) -> list[dict]:
    """Enumerate ``<fs_root>/sessions/<run_id>/`` and build per-run records.

    Returns a list of dicts, one per session with a readable
    ``pipeline_output.json``:

        {
            "run_id": str,          # the session dir name (the run id)
            "run_data": dict,       # config + coverage_pct + task_count + meta
            "tasks": list[ManagedTask],
        }

    ``task_count`` and ``coverage_pct`` are derived the SAME way the app does
    (``len(tasks)`` / ``coverage_report["coverage_pct"]``). Pure: no DB, no
    network — only reads files under ``fs_root``.
    """
    fs_root = Path(fs_root)
    sessions_dir = fs_root / "sessions"
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        return []

    records: list[dict] = []
    for session_dir in sorted(sessions_dir.iterdir()):
        if not session_dir.is_dir():
            continue

        output = _read_json(session_dir / "pipeline_output.json")
        if output is None:
            # A session with no (readable) pipeline output has nothing to migrate.
            continue

        run_id = output.get("run_id") or session_dir.name
        raw_tasks = output.get("tasks") or []
        tasks = [ManagedTask.model_validate(t) for t in raw_tasks]

        coverage_report = output.get("coverage_report") or {}
        coverage_pct = coverage_report.get("coverage_pct")

        meta = _read_json(session_dir / "metadata.json") or {}

        run_data = {
            "run_id": run_id,
            "legacy_run_id": run_id,
            "filename": meta.get("filename", output.get("config", {}).get("sow_pdf_path", "")),
            "status": "completed",
            "config": output.get("config") or {},
            "coverage_report": coverage_report,
            "coverage_pct": coverage_pct,
            # task_count reproduced the same way the orchestrator does: len(tasks).
            "task_count": len(tasks),
        }
        if "health" in output:
            run_data["health"] = output["health"]

        records.append({"run_id": run_id, "run_data": run_data, "tasks": tasks})

    return records


# ── settings -> credential extraction (pure, no DB) ───────────────────────────


def extract_credentials(
    settings: dict,
    *,
    decrypt_secret: Callable[[str], str],
) -> list[tuple[str, dict]]:
    """Turn a ``settings.json`` blob into ``(key, value)`` credential payloads.

    Produces at most two entries: an ``"llm"`` credential (provider + model +
    base_url in ``config``; ``api_key`` decrypted into ``secret``) and a
    ``"jira"`` credential (server url in ``config``; token decrypted into
    ``secret``). ``decrypt_secret`` turns the on-disk ciphertext back into
    plaintext; the caller (the credential repo) re-encrypts it under
    ``APP_ENC_KEY``. Secrets are NEVER put in ``config``. A credential with no
    usable secret is still emitted with its non-secret config (no ``secret``
    key), so provider/model settings migrate even without an api key.
    """
    out: list[tuple[str, dict]] = []
    if not settings:
        return out

    # ── LLM credential ────────────────────────────────────────────────────────
    provider = settings.get("provider")
    providers = settings.get("providers") or {}
    provider_settings = providers.get(provider, {}) if provider else {}

    llm_config = {
        k: provider_settings.get(k)
        for k in ("model", "base_url", "azure_deployment_name", "azure_api_version")
        if provider_settings.get(k)
    }
    llm_value: dict[str, Any] = {
        "kind": "llm",
        "provider": provider,
        "config": llm_config,
    }
    api_key_enc = provider_settings.get("api_key")
    if api_key_enc:
        try:
            llm_value["secret"] = decrypt_secret(api_key_enc)
        except Exception:
            # A secret we can't decrypt is dropped (never stored as plaintext or
            # as unusable ciphertext) — config still migrates.
            pass
    # Only emit an LLM credential if there's something to migrate.
    if provider or llm_config or "secret" in llm_value:
        out.append(("llm", llm_value))

    # ── Jira credential ─────────────────────────────────────────────────────────
    jira_server = settings.get("jira_server_url")
    jira_token_enc = settings.get("jira_api_token")
    if jira_server or jira_token_enc:
        jira_value: dict[str, Any] = {
            "kind": "jira",
            "provider": "atlassian",
            "config": {},
        }
        if jira_server:
            jira_value["config"]["jira_server"] = jira_server
        if jira_token_enc:
            try:
                jira_value["secret"] = decrypt_secret(jira_token_enc)
            except Exception:
                pass
        out.append(("jira", jira_value))

    return out


# ── the migrator ──────────────────────────────────────────────────────────────


def _default_settings_decryptor(fs_root: Path) -> Callable[[str], str]:
    """Build a decryptor over the on-disk settings ciphertext.

    Uses ``config.settings.SettingsManager`` (its own keyfile/SOW_FERNET_KEY
    Fernet) to decrypt the legacy ciphertext, so the plaintext can be re-encrypted
    under ``APP_ENC_KEY`` by the credential repo. Imported lazily so a caller that
    injects its own ``decrypt_secret`` (e.g. offline tests) never constructs a
    SettingsManager or reads a ``.keyfile``.
    """
    from config.settings import SettingsManager

    manager = SettingsManager(data_dir=str(fs_root))
    return manager.decrypt_secret


async def migrate(
    fs_root,
    run_repo,
    task_repo,
    credential_repo,
    *,
    user_id: str,
    decrypt_secret: Optional[Callable[[str], str]] = None,
) -> dict:
    """Migrate the filesystem session store at ``fs_root`` into the DB repos.

    Enumerates ``<fs_root>/sessions/<run_id>/``, writes one run + its N tasks
    through ``run_repo``/``task_repo`` (all owned by ``user_id``), and re-encrypts
    ``<fs_root>/settings.json`` secrets into ``credential_repo``. Returns a small
    summary dict (``{"runs": int, "tasks": int, "credentials": int}``).

    ``decrypt_secret`` decrypts the on-disk settings ciphertext; when omitted it
    defaults to a ``SettingsManager``-backed decryptor over ``fs_root`` (only
    constructed if there's a settings file to migrate). The repos are injected so
    tests pass in-memory fakes — this function opens no connection itself.
    """
    fs_root = Path(fs_root)
    summary = {"runs": 0, "tasks": 0, "credentials": 0}

    # ── runs + tasks ──────────────────────────────────────────────────────────
    for record in load_run_records(fs_root):
        run_id = record["run_id"]
        await run_repo.create(user_id, run_id, record["run_data"])
        summary["runs"] += 1
        for task in record["tasks"]:
            await task_repo.add(user_id, run_id, task)
            summary["tasks"] += 1

    # ── credentials (settings.json) ────────────────────────────────────────────
    settings = _read_json(fs_root / "settings.json")
    if settings:
        decryptor = decrypt_secret or _default_settings_decryptor(fs_root)
        for key, value in extract_credentials(settings, decrypt_secret=decryptor):
            await credential_repo.set(user_id, key, value)
            summary["credentials"] += 1

    return summary


# ── real-DB wiring (walled: runs only via `python -m scripts.migrate_fs_to_pg`) ─


def _main() -> int:  # pragma: no cover - real DB path, untested offline
    """Wire real repos from ``DATABASE_URL`` and run the migration.

    All DB/network work is confined here so it never happens at import time. This
    path is intentionally NOT exercised by the offline tests.
    """
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Migrate the filesystem session store into Postgres."
    )
    parser.add_argument(
        "--fs-root", default="data", help="Filesystem root (default: data)"
    )
    parser.add_argument(
        "--user-id",
        required=True,
        help="Owning user id (tenant) for every migrated row.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set; cannot migrate to Postgres.")

    # Deferred imports: real DB engine/adapters are only pulled in on this path.
    from integrations.repositories import (
        CredentialRepository,
        RunRepository,
        TaskRepository,
    )
    from pipeline.db import get_sessionmaker, make_engine

    async def _run() -> dict:
        engine = make_engine(database_url)
        try:
            sm = get_sessionmaker(engine)
            summary = await migrate(
                args.fs_root,
                RunRepository(sm),
                TaskRepository(sm),
                CredentialRepository(sm),
                user_id=args.user_id,
            )
        finally:
            await engine.dispose()
        return summary

    summary = asyncio.run(_run())
    print(
        f"Migrated {summary['runs']} run(s), {summary['tasks']} task(s), "
        f"{summary['credentials']} credential(s)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(_main())

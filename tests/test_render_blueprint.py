"""Structural checks on the render.yaml Blueprint (WAVE 7 / SC-DEPLOY gate B1).

The hosted topology is four service/cron entries (web + worker + keyvalue +
cron) plus one database, wired through a shared env var group. These tests
assert the topology is complete and internally consistent without needing the
Render CLI or network access.
"""

from pathlib import Path

import yaml

RENDER_YAML = Path(__file__).resolve().parents[1] / "render.yaml"


def _load():
    with RENDER_YAML.open() as fh:
        return yaml.safe_load(fh)


def test_render_yaml_parses():
    doc = _load()
    assert isinstance(doc, dict)
    assert "services" in doc


def test_four_service_and_cron_entries():
    doc = _load()
    services = doc.get("services", [])
    types = sorted(s.get("type") for s in services)
    # Complete 4-service topology: web, worker, keyvalue, cron.
    assert types == ["cron", "keyvalue", "web", "worker"], types
    assert len(services) == 4


def test_worker_service_declared():
    doc = _load()
    worker = next(s for s in doc["services"] if s.get("type") == "worker")
    assert worker["runtime"] == "docker"
    assert worker["dockerfilePath"] == "./Dockerfile"


def test_cron_references_gc_sessions_script():
    doc = _load()
    cron = next(s for s in doc["services"] if s.get("type") == "cron")
    assert "schedule" in cron and cron["schedule"]
    command = cron.get("dockerCommand") or cron.get("startCommand") or ""
    # Must invoke the real scripts/gc_sessions.py entrypoint.
    assert "gc_sessions" in command, command


def test_env_var_groups_defined_and_wired():
    doc = _load()
    groups = doc.get("envVarGroups", [])
    assert groups, "envVarGroups must be defined"
    names = {g["name"] for g in groups}
    # Every non-keyvalue service references the shared group via fromGroup.
    for svc in doc["services"]:
        if svc.get("type") == "keyvalue":
            continue
        refs = {ev.get("fromGroup") for ev in svc.get("envVars", []) if "fromGroup" in ev}
        assert refs, f"{svc['name']} does not wire any envVarGroup"
        assert refs <= names, f"{svc['name']} references unknown group {refs - names}"


def test_pre_deploy_command_present():
    doc = _load()
    web = next(s for s in doc["services"] if s.get("type") == "web")
    assert web.get("preDeployCommand"), "web service must declare a preDeployCommand (DB migrate)"


def test_database_still_declared():
    doc = _load()
    dbs = doc.get("databases", [])
    assert len(dbs) == 1
    assert dbs[0]["name"]

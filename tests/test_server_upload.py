# tests/test_server_upload.py
"""SERVER-B security lane — upload hardening (2.6b).

Applies in BOTH auth modes (these are safe hardening that must not break valid
PDF uploads):

  - path-traversal / unsafe filenames are neutralized to a safe basename (the
    stored file never escapes the upload dir)
  - non-PDF is rejected by BOTH extension AND content sniff → HTTP 400
  - oversize uploads (> SOW_MAX_UPLOAD_MB) → HTTP 413
  - a normal small PDF still succeeds

Uploads are redirected to a tmp dir so nothing real is written.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

import ui.server as srv

_PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


@pytest.fixture
def upload_client(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(srv, "UPLOAD_DIR", upload_dir)
    # Ensure default (single-user) mode for these tests.
    srv.app.dependency_overrides.pop(srv.get_session_store, None)
    if hasattr(srv.app.state, "session_store"):
        delattr(srv.app.state, "session_store")
    client = TestClient(srv.app)
    return client, upload_dir


def _post(client, filename, content, content_type="application/pdf"):
    return client.post(
        "/api/upload",
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


def test_normal_pdf_succeeds(upload_client):
    client, upload_dir = upload_client
    resp = _post(client, "spec.pdf", _PDF_BYTES)
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "spec.pdf"
    assert (upload_dir / "spec.pdf").exists()


def test_path_traversal_filename_neutralized(upload_client):
    client, upload_dir = upload_client
    resp = _post(client, "../../../../etc/evil.pdf", _PDF_BYTES)
    assert resp.status_code == 200
    stored = resp.json()["filename"]
    # Stored name must be a safe basename — no path separators, no traversal.
    assert "/" not in stored and "\\" not in stored and ".." not in stored
    assert stored == "evil.pdf"
    # File lands INSIDE the upload dir, not outside it.
    assert (upload_dir / stored).exists()
    # Nothing escaped upward.
    assert not (upload_dir.parent.parent / "etc" / "evil.pdf").exists()


def test_backslash_traversal_filename_neutralized(upload_client):
    client, upload_dir = upload_client
    resp = _post(client, r"..\..\windows\evil.pdf", _PDF_BYTES)
    assert resp.status_code == 200
    stored = resp.json()["filename"]
    assert "/" not in stored and "\\" not in stored and ".." not in stored
    assert (upload_dir / stored).exists()


def test_non_pdf_extension_rejected_400(upload_client):
    client, _ = upload_client
    resp = _post(client, "malware.exe", b"MZ\x90\x00", content_type="application/octet-stream")
    assert resp.status_code == 400


def test_pdf_extension_but_not_pdf_content_rejected_400(upload_client):
    """Content sniff: a .pdf name with non-PDF bytes must be rejected."""
    client, upload_dir = upload_client
    resp = _post(client, "fake.pdf", b"<html>not a pdf</html>")
    assert resp.status_code == 400
    assert not (upload_dir / "fake.pdf").exists()


def test_oversize_upload_rejected_413(upload_client, monkeypatch):
    client, upload_dir = upload_client
    monkeypatch.setattr(srv, "SOW_MAX_UPLOAD_MB", 1)  # 1 MB cap
    big = _PDF_BYTES + b"\x00" * (2 * 1024 * 1024)  # ~2 MB, valid PDF magic
    resp = _post(client, "big.pdf", big)
    assert resp.status_code == 413
    assert not (upload_dir / "big.pdf").exists()


def test_upload_hardening_active_in_hardened_mode(tmp_path, monkeypatch):
    """Upload hardening applies with auth ON too (not mode-gated)."""
    from auth.deps import SESSION_COOKIE_NAME, get_session_store
    from auth.store import FakeSessionStore

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(srv, "UPLOAD_DIR", upload_dir)

    store = FakeSessionStore()
    u = store.add_user(email="u@calibraint.com")
    raw, _ = store.create_session(u.id)
    srv.app.dependency_overrides[get_session_store] = lambda: store
    srv.app.state.session_store = store
    try:
        client = TestClient(srv.app)
        tok = client.get("/api/csrf", cookies={SESSION_COOKIE_NAME: raw}).json()["csrf_token"]
        # non-PDF still rejected in hardened mode
        resp = client.post(
            "/api/upload",
            files={"file": ("x.txt", io.BytesIO(b"nope"), "text/plain")},
            cookies={SESSION_COOKIE_NAME: raw, srv.CSRF_COOKIE_NAME: tok},
            headers={srv.CSRF_HEADER_NAME: tok},
        )
        assert resp.status_code == 400
    finally:
        srv.app.dependency_overrides.pop(get_session_store, None)
        if hasattr(srv.app.state, "session_store"):
            delattr(srv.app.state, "session_store")

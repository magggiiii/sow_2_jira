"""Object storage adapters (WAVE 1 STEP 1.4).

``LocalObjectStore`` is the offline, on-disk adapter that satisfies the
``ObjectStore`` Protocol (core/ports.py). It preserves the current
filesystem-backed behavior during the strangler period; a Cloudflare R2 / S3
adapter will land later behind the same interface, so callers depend only on the
Protocol, never on this concrete class.

Keys are opaque, forward-slash-delimited relative paths, e.g.
``users/{user_id}/runs/{run_id}/{name}``. All keys are validated against path
traversal so a caller (or a malicious upload name) can never write or read
outside the configured base directory.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Union

__all__ = ["LocalObjectStore", "S3ObjectStore", "key_for"]


def _validate_key(key: str) -> str:
    """Return ``key`` if it is a safe relative object key, else raise ValueError.

    Rejects absolute paths, backslashes, empty keys, and any ``..``/``.`` path
    segment — anything that could escape the store's base directory.
    """
    if not isinstance(key, str) or not key:
        raise ValueError("object key must be a non-empty string")
    if "\x00" in key:
        raise ValueError("object key must not contain null bytes")
    if "\\" in key:
        raise ValueError(f"object key must not contain backslashes: {key!r}")
    if key.startswith("/"):
        raise ValueError(f"object key must be relative, not absolute: {key!r}")
    segments = key.split("/")
    if any(seg in ("", "..", ".") for seg in segments):
        raise ValueError(f"object key must not contain empty or '..'/'.' segments: {key!r}")
    return key


def key_for(user_id: str, run_id: str, name: str) -> str:
    """Build a user-scoped object key ``users/{user_id}/runs/{run_id}/{name}``.

    ``name`` is a single path component (no directories); traversal attempts are
    rejected. ``user_id``/``run_id`` are validated too.
    """
    for label, value in (("user_id", user_id), ("run_id", run_id), ("name", name)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be a non-empty string")
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError(f"{label} must be a single safe component: {value!r}")
    return f"users/{user_id}/runs/{run_id}/{name}"


class LocalObjectStore:
    """Filesystem-backed ``ObjectStore``. Stores each key as a file under
    ``base_dir``, creating parent directories as needed."""

    def __init__(self, base_dir: Union[str, Path] = "data/objectstore"):
        self.base_dir = Path(base_dir)

    def _path(self, key: str) -> Path:
        """Validate ``key`` and return its concrete on-disk path, guaranteeing
        the result stays inside ``base_dir``.

        String validation alone is not enough: a symlink that already exists at
        an intermediate path component could redirect a syntactically-valid key
        outside the store. So we resolve the final path (following any existing
        symlinks) and assert it is still within the resolved base directory.
        """
        _validate_key(key)
        base = self.base_dir.resolve()
        target = (self.base_dir / key).resolve()
        if not target.is_relative_to(base):
            raise ValueError(f"object key resolves outside the store base directory: {key!r}")
        return target

    def put(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key``; return the on-disk locator (path str)."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def get(self, key: str) -> bytes:
        """Fetch the bytes stored under ``key``; raise ``KeyError`` if absent."""
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise KeyError(key) from None

    def exists(self, key: str) -> bool:
        """Return True if an object exists at ``key``."""
        return self._path(key).is_file()

    def delete_run_prefix(self, prefix: str) -> int:
        """Delete every object under ``prefix`` (a directory-like key prefix).

        Returns the number of objects (files) removed; 0 if the prefix is absent.
        """
        target = self._path(prefix)
        if not target.exists():
            return 0
        if target.is_file():
            target.unlink()
            return 1
        # Count only real files rmtree will actually remove — do not count
        # through symlinks.
        count = sum(1 for p in target.rglob("*") if p.is_file() and not p.is_symlink())
        shutil.rmtree(target)
        return count


class S3ObjectStore:
    """S3-compatible ``ObjectStore`` (Supabase Storage S3, both envs).

    One boto3 adapter serves the local Supabase stack and the hosted project
    (ENV-CONFIG.md); the composition root selects it for ``APP_ENV=production``.
    Uses **path-style** addressing (required by the Supabase S3 gateway) and a
    ``region_name``. boto3 is imported lazily and the client is built on first
    use, so importing this module (and ``app.container``) never requires boto3
    and construction opens no connection. Object keys use the same traversal-safe
    validation as ``LocalObjectStore``.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret: str,
        region: str,
        bucket: str,
        client: object = None,
    ):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret = secret
        self.region = region
        self.bucket = bucket
        self._client = client

    def _get_client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint or None,
                aws_access_key_id=self.access_key or None,
                aws_secret_access_key=self.secret or None,
                region_name=self.region or None,
                config=Config(s3={"addressing_style": "path"}),
            )
        return self._client

    def put(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key``; return an ``s3://bucket/key`` locator."""
        _validate_key(key)
        self._get_client().put_object(Bucket=self.bucket, Key=key, Body=data)
        return f"s3://{self.bucket}/{key}"

    def get(self, key: str) -> bytes:
        """Fetch the bytes stored under ``key``."""
        _validate_key(key)
        resp = self._get_client().get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read()

    def exists(self, key: str) -> bool:
        """Return True if an object exists at ``key`` (404/NoSuchKey → False)."""
        _validate_key(key)
        from botocore.exceptions import ClientError

        try:
            self._get_client().head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

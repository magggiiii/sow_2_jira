# tests/test_s3_object_store.py
"""WAVE 2 config layer / STEP 1.4 — S3ObjectStore (Supabase S3, both envs).

The production branch of ``app.container.select_object_store``. One boto3 adapter
serves the Supabase Storage S3 endpoint in both local and hosted envs, with
path-style addressing (ENV-CONFIG.md). boto3 is imported lazily and no client is
built at construction; these tests drive it against a mocked client — no network.
"""

from __future__ import annotations

import io
from unittest import mock

import pytest

from core.ports import ObjectStore
from integrations.object_store import S3ObjectStore

_ENDPOINT = "http://127.0.0.1:54321/storage/v1/s3"


def _store(client=None):
    return S3ObjectStore(
        endpoint=_ENDPOINT,
        access_key="ak",
        secret="sk",
        region="local",
        bucket="bkt",
        client=client,
    )


def test_satisfies_objectstore_protocol():
    assert isinstance(_store(client=mock.Mock()), ObjectStore)


def test_no_client_built_at_construction():
    assert _store(client=None)._client is None


def test_rejects_traversal_key():
    with pytest.raises(ValueError):
        _store(client=mock.Mock()).put("../evil.txt", b"x")


def test_put_delegates_to_client_and_returns_locator():
    c = mock.Mock()
    loc = _store(client=c).put("runs/r1/out.json", b"data")
    c.put_object.assert_called_once_with(Bucket="bkt", Key="runs/r1/out.json", Body=b"data")
    assert loc == "s3://bkt/runs/r1/out.json"


def test_get_delegates_to_client():
    c = mock.Mock()
    c.get_object.return_value = {"Body": io.BytesIO(b"hello")}
    assert _store(client=c).get("k") == b"hello"
    c.get_object.assert_called_once_with(Bucket="bkt", Key="k")


def test_exists_true_when_head_succeeds():
    c = mock.Mock()
    c.head_object.return_value = {}
    assert _store(client=c).exists("k") is True


def test_exists_false_on_404():
    from botocore.exceptions import ClientError

    c = mock.Mock()
    c.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    assert _store(client=c).exists("k") is False


def test_select_object_store_production_returns_s3():
    """The config layer's production branch resolves to S3ObjectStore (no client)."""
    from app.container import select_object_store
    from config.settings import Settings

    settings = Settings(
        app_env="production", s3_endpoint=_ENDPOINT, s3_bucket="bkt", s3_region="local"
    )
    store = select_object_store(settings)
    assert isinstance(store, S3ObjectStore)
    assert store._client is None  # still lazy — no boto3 client built


def test_lazy_boto3_client_uses_path_style_and_endpoint():
    fake_client = mock.Mock()
    with mock.patch("boto3.client", return_value=fake_client) as boto_client:
        _store(client=None).put("k", b"x")  # triggers lazy client build
    assert boto_client.call_args.args[0] == "s3"
    kwargs = boto_client.call_args.kwargs
    assert kwargs["endpoint_url"] == _ENDPOINT
    assert kwargs["region_name"] == "local"
    assert kwargs["config"].s3["addressing_style"] == "path"

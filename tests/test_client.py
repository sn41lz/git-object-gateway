import hashlib
import sqlite3
from contextlib import closing

import httpx2 as httpx
import pytest
from conftest import TOKEN

from git_object_gateway.benchmark import tree_identity
from git_object_gateway.cache import BlobCache
from git_object_gateway.client import Client
from git_object_gateway.fixtures import running_gateway
from git_object_gateway.protocol import Entry, GatewayError, Manifest, blob_id, canonical


def test_real_http_snapshot_and_warm_cache(repo, tmp_path):
    root, commit, repository = repo
    cache = BlobCache(tmp_path / "cache")
    with running_gateway(repository, TOKEN) as url, Client(url, TOKEN, cache=cache) as client:
        receipt = client.snapshot(tmp_path / "out", ["app"])
        warm = client.snapshot(tmp_path / "warm", ["app"], commit=commit)
        assert receipt.commit == commit
        assert receipt.files == 5
        assert receipt.requests == 7
        assert warm.requests == 1
        assert warm.cached_files == 5
        assert warm.downloaded_bytes < receipt.downloaded_bytes
        assert (
            tree_identity(root / "app")
            == tree_identity(tmp_path / "out/app")
            == tree_identity(tmp_path / "warm/app")
        )
        assert not (tmp_path / "out/.git").exists()
        with pytest.raises(GatewayError, match="destination_already_exists"):
            client.snapshot(tmp_path / "out", ["app"])
    assert not list(tmp_path.glob(".gog-staging-*"))
    assert (tmp_path / "out/app/run.sh").stat().st_mode & 0o777 == 0o700


def mock_client(handler):
    return Client("https://gateway.example", TOKEN, transport=httpx.MockTransport(handler))


def one_file():
    value = b"valid\x00binary\xff"
    manifest = Manifest(
        "a" * 40, "sha1", ("app",), (Entry("app/a", blob_id(value, "sha1"), len(value), "100644"),)
    )
    return value, manifest


def manifest_response(manifest):
    data = canonical(manifest.payload())
    return httpx.Response(
        200, content=data, headers={"etag": '"' + hashlib.sha256(data).hexdigest() + '"'}
    )


@pytest.mark.parametrize(
    "failure", ["corruption", "wrong_etag", "wrong_format", "missing", "oversized"]
)
def test_failed_blob_leaves_no_destination(tmp_path, failure):
    value, manifest = one_file()

    def handler(request):
        assert request.headers["authorization"] == "Bearer " + TOKEN
        if request.url.path.endswith("manifest"):
            return manifest_response(manifest)
        if failure == "missing":
            return httpx.Response(404)
        data = b"corrupt" if failure == "corruption" else value
        if failure == "oversized":
            data = b"x" * (8 * 1024 * 1024 + 1)
        return httpx.Response(
            200,
            content=data,
            headers={
                "etag": '"wrong"'
                if failure == "wrong_etag"
                else '"' + manifest.entries[0].oid + '"',
                "x-git-object-format": "sha256" if failure == "wrong_format" else "sha1",
            },
        )

    with mock_client(handler) as client, pytest.raises(GatewayError):
        client.snapshot(tmp_path / "output", ["app"], commit=manifest.commit)
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".gog-staging-*"))


@pytest.mark.parametrize("failure", ["different_commit", "etag", "selection", "symlink"])
def test_bad_manifest_rejected_before_writes(tmp_path, failure):
    _, manifest = one_file()

    def handler(request):
        data = manifest.payload()
        if failure == "different_commit":
            data["commit"] = "b" * 40
        elif failure == "selection":
            data["paths"] = ["app/a"]
        elif failure == "symlink":
            data["files"][0]["mode"] = "120000"
        encoded = canonical(data)
        etag = '"wrong"' if failure == "etag" else '"' + hashlib.sha256(encoded).hexdigest() + '"'
        return httpx.Response(200, content=encoded, headers={"etag": etag})

    with mock_client(handler) as client, pytest.raises(GatewayError):
        client.snapshot(tmp_path / "output", ["app"], commit=manifest.commit)
    assert not (tmp_path / "output").exists()


def test_redirect_never_sends_token_to_other_host():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://attacker.example/"})

    with mock_client(handler) as client, pytest.raises(GatewayError, match="http_302"):
        client.resolve()
    assert calls == ["gateway.example"]


def test_connection_error_sanitized():
    def handler(request):
        raise httpx.ConnectError(TOKEN + " https://secret", request=request)

    with mock_client(handler) as client, pytest.raises(GatewayError) as result:
        client.resolve()
    assert str(result.value) == "gateway_connection_failed"


def test_diff_rejects_invalid_numbers_from_gateway():
    base, head = "a" * 40, "b" * 40

    def handler(request):
        payload = (
            '{"base":"' + base + '","head":"' + head + '","changes":[],"score":1e400}'
        ).encode()
        return httpx.Response(200, content=payload)

    with mock_client(handler) as client, pytest.raises(GatewayError, match="invalid_json"):
        client.diff(base, head)


def test_cached_corruption_is_refetched(repo, tmp_path):
    _, commit, repository = repo
    cache = BlobCache(tmp_path / "cache")

    with running_gateway(repository, TOKEN) as url, Client(url, TOKEN, cache=cache) as client:
        first = client.snapshot(tmp_path / "first", ["app"], commit=commit)
        with closing(sqlite3.connect(cache.path)) as db, db:
            db.execute("UPDATE blobs SET data=?", (b"corrupt",))
        second = client.snapshot(tmp_path / "second", ["app"], commit=commit)
        assert second.cached_files == 0
        assert first.materialized_bytes == second.materialized_bytes

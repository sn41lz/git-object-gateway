import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from git_object_gateway.cache import BlobCache
from git_object_gateway.protocol import GatewayError, blob_id


def test_persistent_cache_quota_and_lru(tmp_path):
    cache = BlobCache(tmp_path / "cache", capacity=8)
    for data in (b"aaaa", b"bbbb"):
        cache.put(blob_id(data, "sha1"), "sha1", data)
    assert cache.get(blob_id(b"aaaa", "sha1"), "sha1") == b"aaaa"
    cache.put(blob_id(b"cccc", "sha1"), "sha1", b"cccc")
    assert cache.get(blob_id(b"bbbb", "sha1"), "sha1") is None
    assert cache.stats()["payload_bytes"] == 8
    reopened = BlobCache(tmp_path / "cache", capacity=4)
    assert reopened.stats()["payload_bytes"] <= 4
    assert reopened.path.stat().st_mode & 0o777 == 0o600


def test_corruption_is_evicted_and_bad_put_rejected(tmp_path):
    cache = BlobCache(tmp_path / "cache")
    expected = blob_id(b"safe", "sha256")
    cache.put(expected, "sha256", b"safe")
    with closing(sqlite3.connect(cache.path)) as db, db:
        db.execute("UPDATE blobs SET data=?", (b"evil",))
    assert cache.get(expected, "sha256") is None
    assert cache.stats()["objects"] == 0
    with pytest.raises(GatewayError, match="blob_integrity_failed"):
        cache.put(expected, "sha256", b"evil")


def test_concurrent_cache_access_and_quota(tmp_path):
    directory = tmp_path / "cache"
    first, second = BlobCache(directory, 128), BlobCache(directory, 128)

    def operation(i):
        cache = first if i % 2 else second
        data = str(i).encode() * 4
        expected = blob_id(data, "sha1")
        cache.put(expected, "sha1", data)
        value = cache.get(expected, "sha1")
        assert value in {None, data}  # Concurrent evictions are allowed; corrupt hits are not.

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(operation, range(100)))
    assert first.stats()["payload_bytes"] <= 128


def test_cache_disabled_and_invalid_path(tmp_path):
    cache = BlobCache(tmp_path / "disabled", 0)
    cache.put(blob_id(b"hello", "sha1"), "sha1", b"hello")
    assert cache.stats()["objects"] == 0
    with pytest.raises(GatewayError):
        BlobCache(tmp_path, -1)
    directory = tmp_path / "unsafe"
    directory.mkdir()
    (directory / "objects.sqlite3").symlink_to(tmp_path / "victim")
    with pytest.raises(GatewayError, match="unsafe_cache_path"):
        BlobCache(directory)

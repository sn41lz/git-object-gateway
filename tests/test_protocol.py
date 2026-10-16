import copy
import hashlib

import pytest

from git_object_gateway.client import origin
from git_object_gateway.protocol import (
    Entry,
    GatewayError,
    Manifest,
    blob_id,
    path,
    selections,
    strict_json,
    verify_blob,
)


@pytest.mark.parametrize(
    "name",
    [
        "../secret",
        "/etc/passwd",
        "x/../../y",
        "x//y",
        ".git/config",
        "a/.GIT/config",
        "C:/boot",
        "x\\y",
        "a/../b",
        "x\nq",
        "NUL.txt",
        "a/COM1",
        "a.",
        "a ",
        "e\u0301.txt",
        "a|b",
        "a?b",
        "a\0b",
    ],
)
def test_unsafe_paths(name):
    with pytest.raises(GatewayError):
        path(name)


@pytest.mark.parametrize(
    "name",
    [
        "app",
        "app/main.py",
        "src/日本語.py",
        "docs/guía.md",
        "a b/file",
        "(literal)/-option",
        "emoji/😀.txt",
    ],
)
def test_safe_paths(name):
    assert path(name) == name


def test_selection_canonicalization():
    assert selections(["app/", "app/main.py", "docs", "app"]) == ("app", "docs")
    for values in ([], ["app"] * 33, ["../secret"]):
        with pytest.raises(GatewayError):
            selections(values)


@pytest.mark.parametrize("algorithm", ["sha1", "sha256"])
def test_git_object_hash_has_header(algorithm):
    data = b"binary\x00\xff content"
    expected = hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    assert blob_id(data, algorithm) == expected
    assert verify_blob(data, expected, algorithm) == data
    with pytest.raises(GatewayError, match="blob_integrity_failed"):
        verify_blob(data + b"!", expected, algorithm)


@pytest.mark.parametrize("data", [b'{"a":1,"a":2}', b'{"a":NaN}', b"\xff", b"{", b"[" * 2000])
def test_strict_json(data):
    with pytest.raises(GatewayError, match="invalid_json"):
        strict_json(data)


def payload():
    return Manifest("a" * 40, "sha1", ("app",), (Entry("app/a", "b" * 40, 1, "100644"),)).payload()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(commit="--help"),
        lambda p: p.update(total_bytes=True),
        lambda p: p.update(extra=1),
        lambda p: p.update(files=[]),
        lambda p: p["files"][0].update(size=True),
        lambda p: p["files"][0].update(size=9_000_000),
        lambda p: p["files"][0].update(mode="120000"),
        lambda p: p["files"][0].update(path="other/file"),
        lambda p: p["files"][0].update(path="../escape"),
        lambda p: p.update(paths=["app", "missing"]),
    ],
)
def test_malicious_manifest(mutate):
    data = payload()
    mutate(data)
    with pytest.raises(GatewayError, match="invalid_manifest"):
        Manifest.parse(data)


@pytest.mark.parametrize(
    "names", [("app/a", "app/A"), ("app/a", "app/a/b"), ("app/A/b", "app/a/c"), ("app/a", "app/a")]
)
def test_manifest_aliases_and_collisions(names):
    data = payload()
    data["files"] = [{**copy.copy(data["files"][0]), "path": n} for n in sorted(names)]
    data["total_bytes"] = len(names)
    with pytest.raises(GatewayError):
        Manifest.parse(data)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://host/x",
        "https://host?token=x",
        "ftp://host",
        "https://host:invalid",
        "https://host/#fragment",
    ],
)
def test_reject_unsafe_origins(url):
    with pytest.raises(GatewayError):
        origin(url)


def test_valid_origins():
    assert origin("https://example.com/") == "https://example.com"
    assert origin("http://127.0.0.1:8090") == "http://127.0.0.1:8090"
    assert origin("http://[::1]:8090") == "http://[::1]:8090"

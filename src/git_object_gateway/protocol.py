"""Shared wire validation. Treat both Git trees and HTTP responses as untrusted data."""

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

MAX_BLOB = 8 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 5000
MAX_MANIFEST = 8 * 1024 * 1024
MAX_PATHS = 32


class GatewayError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def oid(value: str, algorithm: str) -> str:
    length = {"sha1": 40, "sha256": 64}.get(algorithm)
    if (
        not isinstance(value, str)
        or length is None
        or not re.fullmatch(f"[0-9a-f]{{{length}}}", value)
    ):
        raise GatewayError("invalid_object_id")
    return value


def blob_id(data: bytes, algorithm: str) -> str:
    if algorithm not in {"sha1", "sha256"}:
        raise GatewayError("invalid_object_format")
    return hashlib.new(
        algorithm, b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def verify_blob(data: bytes, expected: str, algorithm: str) -> bytes:
    oid(expected, algorithm)
    if len(data) > MAX_BLOB or blob_id(data, algorithm) != expected:
        raise GatewayError("blob_integrity_failed", 502)
    return data


def path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise GatewayError("invalid_path")
    parts = value.split("/")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if (
        any(
            p in {"", ".", ".."}
            or p.casefold() == ".git"
            or p.endswith((".", " "))
            or p.split(".")[0].upper() in reserved
            or any(ord(c) < 32 or ord(c) == 127 or c in '\\:<>"|?*' for c in p)
            for p in parts
        )
        or unicodedata.normalize("NFC", value) != value
    ):
        raise GatewayError("unsafe_path", 422)
    return value


def selections(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not values or len(values) > MAX_PATHS:
        raise GatewayError("select_1_to_32_paths")
    result = tuple(sorted(set(path(v.rstrip("/")) for v in values)))
    # Canonicalize overlapping directory selections for stable manifests.
    return tuple(v for v in result if not any(v.startswith(p + "/") for p in result if p != v))


@dataclass(frozen=True)
class Entry:
    path: str
    oid: str
    size: int
    mode: str


@dataclass(frozen=True)
class Manifest:
    commit: str
    object_format: str
    paths: tuple[str, ...]
    entries: tuple[Entry, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "commit": self.commit,
            "object_format": self.object_format,
            "paths": list(self.paths),
            "files": [asdict(e) for e in self.entries],
            "total_bytes": sum(e.size for e in self.entries),
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(self.payload())).hexdigest()

    @classmethod
    def parse(cls, data: Any) -> "Manifest":
        try:
            if (
                set(data) != {"version", "commit", "object_format", "paths", "files", "total_bytes"}
                or type(data["version"]) is not int
                or data["version"] != 1
            ):
                raise ValueError
            algorithm = data["object_format"]
            commit = oid(data["commit"], algorithm)
            if not isinstance(data["paths"], list) or not all(
                isinstance(p, str) for p in data["paths"]
            ):
                raise ValueError
            chosen = selections(data["paths"])
            if (
                list(chosen) != data["paths"]
                or not isinstance(data["files"], list)
                or not 0 < len(data["files"]) <= MAX_FILES
            ):
                raise ValueError
            entries = []
            for raw in data["files"]:
                if set(raw) != {"path", "oid", "size", "mode"} or type(raw["size"]) is not int:
                    raise ValueError
                name = path(raw["path"])
                if not any(name == p or name.startswith(p + "/") for p in chosen):
                    raise ValueError
                if raw["mode"] not in {"100644", "100755"} or not 0 <= raw["size"] <= MAX_BLOB:
                    raise ValueError
                entries.append(Entry(name, oid(raw["oid"], algorithm), raw["size"], raw["mode"]))
            names = [e.path for e in entries]
            folded: set[str] = set()
            directories: dict[str, str] = {}
            # Reject aliases on case-insensitive filesystems and file/directory collisions.
            files = {n.casefold() for n in names}
            for n in names:
                key = n.casefold()
                components = n.split("/")
                for depth in range(1, len(components)):
                    directory = "/".join(components[:depth])
                    alias = directory.casefold()
                    if alias in directories and directories[alias] != directory:
                        raise ValueError
                    directories[alias] = directory
                if key in folded or any(
                    "/".join(key.split("/")[:i]) in files for i in range(1, len(key.split("/")))
                ):
                    raise ValueError
                folded.add(key)
            if names != sorted(names) or type(data["total_bytes"]) is not int:
                raise ValueError
            total = sum(e.size for e in entries)
            if total != data["total_bytes"] or total > MAX_TOTAL:
                raise ValueError
            if any(
                not any(e.path == p or e.path.startswith(p + "/") for e in entries) for p in chosen
            ):
                raise ValueError
            return cls(commit, algorithm, chosen, tuple(entries))
        except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, GatewayError) as exc:
            raise GatewayError("invalid_manifest", 502) from exc


def strict_json(data: bytes) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(
            data,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise GatewayError("invalid_json", 502) from exc

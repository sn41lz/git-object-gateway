"""Bounded, shell-free access to an operator-owned, complete Git repository."""

import os
import selectors
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .protocol import (
    MAX_BLOB,
    MAX_FILES,
    MAX_MANIFEST,
    Entry,
    GatewayError,
    Manifest,
    oid,
    path,
    selections,
    verify_blob,
)


class Repository:
    def __init__(
        self,
        root: Path,
        refs: tuple[str, ...] = ("refs/heads/main",),
        *,
        timeout: float = 10,
        cache_bytes: int = 64 * 1024 * 1024,
    ):
        self.root = root.resolve(strict=True)
        self.git = shutil.which("git")
        if self.git is None:
            raise GatewayError("git_not_installed", 503)
        if not 0 < timeout <= 120 or not 0 <= cache_bytes <= 1024 * 1024 * 1024:
            raise GatewayError("invalid_repository_limits")
        self.timeout, self.cache_bytes = timeout, cache_bytes
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "LC_ALL": "C",
            }
        )
        self._slots = threading.BoundedSemaphore(8)
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._size = self.hits = self.misses = 0
        self._stripes = [threading.Lock() for _ in range(32)]
        self.algorithm = self.run("rev-parse", "--show-object-format", limit=128).decode().strip()
        if self.algorithm not in {"sha1", "sha256"}:
            raise GatewayError("unsupported_object_format")
        if not refs or len(refs) > 16 or len(set(refs)) != len(refs):
            raise GatewayError("invalid_allowed_refs")
        for ref in refs:
            if not ref.startswith(("refs/heads/", "refs/tags/")) or len(ref) > 256:
                raise GatewayError("invalid_allowed_ref")
            self.run("check-ref-format", ref, limit=1024)
            self._tip(ref)
        self.refs = refs
        # Partial clones could initiate lazy network fetches; reject rather than rely on env alone.
        partial = self.run(
            "config", "--get", "extensions.partialClone", ok_codes=(0, 1), limit=1024
        )
        promisors = self.run(
            "config", "--get-regexp", r"^remote\..*\.promisor$", ok_codes=(0, 1), limit=4096
        )
        if partial or promisors:
            raise GatewayError("partial_repositories_not_supported")

    def run(self, *args: str, limit: int = MAX_MANIFEST, ok_codes: tuple[int, ...] = (0,)) -> bytes:
        if not self._slots.acquire(timeout=0.1):
            raise GatewayError("git_busy", 503)
        try:
            assert self.git is not None
            command = [
                self.git,
                "--no-pager",
                "--no-optional-locks",
                "-c",
                "protocol.allow=never",
                "-c",
                "core.fsmonitor=false",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-C",
                str(self.root),
                *args,
            ]
            with subprocess.Popen(
                command,
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ) as process:
                assert process.stdout is not None
                try:
                    data = bytearray()
                    deadline = time.monotonic() + self.timeout
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise GatewayError("git_timeout", 504)
                            if not selector.select(remaining):
                                raise GatewayError("git_timeout", 504)
                            chunk = os.read(
                                process.stdout.fileno(), min(65536, limit + 1 - len(data))
                            )
                            if not chunk:
                                break
                            data.extend(chunk)
                            if len(data) > limit:
                                raise GatewayError("git_output_limit", 413)
                    code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
                    if code not in ok_codes:
                        raise GatewayError("git_object_unavailable", 404)
                    return bytes(data)
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
        except subprocess.TimeoutExpired as exc:
            raise GatewayError("git_timeout", 504) from exc
        finally:
            self._slots.release()

    def _tip(self, ref: str) -> str:
        return oid(
            self.run("rev-parse", "--verify", ref + "^{commit}", limit=128).decode().strip(),
            self.algorithm,
        )

    def resolve(self, ref: str) -> dict[str, str]:
        normalized = ref if ref.startswith("refs/") else "refs/heads/" + ref
        if normalized not in self.refs:
            raise GatewayError("ref_not_allowed", 404)
        commit = self._tip(normalized)
        return {"commit": commit, "object_format": self.algorithm, "ref": normalized}

    def authorize(self, commit: str) -> None:
        oid(commit, self.algorithm)
        # No reachability cache: a force-push is reflected at the next authorization check.
        for ref in self.refs:
            tip = self._tip(ref)
            if tip == commit:
                return
            try:
                self.run("merge-base", "--is-ancestor", commit, tip, limit=128)
                return
            except GatewayError as exc:
                if exc.code != "git_object_unavailable":
                    raise
        raise GatewayError("commit_not_allowed", 404)

    def _entries(self, commit: str, chosen: tuple[str, ...], *, recursive: bool) -> list[Entry]:
        arguments = ["ls-tree", "--full-tree", "-l", "-z"]
        if recursive:
            arguments.append("-r")
        data = self.run(*arguments, commit, "--", *(f":(literal){p}" for p in chosen))
        result = []
        try:
            for record in data.split(b"\0"):
                if not record:
                    continue
                metadata, raw_name = record.split(b"\t", 1)
                mode, kind, object_id, size = metadata.split()
                name = path(raw_name.decode("utf-8", errors="strict"))
                if mode not in {b"100644", b"100755"} or kind != b"blob":
                    raise GatewayError("unsupported_file_mode", 422)
                n = int(size)
                if n > MAX_BLOB:
                    raise GatewayError("blob_too_large", 413)
                result.append(
                    Entry(name, oid(object_id.decode(), self.algorithm), n, mode.decode())
                )
                if len(result) > MAX_FILES:
                    raise GatewayError("too_many_files", 413)
            return sorted(result, key=lambda entry: entry.path)
        except (ValueError, UnicodeError) as exc:
            raise GatewayError("unsupported_tree_encoding", 422) from exc

    def manifest(self, commit: str, paths: list[str] | tuple[str, ...]) -> Manifest:
        self.authorize(commit)
        chosen = selections(paths)
        entries = self._entries(commit, chosen, recursive=True)
        if any(not any(e.path == p or e.path.startswith(p + "/") for e in entries) for p in chosen):
            raise GatewayError("path_not_found", 404)
        manifest = Manifest(commit, self.algorithm, chosen, tuple(entries))
        # Run exactly the same cross-platform collision and quota checks as the client.
        try:
            Manifest.parse(manifest.payload())
        except GatewayError as exc:
            raise GatewayError("unsupported_tree_or_snapshot_limit", 422) from exc
        return manifest

    def file(self, commit: str, name: str, expected: str) -> tuple[Entry, bytes, bool]:
        self.authorize(commit)
        path(name)
        oid(expected, self.algorithm)
        entries = self._entries(commit, (name,), recursive=False)
        if len(entries) != 1 or entries[0].path != name:
            raise GatewayError("path_not_found", 404)
        entry = entries[0]
        if entry.oid != expected:
            raise GatewayError("object_does_not_match_path", 409)
        # Stripe locks collapse concurrent cache misses without unbounded per-object lock growth.
        with self._stripes[int(expected[:8], 16) % len(self._stripes)]:
            with self._lock:
                value = self._cache.get(expected)
                if value is not None:
                    self._cache.move_to_end(expected)
                    self.hits += 1
                    return entry, value, True
                self.misses += 1
            value = verify_blob(
                self.run("cat-file", "blob", expected, limit=MAX_BLOB), expected, self.algorithm
            )
            if len(value) != entry.size:
                raise GatewayError("git_object_size_mismatch", 502)
            with self._lock:
                if len(value) <= self.cache_bytes:
                    while self._cache and (
                        self._size + len(value) > self.cache_bytes or len(self._cache) >= MAX_FILES
                    ):
                        _, previous = self._cache.popitem(last=False)
                        self._size -= len(previous)
                    self._cache[expected] = value
                    self._size += len(value)
            return entry, value, False

    def diff(self, base: str, head: str) -> dict[str, Any]:
        self.authorize(base)
        self.authorize(head)
        records = self.run(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--no-relative",
            "--name-status",
            "-z",
            base,
            head,
            "--",
        ).split(b"\0")
        records = records[:-1] if records[-1] == b"" else records
        if len(records) > MAX_FILES * 2:
            raise GatewayError("too_many_files", 413)
        changes = []
        try:
            for i in range(0, len(records), 2):
                changes.append(
                    {
                        "status": records[i].decode("ascii"),
                        "path": path(records[i + 1].decode("utf-8")),
                    }
                )
        except (UnicodeError, IndexError) as exc:
            raise GatewayError("unsupported_tree_encoding", 422) from exc
        return {"base": base, "head": head, "changes": changes}

    def cache_stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self.hits,
                "misses": self.misses,
                "bytes": self._size,
                "objects": len(self._cache),
                "capacity_bytes": self.cache_bytes,
            }

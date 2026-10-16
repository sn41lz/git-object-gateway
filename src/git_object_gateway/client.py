"""Small HTTP client and verified, all-or-nothing sparse materialization."""

import hashlib
import ipaddress
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx2 as httpx

from .cache import BlobCache
from .protocol import (
    MAX_BLOB,
    MAX_MANIFEST,
    Entry,
    GatewayError,
    Manifest,
    oid,
    selections,
    strict_json,
    verify_blob,
)
from .service import validate_token


def origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError
        # Force validation of malformed ports as well.
        _ = parsed.port
        loopback = parsed.hostname == "localhost"
        try:
            loopback |= ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
        if parsed.scheme == "http" and not loopback:
            raise ValueError
        return value.rstrip("/")
    except ValueError as exc:
        raise GatewayError("url_requires_https_or_loopback_http") from exc


@dataclass(frozen=True)
class Receipt:
    commit: str
    manifest_sha256: str
    files: int
    materialized_bytes: int
    downloaded_bytes: int
    requests: int
    cached_files: int
    duration_ms: float


class Client:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        cache: BlobCache | None = None,
        workers: int = 8,
        timeout: float = 15,
        transport: httpx.BaseTransport | None = None,
    ):
        if type(workers) is not int or not 1 <= workers <= 16 or not 0 < timeout <= 120:
            raise GatewayError("invalid_client_limits")
        self.workers, self.cache, self.timeout = workers, cache, timeout
        self.http = httpx.Client(
            base_url=origin(url),
            headers={
                "Authorization": "Bearer " + validate_token(token),
                "Accept-Encoding": "identity",
            },
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=workers),
            transport=transport,
        )
        self._lock = threading.Lock()
        self.downloaded = self.requests = self.cached_files = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        self.http.close()

    def _get(
        self,
        endpoint: str,
        params: dict[str, str] | tuple[tuple[str, str], ...] | None = None,
        *,
        limit: int = MAX_MANIFEST,
    ) -> tuple[bytes, httpx.Headers]:
        started = time.monotonic()
        with self._lock:
            self.requests += 1
        try:
            with self.http.stream("GET", endpoint, params=params) as response:
                if response.status_code != 200:
                    raise GatewayError("http_" + str(response.status_code), response.status_code)
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise GatewayError("unexpected_content_encoding", 502)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > self.timeout:
                        raise GatewayError("download_timeout", 504)
                    if len(data) + len(chunk) > limit:
                        raise GatewayError("response_too_large", 502)
                    data.extend(chunk)
                    with self._lock:
                        self.downloaded += len(chunk)
                return bytes(data), response.headers
        except httpx.HTTPError as exc:
            # Upstream URLs, headers and response text never become error messages.
            raise GatewayError("gateway_connection_failed", 502) from exc

    def resolve(self, ref: str = "main") -> tuple[str, str]:
        data, _ = self._get("/v1/resolve", {"ref": ref}, limit=4096)
        result = strict_json(data)
        try:
            algorithm = result["object_format"]
            return oid(result["commit"], algorithm), algorithm
        except (KeyError, TypeError) as exc:
            raise GatewayError("invalid_resolution", 502) from exc

    def manifest(self, commit: str, paths: list[str], algorithm: str) -> Manifest:
        oid(commit, algorithm)
        chosen = selections(paths)
        data, headers = self._get(
            f"/v1/snapshots/{commit}/manifest", tuple(("path", p) for p in chosen)
        )
        result = Manifest.parse(strict_json(data))
        if result.commit != commit or result.object_format != algorithm or result.paths != chosen:
            raise GatewayError("snapshot_identity_mismatch", 502)
        if (
            headers.get("etag") != '"' + hashlib.sha256(data).hexdigest() + '"'
            or result.digest != hashlib.sha256(data).hexdigest()
        ):
            raise GatewayError("manifest_integrity_failed", 502)
        return result

    def _blob(self, manifest: Manifest, entry: Entry) -> bytes:
        data = self.cache.get(entry.oid, manifest.object_format) if self.cache else None
        if data is not None:
            with self._lock:
                self.cached_files += 1
        else:
            data, headers = self._get(
                f"/v1/snapshots/{manifest.commit}/file",
                {"path": entry.path, "oid": entry.oid},
                limit=MAX_BLOB,
            )
            if (
                headers.get("etag") != '"' + entry.oid + '"'
                or headers.get("x-git-object-format") != manifest.object_format
            ):
                raise GatewayError("blob_identity_mismatch", 502)
            verify_blob(data, entry.oid, manifest.object_format)
            if self.cache:
                self.cache.put(entry.oid, manifest.object_format, data)
        if len(data) != entry.size:
            raise GatewayError("blob_size_mismatch", 502)
        return data

    def snapshot(
        self,
        destination: Path,
        paths: list[str],
        *,
        ref: str = "main",
        commit: str | None = None,
        algorithm: str | None = None,
    ) -> Receipt:
        started = time.perf_counter()
        before = self.downloaded, self.requests, self.cached_files
        if destination.exists() or destination.is_symlink():
            raise GatewayError("destination_already_exists", 409)
        parent = destination.parent.resolve(strict=True)
        target = parent / destination.name
        if commit is None:
            commit, algorithm = self.resolve(ref)
        elif algorithm is None:
            algorithm = {40: "sha1", 64: "sha256"}.get(len(commit), "invalid")
        assert algorithm is not None
        manifest = self.manifest(commit, paths, algorithm)
        staging = Path(tempfile.mkdtemp(prefix=".gog-staging-", dir=parent))
        try:

            def write(entry: Entry):
                data = self._blob(manifest, entry)
                output = staging / entry.path
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with output.open("xb") as file:
                    file.write(data)
                # Reproduce the executable bit without inheriting world-readable permissions.
                output.chmod(0o700 if entry.mode == "100755" else 0o600)

            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                list(pool.map(write, manifest.entries))
            if target.exists() or target.is_symlink():
                raise GatewayError("destination_already_exists", 409)
            os.rename(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return Receipt(
            manifest.commit,
            manifest.digest,
            len(manifest.entries),
            sum(e.size for e in manifest.entries),
            self.downloaded - before[0],
            self.requests - before[1],
            self.cached_files - before[2],
            (time.perf_counter() - started) * 1000,
        )

    def diff(self, base: str, head: str) -> dict:
        if len(base) != len(head):
            raise GatewayError("invalid_object_id")
        algorithm = {40: "sha1", 64: "sha256"}.get(len(base), "invalid")
        oid(base, algorithm)
        oid(head, algorithm)
        data, _ = self._get("/v1/diff", {"base": base, "head": head})
        result = strict_json(data)
        if not isinstance(result, dict) or result.get("base") != base or result.get("head") != head:
            raise GatewayError("invalid_diff", 502)
        return result

    def receipt_json(self, receipt: Receipt) -> dict:
        return asdict(receipt)

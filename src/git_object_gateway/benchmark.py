"""Matched snapshots, real HTTP and optimized Git baselines; never fabricate scale claims."""

import hashlib
import importlib.metadata
import io
import json
import platform
import secrets
import sys
import tarfile
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cache import BlobCache
from .client import Client
from .fixtures import fixture, git, running_gateway
from .protocol import GatewayError
from .report import write_report
from .repository import Repository
from .telemetry import Telemetry, percentile


def directory_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def tree_identity(root: Path) -> dict[str, tuple[str, bool]]:
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            bool(p.stat().st_mode & 0o111),
        )
        for p in root.rglob("*")
        if p.is_file()
    }


def private_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def run_demo(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    traces = output / "traces.jsonl"
    telemetry = Telemetry(traces)
    with tempfile.TemporaryDirectory(prefix="gog-demo-") as temp:
        root = Path(temp)
        commit = fixture(root / "origin")
        repository = Repository(root / "origin")
        cache = BlobCache(root / "cache")
        token = secrets.token_urlsafe(32)
        with (
            running_gateway(repository, token, telemetry) as url,
            Client(url, token, cache=cache) as client,
        ):
            first = client.snapshot(root / "first", ["app"])
            second = client.snapshot(root / "second", ["app"], commit=commit)
            if tree_identity(root / "origin" / "app") != tree_identity(root / "second" / "app"):
                raise GatewayError("demo_verification_failed")
            before = git(root / "origin", "rev-parse", "HEAD~1").decode().strip()
            changes = client.diff(before, commit)
            summary = {
                "first_snapshot": asdict(first),
                "cached_snapshot": asdict(second),
                "diff_without_checkout": changes,
                "server_cache": repository.cache_stats(),
                "client_cache": cache.stats(),
                "http": telemetry.stats(),
                "verified": True,
            }
    private_json(output / "summary.json", summary)
    return summary


def run_benchmark(output: Path, runs: int = 5, *, large: bool = True) -> dict:
    if not 1 <= runs <= 30:
        raise GatewayError("benchmark_runs_must_be_1_to_30")
    output.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    labels = [
        "git_shallow",
        "git_partial_sparse",
        "git_archive_local",
        "gateway_client_cold",
        "gateway_client_warm",
    ]
    samples: list[dict[str, Any]] = []
    telemetry = Telemetry(output / "traces.jsonl")
    with tempfile.TemporaryDirectory(prefix="gog-benchmark-") as temp:
        root = Path(temp)
        origin = root / "origin"
        commit = fixture(origin, large=large)
        expected = tree_identity(origin / "app")
        repository = Repository(origin)
        warm_cache = BlobCache(root / "shared-cache")
        with running_gateway(repository, token, telemetry) as url:
            # Explicitly warm shared client and server object caches, outside timed samples.
            with Client(url, token, cache=warm_cache) as client:
                client.snapshot(root / "priming", ["app"], commit=commit)
            for repetition in range(runs):
                order = labels[repetition % len(labels) :] + labels[: repetition % len(labels)]
                for label in order:
                    job = root / f"job-{repetition}-{label}"
                    job.mkdir()
                    destination = job / "snapshot"
                    started = time.perf_counter()
                    receipt = None
                    archive_bytes = None
                    if label == "git_shallow":
                        git(
                            root,
                            "clone",
                            "--quiet",
                            "--no-local",
                            "--depth=1",
                            origin.as_uri(),
                            str(destination),
                        )
                    elif label == "git_partial_sparse":
                        git(
                            root,
                            "clone",
                            "--quiet",
                            "--no-local",
                            "--depth=1",
                            "--filter=blob:none",
                            "--no-checkout",
                            origin.as_uri(),
                            str(destination),
                        )
                        git(destination, "sparse-checkout", "set", "--no-cone", "/app/")
                        git(destination, "checkout", "--quiet")
                        if (
                            git(destination, "config", "--get", "remote.origin.promisor").strip()
                            != b"true"
                        ):
                            raise GatewayError("partial_clone_not_enabled")
                    elif label == "git_archive_local":
                        archive = git(origin, "archive", "--format=tar", commit, "app")
                        archive_bytes = len(archive)
                        destination.mkdir()
                        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                            tar.extractall(destination, filter="data")
                    else:
                        cache = warm_cache if label.endswith("warm") else BlobCache(job / "cache")
                        with Client(url, token, cache=cache) as client:
                            receipt = client.snapshot(destination, ["app"], commit=commit)
                    if tree_identity(destination / "app") != expected:
                        raise GatewayError("benchmark_snapshot_mismatch")
                    if label != "git_shallow" and any(
                        p.name != "app" and p.name != ".git" for p in destination.iterdir()
                    ):
                        raise GatewayError("benchmark_sparse_selection_mismatch")
                    duration_ms = (time.perf_counter() - started) * 1000
                    samples.append(
                        {
                            "strategy": label,
                            "run": repetition,
                            "duration_ms": duration_ms,
                            "job_file_bytes": directory_bytes(job),
                            "http_body_bytes": receipt.downloaded_bytes if receipt else None,
                            "http_requests": receipt.requests if receipt else None,
                            "cached_files": receipt.cached_files if receipt else None,
                            "archive_bytes": archive_bytes,
                            "verified": True,
                        }
                    )
        strategies = {}
        for label in labels:
            selected = [s for s in samples if s["strategy"] == label]
            durations = [s["duration_ms"] for s in selected]
            strategies[label] = {
                "runs": len(selected),
                "verified_runs": sum(s["verified"] for s in selected),
                "p50_ms": percentile(durations, 0.5),
                "p95_ms": percentile(durations, 0.95),
                "job_file_bytes": max(s["job_file_bytes"] for s in selected),
                "http_body_bytes": max(s["http_body_bytes"] for s in selected)
                if selected[0]["http_body_bytes"] is not None
                else None,
            }
        sources = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(__file__).parent.glob("*.py")
        }
        result = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "commit": commit,
            "runs": runs,
            "selected_files": len(expected),
            "selected_bytes": directory_bytes(origin / "app"),
            "strategies": strategies,
            "shared_client_cache": warm_cache.stats(),
            "server_repository_file_bytes": directory_bytes(origin),
            "server_cache": repository.cache_stats(),
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "git": git(origin, "--version").decode().strip(),
                "packages": {
                    n: importlib.metadata.version(n)
                    for n in ("fastapi", "uvicorn", "httpx2", "opentelemetry-sdk")
                },
            },
            "methodology": {
                "transport": "loopback HTTP; Git file:// --no-local; archive direct filesystem",
                "cache_state": "warm OS caches; server cache and shared client cache explicitly primed; cold client cache new per job",
                "order": "rotate strategies by repetition",
                "includes": "setup, download, materialization, SHA-256 content and executable-bit verification",
                "excluded": "server provisioning, priming, TLS, network latency, server CPU/RSS, filesystem allocated-block overhead",
                "limitations": "synthetic binary-heavy fixture; no universal speedup, bandwidth or enterprise-scale claim",
            },
            "source_sha256": sources,
        }
    private_json(output / "summary.json", result)
    with (output / "samples.jsonl").open("w") as file:
        for sample in samples:
            file.write(json.dumps(sample) + "\n")
    (output / "samples.jsonl").chmod(0o600)
    write_report(result, output / "report.html")
    return result

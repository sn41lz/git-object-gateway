"""Command line entry point. Secrets come from the environment, never command arguments."""

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

import uvicorn

from .benchmark import private_json, run_benchmark, run_demo
from .cache import BlobCache
from .client import Client
from .protocol import GatewayError
from .repository import Repository
from .service import create_app, validate_token
from .telemetry import Telemetry


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Verified sparse snapshots and Git queries over HTTP."
    )
    sub = result.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Serve an operator-owned, complete local Git repository.")
    serve.add_argument("--repo", type=Path, required=True)
    serve.add_argument(
        "--ref",
        action="append",
        dest="refs",
        help="Exact refs/heads/... or refs/tags/...; repeatable.",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8090)
    serve.add_argument("--traces", type=Path)
    serve.add_argument("--cache-mib", type=int, default=64)
    fetch = sub.add_parser("snapshot", help="Download a verified snapshot into a new directory.")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--path", action="append", required=True, dest="paths")
    fetch.add_argument("--output", type=Path, required=True)
    revision = fetch.add_mutually_exclusive_group()
    revision.add_argument("--ref", default="main")
    revision.add_argument(
        "--commit", help="Full immutable commit ID; avoids resolving a moving branch."
    )
    fetch.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "git-object-gateway")
    fetch.add_argument("--cache-mib", type=int, default=256)
    fetch.add_argument("--workers", type=int, default=8)
    fetch.add_argument("--receipt", type=Path)
    diff = sub.add_parser("diff", help="Query changed paths without a checkout.")
    diff.add_argument("--url", required=True)
    diff.add_argument("--base", required=True)
    diff.add_argument("--head", required=True)
    for command in ("demo", "benchmark"):
        target = sub.add_parser(command, help="Run offline fixtures using real loopback HTTP.")
        target.add_argument("--output", type=Path, default=Path("artifacts") / command)
        if command == "benchmark":
            target.add_argument("--runs", type=int, default=5)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in {"serve", "snapshot", "diff"}:
            token = validate_token(os.environ.get("GOG_TOKEN", ""))
        if args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise GatewayError("invalid_port")
            repository = Repository(
                args.repo,
                tuple(args.refs or ["refs/heads/main"]),
                cache_bytes=args.cache_mib * 1024 * 1024,
            )
            telemetry = Telemetry(args.traces)
            uvicorn.run(
                create_app(repository, token, telemetry),
                host=args.host,
                port=args.port,
                access_log=False,
                log_level="warning",
                proxy_headers=False,
                server_header=False,
                limit_concurrency=32,
                timeout_keep_alive=5,
            )
        elif args.command == "snapshot":
            with Client(
                args.url,
                token,
                cache=BlobCache(args.cache, args.cache_mib * 1024 * 1024),
                workers=args.workers,
            ) as client:
                receipt = client.snapshot(args.output, args.paths, ref=args.ref, commit=args.commit)
            if args.receipt:
                private_json(args.receipt, asdict(receipt))
            print(json.dumps(asdict(receipt), indent=2))
        elif args.command == "diff":
            with Client(args.url, token) as client:
                print(json.dumps(client.diff(args.base, args.head), indent=2))
        elif args.command == "demo":
            result = run_demo(args.output)
            print(
                f"Verified {result['first_snapshot']['files']} files. Cached run reused {result['cached_snapshot']['cached_files']} files."
            )
            print(f"Results: {args.output.resolve() / 'summary.json'}")
        elif args.command == "benchmark":
            result = run_benchmark(args.output, args.runs)
            for name, values in result["strategies"].items():
                print(
                    f"{name}: p95={values['p95_ms']:.1f} ms, bytes/job={values['job_file_bytes']:,}, verified={values['verified_runs']}/{values['runs']}"
                )
            print(f"Report: {args.output.resolve() / 'report.html'}")
        return 0
    except GatewayError as exc:
        print("error: " + exc.code, file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print("error: local_io_failed", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130

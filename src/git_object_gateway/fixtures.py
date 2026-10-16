"""Deterministic, network-free sample repository for the demo and benchmark."""

import hashlib
import os
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import uvicorn

from .protocol import GatewayError
from .service import create_app


def git(root: Path, *args: str, output_limit: int = 32 * 1024 * 1024) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Gateway Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Gateway Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2026-10-13T12:00:00Z",
            "GIT_COMMITTER_DATE": "2026-10-13T12:00:00Z",
        }
    )
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(root), *args],
        env=env,
        capture_output=True,
        timeout=60,
    )
    if result.returncode or len(result.stdout) > output_limit:
        raise GatewayError("fixture_git_failed")
    return result.stdout


def fixture(root: Path, *, large: bool = False, algorithm: str = "sha1") -> str:
    root.mkdir(parents=True)
    git(root, "init", "-q", "--initial-branch=main", "--object-format=" + algorithm)
    for d in ("app", "docs", "assets"):
        (root / d).mkdir()
    (root / "README.md").write_text(
        "# Example monorepo\nA small application beside large binary fixtures.\n"
    )
    for i in range(16 if large else 4):
        (root / "app" / f"module_{i:02d}.py").write_text(
            f'"""Example module {i}."""\n' + (f"# application logic {i}\n" * 100)
        )
    script = root / "app" / "run.sh"
    script.write_text("#!/bin/sh\nprintf 'hello from a verified snapshot\\n'\n")
    script.chmod(0o755)
    (root / "docs" / "guide.md").write_text("# Example guide\n" * 100)
    for i in range(128 if large else 3):
        # Incompressible deterministic bytes: this is deliberately a binary-heavy workload.
        data = b"".join(
            hashlib.sha256(f"fixture-{i}-{j}".encode()).digest()
            for j in range(2048 if large else 32)
        )
        (root / "assets" / f"sample_{i:03d}.bin").write_bytes(data)
    git(root, "add", "--all")
    git(root, "commit", "-qm", "Initial fixture")
    for i in range(5 if large else 1):
        (root / "docs" / "guide.md").write_text("# Example guide\n" * 100 + f"Revision {i}\n")
        git(root, "add", "docs/guide.md")
        git(root, "commit", "-qm", f"Update guide {i}")
    git(root, "config", "uploadpack.allowFilter", "true")
    return git(root, "rev-parse", "HEAD").decode().strip()


@contextmanager
def running_gateway(repository, token, telemetry=None):
    """Real loopback HTTP, including the ASGI lifecycle; no external network dependency."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(repository, token, telemetry), log_level="critical", access_log=False
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise GatewayError("demo_server_failed", 503)
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        if thread.is_alive():
            raise GatewayError("demo_server_shutdown_timeout", 503)

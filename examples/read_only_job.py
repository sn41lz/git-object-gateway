"""Run against a local gateway. Does not execute downloaded code."""

import os
import tempfile
from pathlib import Path

from git_object_gateway.cache import BlobCache
from git_object_gateway.client import Client

with tempfile.TemporaryDirectory(prefix="review-job-") as directory:
    output = Path(directory) / "input"
    cache = BlobCache(Path.home() / ".cache" / "git-object-gateway")
    with Client("http://127.0.0.1:8090", os.environ["GOG_TOKEN"], cache=cache) as client:
        receipt = client.snapshot(output, ["docs"], ref="main")
    markdown_files = list(output.rglob("*.md"))
    print(f"Verified {len(markdown_files)} Markdown files at {receipt.commit}.")
    print(
        f"Reused {receipt.cached_files} cached files; downloaded {receipt.downloaded_bytes} bytes."
    )

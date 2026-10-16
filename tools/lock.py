"""Refresh both hash locks from a pip resolver report, including all platform artifact hashes.

Run with a development interpreter: python tools/lock.py /tmp/gateway-deps.json.
Review lock diffs; verify them on every supported Python in CI before merging.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

report = json.loads(Path(sys.argv[1]).read_text())
versions = {canonicalize_name(x["metadata"]["name"]): x["metadata"] for x in report["install"]}
root = versions.pop("git-object-gateway")
runtime = set()


def visit(specification):
    req = Requirement(specification)
    if req.marker and not req.marker.evaluate({"extra": ""}):
        return
    name = canonicalize_name(req.name)
    if name in runtime:
        return
    runtime.add(name)
    for child in versions[name].get("requires_dist", []):
        visit(child)


for spec in root.get("requires_dist", []):
    visit(spec)
versions["setuptools"] = {"version": "84.0.0"}
versions["wheel"] = {"version": "0.48.0"}


def fetch(item):
    name, metadata = item
    version = metadata["version"]
    with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
        data = json.load(response)
    hashes = sorted({file["digests"]["sha256"] for file in data["urls"] if not file["yanked"]})
    if not hashes:
        raise ValueError(f"No non-yanked distributions: {name}=={version}")
    return name, name + "==" + version + " \\\n" + " \\\n".join(
        "    --hash=sha256:" + value for value in hashes
    )


with ThreadPoolExecutor(max_workers=8) as pool:
    lines = dict(pool.map(fetch, sorted(versions.items())))
header = (
    "# Exact versions and all non-yanked PyPI distribution hashes. See docs/development.md.\n\n"
)
Path("requirements.lock").write_text(header + "\n\n".join(lines[n] for n in sorted(runtime)) + "\n")
Path("requirements-dev.lock").write_text(
    header
    + "-r requirements.lock\n\n"
    + "\n\n".join(lines[n] for n in sorted(lines.keys() - runtime))
    + "\n"
)
print(
    f"Locked {len(runtime)} runtime packages and {len(lines) - len(runtime)} development packages."
)

import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from git_object_gateway.fixtures import fixture, git
from git_object_gateway.protocol import GatewayError
from git_object_gateway.repository import Repository


@pytest.mark.parametrize("algorithm", ["sha1", "sha256"])
def test_manifest_and_blob_on_real_git(tmp_path, algorithm):
    root = tmp_path / "repo"
    commit = fixture(root, algorithm=algorithm)
    repo = Repository(root)
    result = repo.manifest(commit, ["app", "app/run.sh"])
    assert result.paths == ("app",)
    assert len(result.entries) == 5
    assert result.object_format == algorithm
    for entry in result.entries:
        _, data, hit = repo.file(commit, entry.path, entry.oid)
        assert data == (root / entry.path).read_bytes()
        assert not hit
        assert repo.file(commit, entry.path, entry.oid)[2]
    assert repo.cache_stats()["objects"] == 5


def test_queries_do_not_change_worktree_or_index(repo):
    root, commit, repository = repo
    (root / "app/module_00.py").write_text("uncommitted local edits")
    before = git(root, "status", "--porcelain=v1")
    index = (root / ".git/index").read_bytes()
    manifest = repository.manifest(commit, ["app"])
    assert (
        repository.file(commit, manifest.entries[0].path, manifest.entries[0].oid)[1]
        != b"uncommitted local edits"
    )
    assert git(root, "status", "--porcelain=v1") == before
    assert (root / ".git/index").read_bytes() == index


def test_bare_mirror_supported(repo, tmp_path):
    root, commit, _ = repo
    bare = tmp_path / "mirror.git"
    git(tmp_path, "clone", "--bare", str(root), str(bare))
    assert Repository(bare).manifest(commit, ["app"]).commit == commit


def test_dangling_commit_and_arbitrary_oid_rejected(repo):
    root, commit, repository = repo
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    dangling = git(root, "commit-tree", tree, "-m", "Unpublished").decode().strip()
    with pytest.raises(GatewayError, match="commit_not_allowed"):
        repository.manifest(dangling, ["app"])
    entry = repository.manifest(commit, ["app"]).entries[0]
    with pytest.raises(GatewayError, match="object_does_not_match_path"):
        repository.file(commit, entry.path, "a" * 40)
    for ref in ("HEAD", "main~1", "--all", "refs/heads/private"):
        with pytest.raises(GatewayError):
            repository.resolve(ref)


def test_history_access_and_force_push_revokes_cached_objects(repo):
    root, commit, repository = repo
    previous = git(root, "rev-parse", "HEAD~1").decode().strip()
    repository.authorize(previous)
    entry = repository.manifest(commit, ["app"]).entries[0]
    repository.file(commit, entry.path, entry.oid)
    git(root, "update-ref", "refs/heads/main", previous)
    with pytest.raises(GatewayError, match="commit_not_allowed"):
        repository.file(commit, entry.path, entry.oid)


def test_literal_paths_with_spaces_and_shell_characters(repo):
    root, _, repository = repo
    name = "app/$(touch sentinel); file.txt"
    (root / name).write_text("literal filename")
    git(root, "add", "--all")
    git(root, "commit", "-qm", "Literal name")
    commit = repository.resolve("main")["commit"]
    result = repository.manifest(commit, [name])
    assert result.entries[0].path == name
    assert not (root / "sentinel").exists()


def test_diff_handles_deletions_additions_and_modifications(repo):
    root, before, repository = repo
    (root / "app/module_00.py").unlink()
    (root / "app/new.py").write_text("new")
    (root / "app/module_01.py").write_text("changed")
    git(root, "add", "--all")
    git(root, "commit", "-qm", "Changes")
    head = repository.resolve("main")["commit"]
    changes = {e["path"]: e["status"] for e in repository.diff(before, head)["changes"]}
    assert changes == {"app/module_00.py": "D", "app/module_01.py": "M", "app/new.py": "A"}


def test_symlink_fail_closed_and_missing_selection(repo):
    root, _, repository = repo
    (root / "app/unsafe").symlink_to("../../secret")
    git(root, "add", "--all")
    git(root, "commit", "-qm", "Symlink")
    commit = repository.resolve("main")["commit"]
    with pytest.raises(GatewayError, match="unsupported_file_mode"):
        repository.manifest(commit, ["app"])
    with pytest.raises(GatewayError, match="path_not_found"):
        repository.manifest(commit, ["docs", "missing"])


def test_submodule_fail_closed(repo):
    root, commit, repository = repo
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{commit},app/submodule")
    git(root, "commit", "-qm", "Gitlink")
    with pytest.raises(GatewayError, match="unsupported_file_mode"):
        repository.manifest(repository.resolve("main")["commit"], ["app"])


def test_partial_repository_rejected(repo):
    root, _, _ = repo
    git(root, "config", "remote.origin.promisor", "true")
    with pytest.raises(GatewayError, match="partial_repositories_not_supported"):
        Repository(root)


def test_concurrent_cache_misses_singleflight(repo, monkeypatch):
    _, commit, repository = repo
    entry = repository.manifest(commit, ["app"]).entries[0]
    original = repository.run
    count = 0
    lock = threading.Lock()

    def run(*args, **kwargs):
        nonlocal count
        if args[0] == "cat-file":
            with lock:
                count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "run", run)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: repository.file(commit, entry.path, entry.oid)[1], range(16))
        )
    assert len(set(results)) == 1
    assert count == 1
    assert repository.cache_stats()["hits"] == 15


def test_cache_eviction_and_disabled_cache(repo):
    root, commit, _ = repo
    repository = Repository(root, cache_bytes=2300)
    entries = repository.manifest(commit, ["app"]).entries
    for entry in entries:
        repository.file(commit, entry.path, entry.oid)
    assert repository.cache_stats()["bytes"] <= 2300
    assert repository.cache_stats()["objects"] < len(entries)
    repository = Repository(root, cache_bytes=0)
    repository.file(commit, entries[0].path, entries[0].oid)
    assert repository.cache_stats()["bytes"] == 0


@pytest.mark.parametrize(
    "script,limit,timeout,expected",
    [
        ("import time;time.sleep(5)", 100, 0.03, "git_timeout"),
        ("import sys;sys.stdout.buffer.write(b'x'*1000)", 100, 2, "git_output_limit"),
    ],
)
def test_git_timeout_and_output_bounds(repo, monkeypatch, script, limit, timeout, expected):
    _, _, repository = repo
    original = subprocess.Popen

    def process(command, **kwargs):
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", process)
    repository.timeout = timeout
    with pytest.raises(GatewayError, match=expected):
        repository.run("rev-parse", "HEAD", limit=limit)


def test_busy_and_invalid_limits(repo):
    root, _, repository = repo
    for _ in range(8):
        repository._slots.acquire()
    try:
        with pytest.raises(GatewayError, match="git_busy"):
            repository.resolve("main")
    finally:
        for _ in range(8):
            repository._slots.release()
    for kwargs in ({"cache_bytes": -1}, {"timeout": 0}, {"refs": ()}, {"refs": ("HEAD",)}):
        with pytest.raises(GatewayError):
            Repository(root, **kwargs)

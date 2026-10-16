import pytest

from git_object_gateway.fixtures import fixture
from git_object_gateway.repository import Repository

TOKEN = "test-token-" + "a" * 40


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    commit = fixture(root)
    return root, commit, Repository(root)

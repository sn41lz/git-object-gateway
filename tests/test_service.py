import json

import pytest
from conftest import TOKEN
from fastapi.testclient import TestClient

from git_object_gateway.fixtures import git
from git_object_gateway.protocol import GatewayError
from git_object_gateway.service import create_app, validate_token
from git_object_gateway.telemetry import Telemetry


def test_auth_all_data_routes_and_schema(repo):
    _, commit, repository = repo
    with TestClient(create_app(repository, TOKEN)) as client:
        for endpoint in (
            "/v1/resolve",
            f"/v1/snapshots/{commit}/manifest?path=app",
            f"/v1/snapshots/{commit}/file?path=app/run.sh&oid={'a' * 40}",
            f"/v1/diff?base={commit}&head={commit}",
            "/v1/stats",
        ):
            for headers in (
                {},
                {"Authorization": "Bearer wrong"},
                {"Authorization": "Basic wrong"},
            ):
                response = client.get(endpoint, headers=headers)
                assert response.status_code == 401
                assert response.json() == {"error": "unauthorized"}
        assert client.get("/healthz").status_code == 200
        schema = client.get("/openapi.json").json()
        for route in schema["paths"]:
            if route.startswith("/v1/"):
                assert schema["paths"][route]["get"]["security"] == [{"HTTPBearer": []}]


def test_http_etags_raw_bytes_and_auth_before_conditional_cache(repo):
    root, commit, repository = repo
    auth = {"Authorization": "Bearer " + TOKEN}
    with TestClient(create_app(repository, TOKEN), headers=auth) as client:
        result = client.get(f"/v1/snapshots/{commit}/manifest", params={"path": "app"})
        assert result.status_code == 200
        conditional = {"If-None-Match": result.headers["etag"]}
        assert (
            client.get(f"/v1/snapshots/{commit}/manifest?path=app", headers=conditional).status_code
            == 304
        )
        entry = result.json()["files"][0]
        url = f"/v1/snapshots/{commit}/file"
        params = {"path": entry["path"], "oid": entry["oid"]}
        first = client.get(url, params=params)
        assert first.content == (root / entry["path"]).read_bytes()
        assert first.headers["x-object-cache"] == "miss"
        assert client.get(url, params=params).headers["x-object-cache"] == "hit"
        assert (
            client.get(
                url, params=params, headers={"If-None-Match": first.headers["etag"]}
            ).status_code
            == 304
        )
        assert (
            client.get(
                url,
                params=params,
                headers={"Authorization": "Bearer invalid", "If-None-Match": first.headers["etag"]},
            ).status_code
            == 401
        )
        previous = git(root, "rev-parse", "HEAD~1").decode().strip()
        git(root, "update-ref", "refs/heads/main", previous)
        assert (
            client.get(
                url, params=params, headers={"If-None-Match": first.headers["etag"]}
            ).status_code
            == 404
        )


def test_errors_and_telemetry_do_not_leak_secrets_or_paths(repo, tmp_path, monkeypatch):
    _, _, repository = repo
    traces = tmp_path / "traces.jsonl"
    telemetry = Telemetry(traces)
    with TestClient(
        create_app(repository, TOKEN, telemetry), headers={"Authorization": "Bearer " + TOKEN}
    ) as client:

        def broken(ref):
            raise RuntimeError("secret-code-at-/home/private: " + TOKEN)

        monkeypatch.setattr(repository, "resolve", broken)
        result = client.get("/v1/resolve?ref=secret-path")
        assert result.status_code == 500
        assert result.json() == {"error": "internal_error"}
        assert result.headers["cache-control"] == "no-store"
    raw = traces.read_text()
    assert TOKEN not in raw and "secret" not in raw and "/home" not in raw
    spans = [json.loads(line) for line in raw.splitlines()]
    assert spans[0]["attributes"]["http.route"] == "/v1/resolve"
    assert telemetry.stats()["errors"] == 1
    assert traces.stat().st_mode & 0o777 == 0o600


def test_limits_and_not_found(repo):
    _, commit, repository = repo
    with TestClient(
        create_app(repository, TOKEN), headers={"Authorization": "Bearer " + TOKEN}
    ) as client:
        assert client.get("/v1/resolve?x=" + "a" * 9000).status_code == 414
        assert (
            client.get(
                f"/v1/snapshots/{commit}/manifest", params=[("path", "app")] * 33
            ).status_code
            == 422
        )
        assert client.get(f"/v1/snapshots/{commit}/manifest?path=../secret").status_code == 422
        assert client.get("/v1/snapshots/--all/manifest?path=app").status_code == 400
        assert client.get("/v1/resolve?ref=private").status_code == 404
        assert client.post("/v1/resolve").status_code == 405


@pytest.mark.parametrize("value", ["", "short", "a" * 257, "a" * 32 + " ", "á" * 32])
def test_token_validation(value):
    with pytest.raises(GatewayError):
        validate_token(value)

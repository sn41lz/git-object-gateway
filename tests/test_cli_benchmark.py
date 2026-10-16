import json

import pytest

from git_object_gateway.benchmark import run_benchmark, run_demo
from git_object_gateway.cli import main
from git_object_gateway.protocol import GatewayError
from git_object_gateway.report import write_report
from git_object_gateway.telemetry import Telemetry, percentile


def test_demo_end_to_end_and_trace_privacy(tmp_path):
    result = run_demo(tmp_path)
    assert result["verified"]
    assert result["cached_snapshot"]["cached_files"] == result["first_snapshot"]["files"]
    assert result["diff_without_checkout"]["changes"] == [{"status": "M", "path": "docs/guide.md"}]
    raw = (tmp_path / "traces.jsonl").read_text()
    assert "Gateway Fixture" not in raw
    assert "Bearer" not in raw and "module_" not in raw and "Authorization" not in raw
    assert (tmp_path / "summary.json").stat().st_mode & 0o777 == 0o600


def test_all_benchmark_baselines_verified_and_report_self_contained(tmp_path):
    result = run_benchmark(tmp_path, 1, large=False)
    assert len(result["strategies"]) == 5
    for value in result["strategies"].values():
        assert value["verified_runs"] == 1
        assert value["p95_ms"] > 0
    assert result["strategies"]["gateway_client_warm"]["job_file_bytes"] == result["selected_bytes"]
    assert (
        result["strategies"]["git_partial_sparse"]["job_file_bytes"]
        < result["strategies"]["git_shallow"]["job_file_bytes"]
    )
    samples = [json.loads(line) for line in (tmp_path / "samples.jsonl").read_text().splitlines()]
    assert len(samples) == 5 and all(s["verified"] for s in samples)
    assert next(s for s in samples if s["strategy"] == "gateway_client_warm")["http_requests"] == 1
    assert result["source_sha256"]["repository.py"]
    report = (tmp_path / "report.html").read_text()
    assert "<script" not in report and "https://" not in report
    assert "not measured" in report and "Content-Security-Policy" in report
    result["environment"]["platform"] = "<img src=x onerror=alert(1)>"
    write_report(result, tmp_path / "escaped.html")
    assert "<img src=x" not in (tmp_path / "escaped.html").read_text()


def test_benchmark_run_boundaries(tmp_path):
    for runs in (0, 31):
        with pytest.raises(GatewayError):
            run_benchmark(tmp_path, runs)


def test_cli_no_credentials_fails_before_repo_access(monkeypatch, capsys):
    monkeypatch.delenv("GOG_TOKEN", raising=False)
    assert main(["serve", "--repo", "/does/not/exist"]) == 2
    assert "token_must_be" in capsys.readouterr().err


def test_cli_demo_and_safe_io_error(tmp_path, capsys, monkeypatch):
    assert main(["demo", "--output", str(tmp_path / "demo")]) == 0
    assert "Verified 5 files" in capsys.readouterr().out
    monkeypatch.setenv("GOG_TOKEN", "a" * 40)
    assert main(["serve", "--repo", str(tmp_path / "missing")]) == 2
    assert capsys.readouterr().err == "error: local_io_failed\n"


def test_metrics_bounded_and_instance_scoped():
    first, second = Telemetry(), Telemetry()
    for value in range(1100):
        first.observe(value, 500 if value % 2 else 200, 10)
    stats = first.stats()
    assert stats["requests"] == 1100
    assert stats["errors"] == 550
    assert stats["latency_window"] == 1000
    assert stats["response_body_bytes"] == 11000
    assert stats["p50_ms"] == 599 and stats["p95_ms"] == 1049
    assert second.stats()["requests"] == 0
    assert percentile([], 0.95) == 0
    first.close()
    second.close()

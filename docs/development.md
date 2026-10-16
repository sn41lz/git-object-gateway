# Development and reproducibility

Supported: Linux/macOS, Python 3.12–3.14, Git 2.39+. Windows support is not claimed because bounded pipe reading uses POSIX selectors and executable-bit semantics.

```sh
make setup
make lint typecheck local-test build
make local-demo local-benchmark RUNS=5
```

`make setup` creates `.venv`, installs exact transitive versions using `--require-hashes --only-binary=:all:`, then installs this project without resolving dependencies. `requirements.lock` is the runtime closure; `requirements-dev.lock` includes it plus development tools; `requirements-build.lock` pins the build backend. Locks include every non-yanked PyPI artifact hash for each pinned version so Linux/macOS and supported Python wheels are covered.

For an intentional dependency update, change direct pins in `pyproject.toml`, then:

```sh
.venv/bin/python -m pip install --dry-run --ignore-installed \
  --report /tmp/gateway-deps.json '.[dev]'
.venv/bin/python tools/lock.py /tmp/gateway-deps.json
make setup lint typecheck local-test build
```

If updating the build backend, update `requirements-build.lock` too and keep its versions aligned with the dev lock and `build-system` requirements. The lock refresher uses the current interpreter's resolver environment; CI verifies the closure on every supported Python. Review transitive changes rather than treating lock regeneration as sufficient validation.

The Docker base is pinned to an official multi-platform digest. Debian's Git/CA packages are installed from apt during build, so image rebuilds are not bit-for-bit reproducible. Builds require network; tests and demos need only loopback at execution time. CI runs actual container builds and `network_mode: none` tests/demo, not merely Compose syntax checks.

## Validation

The suite covers real SHA-1/SHA-256 repositories, bare mirrors, staged publication, HTTP authentication and ETags, history reachability, force-pushes, corrupt caches/responses, schema/path attacks, single-flight concurrency, LRU quota behavior, subprocess deadlines/output limits, trace privacy and all benchmark baselines. Fake HTTP transports exercise malformed and hostile responses; an additional integration path uses Uvicorn and real TCP. Tests never call a hosted model or third-party Git server.

CI requires formatting, lint, type checking, tests with branch-aware coverage >=90%, demo/benchmark smoke checks, wheel/sdist build and a wheel demo away from the source tree. Actions are pinned to commit hashes. Artifacts retain raw measurements and coverage for 14 days. Local Docker is optional when reviewing source; container validation is available through GitHub Actions.

## Benchmark methodology

`benchmark.py` builds a deterministic repository with incompressible binary fixtures alongside a small application, then selects only `app/`. It rotates strategy order on every repetition. Shallow and partial clones use `file:// --no-local` to avoid hardlink shortcuts. Sparse Git enables `blob:none`, depth one and non-cone `/app/` selection. Local archive reads Git directly and avoids transport. Gateway calls use actual loopback HTTP, a primed server cache, and either a new client cache or one primed shared cache.

Timings include per-job setup, materialization and independent file/executable-bit verification. They exclude server provisioning, cache priming, TLS, WAN latency and server CPU/RSS. Logical file bytes differ from allocated blocks. Warm job bytes exclude the separately disclosed shared cache. Git transport bytes are not measured, so the report makes no cross-protocol network-byte comparison. Nearest-rank p95 on a small sample is descriptive, not a statistical service-level guarantee.

Reference samples live in `benchmarks/reference/`. To refresh, complete code changes first, run the benchmark, then copy `summary.json`, `samples.jsonl` and `report.html`; source hashes record exactly which implementation was measured. Update the README table from that summary. Runtime traces remain untracked.

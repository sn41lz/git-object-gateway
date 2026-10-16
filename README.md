# Git Object Gateway

[![CI](https://github.com/sn41lz/git-object-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/sn41lz/git-object-gateway/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/license-MIT-82e8ca)](LICENSE)
![Python 3.12–3.14](https://img.shields.io/badge/python-3.12%E2%80%933.14-blue)

**Give read-only agents and CI jobs a verified slice of a repository over HTTP.**

A documentation checker needs `docs/`. An analysis agent needs `src/parser/` and its tests. Creating a fresh clone for every short-lived job duplicates Git metadata and files those jobs never read. This project serves commit-pinned files and changed-path queries from one existing Git repository, with reusable, verified client caching.

[Español](docs/README.es.md) · [Scion research](docs/scion-research.md) · [Architecture](docs/architecture.md) · [Deployment](docs/deployment.md) · [Measured results](benchmarks/reference/summary.json)

## Try it in one command

With Docker and Compose installed, from this checkout:

```sh
make demo
```

The demo creates a temporary Git repository, starts a real loopback HTTP service, downloads `app/`, checks every file, repeats with a shared cache, and queries a diff. Results and private OTel traces appear in `artifacts/demo/`. No account or model API key is needed. Installation needs internet; execution uses Docker's `network_mode: none` with internal loopback.

```sh
make test                    # containerized integration and failure-case tests
make benchmark RUNS=5        # real Git baselines + self-contained HTML report
```

Without Docker, use Python 3.12+ and Git 2.39+ on Linux or macOS:

```sh
make local                   # hash-locked environment + demo + tests
make local-benchmark RUNS=5
```

Open `artifacts/benchmark/report.html` in a browser. A measured [reference report](benchmarks/reference/report.html) and [raw samples](benchmarks/reference/samples.jsonl) are committed; download the HTML to view it locally.

## Use a real repository

In a server terminal, generate a token and start the service. Supply that same private token to the client terminal.

```sh
make setup
export GOG_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/git-object-gateway serve \
  --repo /absolute/path/to/complete-repository \
  --ref refs/heads/main \
  --traces artifacts/server-traces.jsonl
```

Normal checkouts and bare mirrors work. The server reads committed objects; uncommitted edits stay outside snapshots. Updating the repository is the operator's responsibility.

From a client terminal with the same `GOG_TOKEN` exported:

```sh
.venv/bin/git-object-gateway snapshot \
  --url http://127.0.0.1:8090 \
  --path src --path docs \
  --output /tmp/review-input \
  --cache /tmp/shared-git-object-cache \
  --receipt /tmp/review-receipt.json
```

Use `--commit FULL_COMMIT_ID` for repeatable jobs. With `--ref main`, the branch is resolved **once**; all subsequent requests use that immutable commit. Output must be a new directory. Receipts record the commit, manifest digest, verified bytes, requests, cache reuse and elapsed time.

For Docker hosting, export `GOG_REPO` and `GOG_TOKEN`, then run `make serve`. Compose mounts the repository read-only and publishes on host loopback. See [deployment](docs/deployment.md) for TLS and operational limits.

## What is implemented

- **Read API:** allowed-ref resolution, exact sparse manifests, raw files, changed paths, cache statistics and request latency.
- **Verification:** SHA-1/SHA-256 Git object formats, canonical SHA-256 manifests, independent blob hashing, size checks and executable-bit preservation.
- **Reusable objects:** server memory LRU with single-flight misses; transactional SQLite client LRU that verifies every hit and evicts corruption.
- **Safe publication:** bounded parallel download into private staging; output becomes visible after every file passes verification. Failed attempts remove staging and preserve valid cache entries for retries.
- **Bounded work:** admission limits, Git deadlines/output limits, file/byte quotas and bounded caches.
- **Observability:** instance-scoped OTel spans, private JSONL, p50/p95 over the latest 1,000 handlers, cache counters and response-body bytes. There are no model calls or per-token costs.
- **Developer tooling:** CLI, Python client, authenticated OpenAPI, Docker/Compose, exact hash locks and CI on Python 3.12–3.14 plus hardened containers.

## How a snapshot works

```mermaid
sequenceDiagram
    participant Job as Agent / CI
    participant API as Gateway
    participant Git as Existing Git repository
    participant Cache as Client cache
    Job->>API: Resolve allowed ref once
    API->>Git: Resolve commit
    API-->>Job: Commit ID + object format
    Job->>API: Manifest(commit, paths)
    API->>Git: Authorize commit; list selected tree
    API-->>Job: Paths + modes + object IDs + sizes
    Job->>Cache: Verify reusable blobs
    Job->>API: Fetch missing files at pinned commit
    API-->>Job: Exact blob bytes
    Job->>Job: Verify hashes; publish directory
```

The client needs no Git installation for snapshots or diffs. The gateway does. [Python example](examples/read_only_job.py).

## Measured baseline versus cached snapshots

| Strategy | p50 | p95 | Job file bytes | Verified runs |
| --- | ---: | ---: | ---: | ---: |
| `git_shallow` | 222.0 ms | 233.1 ms | 16,871,190 | 15/15 |
| `git_partial_sparse` | 79.0 ms | 83.0 ms | 85,704 | 15/15 |
| `git_archive_local` | 13.0 ms | 14.2 ms | 36,242 | 15/15 |
| `gateway_client_cold` | 126.3 ms | 131.5 ms | 118,162 | 15/15 |
| `gateway_client_warm` | 28.5 ms | 31.9 ms | 36,242 | 15/15 |

Shared warm-client cache: 36,242 payload bytes / 81,920 SQLite file bytes. Server repository: 16,869,792 logical bytes; server cache: 36,242 payload bytes.

The reference fixture is binary-heavy and selects 17 files under `app/`. Every output is checked for identical content and executable bits. The report records environment, samples, source hashes and shared costs.

Baselines include a **shallow clone**, a **shallow filtered sparse clone**, and **local `git archive`**. OS caches are warm; Git uses `file:// --no-local`; the gateway uses loopback HTTP. Cold/warm refer to the **client object cache**. Server caches are explicitly primed. These local measurements do not establish internet throughput or enterprise-scale performance. HTTP bytes exclude headers; Git transport bytes are unmeasured. Local archive avoids transport entirely.

## Tradeoffs and failure modes

- A cold gateway download can cost more than optimized sparse Git. Authorization, per-file HTTP, verification and SQLite transactions have a cost. Repeated read jobs and shared object reuse are the intended workload.
- One token grants access to **history reachable from every configured ref**, including deleted files. There are no directory ACLs or per-user identities. Separate services and caches by trust domain.
- A full commit must still be reachable from an allowed ref. A force-push can revoke access on subsequent requests and cause a clean download failure. Already delivered or cached bytes cannot be revoked.
- Use an operator-owned, complete repository; partial/promisor repositories are rejected. Local repository configuration and cache storage must be trusted. This is not a sandbox for hostile repository owners or local users.
- Only regular/executable files with portable UTF-8 NFC paths work. Symlinks, submodules, case aliases, traversal and unsafe names fail the whole selection. LFS pointers remain pointers. No filters, attributes, hooks or downloaded code execute.
- Defaults: 32 selections, 5,000 files, 8 MiB per file, 128 MiB per snapshot. Expensive queries fail at explicit limits. The server still maintains a complete Git repository.
- Client cache quotas bound payload, not SQLite allocation, journals or RSS. Offline reconstruction, automatic retries, range resumption and GC coordination are outside v0.1.
- Publication uses rename under a caller-controlled parent. It is process-level failure atomicity, not a power-loss or hostile shared-filesystem guarantee. Use one snapshot operation per client instance at a time.

Keep using Git for editing and ordinary development. Its [partial-clone/sparse-checkout options](https://git-scm.com/docs/git-clone) already solve many large-repository problems. [`git archive`](https://git-scm.com/docs/git-archive) is simpler when an archive is enough. This gateway adds an HTTP query surface and verified object reuse for read jobs.

## Relationship to Scion

Anthropic describes Scion as an internal source-control service with server-side operations, content-addressed data and sparse clients. That motivated this narrower read-only design. This independent project has no Anthropic affiliation or Scion protocol compatibility. [Primary source and analysis](docs/scion-research.md).

## Contributing

Run `make lint typecheck local-test build`. Regression cases, benchmark methodology improvements and small protocol changes are welcome. See [development](docs/development.md), [security](SECURITY.md) and the [production checklist](PRODUCTION_CHECKLIST.md). Licensed under [MIT](LICENSE).

# Deployment and operations

## Local or trusted-network deployment

Install Python 3.12+ and Git 2.39+, then `make setup`. Serve a complete normal repository or bare mirror owned by the service user. The token must contain 32–256 non-whitespace ASCII characters. Generate it with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` and export `GOG_TOKEN`; do not put credentials in command arguments, URLs or committed files.

```sh
.venv/bin/git-object-gateway serve --repo /srv/project.git \
  --ref refs/heads/main --ref refs/tags/v1.0.0 \
  --traces /srv/gateway-logs/traces.jsonl
```

Default binding is `127.0.0.1:8090`. To use Compose, export an absolute `GOG_REPO` path, export `GOG_TOKEN`, and run `make serve`. The repository is mounted read-only and host publication stays on loopback. The Compose user must match the mounted repository's owner; `make serve` passes the current UID/GID. A dubious-ownership Git error must be fixed by correct ownership, not a global `safe.directory=*` setting.

Serve a dedicated mirror where practical. Use your existing deployment pipeline to update it; this API never fetches. Avoid destructive pruning during active snapshots, or retain commits long enough for active jobs. A ref force-push can intentionally revoke a commit; subsequent file requests fail cleanly. Normal read-only jobs do not modify the worktree, index or refs.

## Exposing the service beyond loopback

Use a TLS reverse proxy with certificate verification, restricted network access, request/header size limits, connection deadlines and per-client rate limits. The application supplies admission control, not per-identity abuse prevention. HTTP clients refuse non-loopback cleartext URLs. Do not expose this single-token service as a multi-tenant public hosting platform.

Deploy a separate process and cache for each trust domain. The token authorizes all history reachable from allowed refs, including historical secrets or deleted files. Path selection is a bandwidth feature, not a permission boundary. One cannot revoke bytes already received by a client.

Rotate tokens by restarting with a new `GOG_TOKEN`. Existing requests may finish under their prior authorization. Supply secrets through your deployment secret manager. The checked-in Compose file uses environment variables for an accessible local example.

## Storage, logs and limits

The server cache stores up to 64 MiB of blob payload in memory. Interpreter, HTTP buffers and Git subprocess memory are additional. Tune `--cache-mib` to your machine. Compose sets CPU, memory, PID, read-only filesystem and capability limits.

The client SQLite cache stores at most 256 MiB payload by default. Database allocation, free pages and transient rollback journals can exceed that. Use `--cache-mib`, filesystem quotas and a dedicated private cache directory. Do not use an untrusted or concurrently manipulated directory. To reclaim SQLite freelist allocation, stop cache users and recreate the cache directory; data is disposable and will be reverified after refetch.

OTel JSONL is private (0600) but has no automatic rotation. Rotate externally and restart to reopen the file. No credentials, query paths, source content or exception messages are exported. Metrics are in-process and reset on restart. `GET /v1/stats` requires the token; `/healthz` exposes only status/version and is not a deep repository-integrity check.

## Operational responses

| Symptom | Response |
| --- | --- |
| `unauthorized` | Check the token in both environments; never paste it into an issue. |
| `commit_not_allowed` | Resolve an allowed ref again, or retain the expected commit in authorized history. |
| `git_object_unavailable` | Check completeness, ownership and concurrent pruning locally. |
| `partial_repositories_not_supported` | Provision a complete mirror. |
| `server_busy` / `git_busy` | Back off at the caller; this version does not retry automatically. |
| `git_timeout` / output limit | Reduce selection or investigate repository health; no partial output is published. |
| Integrity error | Investigate source/cache/storage corruption. Never bypass verification. |
| Existing destination | Choose a new directory; the client never overwrites existing output. |

These controls have regression tests; no independent security audit or large-cluster validation is claimed.

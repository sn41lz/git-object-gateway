# Architecture and protocol

One process serves one operator-owned repository. A bearer token authorizes reads from configured refs and their reachable history. No endpoint accepts a filesystem root or remote URL. There are no writes, merges, fetches, model calls or execution endpoints.

## Git boundary

Commands use a resolved executable and argument vectors. Global/system Git configuration, inherited `GIT_*` variables, replacement objects, fsmonitor, hooks, optional locks and lazy fetches are disabled. Promisor repositories are rejected. Local repository configuration remains trusted. Every subprocess has a deadline, output limit, eight-slot admission, and termination/reaping on failure.

Literal pathspecs prevent globs or shell interpretation. NUL-delimited `ls-tree` metadata handles filenames independently of whitespace; strict UTF-8 names are validated before JSON. [Git reference](https://git-scm.com/docs/git-ls-tree).

## HTTP API

Schema: `/openapi.json` and committed [openapi.json](openapi.json). `/docs` uses Swagger CDN assets. Health/schema are public; every `/v1/` route requires `Authorization: Bearer TOKEN`.

| Route | Result |
| --- | --- |
| `GET /v1/resolve?ref=main` | Full commit and object format; configured refs only. |
| `GET /v1/snapshots/{commit}/manifest?path=src&path=docs` | Canonical manifest; overlapping selections collapse. |
| `GET /v1/snapshots/{commit}/file?path=src/main.py&oid=FULL_ID` | Exact bytes; both path membership and expected object ID must match. |
| `GET /v1/diff?base=COMMIT&head=COMMIT` | A/M/D/T changed paths between authorized trees; renames become add/delete. |
| `GET /v1/stats` | Cache counters and request timing. |

Only full commit IDs work; Git revision expressions are rejected. A commit must equal or precede a current configured tip. Checks run before reading manifests or serving cache hits, including conditional ETag responses. Concurrent ref changes or GC are not part of one transaction; updates become visible at the next check. Already delivered bytes cannot be revoked.

Responses use `Cache-Control: no-store`; reuse happens in the explicit blob cache. The client requires HTTPS except on loopback, ignores environment proxies and follows no redirects. Stable error codes cover 401 authentication, 404 object/ref/path availability, 409 identity mismatch, 413 output limits, 422 unsafe selections, 503 admission and 504 deadlines. Parameter validation uses FastAPI's 422 schema. Errors omit source text and credentials.

## Integrity and publication

A manifest carries version, commit, object format, selected paths, and file paths/modes/sizes/object IDs. Its digest is SHA-256 of canonical JSON. This checks consistency; it does not provide an independently authenticated Merkle inclusion proof. Trust comes from TLS and a trusted server, with optional commit IDs obtained through a trusted channel.

Server and client independently hash blobs using Git's `blob SIZE\0` header and SHA-1/SHA-256 object format. Every client cache hit is reverified. The client checks sizes, commit identity and selected paths, and rejects symlinks, metadata paths, file/directory collisions and case aliases.

Workers write private staging under the destination parent. After all succeed, rename publishes a new output directory. On failure, workers finish and staging is removed; valid cached objects remain. Parent ownership and ordinary same-directory rename semantics are required. This is not a power-loss durability or hostile concurrent filesystem guarantee.

## Resource budgets

| Resource | Default / bound |
| --- | --- |
| Admitted HTTP handlers | 16; Uvicorn also limits connections/tasks to 32 |
| Git subprocesses | 8; admission wait 100 ms |
| Git deadline / output | 10 s / 8 MiB |
| Snapshot | 32 selections, 5,000 files, 128 MiB total |
| File / path | 8 MiB / 1,024 UTF-8 bytes |
| Query / route | 8,192 bytes / 2,048 characters |
| Server cache | 64 MiB payload and 5,000 objects |
| Client cache | 256 MiB payload and at most 10,000 objects |
| Client concurrency / deadline | 8 / 15 s per request; inactivity may extend failure by one read timeout |
| Latency window | Last 1,000 admitted handlers |

Thirty-two fixed lock stripes collapse server misses without growing a per-object lock map. SQLite transactions serialize cache updates across threads/processes and perform LRU eviction before insertion. Quotas exclude indexes, freelist pages, journals and Python overhead. Cache directories and repository configuration must be trusted. Separate caches by trust domain and apply filesystem quotas where needed.

## Observability

Each service has its own OTel provider. Admitted requests emit `gateway.request` with route template, status and response-body bytes. Private JSONL includes trace/span IDs and duration; omits exception text, source paths, query strings, response contents and credentials. There are no LLM calls.

Stats expose cumulative request/error/byte counts and nearest-rank p50/p95 over the latest 1,000 handlers. Timing ends when a response is prepared, excluding delivery over the socket. Receipts measure full client materialization. Admission and oversized-query rejections are excluded from handler stats. Operators own trace rotation and retention.

## v0.1 scope

A complete Git repository still lives on the server. There is no distributed metadata, directory ACL, remote mirror scheduler, write API, virtual filesystem, LFS resolver, offline manifest store, range resumption or GC coordination. Editing downloaded files does not create a commit. These boundaries keep the first service useful and inspectable.

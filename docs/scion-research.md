# Scion research and our design decision

Reviewed 2026-10-16. Primary source: Anthropic, [Redesigning source control at Anthropic](https://www.anthropic.com/engineering/redesigning-source-control), published 2026-10-13.

## Published mechanism

Scion moves source-control operations to an online service. Changesets record changed files and content-addressed blob references. Derived indexes support history and blame. Clients request missing immutable trees and blobs through layered caches and materialize needed paths. Anthropic describes the design as specific to enormous monorepos, rather than a general Git replacement.

## Our inference and target problem

The transferable idea is a small read interface. Our user runs repeated documentation, analysis or agent jobs needing fixed subsets of a Git repository. Those jobs should be able to ask for files or changed-path metadata without maintaining individual clones.

We chose a read-only gateway because teams can adopt it alongside existing Git hosting and reviews. The operator keeps one complete repository; clients receive manifests and reuse verified objects. This is our engineering decision. The article does not describe or validate this implementation.

## Existing alternatives

[Git clone](https://git-scm.com/docs/git-clone) already supports shallow history, filtering and sparse checkout. A shallow filtered sparse clone is a serious benchmark baseline. [Git archive](https://git-scm.com/docs/git-archive) exports paths and supports remote operation where permitted. The gateway is useful when jobs also need an HTTP query API or persistent verified object reuse.

Implementation uses documented [`ls-tree`](https://git-scm.com/docs/git-ls-tree) and [`cat-file`](https://git-scm.com/docs/git-cat-file) interfaces. It does not reproduce Scion's write-ahead log, derived indexes, distributed storage or merge workflows. We have not inspected private code or measured Anthropic infrastructure.

## Acceptance criteria

1. Anyone can run a complete demo with `make demo`.
2. Snapshots contain selected paths from one pinned commit, with independent content verification.
3. Repeated jobs reuse verified objects; corruption causes refetch or clean failure.
4. Cached objects cannot bypass authentication or commit authorization.
5. Benchmarks include optimized Git baselines, shared costs and slower cases.

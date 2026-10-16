# Production engineering evidence

| Requirement | Evidence |
| --- | --- |
| Concrete problem and adoption boundary | README; Scion research; optimized Git alternatives |
| Open source | MIT license; public source; English/Spanish introduction |
| Single-command setup/run | `make demo`; `make local`; hash-locked dependencies |
| Containerized tests | `make test`; read-only filesystem, dropped capabilities, no external runtime network |
| Data integrity | Canonical manifests; independent Git object hashing; SHA-1/SHA-256 tests |
| Failure atomicity | Private staging, full verification, rename publication, failure cleanup tests |
| Authorization | Required token on all data routes; reachable commits; checks before cache hits/304 |
| Bounded resources | Admission, subprocess limits, snapshot quotas, bounded LRUs, container budgets |
| Observability | Per-instance OTel, private JSONL, p50/p95, byte and cache counters |
| Regression checks | Python 3.12–3.14; lint/types/coverage >=90%; container execution; wheel smoke test |
| Measured tradeoffs | Raw samples + source hashes; shallow/partial Git and archive baselines |
| Honest scope | No Scion compatibility, distributed storage, directory ACL, multi-tenant or scale claims |

Passing this checklist is evidence of specific engineering controls, not certification of suitability for every deployment. See failure modes in the README and deployment guide.

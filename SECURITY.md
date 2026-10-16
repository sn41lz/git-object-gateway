# Security policy

This is an early, single-trust-domain read service. Use a dedicated operator-owned repository, a strong private token, TLS beyond loopback, and private cache/log directories. Review [the deployment boundary](docs/deployment.md) before hosting sensitive code.

The token authorizes all history reachable from configured refs. This version has no path ACLs, tenant isolation, identity provider, per-user rate limit or independent security audit. Cached/downloaded bytes cannot be revoked. Snapshot manifest hashes establish consistency, not a proof against a malicious server.

For a suspected vulnerability, use GitHub's private vulnerability reporting if available. Otherwise open an issue requesting a private reporting channel without including exploit details, source contents, tokens or sensitive URLs. Ordinary reproducible bugs can be public. Only the current main branch is maintained during v0.1 development.

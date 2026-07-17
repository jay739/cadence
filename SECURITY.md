# Security policy

Cadence is a self-hosted service. If you find a security issue, please report it privately
rather than opening a public issue, so users running it do not get exposed before a fix ships.

## Reporting

Open a private security advisory on this repository (Security tab -> Report a vulnerability),
or contact the maintainer directly at the address in the repository's commit history. Include:

- What you found and why it is a security issue (not just a bug).
- Steps to reproduce, if applicable.
- Impact: what an attacker could actually do with it.

## Scope

In scope:

- Auth bypass (session tokens, admin routes, the approval queue).
- SSRF via search queries or configuration reaching an internal address.
- Path traversal in the library file-move/tag-writing step.
- Secrets or API keys leaking into logs, responses, or the repository.
- Rate-limit gaps on auth or search endpoints.

Out of scope:

- Issues that require an attacker to already have admin credentials or filesystem access to
  the host.
- Denial of service against the operator's own third-party services (Prowlarr, qBittorrent,
  slskd) that Cadence proxies to; that is a configuration and network-isolation concern for
  the operator's own deployment.

## Supported versions

Only the latest tagged release is supported. Self-hosters should update promptly when a
security-relevant release ships.

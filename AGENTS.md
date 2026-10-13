# AI Coding Agent Instructions for pp swich

The source of truth for architecture and implementation is [`docs/00-START-HERE.md`](docs/00-START-HERE.md).
Mandatory security controls are [`docs/05-SECURITY.md`](docs/05-SECURITY.md),
[`docs/18-SECRETLESS-DEVELOPMENT.md`](docs/18-SECRETLESS-DEVELOPMENT.md) and
[`docs/16-AGENT-DEVELOPMENT-RULES.md`](docs/16-AGENT-DEVELOPMENT-RULES.md).

## Required behavior

1. Work only with synthetic fixtures, mock services and commit-safe configs.
2. Never read or output `.env`, tokens, credentials, browser session state, OS Keyring contents or Vault secrets.
3. Never create public `get_secret`, `proxy_any_url`, `run_shell` or `execute_any_sql` tools.
4. Trust identity only from validated server-side authentication, never from LLM arguments.
5. All API discovery creates **draft candidates**, not directly executable tools.
6. All tool calls must pass explicit policy and use immutable capability/profile snapshots.
7. Keep real downstream secrets inside trusted Executor / SecretProvider. The public SDK offers only secretless invocation APIs.
8. If the coding agent shares the same OS identity/network as a production runtime, do not claim secrets are protected. Require isolation.
9. Every feature PR includes tests, security implications and documentation updates.
10. Never bypass authentication or disable safety controls to “make the demo work”.

## Implementation order

`domain models → DB migrations → sanitized HAR import → API registry → Profile switch → policy → secretless executor → MCP adapter → browser extension`.

Documentation is a blueprint, not existing code. Create the code and validate dependencies through lockfiles and CI.

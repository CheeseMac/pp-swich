# Security Policy (Pre-alpha)

`pp swich` is currently a **design / pre-alpha** project. No production security guarantees are claimed until implementations, tests and audits are available.

## Reporting vulnerabilities

Do **not** file public GitHub issues containing API Keys, passwords, authentication tokens, HAR captures with personal data, secret-bearing logs or exploit details.

Before public release, maintainers should configure GitHub Private Vulnerability Reporting and publish an official security contact. Until that mechanism exists, avoid sharing sensitive production material with this repository.

## Security design goals

- Prevent downstream API credentials from appearing in LLM prompts/tool results and normal logs.
- Enable secretless application development using aliases and brokered, authenticated Capability invocation.
- Separate AI coding agents from production credentials, execution identities and networks.
- Enforce least privilege, response projection, reviewed API publication and audit logging.

See [`docs/05-SECURITY.md`](docs/05-SECURITY.md) and
[`docs/18-SECRETLESS-DEVELOPMENT.md`](docs/18-SECRETLESS-DEVELOPMENT.md) for details.

## Important limitation

Keeping keys outside `.env` is **not sufficient** if an AI coding agent can run arbitrary code as the same user or container that holds a privileged Broker connection. Runtime identity, socket permissions and network separation must be enforced and tested.

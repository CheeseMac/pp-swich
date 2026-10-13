# Contributing to pp swich

Thank you for helping build an open, secure API discovery and Agent integration platform.

## Before contributing

- Read [START HERE](docs/00-START-HERE.md), [Architecture](docs/02-ARCHITECTURE.md), [Security](docs/05-SECURITY.md), and [AI Agent Rules](docs/16-AGENT-DEVELOPMENT-RULES.md).
- Open an issue proposing significant architectural changes; record decisions in [ADR](docs/13-DECISIONS.md).
- Add tests and docs for behavior changes.
- Use only synthetic data and mock credentials. Never upload real HAR, Cookie, Token, or `.env` secrets.

## Pull request checklist

- [ ] Clear problem statement and scope
- [ ] Tests added or updated
- [ ] Permission and secret-leak risks considered
- [ ] No raw secrets in source, output, logs or snapshots
- [ ] Documentation / API schemas updated
- [ ] Migration and rollback notes if applicable
- [ ] Security reviewer approval for policy, vault or executor changes

## License

License selection is proposed as Apache-2.0. The maintainers must add a complete LICENSE file before representing the project as licensed.

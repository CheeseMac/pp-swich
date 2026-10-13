## What changed

<!-- short summary of the change -->

## Security implications

<!-- per docs/16: every PR states impact on permissions, sensitive data,
     output fields and abuse potential. Write "none" only if truly none. -->

- [ ] No new public secret/URL/SQL/shell surface
- [ ] Workspace scoping tested (cross-tenant reads stay 404)
- [ ] Output projections reviewed (no new fields without allowlist)
- [ ] Reserved-argument policy unchanged or extended with tests

## Definition of Done (docs/09 section 10)

- [ ] Tests added / updated and passing
- [ ] DB changes via Alembic migration
- [ ] CI green (lint / typecheck / tests / secret scan)
- [ ] Docs updated for behavior changes

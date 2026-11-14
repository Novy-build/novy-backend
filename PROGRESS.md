# Novy Backend Progress

**Last reviewed:** 2026-11-14

This is the current-state inventory for the backend workspace. Planned work is not implemented work;
see `BACKEND_PLAN.md` for the approved architecture and delivery sequence.

## Workspace Status

- [x] Reviewed the available product and integration handoff, including the sibling web
  application's DTOs, repositories, mappers, validation, and tests.
- [x] Established backend-specific engineering rules in `AGENTS.md`.
- [x] Saved the approved backend architecture and phased delivery plan in `BACKEND_PLAN.md`.
- [x] Replaced the active frontend README with a backend-specific workspace overview.
- [x] Preserved the supplied frontend-only documents as historical material under
  `docs/frontend-handoff/`; they are not backend policy or implementation evidence.
- [x] FastAPI app factory, Vercel entrypoint, validated environment settings, and versioned
  liveness route. The endpoint reports process liveness only.
- [x] Python 3.12 package configuration and dependency lock, isolated local environment,
  pytest, Ruff, and mypy configuration with focused scaffold tests.
- [ ] Production API contracts, readiness checks, CI, and target-runtime deployment verification.
- [ ] MongoDB repositories, indexes, migrations, transactions, backups, and live Atlas verification.
- [ ] Supabase Auth integration, builder/app-user sessions, CSRF, and account status enforcement.
- [ ] Project and data APIs, revision conflict handling, template provisioning, import, and duplicate.
- [ ] Isolated preview data, strict preview validation, hosted runtime, and project access rules.
- [ ] Verified Vercel deployment and same-origin routing through a real browser.

The implementation currently covers the application foundation and liveness route only. The
application does not connect to MongoDB or Supabase, expose authentication or project APIs, or host
prototypes. Historical frontend documents describe integrations and verification in other contexts;
those claims have not been verified against a backend implementation here.

## Approved Direction

- **Stack:** FastAPI, MongoDB Atlas (`platform_db`), Supabase Auth, Clean Architecture, and Vercel.
- **First release:** hosted prototypes activate and write only preview data; live application
  activation, standalone export, and external deployments are deferred.
- **Authentication:** separate builder and shared app-user identity realms, server-side opaque
  sessions, session-bound CSRF bootstrap, explicit refresh, and current `is_active` checks for every
  applicable account and project/environment membership.
- **Runtime access:** default deny, with explicit `public` operations or `owner_only` access for the
  project builder; per-project logical `_users` memberships and project-bound runtime sessions.
- **Project saves:** loose, structurally safe drafts; strict validation before preview/publish;
  atomic revision checks returning HTTP 409 `stale_revision` on conflict.
- **Data migration:** duplicate schemas by default, optionally copy records, and allocate fresh
  MongoDB ObjectIds for imported persisted resources with typed reference remapping.

These are approved decisions and future work, not completed features.

## Verification

- Local scaffold tests: 22 passed on Python 3.12.16.
- Ruff lint and format checks, mypy on source and tests, and `uv lock --check` passed locally.
- A local Uvicorn HTTP smoke test returned `200 {"status":"alive"}`.
- Vercel deployment and rewrites, real MongoDB transactions/indexes, and live Supabase behavior
  have not been verified. Docker was not available for a local MongoDB replica set.

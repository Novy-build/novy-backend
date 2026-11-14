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
- [x] Configured PyMongo Async client and `platform_db` selection within FastAPI lifespan, with
  bounded connection settings, TLS validation, and credential-redaction tests.
- [x] A local, read-only Atlas server ping succeeded across three fresh client lifecycles.
- [x] An opt-in local Atlas test verified `platform_db` collection creation, read/write/delete,
  unique-index enforcement, transaction commit, and observed rollback in an isolated scratch
  collection. Synthetic records were removed; the scratch collection and index were retained.
- [x] The configured Supabase URL and publishable key passed a local, read-only Auth settings
  smoke test; an invalid control key was rejected. This is not an implemented auth integration.
- [x] Internal builder-profile model, repository, and active-status lookup, with an explicitly
  provisioned unique issuer/subject index in `platform_db.builder_profiles`.
- [x] An isolated Atlas profile test verified concurrent duplicate protection, issuer separation,
  a majority-acknowledged disable followed by a denied lookup, and malformed-status rejection.
- [ ] Production API contracts, readiness checks, CI, and target-runtime deployment verification.
- [ ] Project/data repositories, remaining indexes and migrations, transaction failure and failover
  coverage, database-specific authorization rules, and backups.
- [ ] Supabase Auth integration, builder/app-user sessions, CSRF, and account status enforcement.
- [ ] Project and data APIs, revision conflict handling, template provisioning, import, and duplicate.
- [ ] Isolated preview data, strict preview validation, hosted runtime, and project access rules.
- [ ] Verified Vercel deployment and same-origin routing through a real browser.

The implementation covers the application foundation, MongoDB client lifecycle, and an internal
builder-profile repository. The actual builder-profile collection and index were created, but no
real profile was provisioned. There are no authentication or project APIs, Supabase integration,
browser sessions, or hosted prototypes. Historical frontend documents describe other contexts and
are not evidence of working backend features here.

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

- Default local suite: 232 passed and two live integration tests skipped on Python 3.12.16.
- The explicitly enabled live `platform_db` integration test passed on the configured Atlas cluster.
  It verified CRUD, unique-index enforcement, a committed two-document transaction, and rollback
  visibility with ID reuse; the driver's abort acknowledgement was not established. Its synthetic
  records were removed, and its empty scratch collection and index were intentionally retained.
- Ruff lint and format checks, mypy on source and tests, and `uv lock --check` passed locally.
- A local Uvicorn HTTP smoke test returned `200 {"status":"alive"}`.
- An initial read-only Atlas ping failed with a TLS internal alert. Subsequent TLS checks and three
  read-only pings across fresh client lifecycles succeeded locally; the earlier failure's cause is
  unknown. The sample collection was not read or changed.
- Supabase Auth settings returned HTTP 200 for the configured publishable key and HTTP 401 for an
  invalid control key. Email sign-up was enabled and email auto-confirm was disabled. The public
  JWKS advertised one ES256 key, which alone does not establish the active user-token signing key.
  No user was registered or authenticated, and email delivery was not tested.
- The opt-in Atlas builder-profile test passed using a separate scratch collection. It checked the
  actual compound index, concurrent duplicate creation, issuer isolation, a majority-acknowledged
  disable and subsequent linearizable lookup, and malformed-status rejection. Synthetic profiles
  were removed; the empty scratch collection and index were retained.
- The actual `platform_db.builder_profiles` collection and its identity index were provisioned and
  verified; repeating index setup succeeded. The collection had no records when checked. No Atlas
  Compass UI action or authenticated HTTP request was tested.
- Vercel deployment and rewrites, real application-level transaction/index behavior and failure
  modes, and live Supabase user-authentication behavior have not been verified. Docker was not
  available for a local MongoDB replica set.

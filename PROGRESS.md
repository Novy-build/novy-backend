# Novy Backend Progress

**Last reviewed:** 2026-11-22

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
  smoke test; an invalid control key was rejected. The Supabase Auth client, JWT validator, and
  `BuilderAuthService` are implemented but not verified against live Supabase.
- [x] Internal builder-profile model, repository, and active-status lookup, with an explicitly
  provisioned unique issuer/subject index in `platform_db.builder_profiles`.
- [x] An isolated Atlas profile test verified concurrent duplicate protection, issuer separation,
  a majority-acknowledged disable followed by a denied lookup, and malformed-status rejection.
- [x] Internal builder-session service with independent encryption and lookup keys, encrypted token
  storage, seven-day idle and 30-day absolute expiry, revocation, and current profile checks.
- [x] Explicit session-index provisioning, a host-only secure cookie helper, and an isolated Atlas
  test of session persistence, expiry, and concurrent touch/revoke behavior.
- [ ] Production API contracts, readiness checks, CI, and target-runtime deployment verification.
- [ ] Project/data repositories, remaining indexes and migrations, transaction failure and failover
  coverage, database-specific authorization rules, and backups.
- [x] Supabase Auth integration, CSRF protection, and protected HTTP account checks — auth service
  layer, auth routes (`/auth/csrf`, `/auth/register`, `/auth/login`, `/auth/session`, `/auth/refresh`,
  `/auth/logout`), CSRF service, origin guards, and session cookie handling are implemented and
  unit-tested. Real-browser and live-Supabase verification remain pending.
- [ ] Project and data APIs, revision conflict handling, template provisioning, import, and duplicate.
- [ ] Isolated preview data, strict preview validation, hosted runtime, and project access rules.
- [ ] Verified Vercel deployment and same-origin routing through a real browser.

The implementation covers the application foundation, MongoDB client lifecycle, internal
builder-profile and session components, CSRF protection, and Supabase Auth integration. The
builder-profile and session collections and indexes were provisioned on `platform_db`. Authentication
routes (`/auth/csrf`, `/auth/register`, `/auth/login`, `/auth/session`, `/auth/refresh`,
`/auth/logout`) and CSRF/origin guards are implemented and unit-tested, but no real profile or
browser session has been provisioned against live Supabase. There are no project APIs, hosted
prototypes, or real-browser verification yet. Historical frontend documents describe other contexts
and are not evidence of working backend features here.

## Approved Direction

- **Stack:** FastAPI, MongoDB Atlas (`platform_db`), Supabase Auth, Clean Architecture, and Vercel.
- **First release:** hosted prototypes activate and write only preview data; live application
  activation, standalone export, and external deployments are deferred.
- **Authentication:** separate builder and shared app-user identity realms, server-side opaque
  sessions, session-bound CSRF bootstrap, explicit refresh, and current `is_active` checks for every
  applicable account and project/environment membership.
- **Builder session lifetime:** seven days of inactivity with a 30-day absolute maximum.
- **Runtime access:** default deny, with explicit `public` operations or `owner_only` access for the
  project builder; per-project logical `_users` memberships and project-bound runtime sessions.
- **Project saves:** loose, structurally safe drafts; strict validation before preview/publish;
  atomic revision checks returning HTTP 409 `stale_revision` on conflict.
- **Data migration:** duplicate schemas by default, optionally copy records, and allocate fresh
  MongoDB ObjectIds for imported persisted resources with typed reference remapping.

These are approved decisions and future work, not completed features.

## Verification

- Default local suite: 654 passed and three live integration tests skipped on Python 3.12.16.
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
- The opt-in Atlas session test passed with synthetic tokens in a separate scratch collection. It
  checked index configuration and uniqueness, encrypted storage, idle expiry, and an atomic
  touch/revoke race across clients. Synthetic records were removed; the empty scratch collection
  and indexes were retained. Actual TTL-monitor deletion was not tested.
- The actual `platform_db.builder_sessions` collection and indexes were provisioned and verified;
  repeating index setup succeeded. No real builder session was created, and Supabase login,
  provider refresh, and deployed cookie behavior remain unverified.
- CSRF and authentication routes are implemented and covered by unit tests (CSRF service, pre-auth
  issue/verify, origin allow-listing, session cookie attributes, auth endpoint error mapping).
- Vercel deployment and rewrites, real application-level transaction/index behavior and failure
  modes, real-browser cookie/CSRF flow, and live Supabase user-authentication behavior have not been
  verified. Docker was not available for a local MongoDB replica set.

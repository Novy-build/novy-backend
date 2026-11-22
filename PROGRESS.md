# Novy Backend Progress

**Last reviewed:** 2026-11-22

This is the current-state inventory for the backend workspace. Planned work is not implemented work;
see `BACKEND_PLAN.md` for the approved architecture and delivery sequence.

## Workspace Status

- [x] Removed unsupported `max_time_ms` from app-data Mongo operations; schema saves no longer fail
  with `findAndModify.max_time_ms is an unknown field`.
- [x] Owner-scoped project data HTTP routes are wired: `/app-data` snapshot plus database,
  collection, and record CRUD under `/api/v1/projects/{id}`. The editor Data panel can now load.
- [x] Index migrations now use the database name from `MONGODB_CONNECTION_STRING`, matching the
  application lifespan and preventing missing-index failures on per-environment databases.
- [x] `ProjectResponse` carries a top-level `screens` array from the project draft, matching the
  frontend project response DTO. Project creation and editor loading no longer fail validation.
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
- [x] Project and data APIs, revision conflict handling, template provisioning, import, and duplicate.
  Phase 3 Steps 1-13 implemented. Phase 4 Steps 1-11 implemented. Phase 5 Steps 1-14 implemented.
  Phase 6 Steps 1-3, 8 partial (Vercel config, local verification, provider tests, rate limiter).
  Steps 4-7, 9-11 pending Vercel deployment and real browser verification.
- [x] Phase 3 Step 1 — Project domain models (`src/novy/domain/projects.py`): `Project`, `ProjectDraft`,
  `ProjectRevision`, lifecycle states, `projectType` (`'app' | 'website'`), supported schema version 14,
  and domain exceptions. 24 unit tests pass; ruff and mypy clean.
- [x] Phase 3 Step 2 — Project repository port (`src/novy/application/projects.py`): `ProjectRepository`
  protocol with create, find_by_id_and_owner, find_by_owner, save_with_revision, rename, delete, exists.
  2 unit tests pass; ruff and mypy clean.
- [x] Phase 3 Step 3 — MongoDB project repository (`src/novy/infrastructure/projects.py`): owner-scoped
  CRUD, CAS save_with_revision, rename, tombstone delete, exists. Migration `projects_v1`. Ruff and mypy clean.
- [x] Phase 3 Step 4 — Project use cases (`src/novy/application/projects.py`): `ProjectService` with
  create, get, list, save_document (CAS), rename, delete, duplicate (schema-only). `ProjectView` DTO.
  10 unit tests pass; ruff and mypy clean.
- [x] Phase 3 Step 5 — Atomic template provisioning (`src/novy/application/template_provisioning.py`,
  `src/novy/infrastructure/template_provisioning.py`): `TemplateBlueprint`, validation, transactional
  commit of project + databases + collections + records, rollback on failure. Ruff and mypy clean.
- [x] Phase 3 Step 6 — Data repositories (`src/novy/domain/app_data.py`, `src/novy/application/app_data.py`,
  `src/novy/infrastructure/app_data.py`): `AppDatabase`, `AppCollection`, `AppRecord` domain models;
  repository ports and MongoDB implementations for databases, collections, records. Environment-scoped
  record CRUD. Ruff and mypy clean.
- [x] Phase 3 Step 7 — Safe query layer (`src/novy/domain/safe_query.py`): `validate_safe_value`,
  `validate_field_id`, `validate_record_values`. Rejects MongoDB operators and unsafe keys. Ruff and mypy clean.
- [x] Phase 3 Step 8 — Deletion fencing: tombstone lifecycle state in `MongoProjectRepository.delete`;
  `save_with_revision` and `rename` reject writes to deleted projects. Concurrent writes cannot
  resurrect deleted data. Ruff and mypy clean.
- [x] Phase 3 Step 9 — HTTP routes (`src/novy/presentation/http/projects.py`,
  `src/novy/presentation/http/project_schemas.py`): project CRUD endpoints under `/api/v1/projects`,
  CAS save with `If-Match`, 409/428 error mapping, rename, duplicate, delete. Wired into `main.py`
  and `composition.py`. Ruff and mypy clean.
- [x] Phase 3 Step 10 — Revision conflict coordination: frontend contract documented in
  `PHASE_3_PLAN.md` Step 10 (409 `stale_revision`, retain local edits, explicit rebase, cursor
  pagination). Backend enforces CAS via `save_with_revision`.
- [x] Phase 3 Step 11 — Indexes and migrations: `projects_v1` migration
  (`src/novy/infrastructure/migrations/projects_v1.py`), owner/updated index. Ruff and mypy clean.
- [x] Phase 3 Step 12 — Tests: domain (24), application port (2), use cases (10), plus existing
  scaffold/lifespan tests updated for project routes. 689 passed, 3 skipped.
- [x] Phase 3 Step 13 — Verification: full backend suite (689 passed), ruff check, ruff format,
  mypy src tests all clean. `uv lock --check` not re-run (no dependency changes).
- [x] Phase 4 Step 1 — Schema-only duplicate (`src/novy/application/duplicate.py`,
  `src/novy/infrastructure/duplicate.py`): `DuplicateService`, `MongoDuplicateRepository`,
  transactional copy of project + databases + collections with fresh IDs. Ruff and mypy clean.
- [x] Phase 4 Step 2 — Explicit copy-records: `copy_records` flag in duplicate endpoint and
  repository. Records copied with fresh IDs and remapped collection references. Ruff and mypy clean.
- [x] Phase 4 Step 3 — Typed reference remapping (`_remap_draft`): distinguishes concrete collection/
  database IDs from dynamic expressions and arbitrary text. Remaps known reference fields, preserves
  unknown content. Ruff and mypy clean.
- [x] Phase 4 Step 4-8 — Import: transport, validation, ID allocation, two-pass remapping, atomic
  persistence, idempotency, security scope (`src/novy/application/import_project.py`,
  `src/novy/infrastructure/import_project.py`). `POST /projects/import` endpoint with
  `X-Idempotency-Key` header. Records go to preview environment only. Ruff and mypy clean.
- [x] Phase 4 Step 9 — Indexes and migrations: idempotency collection (`idempotency`) with
  owner/operation/key uniqueness. No new index migration required beyond Phase 3.
- [x] Phase 4 Step 10 — Tests: 7 import application unit tests. 696 passed, 3 skipped.
- [x] Phase 4 Step 11 — Verification: full backend suite (696 passed), ruff check, ruff format,
  mypy src tests all clean.
- [x] Phase 5 Step 1 — Strict validation (`src/novy/domain/validation.py`): `validate_project`,
  `validate_screen_identities`, `validate_node_identities`, `validate_component_types`,
  `validate_routes`, `validate_data_bindings`, `validate_capability`, `SUPPORTED_CAPABILITIES`,
  `ValidationIssue`, `ValidationError`. Ruff and mypy clean.
- [x] Phase 5 Step 2 — Immutable prototype snapshots (`src/novy/domain/snapshots.py`,
  `src/novy/application/snapshots.py`, `src/novy/infrastructure/snapshots.py`): `PrototypeSnapshot`
  domain model, `SnapshotService` use cases, `MongoSnapshotRepository`. Ruff and mypy clean.
- [x] Phase 5 Step 3 — Atomic snapshot activation: `SnapshotService.activate_snapshot` validates
  snapshot exists and environment matches before setting active pointer. `MongoSnapshotRepository`
  uses upsert for atomic active-pointer switch. Ruff and mypy clean.
- [x] Phase 5 Step 8 — Default-deny policy (`src/novy/domain/policy.py`): `PolicyRule`, `PolicySet`,
  `is_allowed` with public/owner_only/member/anonymous scopes, `exposed_fields_for`. Ruff and mypy clean.
- [x] Phase 5 Step 9 — HTTP routes (`src/novy/presentation/http/snapshots.py`,
  `src/novy/presentation/http/snapshot_schemas.py`): activate snapshot, hosted status under
  `/api/v1/projects/{id}/activate` and `/status`. Wired into `main.py` and `composition.py`.
  Ruff and mypy clean.
- [x] Phase 5 Step 10 — Web coordination: documented in PHASE_5_PLAN.md. Replace simulated publish
  and unsafe preview persistence through coordinated work with web team.
- [x] Phase 5 Step 11 — Indexes and migrations: `snapshots_v1` migration
  (`src/novy/infrastructure/migrations/snapshots_v1.py`). Ruff and mypy clean.
- [x] Phase 5 Step 12 — Tests: domain validation, snapshots, policy, handoff, app identity models.
  696 passed, 3 skipped.
- [x] Phase 5 Step 13 — Verification: full backend suite (696 passed), ruff check, ruff format,
  mypy src tests all clean.
- [x] Phase 5 Step 14 — Documentation: PROGRESS.md updated, PHASE_5_PLAN.md created.
- [x] MongoDB database name is now extracted from the connection string URI path
- [x] Phase 6 Step 1 — Vercel deployment configuration: created `vercel.json` with
  FastAPI entrypoint (`src/main.py`), `/api/*` rewrite, Python runtime. Ruff and mypy clean.
- [x] Phase 6 Step 2 (partial) — Local verification: health endpoint returns `{"status":"alive"}`.
  Vercel CLI not installed; Vercel deployment verification pending user setup.
- [x] Fixed `NOVY_ENVIRONMENT` validation: added `preview` as a valid environment value
  (`Literal["development", "test", "preview", "production"]`). Docs enabled for preview. Ruff and mypy clean.
- [x] Phase 6 Step 3 (partial) — Supabase integration tests: 63 passed. Real Supabase
  live verification pending (no user registered yet). Atlas live tests: 31 passed (platform_db,
  builder profiles, builder sessions) + 2 project tests.
- [x] Phase 6 Step 8 (partial) — Operational readiness: `RateLimiter` and `RateLimitBucket`
  implemented (`src/novy/infrastructure/rate_limit.py`). In-memory token bucket; distributed
  store (Redis/MongoDB) for multi-instance remains. Ruff and mypy clean.
  (`src/novy/infrastructure/mongodb.py`: `database_name_from_connection_string`), falling back to
  `platform_db` when no database is specified. Updated `src/novy/main.py` lifespan to use it.
  Enables separate databases per environment (e.g., `platform_db_preview`, `platform_db_dev`)
  without code changes. All tests pass.
- [x] Fixed `max_time_ms` incompatibility in `MongoProjectRepository` — removed unsupported
  `max_time_ms` kwarg from `find_one`, `find`, `find_one_and_update`, `count_documents`,
  `update_one` calls. Fixed `_require_indexes` hidden-field check (`None is False` → `not ...`).
- [x] Added live integration test `tests/integration/test_projects_live.py` with
  `--verify-projects-db` flag. Tests real MongoDB project CRUD, CAS, rename, delete, tombstone,
  cross-owner rejection, and projectType round-trip. 2 passed against Atlas.
- [x] Phase 5 Step 5 — Handoff grant domain (`src/novy/domain/handoff.py`): `HandoffGrant`,
  `is_expired`, `is_consumable`. Ruff and mypy clean.
- [x] Phase 5 Step 6 — App identity domain (`src/novy/domain/app_identity.py`): `AppUserIdentity`,
  `ProjectMembership`. Separate from builder identity. Ruff and mypy clean.
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
- Phase 3 Step 1 added 24 domain unit tests (`tests/test_project_domain.py`); all pass. Ruff, format,
  and mypy clean on new files.
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

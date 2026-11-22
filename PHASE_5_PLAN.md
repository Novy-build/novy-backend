# Phase 5 Plan: Preview, Runtime and Publication

**Repository:** novy-backend
**Status:** Proposed for review
**Depends on:** Phases 2-4 (Builder auth, projects/data, import/duplicate), strict capability/policy contract, authorized renderer/frontend implementation
**Backend plan reference:** `BACKEND_PLAN.md`, Section 11, Phase 5

---

## Goal

Implement strict validation, bounded atomic immutable prototype snapshots, environment-scoped preview/live records, narrow builder handoff, separate generated-app identity/membership and runtime sessions, default-deny per-operation policy, and hosted activation/status. Replace simulated publish and unsafe preview persistence in the web application through coordinated work.

---

## Acceptance Criteria (from BACKEND_PLAN.md)

- A real browser exercises the supported Notes flow end-to-end.
- Concurrent snapshot edits either activate one consistent version or conflict; failure leaves the prior prototype snapshot usable.
- Preview testers cannot read/write live records; changes never copy to live implicitly.
- Public operations work only when individually enabled; `owner_only` admits only the actual active project builder via the narrow runtime session.
- Wrong realm, disabled membership, stale policy, and cross-project grants are denied on the next request.

---

## Step-by-Step Process

### Step 1 — Strict validation

Before preview or publish, validate the exact draft and schema revision:

- Screen and node identities (unique, non-empty, no duplicates)
- Routes and parameters (valid paths, parameter types)
- Responsive layout (valid breakpoints, geometry within bounds)
- Logic graph references and ports (valid node IDs, port types, no cycles)
- Data bindings (valid collection/field references, type compatibility)
- Field types (correct types for collection fields)
- Collection/record references (valid parent-child relationships)
- Policy shape (valid operation names, resource scopes, field exposure)
- Exposed fields (only declared fields exposed)
- Supported runtime capability allowlist (positive v1 list; reject unsupported)

Coordinate issue codes and locations with the frontend validator. Client validation is helpful but never a server trust boundary. Report actionable errors without modifying the last usable snapshot. Never silently simulate an unsupported feature as production functionality.

### Step 2 — Immutable prototype snapshots

Create an immutable snapshot containing:

- Validated project document
- Data schemas (collection definitions)
- Access policies
- Renderer/capability version
- Source revision

Bound the number and size of documents and transaction work. A snapshot's configuration never changes. The preview dataset used by testers is a separate mutable set of records scoped to `project_id + preview`. Live records are separately scoped to `project_id + live`. V1 runtime writes affect preview records only; they do not alter a snapshot or leak into live data.

Collection: `platform_db.snapshots`

Indexes: `(project_id, environment, snapshot_id)`; active pointer switched atomically; bounded contents.

### Step 3 — Atomic snapshot creation and activation

- Read a consistent view and atomically create the snapshot and switch the active prototype pointer.
- Use project/configuration revision fences that are also updated by concurrent schema and policy changes.
- Include a preview-data fence if validation depends on particular preview records.
- CAS/write the relevant fence in the transaction so an edit cannot slip between validation and activation under snapshot isolation.
- Abort/retry or report a conflict if the source changes.
- If multiple snapshot documents are needed, none may be visible via the active pointer until the complete bounded transaction commits.
- Failed validation, transaction, or activation preserves the previously active prototype and data.

Prototype activation must not reset existing tester data without an explicit builder action; any initial template seeding or reset is limited to the preview environment. Never automatically copy preview records or memberships into live data.

### Step 4 — Environment-scoped preview/live records

- Every runtime request resolves and reauthorizes against the current active prototype snapshot.
- Preview records are scoped to `project_id + preview`; live records to `project_id + live`.
- Preview membership and live membership are separate unless an explicit approved workflow creates both.
- No implicit copying between environments.

### Step 5 — Narrow builder handoff

The builder's broad session remains on the builder host. After builder authentication, ownership, CSRF, and origin checks:

1. Issue a short-lived, single-use, high-entropy grant bound to target project, product environment, builder, and intended runtime origin.
2. One viable implementation: origin-checked browser `postMessage` handshake to the trusted renderer, followed by a CSRF- and origin-checked grant exchange on the runtime origin.
3. Validate both message origin and source; use exact target origin.
4. Atomically consume the server-stored hashed grant (no replay).
5. Do not place grant in URLs, logs, or reusable browser storage.
6. Issue a separate opaque runtime cookie whose authority is limited to that project and environment.
7. The runtime never receives the builder cookie or provider tokens.

The exact browser flow must be tested, including popup/iframe restrictions. If it cannot meet these properties, do not ship `owner_only` runtime operations.

Collection: `platform_db.handoff_grants` — owner/operation/key/request digest/result or single-use grant binding and expiry. Unique grant hash; expiry index for cleanup only.

### Step 6 — Generated-app identity realm

Separate from builder identity. Shared generated-app Supabase Auth realm across projects (same app-user account uses same password/recovery across apps).

- Signing into or registering in one project does not automatically confer membership in any other project.
- A separate logical `_users` record, unique per project, product environment, and provider subject, controls each membership.
- Project signup/join must be explicitly permitted by that project's policy.
- Handle an already-existing shared identity safely.
- An active app-user identity without active membership is not an authorized member.
- Preview membership and live membership are separate unless an explicit approved workflow creates both.

Collection: `platform_db.app_user_identities` — app-realm issuer/subject, platform-level `is_active`, minimal profile. Unique `(issuer, subject)`.

Collection: `platform_db.project_memberships` — `project_id`, `environment`, app-user subject/identity, `is_active`, server-managed membership data (logical `_users`). Unique `(project_id, environment, identity_id)`.

### Step 7 — Runtime sessions

- Opaque, high-entropy, host-only, secure HttpOnly browser sessions for the runtime origin.
- Session bound to project and environment.
- Separate CSRF token bound to the runtime session.
- Provider tokens kept server-side (not exposed to browser).
- Seven-day idle / 30-day absolute maximum (or as configured for runtime).

### Step 8 — Default-deny per-operation policy

- `public` is a grant for one specific operation on one specific resource, never implicit.
- `owner_only` means the authenticated builder who currently owns the project (via narrow runtime session), not the creator of a data record.
- A signed-in project member does not gain extra collection access merely by being a member.
- Policies must specify resources and operations, with validated field exposure and constraints.
- Do not expose an entire record, membership list, or administrative field merely because an operation is public.
- Anonymous visitors cannot be individually disabled as an account; use rate limits and abuse controls.

Apply project and product-environment scope to every nested read, mutation, aggregate, lookup, background job, and cache key. Verify parent-child relationships. Scope comes from the authenticated builder or authorized runtime session/route and the current active snapshot.

### Step 9 — Hosted activation and status

Implement endpoints:

- `POST /projects/{id}/activate` (or similar) — activate a validated preview snapshot
- `GET /projects/{id}/status` — hosted status and URL

Rules:
- Record hosted status and URL only after activation succeeds.
- A failed update leaves the previous prototype snapshot and URL usable.
- A future live activation workflow requires its own approved scope, schema-compatibility checks, and data migration policy.

### Step 10 — HTTP routes

All under `/api/v1` (builder-facing) and `/runtime/v1` (generated-app-facing):

Builder-facing:
- Preview validation/snapshot activation endpoints
- Prototype activate, status, optional unpublish
- Runtime app-user registration/login/refresh/logout (proxied or coordinated)

Runtime-facing (separate origin):
- Runtime app-user registration/login/refresh/logout
- Allowed data operations per policy
- Separate identity realm, membership authorization, project/environment-bound cookies, CSRF

### Step 11 — Coordinate web application changes

- Replace simulated publish with real server-enforced environment and hosted activation.
- Replace unsafe preview persistence with server-enforced preview records.
- Update frontend to use real activation/status endpoints.
- Coordinate issue codes and locations with frontend validator.

### Step 12 — Indexes and migrations

- `snapshots` collection indexes: `(project_id, environment, snapshot_id)`
- `handoff_grants` collection: unique grant hash, expiry index for cleanup
- `app_user_identities`: unique `(issuer, subject)`
- `project_memberships`: unique `(project_id, environment, identity_id)` and scoped lookup
- Versioned migrations following existing pattern

### Step 13 — Tests

Unit tests:
- Strict validation rules (all categories)
- Policy matrix (public/owner_only/member/anonymous)
- ID maps and reference preservation
- Snapshot consistency and fence logic

Contract tests:
- HTTP/DTO compatibility for activation, status, runtime auth
- Error envelope shapes and codes
- CSRF/origin guards on all mutation routes

Integration tests (real MongoDB):
- Snapshot creation and atomic activation
- Preview/live isolation (testers cannot read/write live records)
- Handoff grant single-use consumption
- Runtime identity realm separation
- Policy enforcement (public/owner_only/member/anonymous)
- Concurrent snapshot edits → one activates, other conflicts
- Failed activation preserves previous prototype

Security/tenancy tests:
- Two builders, two projects, preview/live, app users in different memberships
- Disabled builder/app identity/membership
- Anonymous callers, stale runtime sessions
- Wrong realm/audience/issuer/algorithm
- Cross-project child IDs, forged roles/owners/environments
- CSRF, origin, direct-backend and forwarded-header bypasses

### Step 14 — Verification and documentation

- Run full backend test suite: `pytest`, `ruff check`, `ruff format --check`, `mypy src tests`, `uv lock --check`
- Real browser exercises Notes flow end-to-end
- Deployed integration: Vercel rewrites, browser cookies, Supabase confirmation/login/refresh/logout
- Update `PROGRESS.md` implementation inventory
- Add dated entry under `Unreleased` in `CHANGELOGS.md`

---

## Notes

- Phase 5 is the largest phase — it introduces an entirely new identity realm (generated-app users), runtime sessions, and a hosted renderer.
- The narrow builder handoff is security-critical: the builder's broad session must never reach the runtime origin.
- Preview and live data are server-enforced boundaries; the frontend cannot select or bypass them.
- A future live activation workflow requires its own approved scope, schema-compatibility checks, and data migration policy.
- V1 activates hosted prototype snapshots against preview data only; live application activation is deferred.
- Cross-repository coordination with the web application is required for replacing simulated publish.
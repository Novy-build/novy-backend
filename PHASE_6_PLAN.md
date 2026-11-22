# Phase 6 Plan: Release Readiness

**Repository:** novy-backend
**Status:** Proposed for review
**Depends on:** Phases 0-5 (all prior phases implemented)
**Backend plan reference:** `BACKEND_PLAN.md`, Section 11, Phase 6

---

## Goal

Run the full backend and coordinated frontend checks, threat/permissions review, real provider and Vercel integration tests, operational restore and key-rotation exercises, capacity/timeout checks, and a documented rollout/rollback. Close or explicitly accept remaining risks without calling an untested feature complete.

---

## Acceptance Criteria (from BACKEND_PLAN.md)

- Every release gate in Section 12 has evidence.
- Deployment-preview isolation is verified.
- Live-provider credentials and procedures are owned.
- Product owner accepts any documented limitations.
- Update implementation inventory and changelog as implementation proceeds.

---

## Step-by-Step Process

### Step 1 — Vercel deployment configuration

Create `vercel.json` and configure the FastAPI entrypoint:

- Python runtime selection and entrypoint (`src/main.py` re-exports `app`)
- `/api/*` rewrite to FastAPI (no browser redirect)
- Environment variable configuration (MongoDB, Supabase, session keys, origins)
- Production vs deployment-preview isolation (separate databases, Supabase projects, keys, origins)
- `.vercelignore` already exists (excludes `.env`, `.venv`, caches)

### Step 2 — Vercel deployment verification

Deploy and verify:

- Actual forwarded paths through the rewrite
- `Set-Cookie` headers, host-only cookie scope, `Origin`, CSRF headers
- Error responses and cache headers in real browsers
- Registration confirmation and refresh/logout flows through the deployed rewrite
- App startup, shutdown, and connection behavior in the target runtime
- Lifespan invocation by the Vercel adapter

### Step 3 — Real provider integration tests

- **Supabase Auth**: real signup/confirmation/login/refresh/logout, JWT key rotation, provider failures
- **MongoDB Atlas**: connection limits, replica-set transactions, pool behavior, recovery during failover
- **Atlas Compass disable**: disable through Compass, verify denied on next protected request
- **Hosted runtime**: real browser exercises supported Notes flow (create, save, preview, activate)

### Step 4 — Browser and cookie verification

- Real browser tests for both builder and runtime origins
- Cookie scope (host-only, `Secure`, `HttpOnly`, `SameSite`)
- CSRF rejection on missing/mismatched tokens
- Origin validation (exact allowlist, missing-Origin policy)
- Session fixation prevention (rotation on login)
- Refresh vs refresh/logout/disable races
- Provider timeout after token rotation
- Replay of handoff grants

### Step 5 — Security and tenancy review

Exercise two builders, two projects, preview/live, app users in different memberships:

- Disabled builder/app identity/membership
- Anonymous callers, stale runtime sessions
- Wrong realm/audience/issuer/algorithm, JWT expiry, JWKS rotation
- Guessing IDs, cross-project child IDs, forged roles/owners/environments
- Unsafe filters/aggregates, CSRF, origin
- Direct-backend and forwarded-header bypasses
- Public operation granularity (record creator != project builder)
- Verify no builder cookie or provider token reaches the runtime

### Step 6 — Concurrency and failure tests

- Refresh vs refresh/logout/disable
- Provider timeout after token rotation
- Lease expiration, replayed handoff
- Save/rename/delete races
- Publish vs schema/data edits
- Retries with changed payload
- Partial external failures, unavailable status store
- 413, rate limiting, client timeouts
- No secret in logs or safe error responses

### Step 7 — Deployed integration

- Actual Vercel rewrites and browser cookies for both origins
- Production-like and deployment-preview isolation
- Real Supabase confirmation/login/refresh/logout
- Atlas Compass disable
- Hosted runtime and preview/live isolation
- Record which providers/topologies were actually exercised

### Step 8 — Operational readiness

- **Backups**: encrypted backups with retention; test restore into isolated environment; protect backup access
- **Secret rotation**: rotation procedures for Atlas, Supabase, encryption keys, session keys
- **Monitoring/alerts**: structured logs with request IDs, safe scope, outcome, latency; alerts for security anomalies, failed jobs, transaction failures, capacity
- **Rate limits**: bounded distributed rate limits for login, signup, refresh, bootstrap, import, public runtime operations
- **Least privilege**: Atlas and Supabase credentials scoped to minimum required
- **Capacity/timeout**: connection limits, replica-set transactions, pool behavior, request-size limits

### Step 9 — Threat and permissions review

- Review all authorization decisions (default deny, public/owner_only, member)
- Verify no implicit grants, no record-creator-as-builder, no cross-tenant existence leakage
- Verify forwarded-header bypass protection
- Verify trusted proxy metadata handling
- Review audit event coverage (no secrets, no raw payloads)

### Step 10 — Documentation and release

- Update `PROGRESS.md` implementation inventory (mark all items complete)
- Add dated entry under `Unreleased` in `CHANGELOGS.md`
- Document supported capabilities and limitations
- Document supported legacy version/migration matrix
- Document operational procedures (backup, restore, key rotation, incident response)
- Product owner accepts documented limitations

### Step 11 — Rollout and rollback

- Document rollout procedure (deploy, migrate indexes, verify)
- Document rollback procedure (revert deployment, restore from backup)
- Test rollback to prior snapshot
- Confirm no data loss on rollback

---

## Notes

- Phase 6 is the final phase. After completion, the backend is release-ready.
- Vercel deployment requires a Vercel account and project setup by the user.
- Real provider testing requires live Supabase, Atlas, and Vercel credentials.
- Cross-repository coordination with the web application is required for frontend integration verification.
- Do not call an untested feature complete — record which providers/topologies were actually exercised.
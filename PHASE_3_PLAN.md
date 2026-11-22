# Phase 3 Plan: Projects and Management Data

**Repository:** novy-backend  
**Status:** Proposed for review  
**Depends on:** Phases 1-2 (FastAPI foundation, MongoDB client, builder profiles/sessions, CSRF/Supabase auth layer)  
**Backend plan reference:** `BACKEND_PLAN.md`, Section 11, Phase 3

---

## Goal

Implement owner-scoped project CRUD, loose drafts, revision CAS, atomic template provisioning, logical database/collection/record CRUD, safe queries, indexes, and deletion fencing. Coordinate frontend conflict resolution and data pagination/environment selection.

---

## Acceptance Criteria (from BACKEND_PLAN.md)

- Two simultaneous saves yield one success and one HTTP 409 `stale_revision` without lost updates.
- Rename, save, and delete races are safe.
- Notes template and sample data commit or roll back together (atomic).
- Real replica-set/Atlas tests reject cross-user, cross-project, and nested-resource access.
- Supported legacy fixtures round-trip without losing IDs or content.

---

## Step-by-Step Process

### Step 1 — Domain models (application-independent)

Create framework-independent domain entities with no imports from FastAPI, Pydantic HTTP DTOs, PyMongo, Supabase, or request objects.

- `Project`: ObjectId, owner (server-derived), name, timestamps, draft JSON, independent `schemaVersion` and `revision`, lifecycle state, configuration/data fences, `projectType` (`'app' | 'website'`, defaults to `'app'` for legacy projects).
- `ProjectDraft`: structurally safe but potentially semantically incomplete project content.
- `ProjectRevision`: independent monotonically changing save/concurrency token (distinct from `schemaVersion`).
- `ProjectLifecycleState`: active, deleted/tombstoned, etc.
- Validation rules for loose drafts: accept bounded JSON, valid transport shape, supported schema version (14), safe representation, safe nesting/size; retain incomplete/invalid draft content; preserve unknown supported draft content, responsive layouts, routes, behavior graphs, data bindings, embedded IDs.

### Step 2 — Project repository port (application layer)

Define `ProjectRepository` protocol in `src/novy/application/`:

- `create(owner_id, name, draft)` -> Project
- `find_by_id_and_owner(project_id, owner_id)` -> Project | None
- `find_by_owner(owner_id, cursor, limit)` -> list[Project]
- `save_with_revision(project_id, owner_id, expected_revision, new_draft)` -> Project | raises conflict
- `rename(project_id, owner_id, new_name)` -> Project
- `delete(project_id, owner_id)` -> tombstone result
- `exists(project_id, owner_id)` -> bool (non-revealing across owners)

All operations use server-derived owner scoping. Never trust client-supplied owner, role, or tenant fields.

### Step 3 — MongoDB project repository implementation

Collection: `platform_db.projects`

Fields: ObjectId, `owner_id`, name, timestamps, draft JSON, `schemaVersion`, `revision`, lifecycle state, fences, `project_type` (`'app' | 'website'`).

Indexes:
- `(owner_id, updated_at, _id)` for listing
- Owner/id/revision CAS index for save-with-revision
- Owner/state filter indexes

Atomic `save_with_revision`:
- Compare project ID + server-derived owner + active lifecycle state + expected revision
- Increment revision with the write
- Return updated project and revision
- Missing precondition (`If-Match` absent) → `precondition_required` (HTTP 428)
- Genuine revision mismatch → HTTP 409 `stale_revision`
- Do not leak project existence across owners

### Step 4 — Project use cases

In `src/novy/application/`:

- `CreateProject`: owner-scoped; optional template DTO carrying `projectType`; atomic provisioning
- `ListProjects`: owner-scoped; cursor-based pagination (coordinate with frontend on cursor encoding/page size)
- `GetProject`: owner-scoped; return full project DTO with `revision`
- `SaveProjectDocument`: CAS with `If-Match`; loose draft acceptance; preserve unknown supported content
- `RenameProject`: separate atomic update from save; does not replace draft document
- `DuplicateProject`: schema-only default; explicit copy-records option (full implementation in Phase 4)
- `DeleteProject`: atomically mark unavailable, revoke/invalidate runtime access, tombstone + bounded cleanup

### Step 5 — Atomic template provisioning (Notes CRUD)

Required example: Notes CRUD starter template.

- Validate the blueprint (template DTO carrying `projectType`)
- Allocate IDs; remap only typed aliases in screens, routes, logic, and data bindings
- Commit project, database, collections, and sample records together in a single MongoDB transaction
- Roll back everything on failure — no partial project or duplicate client-side provisioning may remain
- Preserve embedded screen/node/graph/field IDs and internal references

### Step 6 — Data repositories (databases, collections, records)

Collections in `platform_db`:

- `app_databases`: project ID, logical database name, timestamps. Index: `(project_id, database_id, _id)`.
- `app_collections`: project/database IDs, field definitions with embedded field IDs, record identifier, draft schema/policies. Index: `(project_id, database_id, _id)`.
- `app_records`: project/collection IDs, product environment, field-ID-keyed values, timestamps. Index: `(project_id, environment, collection_id, _id)` plus indexes for supported scoped queries.

All reads/writes enforce:
- `project_id` + `environment` (preview/live boundary) + parent-child relationship verification
- Preview and live data separated by server-enforced environment boundary in every relevant query
- No raw client query operators into MongoDB

### Step 7 — Safe query and data representation layer

- Reject arbitrary MongoDB operators in user input; validate rather than pass raw operators
- Choose and test a reversible representation for problematic keys; reject input that cannot be represented safely rather than discard or silently reinterpret
- Record `values` keyed by field ID (not field label/key) — reconcile the frontend mock that uses field key instead of silently normalizing
- Validate types, identifiers, required fields, allowed field IDs, and server-generated record IDs at the data boundary
- Enforce MongoDB document (16 MiB) and nesting (100 levels) limits; respect Vercel 4.5 MB body limits

### Step 8 — Deletion fencing

- Delete atomically makes the project unavailable and revokes/invalidates runtime access before any potentially bounded cleanup
- Project lifecycle fence prevents concurrent writes from resurrecting deleted data
- Either complete bounded cascades in a transaction or use durable, retryable scoped cleanup with a tombstone and operational recovery
- Do not assume a serverless process keeps running after the response
- Define idempotency for create, duplicate, import, publish, and retryable deletion

### Step 9 — HTTP routes (thin presentation layer)

All routes under `/api/v1`, authenticate, check CSRF/origin, validate transport shape, invoke use case, map results/errors to versioned envelope `{"error":{code,message,details,requestId}}`.

Project routes:
- `GET /projects` — owner-scoped list (includes `projectType`)
- `POST /projects` — create (with optional template; `projectType` travels inside template)
- `GET /projects/{id}` — full project DTO with `revision` and `projectType`
- `PUT /projects/{id}/document` — CAS save with quoted `If-Match`; 409 `stale_revision` on conflict; 428 missing precondition
- `PATCH /projects/{id}` — rename body `{name}`
- `POST /projects/{id}/duplicate` — schema-only default
- `DELETE /projects/{id}` — delete with tombstone

Data routes:
- `GET/POST /projects/{id}/databases`; `PATCH/DELETE /projects/{id}/databases/{databaseId}`
- `GET/POST /projects/{id}/collections`; `GET/PUT/DELETE /projects/{id}/collections/{collectionId}`
- `GET/POST /projects/{id}/collections/{collectionId}/records`; `PUT/DELETE /projects/{id}/collections/{collectionId}/records/{recordId}`

Error mapping:
- Authentication failure → 401
- CSRF/origin denial → 403
- Unauthorized/missing nested resources → non-revealing 403/404 policy
- Conflict → 409
- Oversized payload → 413
- Missing revision precondition → 428
- Validation → coordinated 4xx
- Rate limit → 429
- Provider/database outages → safe 5xx

### Step 10 — Revision conflict coordination (frontend contract)

Document the required frontend fix:
- Stop automatic overwrite on 409 `stale_revision`
- Retain local edits; show conflict; load remote version separately
- Require explicit user resolution/rebase before a new conditional save
- For a lost successful response, use a stable save operation key or safe reconciliation that proves the committed document is the same; otherwise treat as conflict, not permission to overwrite
- Coordinate cursor-based pagination (page size, cursor encoding, full-list consumers) — until the new contract, cap underlying data or return an explicit actionable error rather than an incomplete successful response

### Step 11 — Indexes and migrations

Versioned migrations (not startup side effects), following the pattern of `builder_profiles_v1` and `builder_sessions_v1`:

- `projects_v1` — projects collection indexes and `project_type` field
- `app_databases_v1` — app_databases indexes
- `app_collections_v1` — app_collections indexes
- `app_records_v1` — app_records indexes

Explicit provisioning commands; test real index enforcement. Index changes must not run concurrently with project operations.

### Step 12 — Tests

Unit tests (`tests/unit/`):
- Domain models and rules
- Use cases with controlled ports/repositories
- Safe query validation

Contract tests (`tests/contract/`):
- HTTP/DTO compatibility
- Error envelope shapes and codes (401, 403, 409, 413, 428, 429, 5xx)
- CSRF/origin guards on project mutation routes

Integration tests (`tests/integration/`):
- Real MongoDB (replica set or Atlas)
- Two simultaneous saves → one success, one 409, no lost updates
- Rename, save, delete race safety
- Cross-user, cross-project, nested-resource access rejection
- Notes template and sample data commit/rollback together
- Legacy fixture round-trip (schema v13, preserve IDs and content)
- `projectType` round-trip: create with `'website'`, read back as `'website'`; legacy project without the field loads as `'app'`

### Step 13 — Verification and documentation

- Run full backend test suite: `pytest`, `ruff check`, `ruff format --check`, `mypy src tests`, `uv lock --check`
- Update `PROGRESS.md` implementation inventory and verification section
- Add dated entry under `Unreleased` in `CHANGELOGS.md`
- Confirm cross-repository coordination items: backend `projectType` storage/return (web B1–B4), schema version v14 acceptance, frontend 409 conflict handling and cursor pagination

---

## Notes

- Do not treat the frontend's API clients or mocks as evidence that backend endpoints exist. Coordinate API contracts with the web team but do not modify the frontend repository without authorization.
- `schemaVersion` (14) and `revision` are different concepts — do not conflate them.
- Runtime access defaults to deny; `owner_only` means the project's authenticated builder, not an app-user record creator.
- TTL expiry is cleanup, never a security decision.
- No non-idempotent external calls inside retryable transaction callbacks.

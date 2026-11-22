# Phase 4 Plan: Import and Duplication

**Repository:** novy-backend
**Status:** Proposed for review
**Depends on:** Phase 3 (Projects and management data)
**Backend plan reference:** `BACKEND_PLAN.md`, Section 11, Phase 4

---

## Goal

Implement schema-only duplicate with explicit copy-records option, two-pass typed import remapping with fresh persisted ObjectIds, idempotency records, account-scoped frontend markers, and all failure paths. Ensure no partial or duplicate project is visible and local data survives every failed import.

---

## Acceptance Criteria (from BACKEND_PLAN.md)

- Cover cross-kind ID collisions, embedded references, record-aware navigation.
- Concrete typed record-ID mappings, dynamic expressions and their empty fallbacks.
- Raw ID-looking text preserved without guessing.
- Dangling references rejected with actionable paths.
- Oversized input rejected with 413.
- Transaction abort and lost-response retry handled.
- No partial or duplicate project is visible; local data survives every failed import.

---

## Step-by-Step Process

### Step 1 — Duplicate: schema-only (default)

Extend `ProjectService.duplicate_project` (already partially implemented in Phase 3):

- Copy the project and data schema (databases, collections) with fresh persisted ObjectIds.
- Allocate new project/database/collection IDs; preserve embedded screen/node/graph/field IDs.
- Remap known typed references (parent links, collection-backed list sources, navigation record context, logic actions).
- Copy no records or memberships by default.
- Do not copy provider sessions, credentials, app-user memberships, live runtime grants, or published state.
- Source access and destination ownership are server-derived.

### Step 2 — Duplicate: explicit copy-records option

- Accept `copy_records: bool` flag (already in `duplicate_project` signature).
- When true: allocate fresh record IDs, remap known typed record references.
- Define source dataset and preview/live behavior in coordinated UI (not implicitly across environments).
- Schema-only copies can contain typed references to source records in navigation context or logic actions.
- Never leave those pointing into the source project, silently erase user content, or treat an arbitrary ID-looking string as a reference.
- If a known typed record reference cannot be safely preserved without the record, reject the duplicate with a precise issue and let the builder resolve it or explicitly choose copy-records.
- Copy and idempotency result become visible atomically within bounded transaction limits.

### Step 3 — Distinguish concrete IDs from dynamic expressions

- Distinguish concrete IDs in typed record-ID slots from dynamic expressions such as `$recordId` and their empty fallbacks.
- Remap a concrete ID only when its referenced record is copied.
- Preserve dynamic expressions and unrelated ID-looking text without guessing at their runtime value.

### Step 4 — Import: transport and validation

Implement `POST /projects/import`:

1. Authenticate the builder, enforce CSRF and limits.
2. Validate transport/schema version (supported v14, legacy v13).
3. Check duplicate old IDs and parent relationships.
4. Inventory all typed references.
5. Never trust incoming owner, timestamps as authorization, or project/environment scope.
6. Reject inconsistent, unsupported, ambiguous, or dangling typed references with actionable paths.
7. Enforce bounded request size (413 on oversized).

### Step 5 — Import: ID allocation and remapping

1. Allocate fresh MongoDB ObjectIds for every persisted project, database, collection, and record.
2. Build separate typed old-to-new maps for each resource kind (project, database, collection, record); do not assume IDs of different kinds cannot collide.
3. Preserve embedded screen/node/graph/field IDs and their internal references.
4. In a second pass, rewrite only known typed resource references:
   - Parent links
   - Collection-backed list sources and routes
   - Navigation record context
   - Logic actions (get/update/delete)
   - Other supported typed paths
5. Apply record maps only to actual typed record references.
6. Preserve arbitrary text, unknown safe draft fields, and field-ID-keyed values exactly, even when strings look like ObjectIds.
7. If the format cannot distinguish a typed reference safely, reject the import rather than globally replacing strings or silently dropping a value.

### Step 6 — Import: atomic persistence and idempotency

1. Atomically persist the complete imported project, data, and an owner/operation-scoped idempotency result in a MongoDB transaction.
2. A retry with the same stable key and same payload digest returns the original result after a lost response.
3. Reuse of the key with a different payload is a conflict.
4. Do not depend on comparing old IDs with new remote project IDs.
5. Coordinate an account-scoped frontend import marker and stable request key.
6. Only mark success after a confirmed or idempotently recovered response.
7. Keep local data on failure or 413; do not silently truncate, partially import, or delete it.

### Step 7 — Security and scope

- Do not import untrusted provider credentials, sessions, app-user membership, published grants, or other security state.
- Define the destination environment for imported records explicitly (preview); must not be selectable by untrusted runtime client or silently cross from preview into live.

### Step 8 — HTTP routes

- `POST /projects/import` — `{project, appData: {databases, collections, records}}` → project DTO response.
- `POST /projects/{id}/duplicate` — already exists; add `copyRecords` query/body option.

### Step 9 — Indexes and migrations

- Idempotency collection: `idempotency` with unique `(principal, operation, key)`.
- No new collection indexes required beyond Phase 3.

### Step 10 — Tests

Unit tests:
- ID allocation and remapping logic
- Typed reference detection (concrete vs dynamic vs arbitrary text)
- Cross-kind ID collision handling
- Dangling reference rejection

Contract tests:
- HTTP/DTO compatibility for import and duplicate
- Error envelope shapes (401, 403, 409, 413, 422, 5xx)
- Idempotency key reuse and conflict

Integration tests:
- Real MongoDB transaction: full import commit/rollback
- Duplicate schema-only: no records copied
- Duplicate with copy-records: records copied with remapped IDs
- Lost-response retry: idempotent recovery
- Oversized import: 413, local data preserved
- Dangling reference: rejected with actionable path

### Step 11 — Verification and documentation

- Run full backend test suite: `pytest`, `ruff check`, `ruff format --check`, `mypy src tests`, `uv lock --check`
- Update `PROGRESS.md` implementation inventory
- Add dated entry under `Unreleased` in `CHANGELOGS.md`

---

## Notes

- Phase 4 depends on Phase 3's project CRUD, data repositories, and transaction support.
- Import is not standalone source export — it accepts the frontend's `{project, appData}` payload.
- Duplicate defaults to schema-only; record copying is explicit opt-in.
- All ID remapping must be typed — never globally replace ID-looking strings.
- Idempotency is owner/operation-scoped; distinguish duplicate request from distinct user action.
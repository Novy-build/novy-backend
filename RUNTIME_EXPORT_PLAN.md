# Novy Backend — Generated-App Runtime and Export Plan

**Status:** Partially implemented. The offline React source export (`POST /api/v1/exports/react`,
strict manifest v2 validation, deterministic generator, ZIP response) was completed as workstream C3
on 2026-11-24 and is recorded in `PROGRESS.md:170-210`. The immutable app versions, `/runtime/v1`
action API, preview preparation/reset, and hosted renderer described below remain planned and
unimplemented.
**Date:** 2026-11-24 (status reconciled 2026-11-24)
**Audience:** Backend contributor implementing the export/runtime workstream.

**Related documents**

- `BACKEND_PLAN.md` — approved v1 direction; this plan extends it and must be reconciled with it
  before implementation begins.
- `ROADMAP.md` in `../novy-web/` — binding wire contract for the runtime and export endpoints
  (exact paths, camelCase names, cookies, errors, security boundaries).
- `PROGRESS.md` — current backend implementation inventory.
- `FEATURE_MATRIX.md` at the workspace root — audit of frontend features and missing backend rows.

---

## 1. Goal and definition of done

When a builder clicks **Run App**, **Download React app**, or publishes a project, the exported
application must actually work: render the screens, execute the authored logic
(event → condition → action), read and write its own scoped database, and report unsupported
capabilities instead of pretending they work.

Concretely, this workstream delivers:

1. **Immutable app versions** — a frozen, validated manifest stored server-side so the runtime can
   resolve actions and data scope from trusted state, never from the browser.
2. **Runtime action APIs under `/runtime/v1`** — the public execution surface the generated app calls
   for CRUD, email, and (in future) RAG/CV actions.
3. **React source generator at `POST /api/v1/exports/react`** — turns the frozen manifest into a
   downloadable React/Vite project that runs browser-only logic locally and delegates server actions
   to `/runtime/v1`.
4. **A way to see it working** — a hosted preview surface (recommended: a shared renderer that
   interprets the frozen manifest) plus documented local-run instructions for the ZIP.

**Non-goals for this workstream:** generated-app RAG, biometrics/CV, passwordless app-user identity,
provider-side deployment (Vercel/Netlify publishing), custom domains, and the builder AI assistant.
Those stay future/deferred (`FEATURE_MATRIX.md` category D; `BACKEND_PLAN.md` non-goals). The runtime
dispatch designed here is where they plug in later.

---

## 1.1 Exportability: today vs. after this plan

The distinction this workstream resolves is between exporting the app **definition** and exporting a
**running app**. Today only the definition leaves the browser; nothing runnable does.

| Capability | Today | After this plan |
|---|---|---|
| Layout / UI | Already exported as **data** in the handoff manifest (ProjectDTO v14 + layout v2), but rendered only inside Novy's editor and Preview | Generated React renderer and hosted preview renderer consume that same data and display the real UI |
| Logic section | Saved as `behaviors` + `logicGraphs` JSON in the project document; executed only by the frontend's `runBehaviorAsync` engine in editor Preview | Exported logic engine (and hosted renderer) interpret the same graph; server actions dispatch to `/runtime/v1` |
| Database | Schemas and records saved through `/projects/{id}/...`; Preview simulates data against isolated browser state | Schema and bindings exported; records stay in a server-owned preview data scope, reached through `/runtime/v1` |
| Run App | Cannot start — `POST /exports/runtime-preview` returns 404 | Freezes an immutable app version, seeds preview data, exposes a preview URL |
| Download React app | Implemented (C3): `POST /api/v1/exports/react` returns a buildable React/Vite source ZIP with browser-only logic; it cannot yet reach server actions because `/runtime/v1` does not exist | ZIP runtime client delegates server actions to `/runtime/v1` once that API is implemented |
| Preview URL | None | Hosted shared renderer opens the exported app directly in a browser |

Two points to keep straight:

- **The layout is already exportable as data.** The plan does not invent a new layout format; it adds
  the consumers (generated renderer + hosted preview) that make that layout render outside the editor.
- **The database is not shipped inside the export.** The manifest sets `recordsIncluded: false` and
  contains no secrets, media, or builder credentials. What is exported is the schema and the typed
  bindings (collection IDs, field IDs, record-identity keys, routes); records remain server-side in
  the preview/live data scope, so writes from the exported app persist for real.

## 2. Current state (verified, not assumed)

**Backend exists today:** auth (`/auth/*`), projects (`/projects*`), project data
(`/databases`, `/collections`, `/records`, `/app-data`), snapshots (`/activate`, `/status`),
health, and the offline React export router (`POST /api/v1/exports/react`). Router wiring is in
`src/novy/main.py`. The `/exports/react` route, strict manifest v2 validation, deterministic
React/Vite generator, and ZIP archive are implemented and verified (C3, `PROGRESS.md:170-210`).
There is still no `/runtime/v1` router, no app-version store, no preview prepare/reset endpoints,
and no hosted renderer.

**Frontend already has the complete authoring and saving side:**

- `useProjectAutosave.ts` debounces 800 ms and calls `saveProjectDocument`.
- `ApiProjectRepository.saveProjectDocument()` PUTs the full `ProjectDTO` (schema v14) to
  `/api/v1/projects/{id}/document` with `If-Match: "<revision>"`. Screens contain `nodes`
  (geometry/hierarchy/bindings), `behaviors` (`event`, `targetNodeId`, `graphId`), and `logicGraphs`
  (nodes, edges, payloads, input mappings). **Logic and layout are already persisted together** in
  one revision-controlled MongoDB document.
- App data (databases/collections/fields/records) is persisted separately through the project-data
  endpoints; records carry `environment: "preview" | "live"` in `domain/app_data.py`.
- `generatedAppManifest.ts` builds manifest v2: full project DTO, layout v2, collection schemas
  (`recordsIncluded: false`), routes with `routeParameters`, `runtime.requirements`, and security
  flags all false.
- `domain/logic/runtime.ts` implements the browser logic interpreter (`runBehavior` /
  `runBehaviorAsync`) used by Preview; `ApiRuntimePreviewRepository.ts` and
  `ApiReactExportRepository.ts` already call the endpoints this plan must implement.

**Therefore the saving side needs no changes.** The gap is the compilation/execution side: freezing
the manifest into an app version, serving runtime actions, generating source, and hosting/previewing
it.

**Stack decision required:** `ROADMAP.md` prescribes Python 3.13 + SQLAlchemy + PostgreSQL +
Alembic + JWT access-token cookies. The actual backend is Python 3.12 + PyMongo async + MongoDB
Atlas + opaque server-side sessions. **Recommendation:** keep the existing MongoDB/Atlas stack and
port the wire contract onto it; do not introduce a second database. All paths, field names, error
codes, and security rules from `ROADMAP.md` remain binding; only the persistence technology changes.
Record this decision in `BACKEND_PLAN.md` when the workstream is approved.

---

## 3. Architecture and data flow

```
Builder browser (/api/v1, cookie + CSRF)
  ├─ autosave ──────────────► projects document (layout + logic) ─┐
  └─ app data edits ────────► databases/collections/records ─────┤
                                                                  ├─► build manifest v2
  POST /api/v1/exports/runtime-preview ◄──────────────────────────┘
        │ validate + freeze  ─► app_versions (owner, project, scope, manifest)
        │ first preview prep ─► copy live records into preview scope (fresh IDs)
        │
        ├─► POST /api/v1/exports/react ─► React/Vite source ZIP (download)
        │
        └─► GET /runtime/v1/apps/{appId}/manifest ─► shared renderer SPA (hosted preview)

Generated app / renderer (/runtime/v1, public actions)
  ├─ GET  /apps/{appId}/collections/{id}/records ─► preview-scoped records
  └─ POST /apps/{appId}/actions/{actionId}/execute
          │ resolve actionId from frozen manifest (never trust browser payload)
          │ enforce declared inputs, collection, fields, owner, project, scope
          └─► CRUD / email / (future RAG, CV) use cases ─► app_data collections
```

### New MongoDB collections

| Collection | Purpose | Key indexes / constraints |
|---|---|---|
| `app_versions` | Frozen validated manifest, deterministic `appId`, owner, project, `data_scope` (`preview`/`live`), timestamps | unique `app_id`; `(owner_id, project_id, data_scope, created_at)` |
| `runtime_executions` | Idempotent action results keyed by execution | unique `(app_id, action_id, execution_id)` with input fingerprint, status, canonical output, expiry |
| `runtime_login_codes` / `runtime_sessions` / `vision_*` | Only when the future identity/CV phases are approved | defer until then |

Reuse the existing `app_databases`, `app_collections`, and `app_records` collections and their
`environment` field. Every runtime query must include `owner_id`, `project_id`, and `environment`;
server-derived from `app_versions`, never from request fields.

### Layer boundaries (Clean Architecture)

```
src/novy/
  domain/
    generated_app.py        # AppVersion, DataScope, immutable manifest value rules
    runtime.py              # canonical records, action dispatch ports, RuntimeError taxonomy
  application/
    manifests.py            # strict manifest validation + semantic validation orchestration
    app_versions.py         # AppVersionService: prepare/get/resolve, deterministic IDs
    runtime.py              # RuntimeService: resolve action, enforce inputs, dispatch
    runtime_actions/        # crud.py, email.py (future: rag.py, vision.py)
    source_generation.py    # port: GenerateReactSource -> archive bytes
  infrastructure/
    app_versions.py         # MongoAppVersionRepository
    runtime_executions.py   # Mongo idempotency repository
    react_generator.py      # deterministic template/zip implementation of the port
    migrations/app_versions_v1.py, runtime_executions_v1.py
  presentation/http/
    exports.py              # /exports/runtime-preview, /reset, /exports/react, /manifest
    runtime.py              # /runtime/v1 router (registered WITHOUT /api/v1 prefix)
    runtime_schemas.py      # strict camelCase Pydantic request/response models
    export_schemas.py
  composition.py            # construct new services/repositories
  main.py                   # include export router under /api/v1 and runtime router at root
tests/unit/, tests/contract/, tests/integration/, tests/generation/
```

Routes stay thin: authenticate, validate transport shape, invoke a use case, map errors to the public
envelope. Domain models must not import FastAPI, Pydantic HTTP DTOs, PyMongo, or provider SDKs.

---

## 4. Exact wire contract (must match the frontend)

All JSON field names are **camelCase**. Timestamps are UTC ISO-8601. IDs are opaque strings. Request
models use `extra="forbid"` and never echo submitted values. Every error uses the existing envelope
from `presentation/http/errors.py`:

```json
{"error":{"code":"stable_code","message":"Safe message.","details":null,"requestId":"..."}}
```

### 4.1 Builder endpoints (`/api/v1`, cookie + CSRF)

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/api/v1/exports/runtime-preview` | full manifest v2 JSON | `200 {"appId":"app_..."}` |
| POST | `/api/v1/exports/runtime-preview/reset` | `{"projectId":"..."}` | `200 {"clearedRecords":<int>}` |
| POST | `/api/v1/exports/react` | full manifest v2 JSON | `200 application/zip`, `Content-Disposition: attachment; filename="<slug>.zip"` |
| GET | `/api/v1/exports/app-versions/{appId}/manifest` | — | `200` frozen public manifest (no secrets); used by hosted renderer |

`prepare` and `reset` derive owner from the current builder session. `reset` deletes only
`owner_id + project_id + preview` records; it must never touch `live`. `prepare` is idempotent for
the same frozen version and must not overwrite preview edits.

**Export error codes the frontend already parses** (`ApiReactExportRepository.mapApiExportError`):
`invalid_manifest`, `validation_error` (422), `generation_timeout`, `generation_failed`,
`rate_limited` (429), `session_required` / `invalid_session` (401),
`auth_not_configured` / `auth_provider_unavailable` (503). Use these exact codes.

### 4.2 Runtime endpoints (root `/runtime/v1`, public by default)

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/runtime/v1/apps/{appId}/collections/{collectionId}/records` | — | `200 {"records":[CanonicalRecord,...]}` |
| POST | `/runtime/v1/apps/{appId}/actions/{actionId}/execute` | `{"executionId":"...","inputs":{...}}` | `200 {"branch":"success","output":{...}}` |
| GET | `/runtime/v1/apps/{appId}/manifest` | — | frozen public manifest for the shared renderer |

Canonical record:

```json
{"id":"record_...","projectId":"project_...","collectionId":"collection_...",
 "values":{"field_title":"Example"},"createdAt":"...","updatedAt":"..."}
```

Runtime inputs must satisfy the frontend limit rules: nesting depth ≤ 8, string length ≤ 100,000,
array length ≤ 2,000, object ≤ 200 keys, key length ≤ 200.

Action outputs (must match `PreviewOverlay.tsx` reconciliation):

- `get-records` → `{"records":[...]}`; `get-record` → `{"record":{...}}`
- `insert-record` / `update-record` → `{"record":{...}}`
- `delete-record` → `{"deletedRecordId":"record_...","collectionId":"collection_..."}`
- `send-email` → `{"deliveryId":"email_dev_..."}` (sanitized; no recipient or provider data)

### 4.3 Error mapping (subset needed now)

| Code | HTTP |
|---|---:|
| `validation_error`, `invalid_runtime_input`, `unsupported_capability`, `invalid_manifest` | 422 |
| `session_required`, `invalid_session` | 401 |
| `forbidden` | 403 |
| `runtime_app_not_found`, `runtime_action_not_found`, `runtime_collection_not_found`, `app_data_not_found`, `rag_source_not_found` | 404 |
| `conflict` (execution-id input mismatch), `stale_revision` | 409 |
| `request_too_large` | 413 |
| `unsupported_media_type` | 415 |
| `rate_limited` | 429 (with `Retry-After` and `details.retryAfterSeconds`) |
| `email_delivery_unavailable`, `auth_not_configured` | 503 |
| `internal_error` | 500 |

Browser-only action IDs posted to `/execute` return `422 unsupported_capability`; unknown action IDs
return `404 runtime_action_not_found` (never reveal capabilities not in the frozen version).

### 4.4 CORS and middleware

- Builder CORS: exact configured origins, credentials allowed, headers `Content-Type`,
  `X-CSRF-Token`, `X-Request-ID`, `If-Match`.
- Runtime public routes: `Allow-Origin: *`, no credentials, no builder cookies.
- Future credentialed runtime routes (identity/CV): exact deployed origins + credentials + runtime
  CSRF. Build the split now so those routes can be added without rework.
- Accept/generate `X-Request-ID`; `Cache-Control: no-store` on auth and runtime responses; bounded
  request bodies; rate limits keyed by (principal/anonymous bucket, operation). The existing
  in-memory `RateLimiter` is single-process only; note this as a release requirement for multi-instance
  Vercel.

---

## 5. Implementation phases

Each phase must be green before the next starts. Tests are written with the code, not after.

### Phase 0 — Contract freeze and fixtures

1. Copy representative manifest fixtures from `../novy-web/src/data/generation/generatedAppManifest.ts`
   and its tests into `tests/contract/fixtures/` (do not import frontend code into backend).
2. Decide and document: MongoDB stack, capability allowlist for v1, numeric limits, preview hosting
   option (section 6), and how `previewUrl` is returned (additive field on `prepare` response; the
   frontend only reads `appId`, so this is backward compatible).
3. Add contract tests that assert exact paths, camelCase fields, and error codes from section 4.

**Acceptance:** fixtures parse as valid manifest v2; contract test list covers every route in section 4.

### Phase 1 — Manifest validation and immutable app versions

1. `domain/generated_app.py`: `AppVersion`, `DataScope`, immutable manifest rules; deterministic ID
   `app_` + `sha256(owner_id + ":" + data_scope + ":" + canonical_manifest)` after pinning
   `generatedAt` to the saved project's `updatedAt` so export clock differences do not change the ID.
2. `application/manifests.py`: strict Pydantic contract models (`extra="forbid"`, manifest v2,
   layout v2, project schema 13/14, security flags all false) plus semantic validation reusing
   `domain/validation.py`: unique IDs, references, routes/route parameters, graph/edge limits and
   acyclicity, typed ports, action collection/field references, capability inference, declared data
   types. Emit at most 50 issues plus `issueCount`; failures → `422 invalid_manifest`.
3. `application/app_versions.py` + `infrastructure/app_versions.py`: get-or-create version, verify
   ownership from the builder session (never from the manifest), transactionally seed preview data on
   first preparation, idempotent re-preparation, bounded copy (default 5,000 records) failing
   atomically, isolated preview vector namespace rules reserved for future RAG.
4. `presentation/http/exports.py`: `POST /exports/runtime-preview`, `/reset`; wire in `main.py` and
   `composition.py`; migration `app_versions_v1`.

**Acceptance tests:** equal manifests with different `generatedAt` produce the same app ID; different
owners/scopes produce different IDs; repeat prepare preserves sandbox edits; reset clears only
owner-scoped preview records; foreign project returns 404; oversized seed fails atomically; invalid
schema version / security flag / layout mismatch returns `422 invalid_manifest`.

### Phase 2 — Runtime action API

1. Register a separate router in `main.py`: `app.include_router(runtime_router)` — **no `/api/v1`
   prefix**. Add runtime CORS, body limits, and request-ID middleware.
2. `application/runtime.py`: for every request, load the version by `appId`, re-verify the manifest,
   confirm the owning project still exists and is active, resolve `actionId` by scanning the frozen
   graph nodes, enforce declared input mappings (reject undeclared input keys), and resolve the
   collection from the version's declared collections. Never accept an action payload from the
   browser.
3. Implement dispatch for `get-records`, `get-record`, `insert-record`, `update-record`,
   `delete-record`, and `send-email` (development adapter side-effect free; disabled adapter → 503).
   Canonicalize records, filter values to fields declared by the frozen version, coerce/validate
   types, enforce required fields, and generate backend record IDs in the version's data scope.
4. `infrastructure/runtime_executions.py`: idempotency key `(appId, actionId, executionId)` with an
   input fingerprint; same input returns the first result; different input → `409 conflict`; failed
   executions stay failed.
5. Browser-only types (`navigate`, `change-text`, `show-message`, `set-theme`, `set-variable`,
   `set-component-value`, `add-local-list-item`, `refresh-collection`, `chat-*`, triggers/conditions)
   → `422 unsupported_capability`. Deprecated granular RAG action types → same code. Unknown action
   ID → 404.

**Acceptance tests (port from `ROADMAP.md` §12.3/12.4):** full CRUD lifecycle with canonical outputs;
undeclared action/collection/input rejected; fields added after version creation are filtered out;
email output contains only `deliveryId`; browser-only action rejected; unknown action is 404 not 422;
execution replay/conflict; cross-owner and cross-environment reads denied; public vs credentialed
CORS separation; oversized payload 413.

### Phase 3 — React source generator

> **Status (2026-11-24):** The offline generator, manifest validation, and HTTP surface were
> implemented as C3 (`PROGRESS.md:170-210`; actual modules are `domain/generation.py`,
> `domain/manifest_validation.py`, `infrastructure/react_source/`, `application/react_export.py`,
> `presentation/http/exports.py`). The remaining work in this phase is wiring the generated
> runtime client to `/runtime/v1` once Phase 2 exists; do not re-implement the generator.

1. `application/source_generation.py` defines the port; `infrastructure/react_generator.py`
   implements deterministic generation from the frozen manifest into an in-memory ZIP
   (`zipfile.ZipFile` over `BytesIO`; no shelling out, no per-request Vite build). Stream with
   `Content-Disposition` using the same filename rules as `generatedAppManifestFilename()`
   (`<slug>-handoff.zip` style; frontend falls back to `novy-react-app.zip`).
2. Generated project layout:

```
novy-react-app/
├── package.json, vite.config.ts, tsconfig.json, index.html, README.md
└── src/
    ├── main.tsx, App.tsx, styles.css
    └── generated/
        ├── manifest.json          # frozen manifest; no records, secrets, or media
        ├── config.ts              # backend base URL + appId
        ├── routes.tsx             # screen paths + routeParameters
        ├── screens/<ScreenId>.tsx # typed component trees per screen
        ├── renderer.tsx           # component renderer (canvas/preview parity)
        ├── theme.ts               # light/dark design-system tokens
        ├── bindings.ts            # list-item/current-record bindings
        ├── logicEngine.ts         # trigger → condition → action interpreter
        ├── runtimeClient.ts       # /runtime/v1 calls with executionId
        └── dataScope.ts           # record cache and reconciliation
```

3. **Logic engine parity** with `../novy-web/src/domain/logic/runtime.ts`: `MAX_RUNTIME_STEPS = 100`;
   trigger lookup via the screen's `behaviors` (`event`, `targetNodeId` → `graphId`); If/Else branches;
   input mappings from literals, component values, variables, session, current list item, and
   `$previous`; effect list for `change-text`, `show-message`, `navigate`, `set-theme`,
   `set-variable`, `set-component-value`, `add-local-list-item`, chart/list data, and chat messages;
   `success`/`error` ports awaited for async actions. Browser-only effects execute locally; server
   actions call `runtimeClient.execute(actionId, inputs)` and reconcile returned records into every
   bound ListView (mirroring `PreviewOverlay.reconcileRuntimeRecords`).
4. **Renderer parity:** all supported component types, responsive mobile/tablet/desktop cascade with
   inheritance, appearance/dark overrides, ListView item templates and structured static items,
   route parameters, theme switching, and accessible semantics. Emit only allowlisted action types;
   reject manifests declaring unsupported capabilities with `422 invalid_manifest` instead of
   generating a stub that silently does nothing.
5. Never embed secrets, builder tokens, provider keys, raw media, or biometric templates. Security
   flags in the manifest stay false and are re-checked at generation time.

**Acceptance tests:** generated file snapshots for a representative notes/manifest fixture; generated
`runtimeClient` request shape matches the runtime schema (contract test); generated source passes
`tsc`/Vite build in CI when Node is available (mark as optional skip with reason if not); error-code
mapping matches `ApiReactExportRepository`; repeated generation of the same version is byte-identical.

### Phase 4 — Make the export visibly work

Deliver at least option A and B; option C is a separate later workstream.

- **A. Hosted preview (recommended first).** Serve one shared renderer SPA (static assets built once
  and shipped with the backend or a small separate origin) that loads
  `GET /runtime/v1/apps/{appId}/manifest`, uses the same logic engine/runtime client as the generated
  source, and renders against preview-scoped data. Add an additive `previewUrl` field to the
  `prepare` response. Keep the renderer on a separate origin from the builder where practical, with
  strict CSP, no builder cookies, and no arbitrary HTML/JS execution. This directly satisfies the
  "click and see the app working with logic and a database" requirement without per-app builds.
- **B. Local run.** Generated ZIP README documents `npm install`, `VITE_API_BASE_URL=<backend>`,
  and `npm run dev`. The app works against the same `/runtime/v1` API.
- **C. Provider deployment (later).** `/api/v1/deployments/*` publish/status/history with Vercel or
  Netlify adapters requires real platform credentials and an isolation decision; do not imply it is
  covered here.

**Acceptance:** from a builder session, prepare a preview, open the preview URL (or run the ZIP),
click through the Notes flow: list loads from preview data, insert/update/delete persist and
rerender, navigation to a record detail route works, and live-environment records are unchanged.

### Phase 5 — Hardening and release gates

- Security: cross-owner/cross-project/cross-environment access, guessed IDs, forged inputs/owners,
  replayed execution IDs, oversized inputs, SSRF-safe provider URLs, no secrets in logs or ZIP.
- Persistence: real transaction-capable Atlas tests for version creation + preview seeding/reset and
  idempotency races; indexes verified against a live database, not mocks.
- Operations: bounded sizes/timeouts, distributed rate limiting for multi-instance Vercel, structured
  redacted logs, migration rollback plan, backup/restore of `app_versions` and preview data.
- Full gate: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src
  tests`, `uv run pytest`, plus frontend `npm run typecheck`, `npm test -- --run`, `npm run build`.
  Record actual command results in `PROGRESS.md` and `CHANGELOGS.md`; never report unrun tooling.

---

## 6. Key risks and decisions to resolve

| Risk / decision | Recommendation |
|---|---|
| `ROADMAP.md` stack (PostgreSQL/JWT) vs actual backend (MongoDB/opaque sessions) | Port the contract onto MongoDB; document the deviation in `BACKEND_PLAN.md` before coding. No second database. |
| Generator parity is a duplicate renderer implementation | Treat the frontend renderer as the behavioral spec; keep generated code deterministic and test snapshots. Share DTO shapes, not runtime code (separate languages/repos). |
| Vercel function limits (request/response size, no reliable build step) | Generate source, not compiled bundles, inside the function. Hosted preview uses one prebuilt shared renderer. Keep manifests below the smallest transport limit. |
| Untrusted generated apps and user content | Separate origin + CSP for hosted preview; never execute arbitrary author JS; escape all text; no builder cookies or tokens in generated/runtime contexts. |
| In-memory rate limiter is single-instance | Accept for local development only; require a distributed store before release on Vercel. |
| RAG/CV/passwordless identity | Defer. Reserve route shapes and data scopes now; return fail-closed codes (`rag_not_configured`, `vision_provider_unavailable`) rather than simulating production behavior. |
| Frontend expects `VITE_API_BASE_URL` ending in `/api/v1`; runtime client strips it | Register `/runtime/v1` on the backend root, never under `/api/v1`. |

---

## 7. Frontend coordination

- **Do not change** any frontend URL, field name, cookie expectation, response parser, or DTO to fit
  the backend. The sources of truth are `axiosClient.ts`, `runtimeAxiosClient.ts`, `csrf.ts`,
  `ApiRuntimePreviewRepository.ts`, `ApiReactExportRepository.ts`, `generatedApp.dto.ts`,
  `generatedAppManifest.ts`, and `src/domain/entities/logic.ts`.
- Additive fields (for example `previewUrl` on the prepare response) are acceptable because the
  frontend reads only known keys; confirm with the web workstream before shipping.
- The hosted renderer origin must be added to the runtime CORS allowlist and documented in the
  generated app's `README.md` and `.env.example`.
- Frontend verification after each coordinated milestone: `npm run typecheck`, `npm test -- --run`,
  `npm run build` — unchanged contracts should keep these green.

---

## 8. Milestone / task summary

| Phase | Deliverable | Depends on |
|---|---|---|
| 0 | Frozen contract, fixtures, contract tests | — |
| 1 | Manifest validation, immutable app versions, preview prep/reset | 0 |
| 2 | `/runtime/v1` records + execute, idempotency, CRUD, email | 1 |
| 3 | `/exports/react` generator + generated app source + tests | 1, 2 (contract) |
| 4 | Hosted preview renderer / documented local run | 1, 2, 3 |
| 5 | Hardening, migrations, full quality gate, documentation | all |

---

## 9. Definition of done checklist

- [ ] Frozen manifest validates strictly; unsupported versions/capabilities are rejected with
      `422 invalid_manifest`.
- [ ] Deterministic `appId`; owner and data scope participate in version identity.
- [ ] Preview preparation is idempotent, bounded, and isolated from live data; reset is owner-scoped.
- [ ] `/runtime/v1` is registered at the root and resolves every action from the frozen manifest.
- [ ] Undeclared actions, collections, inputs, and browser-only actions fail closed with exact codes.
- [ ] CRUD and email outputs match the shapes `PreviewOverlay.tsx` consumes.
- [ ] Execution IDs are idempotent with input-fingerprint conflict detection.
- [ ] Downloaded ZIP contains a buildable React/Vite app with a working logic engine and runtime client.
- [ ] A builder can open a preview URL (or run the ZIP) and complete a Notes flow against preview data.
- [ ] No secrets, tokens, raw media, or builder identity reach generated or runtime contexts.
- [ ] Ruff, format, mypy, full pytest, and frontend typecheck/tests/build all pass with results recorded.
- [ ] `PROGRESS.md`, `CHANGELOGS.md`, and `BACKEND_PLAN.md` reflect the implemented scope.

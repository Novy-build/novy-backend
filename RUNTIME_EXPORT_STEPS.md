# Runtime and Export — Step-by-Step Implementation Plan

**Repository:** novy-backend
**Status:** Proposed for review. Nothing here is implemented.
**Date:** 2026-11-24
**Design reference:** `RUNTIME_EXPORT_PLAN.md` (architecture, wire contract, risks)
**Wire contract source of truth:** `../novy-web/ROADMAP.md` and the frontend API clients listed in
section 7 of the design plan.
**Style reference:** `PHASE_3_PLAN.md` (each step: objective, files, behavior, tests, done-when).

---

## How to use this document

Work top to bottom. Do not start a step until the previous step's tests and quality checks pass.
Every step ends with a **Done when** checklist; record the actual commands and results in
`PROGRESS.md` and add a dated entry under `Unreleased` in `CHANGELOGS.md` when the step lands.
Live-provider behavior (Atlas transactions, Supabase, Vercel) must be reported honestly as verified
or unverified — mocks alone never establish correctness.

| Steps | Phase | Deliverable |
|---|---|---|
| 0–1 | 0 — Contract freeze | Decisions recorded, fixtures and contract tests in place |
| 2–9 | 1 — App versions | Manifest validation, immutable versions, preview prep/reset |
| 10–17 | 2 — Runtime API | `/runtime/v1` records + execute, CRUD, email, idempotency |
| 18–23 | 3 — Generator | `/exports/react` producing a runnable React/Vite ZIP |
| 24–26 | 4 — Visibility | Hosted preview renderer, local-run path, acceptance flow |
| 27–29 | 5 — Hardening | Security/integration tests, full gate, documentation |

---

## Step 0 — Freeze contract, stack, and limits (no code)

**Objective:** Remove ambiguity before writing implementation code.

- Decide and record in `BACKEND_PLAN.md`:
  - Port the `ROADMAP.md` wire contract onto the existing **PyMongo/Atlas** stack; do **not** add
    PostgreSQL or a second database.
  - v1 runtime capability allowlist: database CRUD + email only. RAG, CV, and passwordless identity
    are deferred and fail closed.
  - Numeric limits: preview seed cap (default 5,000 records), request body cap, runtime value depth
    (8), string length (100,000), array length (2,000), object keys (200), key length (200), manifest
    issue cap (50).
  - Preview hosting option: shared renderer SPA (recommended), and whether `previewUrl` is returned
    as an additive field on the prepare response.
- Copy representative manifest fixtures from the frontend into
  `tests/contract/fixtures/` (write them by hand from `generatedAppManifest.ts` and its tests; do not
  import frontend code into the backend). Cover a Notes app with screens, behaviors, logic graphs,
  routes with `routeParameters`, and a collection schema. Include one invalid fixture (schema v12,
  missing security flags).

**Done when:** decisions are documented; fixtures exist and are referenced by a contract test; the
frontend workstream has confirmed additive response fields are safe.

---

## Step 1 — Contract test skeleton

**Files:** `tests/contract/test_runtime_contract.py`, `tests/contract/fixtures/*`.

- Assert the exact route table: `/api/v1/exports/runtime-preview`, `/reset`, `/exports/react`,
  `/exports/app-versions/{appId}/manifest`, `/runtime/v1/apps/{appId}/collections/{id}/records`,
  `/runtime/v1/apps/{appId}/actions/{actionId}/execute`, `/runtime/v1/apps/{appId}/manifest`.
- Assert camelCase response keys: `appId`, `clearedRecords`, `records`, `branch`, `output`,
  `deliveryId`, `requestId`.
- Assert error codes and status mapping from design plan section 4.3.
- Create fixture builders for a valid manifest v2 and a valid app version.

**Done when:** contract tests fail only because routes do not exist yet (red phase); no production
code changed.

---

## Step 2 — Domain models: app versions and data scope

**Files:** `src/novy/domain/generated_app.py`, `tests/unit/test_generated_app_domain.py`.

- `DataScope`: `preview | live` with strict validation.
- `AppVersion`: `id`, `owner_id`, `project_id`, `manifest` (immutable dict), `data_scope`,
  `created_at`; no FastAPI/Pydantic/PyMongo imports.
- `AppVersionError`, `InvalidManifestError`, `RuntimeAppNotFoundError` domain exceptions.
- Deterministic ID helper input: canonical manifest JSON with `generatedAt` pinned to the project's
  `updatedAt` before hashing — clock differences must not change the ID.
- Tests for immutability, invalid scope, and ID stability.

**Done when:** unit tests pass; ruff and mypy clean on new files.

---

## Step 3 — Strict manifest contract models

**Files:** `src/novy/application/manifest_contract.py`, `tests/unit/test_manifest_contract.py`.

- Pydantic models with `model_config = ConfigDict(extra="forbid")`:
  `GeneratedAppManifest`, `Project` (schemaVersion 13/14), `GeneratedLayout` (layoutVersion 2),
  `AppData` (`recordsIncluded: Literal[False]`), `Runtime`, `Security` (all three flags `False`),
  `ScreenDTO`, `BehaviorDTO`, `LogicGraphDTO`, action payloads (allowlisted types only), resource
  edges.
- Preserve unknown-but-safe draft fields through explicit compatibility fields rather than `extra="allow"`.
- Validation responses never echo submitted values.

**Done when:** valid frontend fixture parses; invalid fixtures (v12, wrong layout version, security
flag true, unknown field) fail with actionable issues; unit tests pass.

---

## Step 4 — Semantic manifest validation

**Files:** `src/novy/application/manifests.py`, `tests/unit/test_manifest_validation.py`.

- Orchestrate structural parsing (step 3) then semantic checks, reusing `domain/validation.py`:
  - unique screen/component/graph/node IDs and valid references;
  - `behaviors.targetNodeId` exists and `graphId` resolves; exactly one trigger per graph;
  - graph node/edge limits, acyclic control flow, typed ports and port cardinality;
  - action `collectionId`/`fieldId`/`targetNodeId` references declared in the manifest;
  - route paths and `routeParameters` map to collection record identities;
  - collection field types, required fields, identifier metadata;
  - capability inference matches `runtime.requirements` (missing requirement → error);
  - browser-only action types are valid in the manifest but flagged as server-nonexecuted;
  - maximum screens/nodes/graphs/edges/depth within configured limits.
- Return at most 50 issues plus `issueCount`; map failures to `422 invalid_manifest`.
- Explicitly reject unsupported capability requirements without generating a silent stub.

**Done when:** all invalid fixtures are rejected with stable codes; valid fixture passes; unit tests
pass.

---

## Step 5 — App version service and Mongo repository

**Files:** `src/novy/application/app_versions.py`, `src/novy/infrastructure/app_versions.py`,
`src/novy/infrastructure/migrations/app_versions_v1.py`,
`tests/unit/test_app_versions.py`, `tests/integration/test_app_versions_live.py` (opt-in flag).

- Repository port: `get(app_id)`, `get_by_owner_and_project(owner, project, scope)`,
  `get_or_create(version)`, `find_by_owner(owner)`.
- Collection `app_versions`: `app_id`, `owner_id`, `project_id`, `data_scope`, `manifest`, `created_at`.
- Indexes: unique `app_id`; `(owner_id, project_id, data_scope, created_at)`.
- Service: validate manifest (step 4), verify project ownership from the builder session, compute
  deterministic ID, create atomically, return existing version for identical input.
- Migration provisions indexes idempotently in the database selected by the connection string.

**Done when:** equal manifests with different export clocks yield the same ID; different owners or
scopes yield different IDs; migration is idempotent; unit tests pass; live Atlas test passes when
explicitly enabled (otherwise report the skip honestly).

---

## Step 6 — Preview data seeding and reset

**Files:** `src/novy/application/preview_data.py`, `tests/unit/test_preview_data.py`.

- On first preview preparation: copy the project's live records into the preview environment with
  fresh IDs, preserving field-ID-keyed values; bounded by the seed cap; atomic transaction across
  all copied records or none.
- Re-preparation of the same version is idempotent and never overwrites preview edits.
- Reset deletes only `owner_id + project_id + preview` records and returns `clearedRecords`; foreign
  or live records are never touched.
- Reuse existing `app_databases`, `app_collections`, `app_records` repositories and the
  `environment` field from `domain/app_data.py`.

**Done when:** seed/reset tests cover idempotency, cross-owner denial, live isolation, and atomic
failure on oversized seed; real transaction behavior tested against Atlas when enabled.

---

## Step 7 — Builder export routes

**Files:** `src/novy/presentation/http/exports.py`, `src/novy/presentation/http/export_schemas.py`,
`tests/contract/test_export_routes.py`.

- `POST /api/v1/exports/runtime-preview` — builder auth + CSRF/origin guards; body is the full
  manifest; returns `200 {"appId": "..."}`. Derive owner from the session, never from the manifest.
- `POST /api/v1/exports/runtime-preview/reset` — body `{"projectId": "..."}`; returns
  `{"clearedRecords": <int>}`.
- Thin routes only: validate shape, call `AppVersionService`, map domain errors to the shared
  envelope from `errors.py`.
- Wire state accessors in `composition.py` and include the router in `main.py` under `/api/v1`.

**Done when:** contract tests for both routes pass; unauthenticated and CSRF failures return 401/403;
invalid manifest returns `422 invalid_manifest`.

---

## Step 8 — Phase 1 quality gate

- `uv run ruff check src tests`
- `uv run ruff format --check src tests`
- `uv run mypy src tests`
- `uv run pytest` (record counts; no new skips without a reason)
- Optional live Atlas integration test with its explicit flag.

**Done when:** all checks pass; results recorded in `PROGRESS.md` and `CHANGELOGS.md`.

---

## Step 9 — Runtime domain and ports

**Files:** `src/novy/domain/runtime.py`, `tests/unit/test_runtime_domain.py`.

- `CanonicalRecord` with `id`, `projectId`, `collectionId`, `values`, `createdAt`, `updatedAt`.
- `RuntimeAction` resolution port, `ActionInputError`, `UnsupportedCapabilityError`,
  `RuntimeActionNotFoundError`, `RuntimeCollectionNotFoundError`.
- Validation for runtime values: depth 8, string 100,000, array 2,000, object 200 keys, key 200.
- Browser-only action type set (navigate, change-text, show-message, set-theme, set-variable,
  set-component-value, add-local-list-item, refresh-collection, chat-input/output/message, triggers,
  conditions) and deprecated granular RAG type set.

**Done when:** domain tests pass; no framework imports in domain.

---

## Step 10 — Secure action resolution

**Files:** `src/novy/application/runtime.py`, `tests/unit/test_runtime_resolution.py`.

- `RuntimeService.resolve(app_id, action_id, execution_id, inputs)`:
  1. load `AppVersion`; unknown → `404 runtime_app_not_found`;
  2. re-validate the frozen manifest and confirm the owning project still exists and is active;
  3. scan frozen `logicGraphs` for the action by ID; unknown → `404 runtime_action_not_found`;
  4. reject browser-only/deprecated types → `422 unsupported_capability`;
  5. enforce declared `inputMappings` — any undeclared input key → `422 invalid_runtime_input`;
  6. resolve the collection from the version's declared collections + server-derived owner/project/
     environment; unknown → `404 runtime_collection_not_found`;
  7. never accept an action payload from the browser.

**Done when:** tests prove no browser-supplied owner/collection/payload is trusted; exact error codes
asserted.

---

## Step 11 — CRUD action dispatch

**Files:** `src/novy/application/runtime_actions/crud.py`,
`tests/unit/test_runtime_crud_actions.py`.

- `get-records` → `{"records": [...]}`; `get-record` → `{"record": {...}}`;
  `insert-record` / `update-record` → `{"record": {...}}`;
  `delete-record` → `{"deletedRecordId", "collectionId"}`.
- Resolve record ID from `inputs.$recordId`, `inputs.recordId`, then static payload ID.
- Start from static `values`, overlay only declared non-`$` inputs; coerce numbers/booleans; enforce
  required fields and field types; generate backend IDs.
- Filter every output record's values to fields declared by the frozen version, even if the live
  collection gained fields later.
- Use transactions for multi-record mutations; never write across data scopes.

**Done when:** full CRUD lifecycle test returns canonical shapes; fields added after version creation
are filtered; cross-scope writes fail.

---

## Step 12 — Email action dispatch

**Files:** `src/novy/application/runtime_actions/email.py`,
`src/novy/application/ports/email_delivery.py`,
`src/novy/infrastructure/email/{development,unavailable,resend}.py`,
`tests/unit/test_runtime_email.py`.

- Payload `{to, subject, body}`; runtime inputs override matching static fields.
- Validate recipient syntax, subject 1–200, body 1–20,000 without echoing values.
- Idempotency key `{appId}:{actionId}:{executionId}`.
- Development adapter is side-effect free and returns `{"deliveryId": "email_dev_..."}`; disabled
  adapter → `503 email_delivery_unavailable`; production Resend adapter is configured but live
  delivery remains unverified until credentials exist.

**Done when:** output contains only the sanitized delivery ID; no recipient/provider data leaks.

---

## Step 13 — Idempotency storage

**Files:** `src/novy/infrastructure/runtime_executions.py`,
`src/novy/infrastructure/migrations/runtime_executions_v1.py`,
`tests/unit/test_runtime_executions.py`.

- Collection `runtime_executions`: `(app_id, action_id, execution_id)` unique, plus input fingerprint,
  status, canonical output, timestamps, expiry.
- Same key + same fingerprint → first result; same key + different fingerprint → `409 conflict`;
  failed executions stay failed (no partial side effects repeated).
- Indexes provisioned by migration; TTL used only for cleanup, never authorization.

**Done when:** replay and conflict tests pass; concurrent acquisition tested (real Atlas when enabled).

---

## Step 14 — Runtime HTTP router

**Files:** `src/novy/presentation/http/runtime.py`, `src/novy/presentation/http/runtime_schemas.py`,
`tests/contract/test_runtime_routes.py`.

- Register with `app.include_router(runtime_router)` in `main.py` — **no `/api/v1` prefix**.
- Routes:
  - `GET /runtime/v1/apps/{appId}/collections/{collectionId}/records` → `{"records": [...]}`
  - `POST /runtime/v1/apps/{appId}/actions/{actionId}/execute` → `{"branch": "success", "output": {...}}`
- Request schema: `{"executionId": str, "inputs": dict}` with `extra="forbid"`; validate runtime value
  limits; reject oversized bodies with `413 request_too_large`.
- Map application errors through the shared envelope; add `Cache-Control: no-store` and request IDs.
- CORS: public runtime routes allow `*` without credentials; leave the credentialed-route split ready
  for future identity/CV work.

**Done when:** contract tests assert exact paths, methods, response shapes, and error codes; the
frontend `ApiRuntimePreviewRepository` request shape is reproduced byte-for-byte in a test.

---

## Step 15 — Runtime wiring and configuration

**Files:** `src/novy/composition.py`, `src/novy/main.py`, `src/novy/infrastructure/settings.py`,
`tests/contract/test_runtime_availability.py`.

- Add `create_app_version_service`, `create_runtime_service`, and email adapters to composition.
- Initialize services in the lifespan only when MongoDB is configured; otherwise routes fail closed
  with a safe 503 instead of crashing at import.
- Add settings for seed cap, body limits, rate limits, generated-app origin, and preview renderer URL.
- Production startup must reject insecure/unconfigured critical settings per `BACKEND_PLAN.md`.

**Done when:** app starts and shuts down cleanly; unavailable-dependency paths return safe 503s;
tests cover both configured and unconfigured lifespans.

---

## Step 16 — Rate limiting and request hardening

**Files:** `src/novy/infrastructure/rate_limit.py` (extend), runtime middleware in
`src/novy/presentation/http/runtime.py`, `tests/unit/test_runtime_limits.py`.

- Buckets: runtime reads 240/min, actions 60/min (adjustable via settings).
- `429` includes `Retry-After` and `details.retryAfterSeconds`.
- Bound request bodies before parsing; never log bodies, prompts, media, tokens, or CSRF values.
- Note the existing in-memory limiter is single-process: release on Vercel requires a distributed
  store; record this as a known gate, not a silent omission.

**Done when:** limit and redaction tests pass; known limitation documented.

---

## Step 17 — Phase 2 quality gate

- Repeat step 8 commands; add contract tests from `ROADMAP.md` §12.3/12.4:
  canonical CRUD lifecycle, undeclared action/collection/input rejection, browser-only action 422,
  unknown action 404, email output sanitized, field filtering after version creation, execution
  replay/conflict, cross-owner denial.
- Run the real Atlas integration tests with the opt-in flag when a transaction-capable cluster is
  available.

**Done when:** all checks pass; results and skips recorded.

---

## Step 18 — Source generation port and capability gating

**Files:** `src/novy/application/source_generation.py`,
`tests/unit/test_source_generation_port.py`.

- Define `ReactSourceGenerator` port: `generate(app_version) -> GeneratedArchive(filename, bytes)`.
- Input is always the frozen, validated manifest from `AppVersionService` — never a raw browser body
  that bypasses validation.
- Before generation, re-check `runtime.requirements`: any requirement outside the v1 allowlist
  (`database`, `deployment`) is rejected with `422 invalid_manifest` rather than emitting a stub.
  (Browser capabilities such as `camera` are allowed; `rag`, `computer-vision`, `email` gating is
  decided by the allowlist chosen in step 0.)

**Done when:** unsupported requirements fail closed; port tests pass.

---

## Step 19 — Deterministic ZIP generation

**Files:** `src/novy/infrastructure/react_generator.py`, `tests/unit/test_react_generator.py`,
`tests/generation/fixtures/*`.

- Generate in memory with `zipfile.ZipFile(BytesIO)`; no shelling out, no Vite build inside the
  function.
- Layout:

```
novy-react-app/
├── package.json, vite.config.ts, tsconfig.json, index.html, README.md
└── src/
    ├── main.tsx, App.tsx, styles.css
    └── generated/
        ├── manifest.json, config.ts, routes.tsx
        ├── screens/<ScreenId>.tsx
        ├── renderer.tsx, theme.ts, bindings.ts
        ├── logicEngine.ts, runtimeClient.ts, dataScope.ts
```

- Filename follows `generatedAppManifestFilename` rules (`<slug>.zip`; frontend falls back to
  `novy-react-app.zip`). Output must be byte-identical for the same version.
- Never include records, secrets, builder tokens, raw media, or biometric templates; re-check
  security flags before packaging.

**Done when:** snapshot tests for representative manifests pass; repeated generation is identical;
secret-leak assertions pass.

---

## Step 20 — Generated logic engine

**Files:** generator template `src/generated/logicEngine.ts` (and `runtimeClient.ts`, `dataScope.ts`),
`tests/generation/test_logic_engine_parity.py`.

- Port the semantics of `../novy-web/src/domain/logic/runtime.ts`: behavior lookup by
  (`event`, `targetNodeId` → `graphId`), trigger start, condition branches, `MAX_RUNTIME_STEPS = 100`,
  input mappings (literal, component, variable, session, list-item, `$previous`), output map and
  variables map.
- Emit effects for: `change-text`, `show-message`, `navigate` (with record context), `set-theme`,
  `set-variable`, `set-component-value`, `add-local-list-item`, list/chart data, chat messages.
- Await `success`/`error` ports for async actions; server actions call
  `runtimeClient.execute(actionId, inputs)` with a generated `executionId` and reconcile returned
  records into every bound ListView (mirror `PreviewOverlay.reconcileRuntimeRecords`).
- Triggers and conditions are never posted to the server.

**Done when:** a generated-app fixture runs the Notes flow in a Node-based test (or static parity
snapshots if Node is unavailable, with the skip reason recorded); browser-only actions are never sent
to `/runtime/v1`.

---

## Step 21 — Generated renderer, routes, theme, bindings

**Files:** generator templates `renderer.tsx`, `routes.tsx`, `theme.ts`, `bindings.ts`,
`src/generated/screens/*`, `tests/generation/test_renderer_parity.py`.

- Render every supported component type with the same props and semantics as the frontend.
- Mobile-first responsive cascade: tablet inherits mobile; desktop inherits resolved tablet; breakpoint
  overrides may hide components.
- Appearance: light/dark tokens, gradients, shadows, radius, borders, opacity; design-system styles
  via `styleId`; ListView item templates; text typography and line clamp; accessible labels.
- Routes from `screen.path` + `routeParameters` with record identity from the URL.
- Bindings resolve current-item fields into text/image/control values with static fallbacks.

**Done when:** generated source passes `tsc` and Vite build in CI where Node is available (otherwise
record the optional skip); rendered fixture matches expected layout snapshot.

---

## Step 22 — Export route and error mapping

**Files:** `src/novy/presentation/http/exports.py` (extend), `tests/contract/test_export_react.py`.

- `POST /api/v1/exports/react` — builder auth + CSRF; validate manifest via step 4; generate from the
  frozen version; stream `application/zip` with `Content-Disposition`.
- Map errors to the codes `ApiReactExportRepository` parses:
  `invalid_manifest`/`validation_error` (422), `generation_timeout`, `generation_failed`,
  `rate_limited` (429), `session_required`/`invalid_session` (401),
  `auth_not_configured`/`auth_provider_unavailable` (503).
- Bounded generation timeout; safe failure messages with no provider internals.

**Done when:** contract tests assert archive headers, error codes, and that raw bodies are never echoed.

---

## Step 23 — Phase 3 quality gate

- Repeat step 8 commands plus generation tests.
- Verify the generated app's runtime calls match step 14's schemas exactly (contract test).
- Record whether the generated-source build check ran or was skipped and why.

**Done when:** all applicable checks pass; results recorded.

---

## Step 24 — Manifest read endpoints for previews

**Files:** `src/novy/presentation/http/runtime.py` (extend), `src/novy/presentation/http/exports.py`
(extend), `tests/contract/test_manifest_reads.py`.

- `GET /runtime/v1/apps/{appId}/manifest` — public, frozen, sanitized manifest for the shared renderer.
- `GET /api/v1/exports/app-versions/{appId}/manifest` — builder-authenticated variant for diagnostics.
- Both return only manifest content: no records, secrets, media, or builder identity.

**Done when:** contract tests confirm no sensitive fields and frozen immutability.

---

## Step 25 — Hosted shared preview renderer

**Files:** static renderer assets under `src/novy/presentation/preview/`, config in `settings.py`,
`vercel.json` static routing if needed, `tests/contract/test_hosted_preview.py`.

- Serve one generic SPA that loads the frozen manifest from step 24 and uses the same logic/renderer
  behavior as generated source (share templates with step 19–21 where practical).
- Strict CSP, no inline author JS, escaped text, no builder cookies or tokens; separate origin from
  the builder where practical; add the origin to runtime CORS allowlist.
- Additive `previewUrl` field on the prepare response; the frontend reads only `appId`, so this is
  backward compatible (confirm with the web workstream per step 0).

**Done when:** a builder can open the URL and see the rendered app; security headers and cookie
isolation asserted in tests.

---

## Step 26 — End-to-end "it works" acceptance

- From a builder session: prepare a preview, open the preview URL (or run the downloaded ZIP with
  `VITE_API_BASE_URL` pointing at the backend).
- Exercise the Notes flow: list loads from preview data, insert/update/delete persist and rerender all
  bound ListViews, click → change-text/navigate works, detail route resolves a record ID, theme and
  conditions behave as authored.
- Confirm live-environment records are unchanged.
- Write the generated ZIP `README.md` with local run instructions and the required backend URL.

**Done when:** the flow passes end to end; any unverified provider behavior is explicitly listed.

---

## Step 27 — Security and tenancy tests

**Files:** `tests/contract/test_runtime_security.py`, `tests/unit/test_generator_security.py`.

- Guessed/cross-owner `appId`, cross-project collection IDs, forged owner/role/environment fields,
  replayed `executionId`, oversized and deeply nested inputs, unsafe media/CSS text, streamed secrets.
- Assert no provider payload, token, cookie, CSRF value, raw media, or record from another scope
  appears in responses, logs, ZIPs, or error details.
- Fail-closed checks: RAG disabled → `503 rag_not_configured`; CV disabled →
  `503 vision_provider_unavailable`; no production simulation.

**Done when:** all negative tests pass; redaction verified in captured logs.

---

## Step 28 — Real database and migration verification

- Run real Atlas tests for: `app_versions` index uniqueness, preview seed/reset transactions,
  idempotency races, runtime CRUD scoping, and rollback visibility.
- Verify migrations are idempotent and provision indexes in the configured database.
- Record explicit skips when Docker/replica set or credentials are unavailable; never claim mocks prove
  transactional correctness.

**Done when:** live tests pass where available; skips and reasons documented.

---

## Step 29 — Final gate and documentation

- Full backend gate:

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
uv run pytest
```

- Coordinated frontend gate (no frontend contract changes):

```powershell
npm run typecheck
npm test -- --run --maxWorkers=2 --testTimeout=20000
npm run build
```

- Update `PROGRESS.md`, `CHANGELOGS.md`, and `BACKEND_PLAN.md` so implemented scope, contracts, and
  delivery gates match reality. Keep planned work visibly separate from completed work.

**Done when:** every command above has a recorded result; the Definition of Done in
`RUNTIME_EXPORT_PLAN.md` section 9 is fully checked or gaps are explicitly accepted.

---

## Open decisions before step 2

1. Confirm browser-only action types list and the v1 capability allowlist (steps 0, 18).
2. Confirm `previewUrl` is additive and safe for the frontend (steps 0, 25).
3. Decide whether the hosted renderer is served from the backend origin or a separate origin (step 25).
4. Confirm the distributed rate-limit requirement for Vercel multi-instance release (step 16).
5. Approve the MongoDB stack decision in `BACKEND_PLAN.md` (step 0).

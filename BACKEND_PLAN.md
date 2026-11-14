# Novy Backend Structure and Delivery Plan

**Status:** Approved direction and planned work, not an implementation report.

**Reviewed:** 2026-11-14

**Implementation inventory:** [PROGRESS.md](PROGRESS.md)

## 1. Starting Point

This repository now contains an initial FastAPI application, dependency lockfile, liveness route,
validated settings, a lifespan-managed MongoDB client, and focused tests. The internal builder-profile
repository and its explicit identity-index migration are implemented and were exercised in an
isolated Atlas scratch collection; `platform_db.builder_profiles` and its index were provisioned.
The configured collection had no real profiles when checked. An earlier Atlas probe verified
specific reads, writes, index enforcement, transaction commit, and observed rollback. Broader
application persistence and failure modes remain unverified. The repository does not yet contain
project repositories, Supabase integration, project or authentication APIs, hosted runtime, CI,
or a verified Vercel deployment. The liveness route checks only the API process. The sibling
frontend's clients, DTOs, local repositories, tests, and
simulations are integration evidence, not evidence of a working backend integration. See
`PROGRESS.md` for the current implementation inventory.

The product baseline is [PD6_A-04.pdf](../novy-web/project_information/PD6_A-04.pdf). Explicit
newer product-owner decisions in this plan override conflicting parts of that document. In
particular, PD6 UC-07 describes generating assets and submitting a separate Vercel deployment for
each application, including a three-minute target. V1 instead activates a validated, immutable
snapshot in a shared Novy-hosted prototype renderer. It does not generate a downloadable source
package or submit an application's assets to an external deployment. Neither the PD6 deployment
flow nor its timing target is claimed for v1. PD6's AI and administration scenarios are also not
evidence that production AI or an admin console is in scope.

## 2. Goals and Boundaries

### V1 goals

- Deliver a Python/FastAPI service on Vercel, MongoDB Atlas persistence in `platform_db`, and
  Supabase Auth, organized according to Clean Architecture.
- Let an authenticated, active builder create, list, edit, rename, duplicate, import, and delete
  owned projects and manage their project-scoped data. Provision template data atomically.
- Persist structurally safe but incomplete drafts. Enforce optimistic document revisions and return
  HTTP 409 `stale_revision` on a conflicting save; validate strictly before preview or publication.
- Run supported projects through a shared hosted renderer against preview data. Keep live project
  records separately authorized and isolated; preview testers must never read or write them.
- Support a shared generated-app identity realm, explicit project membership, and narrowly scoped
  runtime sessions. Allow only individually configured public operations; deny everything else by
  default.
- Enforce current session and account or membership status on each protected request, including
  disables performed through Atlas Compass.
- Coordinate the browser and backend contract changes and verify the real Vercel, Atlas, and Supabase
  paths before release.

### Non-goals and future scope

- Standalone downloadable source/export, external application deployments, per-project build
  pipelines, production AI generation, RAG, and biometric processing are deferred. Frontend source
  export code and simulation UI do not make those capabilities available; do not advertise them.
- V1 activates hosted prototype snapshots against the preview environment. It does not deploy or
  activate a live application; the live data boundary must still prevent access from prototypes.
- Unsupported email, camera, AI, RAG, biometric, or other actions must not silently become working
  runtime features. Define a positive v1 capability allowlist; reject unsupported requirements at
  strict validation with actionable issues. Browser-only features need an explicit security and
  capability decision before inclusion.
- The planned future website extension uses `projectType: app | website` metadata, defaulting
  missing metadata to `app` while retaining project schema version 13. Website functionality is not
  v1 unless separately approved. This is future metadata, not a claim that the current DTO contains it.
- A full admin console and generalized multi-builder collaboration are not implied by this plan.
  Operational account disable, auditing, and recovery are required; any broader administration
  interface needs its own authorization and scope decision.

## 3. Architecture

```text
Builder browser -> builder Vercel origin /api/* rewrite -> FastAPI on Vercel
       |                                                   |-> Supabase Auth
       |                                                   `-> Atlas platform_db
       `-> narrowly scoped, one-time handoff -> separate hosted runtime origin
                                                 `-> runtime /api/* rewrite -> FastAPI
```

The browser sees same-origin `/api/*` URLs at each frontend origin. The builder web application,
FastAPI service, and shared runtime renderer are separate deployable concerns. The renderer must
not share the builder origin or receive builder session cookies or Supabase provider tokens.
Production and Vercel deployment-preview infrastructure are separate from the product's `preview`
and `live` data environments; the two uses of "preview" must never be conflated.

### Dependency direction

- **Domain:** Framework-independent project, draft, record, identity, policy, snapshot, and
  validation concepts and invariants. No imports from FastAPI, Pydantic HTTP DTOs, PyMongo,
  Supabase, Vercel, or request objects.
- **Application:** Use cases, transaction boundaries, authorization orchestration, typed commands
  and results, and ports for repositories, sessions, identity verification, and time. Authorization
  is performed using server-derived principals and scopes, not request body roles or owners.
- **Infrastructure:** PyMongo async repositories, indexes and migrations, Supabase Auth/JWT
  adapters, encryption, configuration, and concrete clock or other required adapters.
- **Transport:** Thin FastAPI routes authenticate, check CSRF/origin, validate transport shape,
  invoke a use case, and map results or safe errors to versioned responses. DTO mapping stays here
  or in dedicated boundary mappers, not in the domain.
- **Composition root:** Construct settings, shared clients, adapters, use cases, and FastAPI
  dependencies in one place. Do not create abstraction layers without an actual boundary or use.

### Proposed layout

Names are illustrative scaffolding, not existing files or a requirement to create every module at
once. Keep features together within each layer and split files only when the implementation warrants
it.

```text
novy-backend/
  pyproject.toml, lockfile       Python/runtime constraints, dependencies, checks
  vercel.json                    deployment entrypoint or configuration if required
  src/novy/
    main.py                      ASGI entrypoint, app factory and lifespan
    composition.py               settings and dependency wiring
    domain/                      entities, policy and validation rules
    application/                 use cases, authorization, ports and unit of work
    infrastructure/
      mongo/                     async repositories, indexes, migrations
      auth/                      Supabase, JWT verification and protected sessions
      security/                  encryption and other concrete security adapters
    presentation/http/           routers, DTOs, cookies, CSRF and error mapping
  tests/
    unit/                        domain and use cases with controlled ports
    contract/                    HTTP/DTO compatibility and failure responses
    integration/                 real replica-set/Atlas and provider integration
  scripts/                       explicit migration or operational commands
```

Pin compatible Python and package versions supported by the selected Vercel runtime, and commit a
reproducible lockfile when scaffolding. Use PyMongo's native async API, not Motor. Build shared
clients in FastAPI's lifespan at the composition root, use them only with the event loop and client
that created them, bound pools and timeouts, and close them when lifecycle callbacks run. Confirm the
actual Vercel adapter invokes lifespan correctly; instance reuse and clean shutdown are not
guaranteed. Do not depend on shutdown, local files, in-process locks, or background tasks for durable
work. Bound aggregate connections across scaled-out functions.

## 4. Identity, Sessions, and CSRF

### Trust model

Supabase owns passwords and credential recovery. MongoDB owns Novy builder profiles, authorization,
protected server-side session state, and logical project `_users` membership. Never store passwords
in MongoDB. Use separate builder and generated-app identity realms, preferably separate Supabase
projects/configuration and issuers; whatever provider configuration is chosen must demonstrably
prevent a generated-app token from becoming a builder identity. Matching email addresses, a valid
token alone, provider `user_metadata`, or client-supplied role/owner fields do not grant builder
access or membership.

There is one shared generated-app Supabase Auth realm across projects: the same app-user account
uses the same password and recovery identity across apps. Signing into or registering in one project
does not automatically confer membership in any other project. A separate logical `_users` record,
unique per project, product environment, and provider subject, controls each membership. Project
signup/join must be explicitly permitted by that project's policy and handle an already-existing
shared identity safely. An active app-user identity without active membership is not an authorized
member. Preview membership and live membership are separate unless an explicit approved workflow
creates both.

The browser receives a high-entropy opaque session identifier, not a provider access or refresh
token. Store only a keyed hash of the identifier for lookup, and protect provider tokens at rest
using authenticated encryption, with separately managed, versioned encryption keys and a rotation
procedure. Sessions include realm, subject, creation/expiry/revocation, CSRF binding, and, for
runtime, project and environment; never store passwords. Cookies are `Secure`, `HttpOnly`, host-only
(no `Domain`), `Path=/`, with an appropriate `SameSite` setting; prefer the `__Host-` prefix where
feasible. A separate readable host-only CSRF cookie is not the session cookie. Use TLS end to end.

Validate provider JWTs using a maintained library and the realm's configured exact issuer, audience,
allowed signing algorithm, signature, expiry, and subject; apply appropriate clock skew and reject
algorithm confusion or wrong-realm keys. Prefer Supabase asymmetric signing keys and local JWKS
verification with bounded caching, unknown-`kid` refresh, key rotation/purge, and failure-closed
handling. JWKS is not available for legacy symmetric keys; if such a configuration is necessary,
follow Supabase's recommended server-side Auth verification and validate the configured claims and
algorithm. Never trust unverified decoded claims. A valid JWT does not replace the current MongoDB
session and authorization checks.

### Ordered browser flows

1. **Bootstrap:** A same-origin, non-cacheable bootstrap request creates or resumes a short-lived
   anonymous/pre-auth server session and issues its host-only opaque cookie plus a separate readable
   CSRF token. Bind the token cryptographically or by a server-side hash to that specific session.
   Bootstrap must not authenticate or perform other protected state changes. Limit and expire these
   sessions. The web client mirrors the CSRF value in `X-CSRF-Token` on unsafe methods.
2. **Register or log in:** Require the pre-auth session, matching CSRF header and cookie bound to
   that session, and an exact allowed `Origin` for the endpoint; define a conservative missing-Origin
   policy and verify it in browsers. Rate-limit. Call the correct Supabase realm outside any
   retryable MongoDB transaction, validate its result, then check/create only the appropriate
   server-owned profile or explicitly permitted membership. Rotate the opaque session identifier and
   CSRF token on authentication to prevent fixation. Return only the frontend-safe profile.
3. **Confirmation and recovery:** Support Supabase's email-confirmation-enabled signup. A pending
   registration is not a signed-in session; validate callback destinations and state, then perform
   the applicable login/session establishment. Use generic registration, recovery, and login
   responses that do not reveal whether an email is already registered. Reconcile the current
   frontend `email_already_registered` expectation with this enumeration-safe contract. Handle an
   existing shared app-user identity without leaking account existence or creating unauthorized
   membership. Document provider email configuration and failure/retry behavior.
4. **Protected request:** Resolve the opaque session from the correct realm and host; reject
   expired, revoked, invalid, or mismatched project/environment sessions independently of TTL
   deletion. Read the current builder profile or app-user identity and project membership
   `is_active` from the authoritative database on every applicable request. For builder runtime
   grants, also re-check the builder's active status and current project ownership. Reject disabled
   or missing status rather than falling back to anonymous access for that same request. Fail closed
   if the session or status store is unavailable.
5. **Explicit refresh:** Require the current session, CSRF token, and exact allowed origin. Serialize
   a provider refresh across Vercel instances with an atomic database lease/version and fencing or
   equivalent distributed coordination, not an in-memory lock. Call Supabase outside a retryable
   MongoDB transaction. Conditionally persist the encrypted rotated provider tokens only if the
   lease, session version, account status, and revocation state still permit it. A late refresh must
   never resurrect a logged-out session. Bound the provider call and lease, and do not assume a
   timed-out call stopped at the provider or that fencing alone prevents a second external call.
   Resolve uncertain outcomes conservatively using documented provider behavior; if safe recovery
   cannot be established, require reauthentication rather than blindly reusing a refresh token.
   Rotate session/CSRF material as the final coordinated contract specifies.
6. **Logout:** Require CSRF and origin validation, atomically revoke the server session, clear its
   host-only cookies, and prevent an in-flight refresh from restoring it. Revoke the provider session
   as supported, outside retryable transactions; distinguish local revocation from provider
   unavailability and make retries safe. Do not report success merely because `csrf_failed` was
   returned or the browser cleared local UI state. An already-revoked session can be idempotent only
   when revocation is established. A failed CSRF check requires re-bootstrap/retry or an explicit
   failure to the user.

Require session-bound CSRF plus origin validation for every unsafe cookie-authenticated operation,
including login, signup, refresh, logout, runtime handoff redemption, and explicitly public writes.
Anonymous public writes first obtain a bounded anonymous runtime session and CSRF token. CORS and
`SameSite` are defense in depth, not the authorization or CSRF mechanism. Exact browser origin
allowlists are configuration, not values derived from arbitrary `Host` or forwarded headers.

### Cross-origin builder handoff

The builder's broad session remains on the builder host. After builder authentication, ownership,
CSRF, and origin checks, issue a short-lived, single-use, high-entropy grant bound to the target
project, product environment, builder, and intended runtime origin. One viable implementation is
an origin-checked browser `postMessage` handshake to the trusted renderer, followed by a CSRF- and
origin-checked grant exchange on the runtime origin. Validate both message origin and source, use an
exact target origin, and atomically consume the server-stored hashed grant. Do not place it in URLs,
logs, or reusable browser storage. Issue a separate opaque runtime cookie whose authority is limited
to that project and environment. The runtime never receives the builder cookie or provider tokens.
The exact browser flow must be tested, including popup/iframe restrictions; if it cannot meet these
properties, do not ship `owner_only` runtime operations.

## 5. Authorization Policy

All decisions are server-side and default deny. `public` is a grant for one specific operation on
one specific resource, never an implicit grant to every operation or collection. `owner_only` means
the authenticated builder who currently owns the project, not the creator of a data record and not
an app-user with the same email. A runtime builder session proves only its narrow runtime grant and
does not authorize builder-management APIs.

| Operation | Active owning builder, management session | Project-bound runtime builder | Active project app-user member | Anonymous visitor |
| --- | --- | --- | --- | --- |
| Create/list/manage own projects, schemas and live data | Allow for owned scope | Deny | Deny | Deny |
| Save, rename, duplicate, import, delete, validate or activate snapshot | Allow for owned scope and required preconditions | Deny | Deny | Deny |
| Read hosted prototype content | Only via a separately issued runtime grant where needed | Only the specific public or builder-granted read within the project's environment and active snapshot | Only if the specific runtime read is explicitly public | Only if the specific runtime read is explicitly public |
| Runtime collection read/create/update/delete | No direct runtime authority from management cookie | Only the specific public or builder-granted operation, including `owner_only` | Only the specific operation explicitly marked public | Only the specific operation explicitly marked public; unsafe requests need CSRF |
| Manage logical `_users`, policies, or sessions | Owner, through a dedicated management use case | Deny | No management rights merely from membership | Deny |
| Another builder's project, another environment, or platform administration | Deny absent a separately designed privileged admin workflow | Deny | Deny | Deny |

V1 has only the explicitly permitted `public` and `owner_only` operation rules. A signed-in
project member does not gain extra collection access merely by being a member, and no generic
record-creator ownership rule is inferred. Membership supports app-user identity and session
context; additional member-only policies require product approval. Policies must specify resources
and operations, with validated field exposure and constraints where relevant. Do not expose an
entire record, membership list, or administrative field merely because an operation is public.
Anonymous visitors cannot be individually disabled as an account; use rate limits, abuse controls,
and policy changes instead. A disabled logged-in identity is rejected rather than silently gaining
an anonymous fallback within the request.

Apply project and product-environment scope to every nested read, mutation, aggregate, lookup,
background job, and cache key; verify parent-child relationships as well as IDs. Scope comes from
the authenticated builder or an authorized runtime session/route and the current active snapshot,
not a trusted client flag. If a management endpoint accepts a requested environment, authorize and
resolve it server-side. Scope `$lookup` and all aggregation stages that can read other resources;
never pass raw client query operators into MongoDB. Recheck session/status and the current active
policy on each protected runtime request, including after policy changes or snapshot replacement.

## 6. Persistence, Indexes, and Consistency

Use shared physical collections in `platform_db`, not a MongoDB database or collection per user
project. Names below are logical design candidates. IDs exposed by the API are strings; persisted
project/database/collection/record IDs are MongoDB ObjectIds. Embedded screen, component, graph,
node, and field IDs retain their existing string identities and references.

| Logical collection | Main fields and purpose | Required index/constraint direction |
| --- | --- | --- |
| `builder_profiles` | Provider issuer/subject, display profile, explicit `is_active`, timestamps | Unique `(issuer, subject)`; do not use email as authority |
| `app_user_identities` | App-realm issuer/subject, platform-level `is_active`, minimal profile | Unique `(issuer, subject)`; no credentials |
| `project_memberships` | `project_id`, `environment`, app-user subject/identity, `is_active`, server-managed membership data; logical `_users` | Unique `(project_id, environment, identity_id)` and scoped lookup index |
| `sessions` | Hashed opaque ID, realm, principal, expiry/revocation, CSRF binding, encrypted provider tokens, refresh version/lease, optional project/environment | Unique token hash; scoped principal/expiry indexes; TTL only for cleanup |
| `projects` | ObjectId, owner, name, timestamps, draft JSON, independent `schemaVersion` and revision, lifecycle state and configuration/data fences | `(owner_id, updated_at, _id)` for listing; owner/id/revision CAS; owner/state filters |
| `app_databases` | ObjectId, project ID, logical database name and timestamps | Project-scoped list/lookup; any name uniqueness must be explicitly specified |
| `app_collections` | ObjectId, project/database IDs, field definitions with embedded field IDs, record identifier and draft schema/policies | `(project_id, database_id, _id)`; enforce parent scope and chosen name rules |
| `app_records` | ObjectId, project/collection IDs, product environment, field-ID-keyed values and timestamps | `(project_id, environment, collection_id, _id)` and indexes for supported scoped queries |
| `snapshots` | Immutable validated project revision, schemas, policies and renderer/capability version | `(project_id, environment, snapshot_id)`; active pointer switched atomically; bounded contents |
| `idempotency` and handoff grants | Owner/operation/key/request digest/result or single-use grant binding and expiry | Unique `(principal, operation, key)` or unique grant hash; expiry index for cleanup only |
| `audit_events` | Minimal server-derived actor, action, scope, outcome, correlation ID and timestamp | Scoped time index and controlled retention; no secrets or raw payloads |

Indexes are versioned migrations, not startup side effects. Decide actual uniqueness, partial index,
collation, cursor sort, and deletion behavior from the contract, then test real enforcement. Shared
collection reads and indexes include `project_id` and `environment` wherever data is
environment-specific, including preview seed, runtime sessions/memberships, caches, and jobs.
Application-level query guards are mandatory even when ObjectIds are globally unique. User-defined
names never become physical MongoDB database names, query operators, or unchecked field paths.

Represent arbitrary JSON safely: preserve values and keys without treating user content as MongoDB
operators or update paths. Choose and test a reversible representation for problematic keys; reject
input that cannot be represented safely rather than discard or reinterpret it. Record `values` are
keyed by **field ID**, not field label/key. One mocked frontend fixture uses the field key; reconcile
that test instead of silently normalizing or corrupting real data. Validate types, identifiers,
required fields, allowed field IDs, and server-generated record IDs at the relevant data boundary.

For protected requests, read session, account, and membership status from the primary without a
stale authorization cache. Enforce explicit `is_active: true`; missing or disabled is denied on the
next protected request after the disable is acknowledged. Define and test read/write concerns,
including Compass's administrative write settings and failover, so acknowledged disables are not
lost or served from a lagging secondary. In-flight requests that began before a disable cannot be
retroactively undone; subsequent requests must observe it. TTL expiry is cleanup, never a security
decision. Re-enable and session invalidation behavior must be documented and tested.

Use real MongoDB transactions on a replica set or equivalent transaction-capable Atlas topology
when multiple documents must become visible together. Keep transaction operations sequential and
bounded. Retry transient transaction/unknown-commit outcomes using driver semantics and idempotent
operations. No Supabase calls, emails, browser responses, or other non-idempotent external effects
inside retryable callbacks. Use majority durability and suitable snapshot/primary reads where
needed; establish operational settings and test them rather than assuming a mock proves atomicity.

## 7. Project and Data Workflows

### Drafts, revisions, and templates

- Accept bounded JSON with valid transport shape, supported schema version, safe representation,
  and safe nesting/size, while retaining incomplete or semantically invalid drafts. Preserve
  unknown supported draft content and all responsive layouts, routes, behavior graphs, bindings,
  and embedded IDs; do not force complete-project validation on save.
- `schemaVersion` describes the project format; `revision` is an independent monotonically
  changing save/concurrency token. Current frontend mappers write schema version 13. Support only
  explicitly identified legacy versions and migrations proven against frontend fixtures; reject
  unsupported future or unsafe versions clearly, never rewrite them speculatively.
- The current client sends quoted `If-Match: "<revision>"` with document saves. Atomically compare
  project ID, server-derived owner, active lifecycle state, and expected revision and increment the
  revision with the write. Return the updated project and revision. A missing precondition is
  `precondition_required`; a genuine mismatch is HTTP 409 `stale_revision`. Do not leak project
  existence across owners.
- The current repository refreshes its revision after a conflict and autosave may retry the old
  document, risking lost writes. Coordinate a client fix: stop automatic overwrite on 409, retain
  local edits, show conflict, load the remote version separately, and require explicit user
  resolution/rebase before a new conditional save. For a lost successful response, use a stable
  save operation key or safe reconciliation that proves the committed document is the same;
  otherwise treat it as a conflict, not permission to overwrite.
- The frontend sends a template DTO with project creation and marks remote creation as provisioning
  template data. Validate the blueprint, allocate IDs, remap only typed aliases in screens, routes,
  logic and data bindings, and commit the project, database, collections and sample records together.
  The Notes CRUD template is a required example. No partial project or duplicate client-side
  provisioning may remain after a failed or retried creation.

Rename and save must not overwrite each other: use separate atomic updates or a shared version/CAS
protocol and specify which revisions change. Rename must not replace the draft document. Delete
must atomically make the project unavailable and revoke or invalidate runtime access before any
potentially bounded cleanup; coordinate child writes with a project lifecycle fence so a concurrent
write cannot resurrect deleted data. Either complete bounded cascades in a transaction or use a
durable, retryable scoped cleanup mechanism with a tombstone and operational recovery. Do not assume
a serverless process keeps running after the response. Define idempotency for create, duplicate,
import, publish, and retryable deletion; distinguish a duplicate request from a distinct user action.

### Strict preview and publication

Before preview or publish, validate the exact draft and schema revision, screen and node identities,
routes/parameters, responsive layout, logic graph references and ports, data bindings, field types,
collection/record references, policy shape, exposed fields, and the supported runtime capability
allowlist. Coordinate issue codes and locations with the frontend validator. Client validation is
helpful but never a server trust boundary. Report actionable errors without modifying the last
usable snapshot; never silently simulate an unsupported feature as production functionality.

Create an immutable snapshot containing the validated project document, data schemas, access
policies, renderer/capability version, and source revision. Bound the number and size of documents
and the transaction work. A snapshot's configuration never changes; the preview dataset used by
testers is a separate mutable set of records scoped to `project_id + preview`. Live records are
separately scoped to `project_id + live`. V1 runtime writes affect preview records only; they do not
alter a snapshot or leak into live data.

Read a consistent view and atomically create the snapshot and switch the active prototype pointer,
using project/configuration revision fences that are also updated by concurrent schema and policy
changes. Include a preview-data fence if validation depends on particular preview records. CAS/write
the relevant fence in the transaction so an edit cannot slip between validation and activation
under snapshot isolation. Abort/retry or report a conflict if the source changes. If multiple
snapshot documents are needed, none may be visible via the active pointer until the complete
bounded transaction commits. Failed validation, transaction, or activation preserves the previously
active prototype and data.

Prototype activation must not reset existing tester data without an explicit builder action; any
initial template seeding or reset is limited to the preview environment. Never automatically copy
preview records or memberships into live data. Every runtime request resolves and reauthorizes
against the current active prototype snapshot so a stale session cannot keep revoked permissions.
Record hosted status and URL only after activation succeeds; a failed update leaves the previous
prototype snapshot and URL usable. A future live activation workflow requires its own approved
scope, schema-compatibility checks, and data migration policy.

### Duplicate

Default to schema-only duplication: copy the project and data schema, allocate new persisted
project/database/collection IDs, preserve embedded IDs, remap known typed references, and copy no
records or memberships. An explicit frontend toggle may request record copying; allocate fresh
record IDs and remap known typed record references in that case. Source access and destination
ownership are server-derived. Do not copy provider sessions, credentials, app-user memberships,
live runtime grants, or published state. Define the source dataset and preview/live behavior in the
coordinated UI rather than implicitly copying across environments.

Schema-only copies can contain typed references to source records in navigation context or logic
actions. Never leave those pointing into the source project, silently erase user content, or treat an
arbitrary ID-looking string as a reference. If a known typed record reference cannot be safely
preserved without the record, reject the duplicate with a precise issue and let the builder resolve
it or explicitly choose the copy-records option. The copy and its idempotency result become visible
atomically within bounded transaction limits.

Distinguish concrete IDs in typed record-ID slots from dynamic expressions such as `$recordId` and
their empty fallbacks. Remap a concrete ID only when its referenced record is copied; preserve
dynamic expressions and unrelated ID-looking text without guessing at their runtime value.

### Import

The existing frontend sends `{ project, appData: { databases, collections, records } }` to its
import client. Local projects remain in browser storage. Import is not
standalone source export. Implement a bounded, all-or-nothing import:

1. Authenticate the builder, enforce CSRF and limits, validate transport/schema version, check
   duplicate old IDs and parent relationships, and inventory all typed references. Never trust
   incoming owner, timestamps as authorization, or project/environment scope. Reject inconsistent,
   unsupported, ambiguous, or dangling typed references with actionable paths.
2. Allocate fresh MongoDB ObjectIds for every persisted project, database, collection, and record.
   Build separate typed old-to-new maps for each resource kind; do not assume IDs of different kinds
   cannot collide. Preserve embedded screen/node/graph/field IDs and their internal references.
3. In a second pass, rewrite only known typed resource references: parent links, collection-backed
   list sources and routes, navigation record context, logic actions such as get/update/delete,
   and other supported typed paths. Apply record maps only to actual typed record references.
   Preserve arbitrary text, unknown safe draft fields, and field-ID-keyed values exactly, even when
   strings look like ObjectIds. If the format cannot distinguish a typed reference safely, reject
   the import rather than globally replacing strings or silently dropping a value.
4. Atomically persist the complete imported project, data, and an owner/operation-scoped
   idempotency result. A retry with the same stable key and same payload digest returns the original
   result after a lost response; reuse of the key with a different payload is a conflict. Do not
   depend on comparing old IDs with new remote project IDs.
5. Coordinate an account-scoped frontend import marker and stable request key. Only mark success
   after a confirmed or idempotently recovered response. Keep local data on failure or 413; do not
   silently truncate, partially import, or delete it.

Do not import untrusted provider credentials, sessions, app-user membership, published grants, or
other security state from an exported project. Define the destination environment for imported
records explicitly in the contract; it must not be selectable by an untrusted runtime client or
silently cross from preview into live.

## 8. HTTP Contract and Web Coordination

All rows are **planned backend behavior**. The first column below identifies requests the current
sibling frontend code constructs, not implemented server routes. Paths are relative to its API
base URL; the intended browser-facing base is same-origin `/api/v1`, routed by an `/api/*` rewrite.
Exact prefix handling and response details must be checked through the real rewrite. Any proposed additions are design placeholders
until the two repositories agree on an OpenAPI contract.

| Current frontend request | Existing client expectation | Planned backend/coordination |
| --- | --- | --- |
| `GET /auth/session`; `POST /auth/login`, `/auth/register`, `/auth/logout` | User object `{id, displayName, email}`, pending confirmation flag on signup, cookie credentials | Add bootstrap and explicit refresh operations; session-bound CSRF on all unsafe calls; generic enumeration-safe signup; fix logout failure handling |
| `GET /projects`; `POST /projects` | `{projects: [...]}` summaries; create sends `{name, template}` and expects a project with `revision` | Owner-scoped list and atomic template/data provisioning; add safe create idempotency and coordinate pagination |
| `GET /projects/{id}`; `PUT /projects/{id}/document` | Full project DTO, quoted `If-Match`, `schemaVersion: 13`, screens and revision | Scoped CAS, missing-precondition error, 409 `stale_revision`, safe client conflict resolution and lost-response handling |
| `PATCH /projects/{id}`; `POST /projects/{id}/duplicate`; `DELETE /projects/{id}` | Rename body `{name}`; duplicate currently sends no copy option; delete treats not found as complete | Race-safe rename/delete, schema-only duplicate default, coordinated explicit copy-records toggle and idempotency |
| `POST /projects/import` | `{project, appData}` and project DTO response; current local marker is global | Typed two-pass remap, account-scoped marker and server idempotency key; explicit bounded 413 behavior |
| `GET/POST /projects/{id}/databases`; `PATCH/DELETE .../databases/{databaseId}` | Database lists are bare arrays; objects use string IDs and ISO dates | Builder-scoped logical data; authorize project and parent IDs; environment routing/contract must be coordinated |
| `GET/POST /projects/{id}/collections`; `GET/PUT/DELETE .../collections/{collectionId}` | Collection lists are bare arrays; optional `databaseId` filter, fields and record identifier | Scoped schema/field validation, safe parent checks and atomic dependent changes |
| `GET/POST .../collections/{collectionId}/records`; `PUT/DELETE .../records/{recordId}` | Bare record arrays and `{values}` payloads | Field-ID-keyed values, server-created IDs, scoped record CRUD; coordinated pagination and explicit environment |

| Proposed addition, not a current frontend request | Purpose and required coordination |
| --- | --- |
| CSRF bootstrap and explicit session refresh operations | Agree method/path, cookie names/rotation, pending registration, retry and client lifecycle. Bootstrap is required before login and anonymous writes. |
| Preview validation/snapshot activation and hosted runtime bootstrap | Agree issue DTOs, product-environment selection, preview data initialization/reset, short-lived builder handoff, runtime session and renderer URLs. |
| Prototype activate, status, and optional unpublish operations | Activate a validated preview snapshot, preserve the previous prototype on failure, and expose safe status/URL. This is not a live deployment or per-project Vercel build API. |
| Runtime app-user registration/login/refresh/logout and allowed data operations | Separate runtime origin, identity realm, membership authorization, project/environment-bound cookies, CSRF, and operation-specific policy. |
| Cursor-based project/data listing and idempotency metadata | Version and coordinate response envelopes, page size, cursor encoding and full-list consumers before replacing current arrays; no silent truncation. |

The existing generated-app manifest is a frontend schema/configuration handoff, not a backend API or
proof of generated source. Its version and `recordsIncluded: false` do not determine how preview
records are stored. The current preview can opt in to writing configured
project data and publish is simulated locally; both flows need deliberate changes to use the
server-enforced environment and real hosted activation. The current Axios client has a 10-second
timeout and mirrors readable `novy_csrf` into `X-CSRF-Token`; it has no explicit refresh flow.

Use a stable, versioned error envelope compatible with the current client where possible:
`{"error":{"code":"...","message":"...","details":{"issues":[...]}}}`. Map authentication
failure to 401, CSRF/origin denial to 403, unauthorized/missing nested resources to a non-revealing
403/404 policy, conflict to 409, oversized payload to 413, missing revision precondition to 428,
validation to a coordinated 4xx, rate limit to 429, and provider/database outages to safe 5xx.
Specify codes, details, retryability and request IDs in OpenAPI and contract tests. Do not expose
tokens, internal exceptions, provider payloads, or cross-tenant existence. A Vercel-generated 413
can bypass FastAPI and its envelope; the client must handle both platform and application responses.

## 9. Limits and Runtime Safety

MongoDB generally limits a BSON document to **16 MiB** and **100 levels of nesting**; selected
Atlas tiers can impose a lower nesting limit. Vercel's
current Functions documentation states a **4.5 MB** request or non-streamed response body limit;
platform or plan limits may change, and upstream rewrites can add constraints. The current frontend
times out after **10 seconds**. Therefore set and document application limits comfortably below the
smallest effective transport, BSON, transaction, memory, and time limits; measure serialized JSON
and BSON, not just character counts. Establish caps for nesting, screens/nodes/graphs, fields,
records, value sizes, list pages, imports, snapshot size, and transaction operations before
implementation is released.

Reject oversized imports and drafts before committing anything, with actionable 413 or validation
issues where the application receives the request. Preflight in the client where possible and handle
Vercel's own 413. V1 may reject an oversized non-staged import while leaving browser-local data
intact; staged/chunked import is not promised. Do not silently discard fields, return partial lists,
or claim a timeout means a write failed. Use idempotency and reconciliation for ambiguous outcomes.
Coordinate bounded pagination with the frontend, which currently expects unpaginated arrays and
full-list consumers. Until the new contract is implemented, cap the underlying data or return an
explicit actionable error rather than return an incomplete successful response. Set database,
provider, and function deadlines consistent with the browser contract, and revisit the timeout with
the web team if an operation cannot reliably finish within it.

The renderer must consume only validated, supported declarations, never arbitrary user JavaScript
or unchecked HTML as trusted code. Apply context-appropriate escaping/sanitization, restrictive CSP,
safe navigation/URL schemes, and request/response limits. Prefer separate per-project runtime
origins, including product environment where practical, to limit cross-project browser exposure. If a
shared runtime origin is chosen, prove equivalent project isolation for cookies, CSRF, storage,
untrusted content, and messaging before release. Do not cache personalized, authorization-sensitive,
or session responses at the CDN; any deliberate public cache must include project, environment,
snapshot and policy scope and have tested invalidation.

## 10. Deployment and Operations

- Configure the builder's Vercel origin to rewrite `/api/*` to FastAPI without a browser redirect.
  Configure the separate runtime origin's API route equivalently. Check actual forwarded paths,
  `Set-Cookie`, host-only cookie scope, `Origin`, CSRF headers, errors, and cache headers in real
  browsers through the deployed rewrite, including registration confirmation and refresh/logout.
- A publicly reachable backend is not automatically a trusted proxy. Enforce authentication,
  authorization, CSRF/origin, and input validation on direct requests too; never authorize based
  solely on forwarded host/IP headers. If trusted proxy metadata is required, use a verified
  Vercel-to-origin restriction or service binding and strip/overwrite client headers, then test
  bypass attempts. Use a verified client-IP source for IP rate limits or choose a safe alternative.
- Isolate production and Vercel deployment-preview configuration, databases, Supabase projects,
  encryption keys, origins, cookies, and secrets. Within each infrastructure environment, product
  `preview` and `live` remain separate server-enforced data scopes. Never point an untrusted preview
  deployment at production credentials or data. Specify preview deployment access controls.
- Validate settings at startup: Atlas TLS URI/database, separate realm issuers/audiences and public
  key configuration, application encryption keys, exact builder/runtime origins, cookie and session
  lifetimes, body limits, and provider/database deadlines. Fail closed on missing production
  security settings. Store secrets in the deployment secret manager, use least-privilege Atlas and
  Supabase credentials, and document rotation/revocation. Never expose provider service secrets in
  frontend bundles.
- Deploy versioned indexes/migrations deliberately, with backup/rollback procedures, not on every
  request. Configure Atlas network access suitable for Vercel's actual egress; do not assume stable
  source IPs without the corresponding feature. Test connection limits, replica-set transactions,
  pool behavior, and recovery during failover.
- Establish encrypted backups and retention for platform data, snapshots, and preview/live records;
  test restores into an isolated environment and protect backup access. Document deletion, data
  retention, key recovery, and incident response. Audit sensitive administrative and policy actions
  without credentials, tokens, cookies, CSRF values, raw request bodies, or arbitrary record data.
- Add bounded distributed rate limits for login, signup, refresh, bootstrap, import, and public
  runtime operations; do not rely on per-instance memory. Log structured request IDs, safe scope,
  outcome, latency, conflict and provider failure counts with redaction. Add alerts for security
  anomalies, failed jobs, transaction failures, capacity and backup/restore status. Health/readiness
  endpoints must not disclose secrets or claim downstream availability without checking it.

## 11. Phased Delivery and Acceptance

Phase 1 has begun with the FastAPI and MongoDB foundation, and Phase 2 has begun with internal
builder-profile persistence. Their remaining verification, CI, deployment, and authentication gates
are not complete. A phase is accepted only when its dependencies and stated checks pass;
cross-repository changes require separate authorization.

### Phase 0: Contract and capability baseline

**Dependencies:** Product decisions in this plan and the current sibling frontend.

Define the versioned OpenAPI/error contract, exact supported project/legacy versions, runtime
capability allowlist, auth realms, data and snapshot bounds, and frontend change list. Inventory
typed reference paths and decide pagination and idempotency semantics. Confirm deployment origins,
provider sign-in/confirmation settings, and real transaction-capable test topology.

**Acceptance:** Reviewed contract fixtures cover current DTOs and proposed additions; conflicts
with the frontend are assigned rather than silently papered over. No UI advertises unsupported v1
capabilities as implemented.

### Phase 1: Foundation

**Dependencies:** Phase 0 contract, runtime/version and infrastructure choices.

Scaffold the package and composition root, pinned dependencies, FastAPI lifespan, validated settings,
safe errors/logging, async PyMongo client, initial migrations/index management, and CI checks. Set up
a real local or dedicated test replica set and an isolated Vercel deployment path.

**Acceptance:** Unit/contract tests and lint, formatting and type checks run in CI; app startup,
shutdown and connection behavior are verified in the target runtime; real MongoDB indexes and a
transaction commit/abort are exercised. No provider integration is claimed solely from a mock.

### Phase 2: Builder authentication

**Dependencies:** Phases 0-1; Supabase builder realm and secret management.

Implement pre-auth CSRF bootstrap, enumeration-safe signup/confirmation, login, protected session,
distributed refresh and logout, per-request active-profile checks, origin validation, encryption,
and rate limits. Coordinate the builder client's bootstrap/refresh/logout behavior.

**Acceptance:** Real-browser rewrite tests prove cookie scope, CSRF/origin rejection, confirmation,
refresh and logout. Real Supabase tests cover JWT key rotation/expiry and provider failures. Disable
through Atlas is effective on the next protected request; concurrent refresh/logout cannot revive a
session. False logout success and session fixation tests fail before the client fix and pass after.

### Phase 3: Projects and management data

**Dependencies:** Phases 1-2 and agreed DTOs/limits.

Implement owner-scoped project CRUD, loose drafts, revision CAS, atomic template provisioning,
logical database/collection/record CRUD, safe queries, indexes and deletion fencing. Coordinate
frontend conflict resolution and data pagination/environment selection.

**Acceptance:** Two simultaneous saves yield one success and one 409 without lost updates; rename,
save and delete races are safe. Notes template and sample data commit or roll back together. Real
replica-set tests reject cross-user, cross-project and nested-resource access. Supported legacy
fixtures round-trip without losing IDs or content.

### Phase 4: Import and duplication

**Dependencies:** Phase 3, reference inventory, bounded request and idempotency contracts.

Implement schema-only duplicate and explicit copy-records option, two-pass typed import remapping,
fresh persisted ObjectIds, idempotency records, account-scoped frontend markers, and failure paths.

**Acceptance:** Cover cross-kind ID collisions, embedded references, record-aware navigation,
concrete typed record-ID mappings, dynamic expressions and their empty fallbacks, raw ID-looking
text, dangling references, oversized input, transaction abort and lost-response retry. No partial or
duplicate project is visible; local data survives every failed import.

### Phase 5: Preview, runtime and publication

**Dependencies:** Phases 2-4, strict capability/policy contract, and an authorized renderer/frontend
implementation in the separate web workstream.

Implement strict validation, bounded atomic immutable prototype snapshots, environment-scoped preview/live
records, narrow builder handoff, separate generated-app identity/membership and runtime sessions,
default-deny per-operation policy, and hosted activation/status. Replace simulated publish and
unsafe preview persistence in the web application through coordinated work.

**Acceptance:** A real browser exercises the supported Notes flow. Concurrent snapshot edits either
activate one consistent version or conflict; failure leaves the prior prototype snapshot usable. Preview
testers cannot read/write live records, and changes never copy to live implicitly. Public operations
work only when individually enabled; `owner_only` admits only the actual active project builder via
the narrow runtime session. Wrong realm, disabled membership, stale policy and cross-project grants
are denied on the next request.

### Phase 6: Release readiness

**Dependencies:** Phases 0-5 and deployed isolated production-like environments.

Run the full backend and coordinated frontend checks, threat/permissions review, real provider and
Vercel integration tests, operational restore and key-rotation exercises, capacity/timeout checks,
and a documented rollout/rollback. Close or explicitly accept remaining risks without calling an
untested feature complete.

**Acceptance:** Every release gate in section 12 has evidence, deployment-preview isolation is
verified, live-provider credentials and procedures are owned, and the product owner accepts any
documented limitations. Update implementation inventory and changelog as implementation proceeds.

## 12. Verification and Release Gates

- **Unit:** Domain rules, loose versus strict validation, policy matrix, ID maps, arbitrary value
  preservation, version migrations, CSRF/session lifecycle, encryption/key versions, bounds, error
  mapping, and capability rejection. Use controlled clocks and provider ports for failure paths.
- **Contract:** Exercise real FastAPI request/response DTOs, current frontend client fixtures,
  quoted `If-Match`, error envelopes, missing preconditions, 409, pagination, idempotency keys,
  pending signup, and platform-generated error handling. Update both repositories' tests when the
  contract changes; mocked client tests alone do not establish backend existence.
- **Real MongoDB:** Run against a transaction-capable replica set and the selected Atlas tier:
  migrations/index uniqueness, scoping on all CRUD and aggregates, transactional rollback,
  write/commit retries, lost responses, concurrent saves, template provisioning, import,
  duplicate, lifecycle fences, snapshot consistency and failover. Include current-status reads
  after Compass disable and TTL-independent expiry checks.
- **Security/tenancy:** Exercise two builders, two projects, preview/live, app users in different
  memberships, disabled builder/app identity/membership, anonymous callers, stale runtime sessions,
  wrong realm/audience/issuer/algorithm, JWT expiry and JWKS rotation. Try guessing IDs,
  cross-project child IDs, forged roles/owners/environments, unsafe filters/aggregates, CSRF, origin,
  direct-backend and forwarded-header bypasses. Verify public operation granularity and that a
  record creator is not treated as the project builder.
- **Concurrency/failure:** Test refresh versus refresh/logout/disable, provider timeout after token
  rotation, lease expiration, replayed handoff, save/rename/delete races, publish versus schema/data
  edits, retries with changed payload, partial external failures, unavailable status store, 413,
  rate limiting, and client timeouts. No secret may appear in logs or safe error responses.
- **Deployed integration:** Test actual Vercel rewrites and browser cookies for both origins,
  production-like and deployment-preview isolation, real Supabase confirmation/login/refresh/logout,
  Atlas Compass disable, hosted runtime and preview/live isolation. Confirm no builder cookie or
  provider token reaches the runtime. Record which providers/topologies were actually exercised.
- **Operational gate:** Verify backups with a restore, secret rotation and incident response,
  monitoring/alerts, least privilege, reasonable load/connection/timeout and request-size limits,
  documented supported capabilities, and rollback to the prior snapshot. Run the complete
  available backend suite, lint, formatting, and type checks, plus relevant frontend tests. Do not
  substitute mocks for the live browser, Atlas, Supabase, or Vercel checks.

## 13. Open Implementation Decisions

These details remain to be selected without reopening approved scope or weakening its invariants:

- Exact Python and dependency versions, Vercel entrypoint, provisioning method, Atlas tier/network
  access, pool and timeout values, and local replica-set setup.
- Separate Supabase projects versus another demonstrably isolated realm configuration, asymmetric
  signing-key setup, verified signup/confirmation/recovery policy, membership join workflow, and
  narrowly scoped provider permissions.
- Exact session/CSRF cookie names and lifetimes, refresh lease/fencing protocol, key management and
  rotation, precise logout/provider-failure response, and secure runtime handoff UX.
- Runtime hostname strategy and validated browser isolation, per-operation policy DTO,
  capability allowlist, renderer versioning, preview reset UX, and whether any
  explicit live-data initialization workflow is later approved.
- Concrete numerical limits, supported legacy version/migration matrix, reversible JSON storage
  representation, pagination format, idempotency key retention, and behavior for incompatible
  live schemas or transaction-size limits.
- Exact snapshot/data fence implementation and cleanup mechanism, index definitions and migration
  sequencing, deletion retention, backup recovery objectives, audit retention, and distributed
  rate-limit implementation. Any chosen mechanism must meet the acceptance criteria above.

## 14. Compatibility and Official References

Sibling paths are compatibility references and may evolve independently; inspect current code
again when implementing:

- Product baseline: [PD6_A-04.pdf](../novy-web/project_information/PD6_A-04.pdf).
- Project and API shapes: [project.dto.ts](../novy-web/src/data/dto/project.dto.ts),
  [projectApi.dto.ts](../novy-web/src/data/dto/projectApi.dto.ts),
  [project.mapper.ts](../novy-web/src/data/mappers/project.mapper.ts), and
  [ApiProjectRepository.ts](../novy-web/src/data/repositories/api/ApiProjectRepository.ts).
- App data and import: [appData.dto.ts](../novy-web/src/data/dto/appData.dto.ts),
  [ApiAppDataRepository.ts](../novy-web/src/data/repositories/api/ApiAppDataRepository.ts),
  [ApiProjectImportRepository.ts](../novy-web/src/data/repositories/api/ApiProjectImportRepository.ts),
  and [instantiateTemplate.ts](../novy-web/src/domain/templates/instantiateTemplate.ts).
- Auth and browser transport: [ApiAuthRepository.ts](../novy-web/src/data/repositories/api/ApiAuthRepository.ts),
  [csrf.ts](../novy-web/src/data/api/csrf.ts), and [axiosClient.ts](../novy-web/src/data/api/axiosClient.ts).
- Validation and renderer handoff: [projectValidation.ts](../novy-web/src/domain/validation/projectValidation.ts),
  [generatedApp.dto.ts](../novy-web/src/data/dto/generatedApp.dto.ts), and
  [PreviewOverlay.tsx](../novy-web/src/presentation/editor/workspace/PreviewOverlay.tsx).
- [FastAPI lifespan events](https://fastapi.tiangolo.com/advanced/events/).
- [PyMongo connection and async examples](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/)
  and [PyMongo transactions](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/).
- [MongoDB limits and thresholds](https://www.mongodb.com/docs/manual/reference/limits/).
- [Supabase Auth sessions](https://supabase.com/docs/guides/auth/sessions) and
  [JWT verification and signing keys](https://supabase.com/docs/guides/auth/jwts).
- [Vercel FastAPI deployment](https://vercel.com/docs/frameworks/backend/fastapi),
  [rewrites](https://vercel.com/docs/routing/rewrites), and
  [Functions limits](https://vercel.com/docs/functions/limitations).

Provider documentation and limits can change; verify the selected versions, product tier, and
deployed behavior again during implementation. References do not constitute live verification.
Context7 and Repomix MCP tools were not available during this planning review; repository files and
the official documentation linked above were inspected with the available tools instead.

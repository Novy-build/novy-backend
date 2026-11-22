# Novy Backend Engineering Rules

These rules apply to work in this backend repository. `BACKEND_PLAN.md` contains the approved
delivery plan and `PROGRESS.md` is the current backend implementation inventory. The older files
under `docs/frontend-handoff/` are historical context, not rules for this repository.

## Scope And Stack

- Build the Novy backend with Python, FastAPI, MongoDB Atlas, and Supabase Auth.
- Apply Clean Architecture. Vercel hosts the FastAPI service and the separate web application.
- The first release is a hosted prototype with preview-only activation and runtime writes. Live
  application activation, standalone source export, external deployments, and production AI, RAG,
  and biometric processing are deferred unless the product owner changes scope.
- Do not treat a plan, a frontend API client, a mock, or a historical handoff statement as evidence
  that a backend endpoint, provider integration, deployment, or test already exists.
- Coordinate API and runtime changes with the web application. Do not modify the other repository
  unless the task requires it and the user has authorized that work.

## Architecture

- Keep domain models and rules independent of FastAPI, MongoDB, Supabase, Vercel, and HTTP DTOs.
- Put use cases, authorization orchestration, and interfaces for external dependencies in the
  application layer. Implement those interfaces in infrastructure adapters.
- Keep FastAPI routes thin: authenticate, validate the transport shape, invoke a use case, and map
  the result or a safe domain/application error to the public response.
- Wire dependencies at the composition root. Manage shared clients through a suitable FastAPI
  lifespan and respect the driver, event-loop, and serverless lifecycle requirements.
- Use PyMongo's async API for new async MongoDB work. Lock compatible Python and dependency versions
  when the project is scaffolded; do not add Motor for new code.
- Prefer the smallest coherent implementation. Do not add abstractions, compatibility paths,
  providers, or dependencies without a concrete product or integration need.

## Identity And Authorization

- Supabase Auth owns credentials. MongoDB owns Novy profiles, application authorization, protected
  session state, and project-scoped app-user membership; do not store passwords in MongoDB.
- Keep builder identities separate from the shared generated-app user identity realm. A valid token
  or a matching email does not grant builder access or membership in any project.
- Use opaque, high-entropy, host-only, secure HttpOnly browser sessions, with provider tokens kept
  server-side and protected at rest. Never log secrets, passwords, tokens, cookies, or raw requests.
- Protect state-changing requests, including login and refresh, with session-bound CSRF validation
  and appropriate origin checks. Verify bootstrap, refresh, logout, and cookies through the actual
  Vercel rewrite; do not rely on CORS or SameSite as the only CSRF defense.
- Validate the appropriate Supabase token issuer, audience, signature, expiry, and signing method.
  Coordinate refresh and logout across serverless instances. TTL cleanup is not authorization.
- Check the current session and every applicable `is_active` status on protected requests, including
  both app-user identity and project/environment membership. An Atlas Compass disable must be
  enforced on subsequent requests without relying on a stale cache.
- Authorize every read, mutation, aggregate, job, cache entry, and nested resource by server-derived
  identity, project, and environment. Do not trust client-supplied owner, role, or tenant fields.
- Runtime access defaults to deny. `public` applies only to explicitly permitted operations;
  `owner_only` means the project's authenticated builder, not an app-user record creator.
- Keep builder management permissions and project-bound runtime sessions separate. Never pass a
  builder's broad session or provider tokens into a hosted prototype.

## Persistence And Compatibility

- Use `platform_db` for platform and logically project-scoped data. Preview and live data must be
  separated by a server-enforced environment boundary in every relevant query and index.
- Preserve project schema versions, nested IDs, responsive layouts, routes, behavior graphs, data
  bindings, and supported legacy data. Current frontend project DTOs write schema version 14.
- Treat `schemaVersion` and save `revision` as different concepts. Require an atomic owner-scoped
  revision comparison and return HTTP 409 with `stale_revision` for a conflicting document save.
- Store structurally safe drafts without enforcing complete project semantics; strictly validate
  projects and supported runtime capabilities before creating or updating a hosted snapshot.
- Use transactions where multiple database documents must become visible atomically. Test against a
  transaction-capable MongoDB topology. Do not put non-idempotent external calls inside retryable
  transaction callbacks.
- Duplicate project schemas without records by default; copy records only on explicit request.
  Import creates fresh MongoDB ObjectIds for persisted resources and uses typed reference maps.
- Preserve arbitrary user data. Do not globally replace ID-looking strings or silently discard values
  that cannot be safely remapped. Reject unsupported versions or unsafe imports with clear errors.
- Respect MongoDB document and nesting limits, Vercel request/response limits, and reasonable
  operation timeouts. Validate user input rather than passing it as raw MongoDB query operators.
- Version and test API contracts, errors, migrations, and indexes. Change existing public behavior
  only with a concrete compatibility plan and corresponding integration coverage.

## Verification And Operations

- Add focused unit, contract, and integration tests for behavior changes. Test authentication,
  authorization, cross-project access, preview isolation, conflicts, retries, and failure paths.
- Run the relevant tests and the full available backend test suite, lint, formatting, and type checks
  after implementation. Do not report commands or results for tooling that has not been created or
  run. Record blockers and unverified live-provider behavior honestly.
- Test transaction and index behavior with real MongoDB. Mocks alone do not establish database,
  Supabase, Vercel, or browser-cookie correctness.
- Use least-privilege credentials, TLS, validated settings, bounded request sizes, rate limits,
  safe error messages, and structured logs with sensitive fields redacted.
- Use official documentation and available project-context tools before introducing or changing a
  library or provider integration. If requested MCP tools are unavailable, use available repository
  tools and official documentation and report the limitation.
- Preserve existing user changes and historical changelog entries. Do not reset or rewrite history.

## Handoff And Documentation

- The product baseline is `../novy-web/project_information/PD6_A-04.pdf` when that sibling checkout
  is available. Explicit newer product decisions take precedence over conflicting historical plans.
- For existing integration behavior, inspect the current frontend DTOs, mappers, repositories,
  validation, and tests under `../novy-web/src/`. Frontend mocks and snapshots are not proof of a
  working backend. Preserve only relevant cross-repository contract references in backend docs.
- Update `PROGRESS.md` for every backend change and add a dated entry under `Unreleased` in
  `CHANGELOGS.md`. Keep current status separate from the plan and retain previous changelog entries.
- Update `BACKEND_PLAN.md` when approved scope, architecture, contracts, or delivery gates change.

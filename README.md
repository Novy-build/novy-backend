# Novy Backend

Novy Backend is the API and persistence service being built for the Novy web application builder.
It uses FastAPI, PyMongo Async, and Clean Architecture, with Supabase Auth and Vercel integration
planned for later work.

The first release is a hosted prototype with project-scoped preview data. Downloadable source
export, external deployments, and production AI, RAG, and biometric processing are deferred.

## Current Status

The current implementation includes a FastAPI foundation, one liveness endpoint, and a
lifespan-managed MongoDB client. An internal builder-profile repository and its unique identity
index are implemented; no builder accounts are created automatically. Isolated local Atlas tests
verified database operations and profile behavior. Authentication, project persistence APIs,
hosted prototypes, and deployment are not implemented. Do not treat the frontend's API clients or
older handoff documents as evidence of a working backend.

## Local Development

The project targets Python 3.12 and uses a locked dependency set managed by
[uv](https://docs.astral.sh/uv/getting-started/installation/). Install uv, then run:

```powershell
uv sync --locked --group dev
uv run --locked --group dev uvicorn novy.main:app --reload --host 127.0.0.1 --port 8000
```

An existing Windows virtual environment can also run the server directly:

```powershell
.\.venv\Scripts\python.exe -m uvicorn novy.main:app --reload --host 127.0.0.1 --port 8000
```

This workspace's virtual environment uses a local Python installation under `.tools/python`.
Both `.tools/` and `.venv/` are ignored by Git; if the Python installation is removed, the virtual
environment must be recreated with a suitable Python 3.12 installation.

The optional `NOVY_ENVIRONMENT` setting can be set through the process environment or a local
`.env` file. Values are `development`, `test`, and `production`; the default is `production`.
The local `.env.example` shows the development value, and this workspace has an ignored development
`.env`. Environment variables take precedence over the file. Unknown dotenv entries and invalid
values fail validation. The interactive documentation at `/docs` and schema at `/openapi.json`
are available only in development or test. Debug mode is always off. Hiding documentation is not
an authentication or authorization control.

`MONGODB_CONNECTION_STRING` is read from the process environment or local `.env`. The URI is treated
as a secret, and the client selects `platform_db` using TLS with certificate and hostname validation.
Explicit insecure TLS options, unescaped `#`, and semicolon-separated query options are rejected.
Credentials must not be placed in tracked files, logs, or public responses. The existing
`NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` dotenv entries are accepted but
are not used for authentication yet.

When the MongoDB connection string is absent, the process can run the liveness-only scaffold without
a database client. A present but invalid connection string fails configuration validation. Creating
a client does not prove server connectivity or database permissions; no project data is read or
written by the current implementation.

## Builder Profiles

`platform_db.builder_profiles` stores builder identity references, display information, timestamps,
and an explicit `is_active` status. It never stores passwords or provider tokens. Profiles can only
be created by a trusted internal onboarding flow; profile lookup does not create or reactivate one.
The repository is not yet exposed through an API and does not itself authenticate callers.
Profile ObjectIds are deterministically derived from issuer and subject; use `created_at` rather
than the ObjectId's embedded timestamp when inspecting their creation time.

The identity index is provisioned explicitly, not during application startup:

```powershell
uv run --locked --group dev python -m novy.infrastructure.migrations.builder_profiles_v1
```

Run this command only after confirming the configured database and deployment environment. It
creates the `builder_identity_unique_v1` index if needed and does not remove existing data or indexes.
Profile operations fail closed if the expected unique index is missing or incompatible. Index
changes must not run concurrently with profile operations; production runtime credentials should
not have index-management privileges.

Status lookups use primary, linearizable reads without caching. To make an Atlas Compass disable
durable across failover, the `is_active: false` write must be majority-acknowledged; a timeout has an
unknown outcome. This does not cancel requests that already passed a status check. No protected
requests or browser sessions exist yet, so end-to-end account disabling remains unverified.

`GET /api/v1/health/live` returns `{"status":"alive"}` with `Cache-Control: no-store`. It checks
only that the API can respond; it does not check MongoDB, Supabase, or deployment readiness. There
is no readiness endpoint yet.

## Verification

```powershell
uv run --locked --group dev pytest
uv run --locked --group dev ruff check .
uv run --locked --group dev ruff format --check .
uv run --locked --group dev mypy src tests
uv lock --check
```

The default suite skips the live Atlas verification. To run it explicitly against a confirmed test
deployment, use:

```powershell
uv run --locked --group dev python -W ignore -m pytest -q -s --tb=no tests/integration/test_platform_db.py::test_platform_db_live --verify-platform-db
```

This is a write test. Each run creates a uniquely named scratch collection, validator, and index in
the configured `platform_db`. It deletes only exact synthetic test records and intentionally leaves
the empty collection and index in place. A failure or interruption can leave synthetic records for
investigation. Never run it against an unapproved production database.

An additional profile-specific live test is also opt-in:

```powershell
uv run --locked --group dev python -W ignore -m pytest -q -s --tb=no tests/integration/test_builder_profiles_live.py::test_builder_profiles_live --verify-builder-profiles-db
```

It creates another uniquely named scratch collection and index, removes only exact synthetic
profiles, and retains the empty collection. A failure can leave synthetic documents for
investigation; run it only against an approved test deployment. It simulates a majority-acknowledged
disable from a second client, not an Atlas Compass edit or authenticated HTTP request.

These checks do not verify Vercel's runtime or rewrite, Supabase integration, or every MongoDB
permission and failure mode. The development dependency group is explicit so it is not installed
by a default production sync.

## Architecture

- `src/novy/domain/`: framework-independent models and rules as they are added.
- `src/novy/application/`: use cases and external dependency interfaces as they are added.
- `src/novy/infrastructure/`: validated settings, the MongoDB profile adapter, and index setup.
- `src/novy/presentation/http/`: FastAPI routes and transport models.
- `src/novy/main.py`: application composition; `src/main.py` re-exports the app for Vercel.

The MongoDB client is created and closed through FastAPI lifespan. The builder-profile repository
is an internal component; project repositories and the Supabase authentication adapter remain to
be implemented.

## Documentation

- [Backend engineering rules](AGENTS.md)
- [Current backend progress](PROGRESS.md)
- [Backend architecture and delivery plan](BACKEND_PLAN.md)
- [Changelog](CHANGELOGS.md)
- [Historical handoff archive](docs/frontend-handoff/README.md)

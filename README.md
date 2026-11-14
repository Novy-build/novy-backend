# Novy Backend

Novy Backend is the API and persistence service being built for the Novy web application builder.
It uses FastAPI, PyMongo Async, and Clean Architecture, with Supabase Auth and Vercel integration
planned for later work.

The first release is a hosted prototype with project-scoped preview data. Downloadable source
export, external deployments, and production AI, RAG, and biometric processing are deferred.

## Current Status

The current implementation includes a FastAPI foundation, one liveness endpoint, and a
lifespan-managed MongoDB client. An isolated local Atlas test verified `platform_db` reads, writes,
unique-index enforcement, transaction commit, and observed rollback. Authentication, project
persistence APIs, hosted prototypes, and deployment are not implemented. Do not treat the
frontend's API clients or older handoff documents as evidence of a working backend.

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

These checks do not verify Vercel's runtime or rewrite, Supabase integration, or every MongoDB
permission and failure mode. The development dependency group is explicit so it is not installed
by a default production sync.

## Architecture

- `src/novy/domain/`: framework-independent models and rules as they are added.
- `src/novy/application/`: use cases and external dependency interfaces as they are added.
- `src/novy/infrastructure/`: validated settings and future provider/database adapters.
- `src/novy/presentation/http/`: FastAPI routes and transport models.
- `src/novy/main.py`: application composition; `src/main.py` re-exports the app for Vercel.

The MongoDB client is created and closed through FastAPI lifespan. Project repositories and the
Supabase authentication adapter remain to be implemented.

## Documentation

- [Backend engineering rules](AGENTS.md)
- [Current backend progress](PROGRESS.md)
- [Backend architecture and delivery plan](BACKEND_PLAN.md)
- [Changelog](CHANGELOGS.md)
- [Historical handoff archive](docs/frontend-handoff/README.md)

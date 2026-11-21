# CSRF coordination — frontend/backend contract

This document explains how the frontend and backend establish CSRF protection
for cookie-authenticated builder sessions. It covers what the backend already
does, what the frontend must do, the tests that exist, and what still needs
verification.

## Goal

Protect cookie-authenticated mutation requests (registration, login, session,
refresh, logout, project saves, project data, runtime actions) against
Cross-Site Request Forgery. The design is:

- **Session cookie** (`__Host-novy_builder_session`): opaque, `Secure`,
  `HttpOnly`, `SameSite=Lax`, `Path=/`. Not readable by JavaScript. This is the
  actual session.
- **CSRF cookie** (`novy_csrf`): opaque, `Secure`, not `HttpOnly`,
  `SameSite=Lax`, `Path=/`. Readable by JavaScript. This is the
  double-submit token.
- **CSRF header** (`X-CSRF-Token`): set by the browser from `document.cookie`
  on every unsafe request. The server compares the header value to the cookie
  value. Because only same-origin JavaScript can read `document.cookie`, a
  matching header proves the request originated from the same origin.

## What the backend already does

All of the following is implemented and tested in the backend:

### CSRF service (`src/novy/infrastructure/csrf.py`)

- `CsrfService` with domain-separated HMAC-SHA256 signing.
- `issue_preauth()` — creates a short-lived anonymous pre-auth session
  (nonce + expiry + signature) plus a CSRF token bound to that state.
- `verify_preauth(state, token)` — constant-time verification of state and
  token binding, expiry, and skew tolerance.
- `token_for_session(credential)` — derives a CSRF token from an opaque
  session credential.
- `verify_session(credential, token)` — verifies a session cookie token
  against a credential.
- `is_canonical_token(value)` — validates the 43-character unpadded
  base64url format.

### Session cookie helper
(`src/novy/presentation/http/builder_session_cookie.py`)

- `set_builder_session_cookie(response, issued)` — sets
  `__Host-novy_builder_session` with `secure=True`, `httponly=True`,
  `path='/'`, `samesite='lax'`, max-age = 30 days, plus
  `Cache-Control: no-store`.
- `set_csrf_cookie(response, token)` — sets `novy_csrf` with
  `secure=True`, `httponly=False`, `path='/'`, `samesite='lax'`, max-age =
  30 days, plus `Cache-Control: no-store`.
- `set_preauth_cookie(response, state)` — sets `__Host-novy_preauth` with
  `secure=True`, `httponly=True`, `path='/'`, `samesite='lax'`, max-age =
  900 seconds, plus `Cache-Control: no-store`.
- `clear_auth_cookies(response)` — deletes all three cookies with matching
  attributes.

### HTTP error handling
(`src/novy/presentation/http/errors.py`)

- `ApiError` with `status_code`, `code`, `message`, optional `details`.
- Standard envelope: `{"error":{"code","message","details?","requestId"}}`.
- No raw exceptions, tokens, or credentials in responses.
- `install_error_handlers(app)` registers handlers for `ApiError`,
  `RequestValidationError`, Starlette `HTTPException`, and unhandled
  exceptions.

### Authentication routes
(`src/novy/presentation/http/auth.py`)

- `GET /api/v1/auth/csrf` — issues a pre-auth session and returns a CSRF
  token. Sets both the `novy_csrf` cookie and the `__Host-novy_preauth`
  cookie. No authentication performed.
- `POST /api/v1/auth/register` — requires CSRF + origin guards.
- `POST /api/v1/auth/login` — requires CSRF + origin guards.
- `GET /api/v1/auth/session` — safe method, no CSRF check (read-only).
- `POST /api/v1/auth/refresh` — requires CSRF + origin guards.
- `POST /api/v1/auth/logout` — requires CSRF + origin guards.

### Route dependencies
(`src/novy/presentation/http/auth_dependencies.py`)

- `require_unsafe_request_guards(request)` — for POST/PUT/PATCH/DELETE:
  - Validates `Origin` header against allowed origins (exact scheme+host
    match, trailing slash normalized). Missing or disallowed origin → 403.
  - Validates `X-CSRF-Token` header equals `novy_csrf` cookie value,
    both present and canonical format, constant-time comparison → 403
    otherwise.
  - If a valid pre-auth cookie+token pair is present → allowed.
  - Otherwise requires a valid builder session cookie + CSRF token → allowed.
  - Otherwise → 403 `csrf_failed`.
- `get_session_credential(request)` — extracts `novy_csrf` cookie, validates
  canonical format → 401 `session_required` if missing/invalid.
- `require_session_service(request)` — returns the builder session service
  from app state → 503 if not configured.

## What the frontend needs to do

The frontend currently reads `novy_csrf` from `document.cookie` and mirrors it
to `X-CSRF-Token` (`src/novy-web/src/data/api/csrf.ts` and
`axiosClient.ts`). However:

1. **Call `GET /api/v1/auth/csrf` before the first mutation.** The frontend
   currently never calls this endpoint, so the `novy_csrf` cookie may never be
   set by the backend. The frontend should call this endpoint on app load or
   before the first unsafe request.

2. **Send credentials.** The Axios client already uses `withCredentials: true`,
   so cookies are sent. Confirm this is set on all unsafe requests.

3. **Handle 403 `csrf_failed`.** If the CSRF cookie is missing or the header
   doesn't match, the backend returns 403. The frontend should treat this as
   "need to bootstrap CSRF" — call `GET /auth/csrf` again and retry.

4. **Same-origin deployment.** The CSRF design assumes frontend and backend
   are same-origin (or at least same-host) so the browser sends the cookie
   and allows reading `document.cookie`. If they are ever split across
   origins, this design breaks and needs rethinking.

## Existing tests

- `tests/test_csrf.py` — 23 tests covering:
  - CSRF service construction and repr (no secret leakage)
  - Preauth issue/verify round-trip
  - Preauth expiry bounds and skew tolerance
  - Tampering with state/token signatures
  - Canonical encoding validation
  - Session token binding to credential
  - Origin allow-listing (dev/test defaults and production config)
  - Safe-method exclusion
  - Missing/invalid origin rejection
  - Missing/invalid CSRF header/cookie rejection
  - Service dependency wiring
- `tests/test_builder_session_cookie.py` — 4 tests covering cookie attributes
  (Secure, HttpOnly, SameSite=Lax, Path=/, max-age) and deletion attributes.
- `tests/test_builder_auth.py` — authentication endpoints that require CSRF +
  origin guards.

## Tests still needed

1. **Real-browser test**: Verify the `novy_csrf` cookie is actually set by the
   `GET /auth/csrf` response and is readable by JavaScript.
2. **End-to-end CSRF flow**: Call `GET /auth/csrf`, read the cookie, send a
   POST request with the header matching the cookie, verify success; then
   tamper with the header and verify 403.
3. **Origin rejection test**: Send an unsafe request with a different Origin
   and verify 403.
4. **Refresh-without-CSRF test**: Confirm `POST /auth/refresh` rejects
   requests without the CSRF header.
5. **Frontend integration test**: After calling `GET /auth/csrf`, confirm the
   subsequent login/register request includes the matching `X-CSRF-Token` header
   automatically via the Axios interceptor.

## Progress impact

Unblocking CSRF lets the backend authentication endpoints accept real browser
requests. Once the frontend calls `GET /auth/csrf` and mirrors the cookie to
`X-CSRF-Token`, login/register/logout/refresh all work end-to-end.

This is a prerequisite for:

- **Phase 3 (Project/data APIs)** — all project mutations are cookie-authenticated
  and require CSRF.
- **Preview/runtime access** — runtime actions are also cookie-authenticated.
- **Vercel deployment** — the frontend and backend must be same-origin (or the
  rewrite must preserve cookies) for `document.cookie` to work.

CSRF coordination is a small but critical integration point: one frontend call
to `GET /auth/csrf` and the existing Axios interceptor handling completes the
security boundary between builder browser and backend.

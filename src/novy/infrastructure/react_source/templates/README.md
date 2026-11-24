# Novy generated app

This project was generated from a Novy manifest v2 export. It contains layout, routes, logic and
collection metadata only. No records, credentials, raw media or biometric templates are included.

## Run locally

```
npm install
npm run dev
```

Set `VITE_NOVY_RUNTIME_BASE_URL` to the backend origin if you want generated logic to reach
server-backed data or email actions. The generated runtime client calls the `/runtime/v1` surface;
that surface is a separate backend workstream and may not be deployed yet. Without it, browser-only
and local behaviors still work, while server-backed actions will report a network error.

## Layout

- `src/generated/screens/` — one component per screen.
- `src/generated/routes.ts` — the screen route table.
- `src/generated/logic/` — behavior functions derived from logic graphs.
- `src/generated/data/collections.ts` — collection and field metadata.
- `src/generated/runtime/client.ts` — browser runtime adapter.
- `src/generated/tokens.ts` — design tokens and responsive breakpoints.

Generated files are deterministic and may be regenerated from a new export.

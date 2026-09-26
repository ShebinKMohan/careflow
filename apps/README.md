# Apps

Three Next.js 16 apps (App Router, TypeScript, Tailwind v4) built from `@careflow/ui` and typed by `@careflow/contracts`. Research prototype. Synthetic data only. Not for clinical use.

| App | Port | Screens (M0-08 placeholders) |
|---|---|---|
| `kiosk` | 3000 | `/` language choice, `/setup` kiosk sign-in (staff stub), `/checkin` keypad → `POST /v1/checkin/keypad`, `/intake` |
| `staff` | 3001 | `/login` (stub), `/nurse` worklist, `/doctor/[sessionId]` briefing, `/board?department=ed`, `/admin` providers |
| `companion` | 3002 | `/` QR hand-over claim → `POST /v1/checkin/qr/claim`, `/status` queue status; installable PWA |

```bash
make mock              # Prism serves the openapi.yaml examples on :4010 (no --dynamic: deterministic)
pnpm -F kiosk dev      # or staff / companion
pnpm -r lint typecheck test
pnpm test:e2e          # Playwright: each app starts its own mock (4010/4011/4012) and a production build
```

- **API:** `src/lib/api.ts` calls `createCareflowClient(NEXT_PUBLIC_API_BASE_URL, getToken)` from `@careflow/contracts/client` (openapi-fetch over the generated types; no hand-written API types). The default base URL is the mock, `http://localhost:4010`; use `http://localhost:8000` for the real API. Apps never call HAPI, Postgres or a model provider directly.
- **Tokens:** kept in the tab's `sessionStorage` and sent only in the `Authorization` header. The sign-in screens are development stubs (`/setup` on the kiosk, `/login` on staff); the mock accepts any credentials.
- **Languages:** English and Arabic with next-intl, without locale routing. The `NEXT_LOCALE` cookie (set by `LanguageToggle`) selects the messages in `messages/<locale>.json`, and the root layout sets `<html lang dir>`. Only logical utilities (`ms-`, `pe-`, `text-start` …) are allowed; `packages/ui/tests/logical-utilities.test.ts` fails on `ml-`, `pr-`, `left-`, `text-right` and similar. Arabic supported; not validated by native speakers.
- **Design:** `@careflow/ui/tokens.css` (Tailwind theme variables: high-contrast colours, 64 px touch targets, type scale) and `base.css`; components `BigButton`, `Keypad`, `LanguageToggle`, `Card`, `PrototypeFooter`. The kiosk shows initials only.
- **Companion PWA:** `src/app/manifest.ts` and `public/sw.js`. The service worker is registered in production builds only. It caches the static shell (start page, manifest, icons, hashed `/_next/static/` assets); API requests are network-only and never cached.
- **Playwright** uses the installed Google Chrome (`channel: "chrome"`); set `PLAYWRIGHT_CHANNEL=chromium` after `playwright install chromium` to use Playwright's own browser.

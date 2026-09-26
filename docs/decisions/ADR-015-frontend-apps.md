# ADR-015: Frontend apps, shared UI and the mock API

## Status

Accepted, 27 Sep 2026 ([M0-08]).

## Context

Three Next.js apps (kiosk, staff, companion PWA) must start before the API exists. They run against a Prism mock of `openapi.yaml`, share one design system, switch between English and Arabic (right to left), and each has Vitest and Playwright tests. The repo pins ESLint 10 and TypeScript 6. pnpm 12 enforces supply-chain policies, and the CI security job runs osv-scanner on `pnpm-lock.yaml`. From the dev machine, Playwright's browser CDN (`cdn.playwright.dev`, Azure Front Door) times out, while GitHub, npm and Google Fonts are reachable.

## Decision

- **Versions (exact, in the pnpm catalog):** Next 16.3.6 (`npm view next version`); React 19.2.8, the pair that create-next-app 16.3.6 pins; next-intl 4.14.7; Tailwind 4.3.3; openapi-fetch 0.17.0; zod 4.6.5; Prism 5.16.0; Playwright 1.63.0; Vitest 5.0.2 with jsdom 30.1.1. `@types/node` is 24.13.6, because 24.19.0 was under a day old and failed pnpm's minimum-release-age check.
- **Lint:** ESLint 10 with `eslint-config-next`, plus `settings.react.version: "19.2"`. eslint-plugin-react's version detection calls `context.getFilename()`, which ESLint 10 removed. With the version set, every rule family runs; a probe file triggered react/jsx-key, jsx-a11y/alt-text, @next/next/no-img-element, react-hooks and typescript-eslint. The peer ranges of eslint-plugin-react, eslint-plugin-import and eslint-plugin-jsx-a11y stop at ESLint 9, so pnpm warns. Revisit when they declare ESLint 10.
- **Install scripts stay off** (`allowBuilds: false`): sharp, unrs-resolver, @swc/core and @parcel/watcher use prebuilt binaries, and @scarf/scarf is install telemetry.
- **i18n:** next-intl without locale routing. The `NEXT_LOCALE` cookie, set by `LanguageToggle`, selects `messages/<locale>.json` in `src/i18n/request.ts`, and the root layout sets `<html lang dir>`. Arabic must have every English key; the type and a test check this. Time zone is Asia/Dubai. Only logical Tailwind utilities are allowed, and a guard test fails on physical ones.
- **Design tokens:** `@careflow/ui/tokens.css` holds Tailwind theme variables with a high-contrast palette (text pairs at least 7:1) and 64 px touch targets. A test checks WCAG contrast: text at least 4.5:1, focus ring and borders at least 3:1.
- **API and auth stubs:** `createCareflowClient(baseUrl, getToken)` in `packages/contracts/ts/client.ts` uses openapi-fetch with bearer middleware; responses are typed, never `any`. Tokens live in the tab's `sessionStorage` and travel only in the `Authorization` header. The sign-in screens are development stubs: kiosk `/setup` (device token; its `clinic_id` and the device id fill the keypad request), staff `/login`, and the companion QR claim. Data loads client side, where the token is; server components render the shell and the translations.
- **Companion PWA:** `manifest.ts` plus `public/sw.js`, registered from a client component in production builds only. The worker precaches `/`, the manifest and the icons, and serves hashed `/_next/static/` assets cache-first. Everything else is network-only and never cached: other origins (the API), `/v1/` paths, RSC requests and other pages.
- **Tests:** each app's Vitest test runs the real `request.ts` with a mocked `next/headers` and renders the document into `document`. Each app's Playwright suite starts its own Prism port (4010, 4011, 4012) and a production build, so `pnpm -r test:e2e` can run the apps in parallel. Suites use the installed Google Chrome (`channel: "chrome"`; `PLAYWRIGHT_CHANNEL=chromium` switches). Web servers run the binaries directly with a SIGTERM shutdown, because pnpm 12 starts children in their own process group, which Playwright's kill misses. Screenshots wait for `document.fonts.ready`, so they are byte-stable.
- **Prism's transitive advisories:** postman-collection 4.5.0 (via @stoplight/http-spec) pins vulnerable releases. `overrides` move its lodash to 4.18.1 and its uuid to 11.1.1. Its @faker-js/faker 5.5.3 (GHSA-qxc2-j82w-r537, code execution through `helpers.fake()` templates) cannot move: faker 10 breaks the module at load, and postman-collection 5.3.1 still pins 5.5.3. The path is unreachable here, since Prism serves static OpenAPI examples and never resolves a Postman collection. `osv-scanner.toml` ignores that single advisory until 2027-03-31, with this reason.

## Consequences

- M1-13 (kiosk), M1-15 (nurse), M1-17 (doctor, board, admin) and M2-06/M2-07 (companion) build on these apps and replace the auth stubs.
- The real API has no CORS yet. When an app first calls `http://localhost:8000` from its own origin, the API needs CORS for ports 3000-3002 (explicit origins, no wildcard).
- CI runs lint, typecheck and Vitest for the apps. Playwright runs locally (`make e2e`) until an e2e CI job exists.
- The osv exception expires on 2027-03-31. The security scan then fails until it is reviewed, or until Prism no longer brings postman-collection 4.

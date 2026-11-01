# WebBlock Owner Portal (standalone)

The Owner Portal screens (Products, Business information, Monitor) from the WebBlock app, as their
own Next.js app. It has its own `package.json`, styles (`src/app/globals.css`), config and mock data,
and imports nothing from the project around it.

## Run

```bash
pnpm install
pnpm dev        # http://localhost:3001 → opens Petal & Stem's Products page
```

Also: `pnpm build`, `pnpm start`, `pnpm typecheck`, `pnpm lint`.

## What differs from the main app

- **No sign-in.** You are always the mock owner in `src/mocks/session.ts`; only Petal & Stem is in the mocks.
  Other website ids get a 404. "Log out" just goes back to the home page.
- **Links to the rest of WebBlock** (logo, "Back to editor", "See plans") come from `appLinks` in
  `src/config/site.ts`. Set `NEXT_PUBLIC_WEBBLOCK_APP_URL=http://localhost:3000` to send them to the
  main app; without it they stay inside this portal.
- `features/websites` and `features/account` only keep the few types and calls the portal needs.
- Mock data lives in memory: changes (new products, edited info) reset when the server restarts.

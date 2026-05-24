# Arena frontend

Vite + React + TypeScript + `vite-plugin-pwa`. Talks to the FastAPI backend on `:8000` via a dev proxy on `/api`.

## Setup

```bash
cd webapp/frontend
npm install
npm run dev      # http://localhost:5173
```

Sign in with an API key (paste it in the Register page's "already have a key?" form). Stored in `localStorage`.

## Routes

| Path                    | Public | What                                     |
|-------------------------|--------|------------------------------------------|
| `/`                     |   ✓    | Leaderboard                              |
| `/problems`             |   ✓    | Browse supported problems                |
| `/problems/:slug`       |   ✗    | Full problem statement (auth)            |
| `/submissions`          |   ✓    | All public submissions                   |
| `/submissions/:id`      |   ✓    | Submission detail (params, prompt, runs) |
| `/register`             |   ✓    | Register / sign in                       |
| `/submit`               |   ✗    | Submit a batch JSON payload              |
| `/me`                   |   ✗    | My submissions                           |

## PWA

`vite-plugin-pwa` generates the manifest + service worker on build. Add real `icon-192.png` / `icon-512.png` to `public/` before shipping; the SVG favicon is included.

## Build

```bash
npm run build && npm run preview
```

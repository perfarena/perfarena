import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import pkg from "./package.json";

export default defineConfig({
  // Embed the build's version so the footer can show it and flag a stale deploy.
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg"],
      manifest: {
        name: "PerfArena",
        short_name: "PerfArena",
        description: "Open leaderboard ranking LLMs by real LeetCode runtime, memory, and pass@1.",
        theme_color: "#0b0f17",
        background_color: "#0b0f17",
        display: "standalone",
        icons: [
          { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
        ],
      },
      // Workbox's default navigation fallback returns index.html for ANY
      // navigation that misses the precache, which means the SW happily
      // intercepts /docs, /redoc, /openapi.json, and /api/* and feeds the
      // SPA shell back to the browser. In dev (no SW) the footer links
      // work; once the container's SPA is installed they 404 into the
      // React Router not-found view. Denylist every backend-owned path
      // so the SW forwards those requests to FastAPI untouched.
      workbox: {
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/docs/,
          /^\/redoc/,
          /^\/openapi\.json$/,
        ],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      // Surface FastAPI's auto-generated Swagger UI / ReDoc / spec under the
      // same origin so the footer link works in dev without CORS gymnastics.
      "/docs": "http://localhost:8000",
      "/redoc": "http://localhost:8000",
      "/openapi.json": "http://localhost:8000",
    },
    // Allow tunnelled hosts to reach the dev server. A leading "." means
    // "match any subdomain" — so `.serveousercontent.com` covers every
    // Serveo URL like https://<hash>-<ip>.serveousercontent.com, and the
    // ngrok patterns cover the usual *.ngrok.app / *.ngrok-free.app
    // throwaway hostnames. Adjust as needed.
    allowedHosts: [
      "localhost",
      ".serveousercontent.com",
      ".serveo.net",
      ".ngrok.app",
      ".ngrok-free.app",
      ".ngrok.io",
    ],
  },
});

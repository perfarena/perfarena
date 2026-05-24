import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
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

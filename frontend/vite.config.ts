import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.ico", "logo.svg", "apple-touch-icon.png", "icons/*.png"],
      manifest: {
        name: "MediaMate",
        short_name: "MediaMate",
        description: "Find, download, encode and organise your media",
        theme_color: "#0f172a",
        background_color: "#0f172a",
        display: "standalone",
        orientation: "any",
        scope: "/",
        start_url: "/",
        shortcuts: [
          { name: "Activity", url: "/activity", icons: [{ src: "/icons/pwa-192x192.png", sizes: "192x192" }] },
          { name: "Library", url: "/library", icons: [{ src: "/icons/pwa-192x192.png", sizes: "192x192" }] },
          { name: "Search", url: "/search", icons: [{ src: "/icons/pwa-192x192.png", sizes: "192x192" }] },
        ],
        icons: [
          {
            src: "/icons/pwa-192x192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "/icons/pwa-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
          {
            // Full-bleed artwork inside the 80% safe zone, for launchers that crop to a shape
            src: "/icons/maskable-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
        // Push notification handlers live in public/push-sw.js.
        importScripts: ["/push-sw.js"],
        // The SPA shell is served for app routes only — never for the API, the
        // live event stream or media streams, and API responses aren't cached.
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//, /^\/health/],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
  // Production bundles ship without source maps, and only VITE_-prefixed env vars
  // could ever be inlined (the app reads none): secrets stay on the server.
  build: {
    sourcemap: false,
  },
  envPrefix: "VITE_",
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/health": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});

import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";

// API URL is build-time configurable for different environments.
// Caddy serves the SPA at the same origin in prod, so default to relative
// paths. Dev mode proxies /v1 + /metrics to the api container.
const API_PROXY_TARGET =
  process.env.GSFLUENT_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [
    TanStackRouterVite({ target: "react", autoCodeSplitting: true }),
    react(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5174,
    proxy: {
      "/v1/stream": {
        target: API_PROXY_TARGET.replace(/^http/, "ws"),
        ws: true,
        changeOrigin: true,
      },
      "/v1": { target: API_PROXY_TARGET, changeOrigin: true },
      "/metrics": { target: API_PROXY_TARGET, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});

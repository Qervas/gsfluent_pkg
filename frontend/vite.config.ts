import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Backend the dev server proxies /api to. Defaults to 8080 (local
// uvicorn). Set GSFLUENT_BACKEND_PORT=18080 when targeting an SSH-tunneled
// server backend (split-topology dev: laptop frontend, server backend).
const BACKEND_PORT = process.env.GSFLUENT_BACKEND_PORT ?? "8080";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      "/api/stream": { target: `ws://localhost:${BACKEND_PORT}`, ws: true },
      "/api":        { target: `http://localhost:${BACKEND_PORT}` },
    },
  },
  build: {
    outDir: "../server/gsfluent/static",
    emptyOutDir: true,
  },
});

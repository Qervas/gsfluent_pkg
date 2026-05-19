import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Backend the dev + preview servers proxy /api to. Two configurations:
//
//   GSFLUENT_BACKEND_URL=http://host:port    (full URL — takes precedence)
//       Used when the backend is LAN- or WAN-reachable directly (e.g.,
//       a public IP / port mapping). WS scheme derived from http(s).
//
//   GSFLUENT_BACKEND_PORT=<port>             (default 8080)
//       Legacy localhost pattern for SSH-tunnelled deployments
//       (run-client.sh's $LOCAL_PORT).
//
// Strong frontend/backend split: SPA served from THIS machine, talks to
// the API via this proxy.
const BACKEND_URL =
  process.env.GSFLUENT_BACKEND_URL ??
  `http://localhost:${process.env.GSFLUENT_BACKEND_PORT ?? "8080"}`;
const WS_URL = BACKEND_URL.replace(/^http/, "ws");

const proxy = {
  "/api/stream": { target: WS_URL, ws: true, changeOrigin: true },
  "/api":        { target: BACKEND_URL, changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  // `server` = `vite` / `vite dev` (HMR mode)
  server:  { port: 5173, proxy },
  // `preview` = `vite preview` (serves the built dist/, used by run-client.sh)
  preview: { port: 4173, proxy },
  build: {
    // Strong split: SPA build artifacts stay inside frontend/. The
    // server no longer hosts the SPA, so we don't reach over into the
    // server tree. .gitignore excludes dist/.
    outDir: "dist",
    emptyOutDir: true,
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Backend the dev + preview servers proxy /api to. Defaults to 8080
// (local uvicorn or SSH tunnel forwarding to the server). Strong
// frontend/backend split: the SPA is served from THIS machine
// (`vite dev` for development, `vite preview` for distribution-style
// served-from-disk runs), and it talks to the API process via this
// proxy. `run-client.sh` sets this to $LOCAL_PORT to land on the
// tunnel's client end.
const BACKEND_PORT = process.env.GSFLUENT_BACKEND_PORT ?? "8080";

const proxy = {
  "/api/stream": { target: `ws://localhost:${BACKEND_PORT}`, ws: true },
  "/api":        { target: `http://localhost:${BACKEND_PORT}` },
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

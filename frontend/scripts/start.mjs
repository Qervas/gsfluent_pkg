#!/usr/bin/env node
// Client-local launcher. Fires from `npm start`.
//
// Starts two long-running processes with shared Ctrl-C cleanup:
//   1. viser_headless    127.0.0.1:8091 (splat WS) + 127.0.0.1:8092 (control)
//   2. vite preview      :$UI_PORT, serves frontend/dist/ and proxies /api/*
//                        at the shared backend ($GSFLUENT_BACKEND_URL).
//
// No process here talks to anything except 127.0.0.1 (viser) and the
// shared backend over /api/*. The splat WS never leaves loopback.
//
// Env (all have safe defaults):
//   GSFLUENT_BACKEND_URL   default ${BACKEND_URL} from .env
//   UI_PORT                default 5173
//   VISER_PORT             default 8091
//   CONTROL_PORT           default 8092
//   VISER_NPZ_DIR          default <repo>/work/cache/viser
//   OPEN_BROWSER           default 1 (0 disables auto-open)

import { spawn } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import concurrently from "concurrently";
import waitOn from "wait-on";
import open from "open";
import dotenv from "dotenv";

const FRONTEND_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const PKG_ROOT = dirname(FRONTEND_DIR);
const VENV_PY = resolve(PKG_ROOT, ".venv/bin/python");
const VISER_SCRIPT = resolve(FRONTEND_DIR, "python/viser_headless.py");

dotenv.config({ path: resolve(PKG_ROOT, ".env") });

const UI_PORT = process.env.UI_PORT ?? "5173";
const VISER_PORT = process.env.VISER_PORT ?? "8091";
const CONTROL_PORT = process.env.CONTROL_PORT ?? "8092";
const VISER_NPZ_DIR = process.env.VISER_NPZ_DIR ?? resolve(PKG_ROOT, "work/cache/viser");
const BACKEND_URL = process.env.GSFLUENT_BACKEND_URL ?? process.env.BACKEND_URL ?? "";
const OPEN_BROWSER = process.env.OPEN_BROWSER !== "0";

// ---- preflight -------------------------------------------------------------

if (!existsSync(VENV_PY)) {
  console.error(`ERROR: ${VENV_PY} not found.\n\nRun \`npm install\` first; it creates the venv + builds the SPA.`);
  process.exit(1);
}
if (!existsSync(resolve(FRONTEND_DIR, "dist/index.html"))) {
  console.error("ERROR: frontend/dist/index.html missing.\n\nRun `npm install` first, or `npm run build` to rebuild.");
  process.exit(1);
}
if (!existsSync(VISER_SCRIPT)) {
  console.error(`ERROR: ${VISER_SCRIPT} missing — wrong working tree?`);
  process.exit(1);
}
mkdirSync(VISER_NPZ_DIR, { recursive: true });

console.log(`>>> backend:         ${BACKEND_URL || "(unset)"}`);
console.log(`>>> SPA:             http://localhost:${UI_PORT}/`);
console.log(`>>> viser_headless:  127.0.0.1:${VISER_PORT} (splats)  127.0.0.1:${CONTROL_PORT} (control)`);
console.log(`>>> npz cache:       ${VISER_NPZ_DIR}\n`);

// ---- run viser + vite in parallel with shared cleanup ---------------------

const { result } = concurrently(
  [
    {
      name: "viser",
      command: `"${VENV_PY}" "${VISER_SCRIPT}" --npz_dir "${VISER_NPZ_DIR}" --viser_port ${VISER_PORT} --control_port ${CONTROL_PORT}`,
    },
    {
      name: "vite",
      cwd: FRONTEND_DIR,
      command: `npx vite preview --port ${UI_PORT} --strictPort`,
      env: { GSFLUENT_BACKEND_URL: BACKEND_URL },
    },
  ],
  {
    killOthersOn: ["failure", "success"],
    prefixColors: ["cyan", "magenta"],
    restartTries: 0,
  },
);

// ---- wait for ports, open browser -----------------------------------------

if (OPEN_BROWSER) {
  waitOn({
    resources: [`tcp:127.0.0.1:${UI_PORT}`, `tcp:127.0.0.1:${CONTROL_PORT}`],
    timeout: 15000,
    interval: 200,
  })
    .then(() => open(`http://localhost:${UI_PORT}/`))
    .catch((err) => console.warn(`WARN: ${err.message} — ports didn't bind, not opening browser`));
}

result.catch(() => process.exit(1));

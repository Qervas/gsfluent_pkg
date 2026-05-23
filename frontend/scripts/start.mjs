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
//   VISER_CACHE_DIR        default <repo>/work/cache/viser
//   OPEN_BROWSER           default 1 (0 disables auto-open)

import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, statSync } from "node:fs";
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
const VISER_CACHE_DIR = process.env.VISER_CACHE_DIR ?? resolve(PKG_ROOT, "work/cache/viser");
const BACKEND_URL = process.env.GSFLUENT_BACKEND_URL ?? process.env.BACKEND_URL ?? "";
const OPEN_BROWSER = process.env.OPEN_BROWSER !== "0";

// ---- preflight -------------------------------------------------------------

if (!existsSync(VENV_PY)) {
  console.error(`ERROR: ${VENV_PY} not found.\n\nRun \`npm install\` first; it creates the venv + builds the SPA.`);
  process.exit(1);
}
if (!existsSync(VISER_SCRIPT)) {
  console.error(`ERROR: ${VISER_SCRIPT} missing — wrong working tree?`);
  process.exit(1);
}
mkdirSync(VISER_CACHE_DIR, { recursive: true });

// ---- rebuild dist if any source file is newer than the last build ----------
// Without this, `git pull && npm start` silently serves a stale bundle —
// the SPA's auto-trigger / pill / etc. only exists in source, not in dist/.
// Set SKIP_BUILD=1 to bypass (e.g. when iterating on viser_headless only).

function srcIsNewerThanDist() {
  const distHtml = resolve(FRONTEND_DIR, "dist/index.html");
  if (!existsSync(distHtml)) return true;
  const distMtime = statSync(distHtml).mtimeMs;
  // Walk frontend/src/ and frontend/index.html — the inputs to vite build.
  // node_modules + dist are excluded automatically since we cap depth.
  const r = spawnSync(
    "find",
    [
      resolve(FRONTEND_DIR, "src"),
      resolve(FRONTEND_DIR, "index.html"),
      "-type", "f",
      "-newer", distHtml,
      "-print", "-quit",
    ],
    { encoding: "utf8" },
  );
  return (r.stdout || "").length > 0;
}

if (process.env.SKIP_BUILD !== "1" && srcIsNewerThanDist()) {
  console.log(">>> dist is stale — rebuilding (set SKIP_BUILD=1 to skip)");
  const b = spawnSync("npm", ["run", "build"], { cwd: FRONTEND_DIR, stdio: "inherit" });
  if (b.status !== 0) {
    console.error("ERROR: vite build failed. Fix the errors above and re-run.");
    process.exit(1);
  }
  console.log(">>> dist rebuilt\n");
} else if (!existsSync(resolve(FRONTEND_DIR, "dist/index.html"))) {
  console.error("ERROR: frontend/dist/index.html missing. Run `npm install` to bootstrap.");
  process.exit(1);
}

console.log(`>>> backend:         ${BACKEND_URL || "(unset)"}`);
console.log(`>>> SPA:             http://localhost:${UI_PORT}/`);
console.log(`>>> viser_headless:  127.0.0.1:${VISER_PORT} (splats)  127.0.0.1:${CONTROL_PORT} (control)`);
console.log(`>>> viser cache:     ${VISER_CACHE_DIR}\n`);

// ---- run viser + vite in parallel with shared cleanup ---------------------

const { result } = concurrently(
  [
    {
      name: "viser",
      // --server is required: viser_headless fetches model .ply files
      // from the backend on model-cell resolution, and downloads .gsq
      // caches via the new /sync_cell endpoint. Default in the script
      // is http://localhost:8080, which doesn't match this deployment.
      //
      // No surrounding quotes on the command string: concurrently@9
      // strips the outermost quote pair before passing to /bin/sh,
      // which produced the famous `python" "/path/script.py" ... :24701`
      // mangling. Our paths and BACKEND_URL contain no spaces, so the
      // unquoted form is safe.
      command: [
        VENV_PY,
        VISER_SCRIPT,
        "--cache-dir", VISER_CACHE_DIR,
        "--viser_port", VISER_PORT,
        "--control_port", CONTROL_PORT,
        ...(BACKEND_URL ? ["--server", BACKEND_URL] : []),
      ].join(" "),
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
  // HTTP probe instead of raw TCP — vite preview binds IPv6-only by
  // default on many Linux setups, and tcp:127.0.0.1 misses that. The
  // HTTP probe goes through the OS resolver and picks whichever
  // family is up.
  waitOn({
    resources: [`http://localhost:${UI_PORT}/`, `http://127.0.0.1:${CONTROL_PORT}/state`],
    timeout: 15000,
    interval: 200,
    validateStatus: () => true,
  })
    .then(() => open(`http://localhost:${UI_PORT}/`))
    .catch((err) => console.warn(`WARN: ${err.message} — ports didn't bind, not opening browser`));
}

result.catch(() => process.exit(1));

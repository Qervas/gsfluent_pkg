#!/usr/bin/env node
// Client-local launcher. Fires from `npm start`.
//
// Builds the SPA if the bundle is stale, then serves frontend/dist/ with
// `vite preview` (proxying /api/* to the shared backend) and opens the
// browser. Splats render IN-BROWSER (Spark + three.js, download-then-play)
// — there is no separate viewer process and no client-side Python.
//
// Env (all have safe defaults):
//   GSFLUENT_BACKEND_URL   default ${BACKEND_URL} from .env
//   UI_PORT                default 5173
//   OPEN_BROWSER           default 1 (0 disables auto-open)
//   SKIP_BUILD             default 0 (1 skips the stale-dist rebuild)

import { spawn, spawnSync } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import waitOn from "wait-on";
import open from "open";
import dotenv from "dotenv";

const FRONTEND_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const PKG_ROOT = dirname(FRONTEND_DIR);

dotenv.config({ path: resolve(PKG_ROOT, ".env") });

const UI_PORT = process.env.UI_PORT ?? "5173";
const BACKEND_URL = process.env.GSFLUENT_BACKEND_URL ?? process.env.BACKEND_URL ?? "";
const OPEN_BROWSER = process.env.OPEN_BROWSER !== "0";

// ---- rebuild dist if any source file is newer than the last build ----------
// Without this, `git pull && npm start` silently serves a stale bundle.
// Set SKIP_BUILD=1 to bypass.

function srcIsNewerThanDist() {
  const distHtml = resolve(FRONTEND_DIR, "dist/index.html");
  if (!existsSync(distHtml)) return true;
  const distMtime = statSync(distHtml).mtimeMs;
  void distMtime; // (find -newer uses the file directly below)
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

console.log(`>>> backend:  ${BACKEND_URL || "(unset — set GSFLUENT_BACKEND_URL or BACKEND_URL in .env)"}`);
console.log(`>>> SPA:      http://localhost:${UI_PORT}/\n`);

// ---- serve the SPA ---------------------------------------------------------

const child = spawn(
  "npx",
  ["vite", "preview", "--port", UI_PORT, "--strictPort"],
  {
    cwd: FRONTEND_DIR,
    stdio: "inherit",
    env: { ...process.env, GSFLUENT_BACKEND_URL: BACKEND_URL },
  },
);

// Propagate Ctrl-C to the child, then exit when it does.
process.on("SIGINT", () => child.kill("SIGINT"));
process.on("SIGTERM", () => child.kill("SIGTERM"));
child.on("exit", (code) => process.exit(code ?? 0));

// ---- wait for the port, open the browser -----------------------------------

if (OPEN_BROWSER) {
  waitOn({
    resources: [`http://localhost:${UI_PORT}/`],
    timeout: 15000,
    interval: 200,
    validateStatus: () => true,
  })
    .then(() => open(`http://localhost:${UI_PORT}/`))
    .catch((err) => console.warn(`WARN: ${err.message} — port didn't bind, not opening browser`));
}

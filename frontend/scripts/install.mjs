#!/usr/bin/env node
// Client-local installer. Fires from `npm install` as a postinstall hook.
//
// What it does:
//   1. Probes python3 (3.10+ required).
//   2. Creates a .venv at repo root and pip-installs viser + the
//      sidecar deps (fastapi/uvicorn/httpx/eval_type_backport).
//   3. Runs `npm run build` to populate frontend/dist/  (unless
//      PYTHON_ONLY=1, used by `npm run install:python`).
//   4. Drops a placeholder .npz into work/cache/viser/ so viser_headless
//      can start on a fresh cache (its --npz_dir loader requires ≥1 file).
//
// Env:
//   PYTHON_BIN                 python3.10+ to use (default: python3)
//   PYTHON_ONLY                if 1, skip the SPA build
//   GSFLUENT_SKIP_NPM_INSTALL  if 1, skip `npm ci` (set by postinstall)

import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const PKG_ROOT = dirname(FRONTEND_DIR);
const VENV_DIR = resolve(PKG_ROOT, ".venv");
const VENV_PY = resolve(VENV_DIR, "bin/python");
const VISER_NPZ_DIR = resolve(PKG_ROOT, "work/cache/viser");

const note = (msg) => console.log(`>>> ${msg}`);
const die = (msg) => { console.error(`ERROR: ${msg}`); process.exit(1); };

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (r.status !== 0) die(`${cmd} ${args.join(" ")} exited ${r.status}`);
}

function runCapture(cmd, args) {
  const r = spawnSync(cmd, args, { encoding: "utf8" });
  return r.status === 0 ? r.stdout.trim() : null;
}

// ---- 1/4: python preflight -------------------------------------------------

const PYTHON_BIN = process.env.PYTHON_BIN ?? "python3";
const pyVer = runCapture(PYTHON_BIN, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"]);
if (!pyVer) die(`${PYTHON_BIN} not found. Install Python 3.10+ or set PYTHON_BIN=/path/to/python3.`);
note(`python: ${PYTHON_BIN} (${pyVer})`);

const [major, minor] = pyVer.split(".").map(Number);
if (major < 3 || (major === 3 && minor < 10)) {
  die(`Python 3.10+ required; got ${pyVer}. Set PYTHON_BIN=/path/to/python3.10 (or newer).`);
}

// ---- 2/4: venv + python deps -----------------------------------------------

if (!existsSync(VENV_DIR)) {
  note(`creating venv at ${VENV_DIR}`);
  run(PYTHON_BIN, ["-m", "venv", VENV_DIR]);
}
if (!existsSync(VENV_PY)) die(`venv creation failed; expected ${VENV_PY}`);

note("upgrading pip in venv");
run(VENV_PY, ["-m", "pip", "install", "--quiet", "--upgrade", "pip"]);

note("installing python deps into venv");
run(VENV_PY, ["-m", "pip", "install", "--quiet",
  "viser>=1.0,<2",
  "numpy>=1.24",
  "fastapi>=0.110",
  "uvicorn[standard]>=0.30",
  "httpx>=0.27",
  "eval_type_backport>=0.2",
  // plyfile: required by viser_headless.mmap_model_cell to parse 3DGS
  // .ply files. Missing it crashes model loads with parse_failed.
  "plyfile>=1.0",
  // zstandard: decoder for the .gsq streamable splat format
  // (server/tools/pack_splats.py emits zstd-compressed per-frame chunks).
  "zstandard>=0.22",
]);

// Patch viser's bundled shader to remove the lambda2 < 0 cull that
// produces visible horizontal-line / region-shaped artifacts on
// anisotropic 3DGS splats. Idempotent — re-running detects the patch
// is already applied and exits early. Without this, freshly-installed
// viser shows visual garbage on every realistic 3DGS scene.
const PATCH_SCRIPT = resolve(FRONTEND_DIR, "patches/patch-viser.sh");
if (existsSync(PATCH_SCRIPT)) {
  note("patching viser shader (one-time per install, ~30s)");
  run("bash", [PATCH_SCRIPT]);
}

// ---- 3/4: SPA build --------------------------------------------------------

if (process.env.PYTHON_ONLY === "1") {
  note("PYTHON_ONLY=1 — skipping SPA build");
} else {
  if (process.env.GSFLUENT_SKIP_NPM_INSTALL === "1") {
    note("GSFLUENT_SKIP_NPM_INSTALL=1 — skipping npm ci (already done by parent)");
  } else {
    note("installing frontend npm deps (npm ci)");
    run("npm", ["ci", "--no-fund", "--no-audit"], { cwd: FRONTEND_DIR });
  }
  note("building SPA into frontend/dist/");
  run("npm", ["run", "build"], { cwd: FRONTEND_DIR });
  if (!existsSync(resolve(FRONTEND_DIR, "dist/index.html"))) {
    die("build finished but frontend/dist/index.html is missing — check npm output");
  }
}

// ---- 4/4: placeholder npz --------------------------------------------------
// viser_headless refuses to start on an empty --npz_dir. Drop a one-splat
// placeholder so a fresh install works before any real sequences are
// synced down. Name starts with `_`, which the backend regex rejects, so
// it can never collide with a real cell.

// ---- bootstrap .env from .env.example if missing --------------------------
// Without .env, `npm start` falls back to localhost:8080 and every /api/*
// call 502s. Copy the template so teammates only have to edit one line
// (BACKEND_URL) instead of figuring out the full key set.

const ENV_FILE = resolve(PKG_ROOT, ".env");
const ENV_EXAMPLE = resolve(PKG_ROOT, ".env.example");
if (!existsSync(ENV_FILE) && existsSync(ENV_EXAMPLE)) {
  copyFileSync(ENV_EXAMPLE, ENV_FILE);
  note(`bootstrapped .env from .env.example — edit ${ENV_FILE} and set BACKEND_URL`);
}

mkdirSync(VISER_NPZ_DIR, { recursive: true });
const hasNpz = readdirSync(VISER_NPZ_DIR).some((f) => f.endsWith(".npz"));
if (!hasNpz) {
  note("writing placeholder .npz");
  run(VENV_PY, ["-c", `
import numpy as np
np.savez(
    "${resolve(VISER_NPZ_DIR, "_placeholder.npz")}",
    frames=np.zeros((1, 1, 3), dtype=np.float32),
    cov=np.eye(3, dtype=np.float32)[None],
    rgb=np.zeros((1, 3), dtype=np.float32),
    opacity=np.zeros((1,), dtype=np.float32),
)
`]);
}

note("done.");
console.log("\nNext:  cd frontend && npm start");

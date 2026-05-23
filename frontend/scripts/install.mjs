#!/usr/bin/env node
// Client-local installer. Fires from `npm install` as a postinstall hook.
//
// What it does:
//   1. Probes uv (preferred) or python3 (fallback) and confirms a
//      Python 3.10+ interpreter is reachable.
//   2. Creates the unified .venv at the repo root (Python 3.12 via uv
//      when possible). One venv serves both the server stack AND the
//      client-side viser/sidecar stack — there is no separate
//      server/.venv anymore.
//   3. Installs the gsfluent package (editable) plus its `dev` + `client`
//      extras from server/pyproject.toml. That single source-of-truth
//      definition pulls fastapi/uvicorn/pydantic/numpy/scipy/zstandard/
//      plyfile (server stack), viser/httpx/eval_type_backport (client
//      stack), and pytest/ruff/mypy/hypothesis (dev stack).
//   4. Runs `npm run build` to populate frontend/dist/ (unless
//      PYTHON_ONLY=1, used by `npm run install:python`).
//   5. Ensures work/cache/viser/ exists for viser_headless (--cache-dir).
//
// Env:
//   PYTHON_BIN                 python3.10+ to use when uv is absent
//                              (default: python3). Ignored when uv is on PATH.
//   PYTHON_ONLY                if 1, skip the SPA build
//   GSFLUENT_SKIP_NPM_INSTALL  if 1, skip `npm ci` (set by postinstall)
//   GSFLUENT_PIN_PYTHON        Python version to install with uv
//                              (default: 3.12 — matches CI + the prior
//                              server/.venv)

import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const PKG_ROOT = dirname(FRONTEND_DIR);
const SERVER_DIR = resolve(PKG_ROOT, "server");
const VENV_DIR = resolve(PKG_ROOT, ".venv");
const VENV_PY = resolve(VENV_DIR, "bin/python");
const VISER_CACHE_DIR = resolve(PKG_ROOT, "work/cache/viser");

const PIN_PYTHON = process.env.GSFLUENT_PIN_PYTHON ?? "3.12";

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

function hasCommand(cmd) {
  const r = spawnSync("sh", ["-c", `command -v ${cmd}`], { stdio: "ignore" });
  return r.status === 0;
}

// ---- 1/4: python preflight -------------------------------------------------

const USE_UV = hasCommand("uv");
if (USE_UV) {
  const uvVer = runCapture("uv", ["--version"]);
  note(`uv: ${uvVer ?? "(version unknown)"}`);
  note(`pinned python: ${PIN_PYTHON}`);
} else {
  const PYTHON_BIN = process.env.PYTHON_BIN ?? "python3";
  const pyVer = runCapture(PYTHON_BIN, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"]);
  if (!pyVer) die(`${PYTHON_BIN} not found and uv is unavailable. Install uv (https://docs.astral.sh/uv/) or Python 3.10+, or set PYTHON_BIN=/path/to/python3.`);
  note(`python: ${PYTHON_BIN} (${pyVer})`);

  const [major, minor] = pyVer.split(".").map(Number);
  if (major < 3 || (major === 3 && minor < 10)) {
    die(`Python 3.10+ required; got ${pyVer}. Install uv (https://docs.astral.sh/uv/) for an auto-managed 3.12, or set PYTHON_BIN=/path/to/python3.10 (or newer).`);
  }
}

// ---- 2/4: unified venv + python deps --------------------------------------
//
// One .venv at the repo root for everything: server (fastapi/pydantic/
// scipy/etc), client (viser/httpx/eval_type_backport), and dev
// (pytest/ruff/mypy/hypothesis). The single source of truth is
// server/pyproject.toml — extras live there, not in a hand-rolled pip
// list below.

if (!existsSync(VENV_DIR)) {
  note(`creating venv at ${VENV_DIR}`);
  if (USE_UV) {
    run("uv", ["venv", VENV_DIR, "--python", PIN_PYTHON]);
  } else {
    const PYTHON_BIN = process.env.PYTHON_BIN ?? "python3";
    run(PYTHON_BIN, ["-m", "venv", VENV_DIR]);
  }
}
if (!existsSync(VENV_PY)) die(`venv creation failed; expected ${VENV_PY}`);

note("installing python deps into venv (server + client + dev extras)");
if (USE_UV) {
  // `uv sync` respects server/uv.lock for fully reproducible installs.
  // UV_PROJECT_ENVIRONMENT pins it at the unified <repo>/.venv instead
  // of uv's default <project>/.venv (which would resurrect server/.venv).
  run("uv", [
    "sync",
    "--project", SERVER_DIR,
    "--extra", "dev",
    "--extra", "client",
  ], {
    env: { ...process.env, UV_PROJECT_ENVIRONMENT: VENV_DIR },
  });
} else {
  // Plain-pip fallback. Slower and not lockfile-aware, but works in
  // environments where uv isn't yet installed.
  run(VENV_PY, ["-m", "pip", "install", "--quiet", "--upgrade", "pip"]);
  run(VENV_PY, ["-m", "pip", "install", "--quiet",
    "-e", `${SERVER_DIR}[dev,client]`,
  ]);
}

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

// Make sure the cache dir exists. viser_headless now boots fine on an
// empty dir (lazy cell load on first /set), so no placeholder needed.
mkdirSync(VISER_CACHE_DIR, { recursive: true });

note("done.");
console.log("\nNext:  cd frontend && npm start");

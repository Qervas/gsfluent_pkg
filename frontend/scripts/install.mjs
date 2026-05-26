#!/usr/bin/env node
// Client-local installer. Fires from `npm install` as a postinstall hook.
//
// Default (`npm install`): JS only — installs frontend npm deps and builds
// the SPA into frontend/dist/. A teammate who only runs the SPA needs nothing
// but Node: splats render in-browser and the backend is remote.
//
// The Python venv (server + dev deps: fastapi/numpy/pytest/...) is OPT-IN,
// for a box that also runs the backend or the test suite:
//     npm run install:python          # venv only (PYTHON_ONLY=1)
//   or GSFLUENT_WITH_PYTHON=1 npm install   # venv + SPA build
//
// Env:
//   PYTHON_ONLY                if 1, set up the venv and SKIP the SPA build
//   GSFLUENT_WITH_PYTHON       if 1, set up the venv IN ADDITION to the SPA
//   PYTHON_BIN                 python3.10+ when uv is absent (default python3)
//   GSFLUENT_PIN_PYTHON        uv-managed python version (default 3.12)
//   GSFLUENT_SKIP_NPM_INSTALL  if 1, skip `npm ci` (set by the postinstall hook)

import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const PKG_ROOT = dirname(FRONTEND_DIR);
const SERVER_DIR = resolve(PKG_ROOT, "server");
const VENV_DIR = resolve(PKG_ROOT, ".venv");
const VENV_PY = resolve(VENV_DIR, "bin/python");

const PIN_PYTHON = process.env.GSFLUENT_PIN_PYTHON ?? "3.12";
const PYTHON_ONLY = process.env.PYTHON_ONLY === "1";
const INSTALL_PYTHON = PYTHON_ONLY || process.env.GSFLUENT_WITH_PYTHON === "1";

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

// ---- Python venv (opt-in: server + dev deps) ------------------------------
// One .venv at the repo root for the backend + test suite (fastapi/pydantic/
// numpy/scipy/zstandard/plyfile + pytest/ruff/mypy). Source of truth is
// server/pyproject.toml's `dev` extra. The SPA itself is pure JS and uses
// none of this — hence opt-in.

if (INSTALL_PYTHON) {
  const USE_UV = hasCommand("uv");
  if (USE_UV) {
    note(`uv: ${runCapture("uv", ["--version"]) ?? "(version unknown)"}  pinned python: ${PIN_PYTHON}`);
  } else {
    const PYTHON_BIN = process.env.PYTHON_BIN ?? "python3";
    const pyVer = runCapture(PYTHON_BIN, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"]);
    if (!pyVer) die(`${PYTHON_BIN} not found and uv is unavailable. Install uv (https://docs.astral.sh/uv/) or Python 3.10+, or set PYTHON_BIN=/path/to/python3.`);
    note(`python: ${PYTHON_BIN} (${pyVer})`);
    const [major, minor] = pyVer.split(".").map(Number);
    if (major < 3 || (major === 3 && minor < 10)) {
      die(`Python 3.10+ required; got ${pyVer}. Install uv for an auto-managed 3.12, or set PYTHON_BIN=/path/to/python3.10+.`);
    }
  }

  if (!existsSync(VENV_DIR)) {
    note(`creating venv at ${VENV_DIR}`);
    if (USE_UV) {
      run("uv", ["venv", VENV_DIR, "--python", PIN_PYTHON]);
    } else {
      run(process.env.PYTHON_BIN ?? "python3", ["-m", "venv", VENV_DIR]);
    }
  }
  if (!existsSync(VENV_PY)) die(`venv creation failed; expected ${VENV_PY}`);

  note("installing python deps into venv (server + dev extras)");
  if (USE_UV) {
    run("uv", ["sync", "--project", SERVER_DIR, "--extra", "dev"], {
      env: { ...process.env, UV_PROJECT_ENVIRONMENT: VENV_DIR },
    });
  } else {
    run(VENV_PY, ["-m", "pip", "install", "--quiet", "--upgrade", "pip"]);
    run(VENV_PY, ["-m", "pip", "install", "--quiet", "-e", `${SERVER_DIR}[dev]`]);
  }
} else {
  note("SPA-only install (no Python venv). For backend/test deps: `npm run install:python`.");
}

// ---- SPA build (unless PYTHON_ONLY) ---------------------------------------

if (PYTHON_ONLY) {
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
// Without .env, `npm start` has no backend URL and every /api/* call fails.
// Copy the template so teammates only edit one line (BACKEND_URL).

const ENV_FILE = resolve(PKG_ROOT, ".env");
const ENV_EXAMPLE = resolve(PKG_ROOT, ".env.example");
if (!existsSync(ENV_FILE) && existsSync(ENV_EXAMPLE)) {
  copyFileSync(ENV_EXAMPLE, ENV_FILE);
  note(`bootstrapped .env from .env.example — edit ${ENV_FILE} and set BACKEND_URL`);
}

note("done.");
console.log("\nNext:  cd frontend && npm start");

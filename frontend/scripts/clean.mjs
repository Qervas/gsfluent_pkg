#!/usr/bin/env node
// Teardown for `npm run clean`.
//
// Nukes everything `npm install` + `npm start` leave behind, so a
// teammate can redo the whole bootstrap from a clean slate:
//
//   - .venv/        (pkg root)   pip venv from scripts/_install.sh
//   - frontend/dist/                    vite build output
//   - frontend/node_modules/.vite/      vite's preview/dev cache
//   - frontend/tsconfig*.tsbuildinfo    tsc -b incremental state
//   - frontend/vite.config.js / .d.ts   emitted from vite.config.ts
//
// Then sweeps stray viser_headless + `vite preview` processes that a
// previous `npm start` left running (ECONNRESET on Ctrl-C, orphans
// after a terminal close, etc.). pkill is best-effort — failure to
// find a process is fine.
//
// Intentionally does NOT nuke frontend/node_modules itself — that's a
// 5-minute reinstall on cold cache, and `npm install` will reconcile
// it. Pass `--deep` if you really want the slow path.

import { execFileSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PKG_ROOT = resolve(FRONTEND, "..");
const DEEP = process.argv.includes("--deep");

const targets = [
  resolve(PKG_ROOT, ".venv"),
  resolve(FRONTEND, "dist"),
  resolve(FRONTEND, "node_modules", ".vite"),
  resolve(FRONTEND, "tsconfig.tsbuildinfo"),
  resolve(FRONTEND, "tsconfig.node.tsbuildinfo"),
  resolve(FRONTEND, "vite.config.js"),
  resolve(FRONTEND, "vite.config.d.ts"),
];
if (DEEP) {
  targets.push(resolve(FRONTEND, "node_modules"));
}

for (const p of targets) {
  if (existsSync(p)) {
    console.log(`>>> rm  ${p}`);
    rmSync(p, { recursive: true, force: true });
  } else {
    console.log(`    skip ${p} (absent)`);
  }
}

// Best-effort: kill leftover viser_headless / `vite preview` processes
// that a previous `npm start` may have orphaned. Patterns are hardcoded
// constants (no user-supplied input — execFileSync, not exec).
const patterns = [
  "tools/viser_headless.py",
  "vite preview --port",
];
for (const pat of patterns) {
  try {
    execFileSync("pkill", ["-f", pat], { stdio: "ignore" });
    console.log(`>>> killed processes matching: ${pat}`);
  } catch {
    // pkill exits non-zero when no match — that's the happy path.
    console.log(`    no process matched: ${pat}`);
  }
}

console.log("");
console.log(">>> clean done.");
console.log("    Next:  npm install   # rebuild venv + dist");

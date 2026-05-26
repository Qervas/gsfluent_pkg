# server/Makefile — convenience targets for the gsfluent backend.
#
# Strong split: this builds/tests the SERVER ONLY. The SPA lives on the
# client (see ../frontend/).
#
# Venv layout: there is a SINGLE unified .venv at the repo root
# (../.venv/) that holds both server and client deps. It is normally
# created by `npm install` (which runs frontend/scripts/install.mjs).
# For server-only installs (no SPA build), call the install target
# below — it uses uv against pyproject.toml's extras.
.PHONY: install install-server test sync lock

ROOT_VENV := ../.venv
ROOT_PY   := $(ROOT_VENV)/bin/python

# Reproducible install via uv into the root venv (backend + dev/test deps).
install:
	uv pip install --python $(ROOT_PY) -e '.[dev]'

# Alias kept for muscle memory; identical to `install` now that the SPA
# renders in-browser and the backend has no client-side render deps.
install-server:
	uv pip install --python $(ROOT_PY) -e '.[dev]'

# Bump the lockfile after editing dependencies in pyproject.toml. Kept
# for parity with the uv-managed workflow even though the unified venv
# is materialised by `uv pip install -e` rather than `uv sync`.
lock:
	uv lock

# Legacy alias — `sync` used to create server/.venv via `uv sync`. The
# unified venv replaces that; aim at the install target instead.
sync: install

test:
	PYTHONPATH=. $(ROOT_PY) -m pytest -v

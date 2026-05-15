# server/Makefile — convenience targets for the gsfluent backend.
# Strong split: this builds/tests the SERVER ONLY. The SPA lives on the
# client (see ../setup-client.sh + ../run-client.sh).
.PHONY: install test sync lock

# Reproducible install via uv (matches what setup-server.sh does).
sync:
	uv sync

# Bump the lockfile after editing dependencies in pyproject.toml.
lock:
	uv lock

test:
	uv run pytest -v

# Editable install w/ dev extras (for IDE / running pytest manually).
install:
	uv sync --extra dev

# server/Makefile — convenience targets for the gsfluent backend.
.PHONY: build dev test install

# Build everything: Vite frontend → server/gsfluent/static, then pip install -e.
build:
	cd ../frontend && npm install --no-fund --no-audit && npm run build
	pip install -e .

# Print dev instructions (interactive, run manually).
dev:
	@echo "Run these in separate terminals:"
	@echo "  terminal 1:  cd server && python -m gsfluent serve --no-browser --reload"
	@echo "  terminal 2:  cd frontend && npm run dev    # opens http://localhost:5173"

test:
	pytest -v

install:
	pip install -e ".[dev]"

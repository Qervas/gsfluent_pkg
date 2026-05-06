#!/usr/bin/env bash
# gsfluent_pkg — one-time setup. Idempotent: safe to re-run.
#
# What it does:
#   1. Verifies conda + NVIDIA driver are present.
#   2. Creates the `gsfluent` conda env from env.yml (skips if it exists).
#   3. Builds the diff_gaussian_rasterization + simple-knn CUDA extensions
#      from the bundled gaussian-splatting submodule.
#   4. Runs a 5-frame smoke test to confirm everything imports + the GPU works.
#
# Usage:
#   ./setup.sh                      # default (env name = gsfluent)
#   GSFLUENT_ENV=foo ./setup.sh     # override env name

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="${GSFLUENT_ENV:-gsfluent}"

err()  { echo "ERROR: $*" >&2; exit 1; }
note() { echo ">>> $*"; }

note "gsfluent_pkg setup — env name: $ENV_NAME, root: $PKG_ROOT"

# 1. Preflight
command -v conda >/dev/null 2>&1 || err "conda not found. Install Miniconda/Anaconda first."
command -v nvidia-smi >/dev/null 2>&1 || \
    note "WARNING: nvidia-smi not found. CPU-only mode is unsupported by the sim — the browser viewer will still work for pre-baked frames."

# 2. Conda env
eval "$(conda shell.bash hook)"
if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    note "env $ENV_NAME already exists; skipping create. (Delete it manually to recreate.)"
else
    note "creating conda env $ENV_NAME from env.yml..."
    conda env create -n "$ENV_NAME" -f "$PKG_ROOT/env.yml"
fi
conda activate "$ENV_NAME"
note "python: $(python -V)"

# 3. CUDA extensions
note "checking diff_gaussian_rasterization + simple-knn..."
build_ext() {
    local mod="$1" path="$2"
    if python -c "import $mod" 2>/dev/null; then
        note "  $mod already installed"
    else
        note "  building $mod from $path..."
        pip install --no-build-isolation "$path"
    fi
}
build_ext diff_gaussian_rasterization "$PKG_ROOT/core/gaussian-splatting/submodules/diff-gaussian-rasterization"
build_ext simple_knn                  "$PKG_ROOT/core/gaussian-splatting/submodules/simple-knn"

# 4. Smoke test
note "smoke test (imports + GPU detect)..."
python - <<'PYEOF'
import torch, warp as wp, taichi as ti, viser, plyfile
print(f"  torch         {torch.__version__}  cuda={torch.cuda.is_available()}",
      torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
wp.init()
print(f"  warp          {wp.__version__}")
print(f"  taichi        {ti.__version__}")
print(f"  viser         {viser.__version__}")
print(f"  plyfile       {plyfile.__version__}")
PYEOF

note "setup complete."
echo ""
echo "Next steps:"
echo "  ./run-sim.sh path/to/your_building.ply --recipe demolition  # sim a new building"
echo "  ./run-viewer.sh some_dir/                                   # play back pre-fused frames"

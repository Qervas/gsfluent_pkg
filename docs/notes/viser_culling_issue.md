# Upstream issue draft — viser: expose Gaussian-splat culling threshold

**Target repo:** https://github.com/nerfstudio-project/viser
**File:** `src/viser/client/src/Splatting/GaussianSplatsHelpers.ts`
**Tested version:** viser 1.0.27 (current pinned in `server/uv.lock`).

## Title

`GaussianSplats: expose vertex-shader weightedDeterminant cull threshold (currently hardcoded 0.25)`

## Symptom

When rendering a Gaussian-splat scene whose source 3DGS reconstruction
contains many splats with small linear scale (≲ 0.02 world units) and
low opacity (median ≈ 0.31, p1 ≈ 0.004), entire **regions** of the model
visibly wink in/out as the camera orbits.

The region that culls is reproducible per camera angle: orbit back and
the same splats return. The culled region is spatially coherent (it
follows a cluster of similar-attribute splats), not random per-frame
flicker.

Points-mode (vanilla three.js scatter) of the same `.ply` renders
without any culling, confirming the data is fine — only the
GaussianSplat path is affected.

## Cause

`src/Splatting/GaussianSplatsHelpers.ts`, vertex shader:

```glsl
// Throw the Gaussian off the screen if it's too close, too far, or too small.
float weightedDeterminant = vRgba.a * (diag1 * diag2 - offDiag * offDiag);
if (weightedDeterminant < 0.25)
  return;
```

`diag1 / diag2 / offDiag` are the 2D projected covariance entries in
pixel units (after `mat3 J` perspective Jacobian + the `+ 0.3`
anti-aliasing dilation). Their product is the projected determinant —
a strong function of camera distance, FOV, and splat orientation
relative to the view ray.

For low-opacity small-scale splats, `vRgba.a * det_2d` sits right at
the 0.25 boundary. A small camera move pushes the projected
determinant on either side → splat appears or disappears.

This is intentional culling, but the threshold is not configurable.

## Reproduction

A 3DGS reconstruction with many sub-0.02 linear-scale splats at
moderate opacity reproduces it reliably. Public Mip-NeRF360 scenes
trained with default Inria 3DGS settings exhibit this.

Minimal Python repro using viser directly (no fuse / no sim
machinery — just feeds the .ply through):

```python
import viser, numpy as np
from plyfile import PlyData

ply = PlyData.read("path/to/3dgs/point_cloud.ply")["vertex"].data
n = len(ply)
centers   = np.stack([ply["x"], ply["y"], ply["z"]], -1).astype(np.float32)
log_s     = np.stack([ply["scale_0"], ply["scale_1"], ply["scale_2"]], -1)
scales    = np.exp(log_s).astype(np.float32)
opacities = (1.0 / (1.0 + np.exp(-ply["opacity"]))).reshape(-1,1).astype(np.float32)
# Identity quaternions for the demo — keep it simple
R = np.tile(np.eye(3, dtype=np.float32), (n, 1, 1))
cov = np.einsum("nij,nj,nkj->nik", R, scales*scales, R).astype(np.float32)
rgbs = np.full((n, 3), 0.5, dtype=np.float32)

server = viser.ViserServer()
server.scene.add_gaussian_splats(
    "splat", centers=centers, covariances=cov,
    rgbs=rgbs, opacities=opacities,
)
input("orbit camera and observe regions of the scene winking in/out")
```

## Suggested fix

Expose the threshold as a shader uniform with the current value as
default, so callers who care more about completeness than performance
can lower it (or set it to 0 for no culling):

```glsl
// in the uniforms block
uniform float cullThreshold; // default 0.25; 0 disables threshold cull

// later
if (weightedDeterminant < cullThreshold)
  return;
```

Plumb a matching `cull_threshold: float = 0.25` parameter through
`add_gaussian_splats(...)` in `_scene_api.py` and the message types.

Setting it to a smaller value (0.01 or 0.0) eliminates the
view-dependent winking for scenes with many low-opacity small-scale
splats, at a modest cost to frame rate.

## Workaround for current users (no upstream change)

Either of these masks the symptom without patching viser:

1. **Inflate scales before feeding to viser.** Clamp each splat's
   linear scale to a floor of ~0.03 world units (typical scene
   ~50 units across). Splats stay visible at all camera angles. A
   matching `np.maximum(scales, 0.03, out=scales)` after
   `np.exp(log_scales)`. Visually imperceptible.
2. **Pre-drop low-opacity splats.** `opacity_thresh = 0.05`, discard
   splats whose `opacity * max_scale^2 < threshold`. Reduces splat
   count and avoids the boundary entirely.

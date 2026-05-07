# Splat lib spike — outcome

**Library:** @mkkellogg/gaussian-splats-3d v0.4.7 (resolved from `^0.4.4`)
**Question:** Can we update `centers` in-place per frame at 200k splats and sustain >= 30 fps?

## How to verify

1. `cd spike/splat-test && npm install && npm run dev`
2. Open http://localhost:5173 in Chrome.
3. Watch the FPS counter in the top-left for 30 seconds. Initial bake takes ~1-3 s on a laptop CPU; after the "ready" message appears, the 200k splats animate on a sin-wave Y offset and the FPS counter shows the steady-state R3F frame rate.
4. Drag with the mouse to orbit the camera; scroll to zoom. The animation must stay smooth while orbiting (the bottleneck candidate is the per-frame data-texture upload + sort, both of which scale with splat count and camera motion).

## Result

- [ ] PASS — sustained >= 30 fps with 200k splats animating: proceed with this lib in Phase 3.
- [ ] FAIL — fps drops below 30: fall back to a custom R3F shader splat renderer (~+1 week to Phase 3 budget).

The implementer scaffolded + booted; the human checks the box after visual verification.

## Confirmed API surface

The code below is what the spike actually calls. These are the real symbols on v0.4.7 — verified by reading `build/gaussian-splats-3d.module.js`.

### Build splats from in-memory data (no PLY round-trip)

```ts
const arr = new GaussianSplats3D.UncompressedSplatArray(0); // arg = SH degree
arr.addSplatFromComonents(
  x, y, z,           // center
  sx, sy, sz,        // scale (3 components)
  qw, qx, qy, qz,    // rotation quaternion, w first
  r, g, b, alpha     // color (uint8 0..255 per channel)
);
// ...repeat for each splat...

const gen = GaussianSplats3D.SplatBufferGenerator.getStandardGenerator(
  0,                       // alphaRemovalThreshold
  0,                       // compressionLevel — 0 = uncompressed (required for live mutation)
  0,                       // sectionSize (0 = auto)
  new THREE.Vector3()      // sceneCenter
);
const splatBuffer = gen.generateFromUncompressedSplatArray(arr);
```

Note: `addSplatFromComonents` is spelled exactly that way in the lib (a typo in the source — "Comonents", missing the second "p"). Do not "fix" it.

### Mount the viewer in an R3F scene

```ts
const dropIn = new GaussianSplats3D.DropInViewer({
  gpuAcceleratedSort: true,
  sharedMemoryForWorkers: false,   // avoid COOP/COEP requirement
  dynamicScene: true,              // REQUIRED to disable static-scene optimisations
  sphericalHarmonicsDegree: 0,
});
// JSX: <primitive object={dropIn} />
```

`DropInViewer` extends `THREE.Group`. Its `onBeforeRender` callback calls
`viewer.update(renderer, camera)` automatically each frame, so we don't need
to drive `update()`/`render()` ourselves under R3F.

### Add splats to the viewer

```ts
await dropIn.viewer.addSplatBuffers(
  [splatBuffer],
  [{}],         // per-buffer options (rotation/position/scale/alphaThreshold)
  true,         // finalBuild
  false,        // showLoadingUI
  false,        // showLoadingUIForSplatTreeBuild
  false,        // replaceExisting
  true,         // enableRenderBeforeFirstSort
  true          // preserveVisibleRegion
);
```

`DropInViewer` does NOT expose `addSplatBuffers` directly; reach through
`dropIn.viewer.addSplatBuffers(...)`. (DropInViewer wraps a `Viewer`.)

### Update centers per frame (the load-bearing bit)

There is **no public `updateCenters()` method**. The lib's design is to load
splats from a file and re-sort each frame against a static splat texture. To
mutate centers per frame we use the internal data-texture pipeline:

```ts
const sm = dropIn.splatMesh;
const centers: Float32Array = sm.splatDataTextures.baseData.centers; // mutable, length = N*3
// ...write new xyz values into `centers` in-place...
sm.updateDataTexturesFromBaseData(0, n - 1); // re-pack and push to GPU
```

`updateDataTexturesFromBaseData(fromSplat, toSplat)` re-packs the RGBA32UI
"centersColors" texture from `splatDataTextures.baseData.centers` and
`splatDataTextures.baseData.colors`, then dispatches a sub-image upload via
`gl.texSubImage2D` (see `SplatMesh.updateDataTexture`). With
`gpuAcceleratedSort: true`, the sort kernel reads splat positions from this
same texture, so no extra sort-worker refresh is needed.

This is **internal API**: a future minor version of the lib could rename or
remove `splatDataTextures` / `updateDataTexturesFromBaseData`. The risk is
acceptable for the spike; the production renderer should pin
`@mkkellogg/gaussian-splats-3d` to an exact version.

### Cleanup

```ts
await dropIn.dispose(); // delegates to viewer.dispose()
```

## Notes

- **SH degree 0** keeps splats RGB-only, no view-dependent color. Matches what
  GaussianFluent emits per simulation frame.
- **`compressionLevel: 0`** in the generator is mandatory for in-place center
  mutation. With `compressionLevel >= 1` the SplatBuffer stores quantised,
  bucket-relative positions and `baseData.centers` is recomputed inside
  `updateBaseDataFromSplatBuffers()` from the immutable buffer — overwriting
  any per-frame changes.
- **Bake cost:** building the `SplatBuffer` from a 200k `UncompressedSplatArray`
  is single-threaded JS and takes roughly 1-3 s on a typical laptop CPU. We
  only do this once at startup; per-frame updates skip the bake entirely.
- **Sort cost at 200k:** `gpuAcceleratedSort: true` does the splat-distance
  pass on the GPU and runs a CPU radix sort over the 200k indices each frame.
  This is the main fps gate when the camera moves.
- **Memory footprint at 200k:** centers texture is RGBA32UI 4096x?? (the lib
  picks a power-of-two height that fits 200k RGBA texels). Plus covariances
  texture + scale/rotation textures. Roughly on the order of 10 MB GPU memory
  for the splat data textures alone.
- **Per-frame cost:** rewriting 200k * 3 floats in JS is ~600k float writes
  per frame, plus one `updateDataTexture` sub-image upload of ~3.2 MB
  (200k * 16 B per RGBA32UI texel). The sub-image upload covers `[0, n-1]`,
  i.e. the whole texture — there's no incremental fast-path in this version.

## Failure mode caveat

If `dropIn.splatMesh.splatDataTextures.baseData.centers` is `undefined` after
`addSplatBuffers` resolves, the spike automatically reports `FAIL: ...
not exposed` in the on-screen status line and the animation does nothing.
Treat that as a hard FAIL.

# Pipeline Profiling

Measured 2026-05-27. Pipeline shape (see `ARCHITECTURE.md`):
`recipe → sim (MPM) → fuse → pack .gsq → serve → in-browser render`.

**Conditions:** idle A100 80 GB (GPU0) · 200 k MPM particles · `cluster_6_15`
(683 k splats) · `demolition` recipe · two runs (10 & 30 frames) used to
separate fixed cost from per-frame cost.

## Per-stage cost

| Stage | Fixed cost | Per frame | Notes |
|-------|-----------|-----------|-------|
| Sim (MPM solver) | ~64 s (Taichi JIT + init + model load) | ~1.49 s | GPU; substeps = `frame_dt / substep_dt` (600 for demolition) |
| Fuse (KNN-Kabsch) | small | ~0.79 s | 200 k particles → 683 k splats |
| Pack `.gsq` | ~1.2 s | ~0.51 s | int16 quantize + temporal delta |
| **Total → servable `.gsq`** | | | **≈ 65 + 2.8 · N seconds** (N = frames) |

Model validated: predicts 151.7 s for N=31; measured 151.9 s.

## Headline numbers

- **30-frame run:** sim 110 s + fuse 24 s + pack 17 s ≈ **2.5 min** end-to-end.
- **Full 150-frame run (if numerically stable):** **≈ 8 min** to a playable `.gsq`.
- **`.gsq` size:** ~5.5 MB base keyframe + **~0.34 MB / delta-frame** (new
  keyframe every 30); 30 frames = 15.9 MB.
- **Download:** size ÷ client bandwidth (15.9 MB ≈ 0.8 s @ 20 MB/s).

## Caveats

- **GPU contention is a 5–10× tax.** The shared 8-GPU box: the same run on a
  contended GPU took ~23 min vs ~2 min idle. The sim GPU is pinned via
  `.env CUDA_VISIBLE_DEVICES`; auto-selecting the least-busy GPU at launch is
  still a TODO (`server/gsfluent/core/sim_engines/mpm.py`).
- **Numerical stability is recipe-dependent (the real reliability risk).** The
  `demolition` recipe is numerically unstable: the MPM sim diverges to NaN
  *nondeterministically* (one 30-frame run was clean; a 10-frame run with
  identical params blew up at frame 4). The fuser correctly drops NaN frames
  (`core/fusers/knn_kabsch.py`), so an unstable run silently yields an
  **incomplete** sequence while the run is still marked `done`. `substep_dt` is
  already CFL-safe, so this is a **material-model** instability (stiff +
  plastic + `softening`), not a time-step one. Fixing sim stability + failing
  loudly on dropped frames is prerequisite to trusting full runs.

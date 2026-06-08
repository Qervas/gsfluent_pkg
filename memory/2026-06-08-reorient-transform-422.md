# Reorient Transform 422

Date: 2026-06-08

Symptom: frontend `POST /api/models/{name}/reorient` failed with HTTP 422:
`unknown transform 'rotate_x_neg_90'; expected one of ['y_up_to_z_up', 'flip_180']`.

Root cause: configuration drift between the updated frontend and the remote API
process. The frontend sent Blender-style axis rotation names, but the remote
backend still imported the old `coord_convert.TRANSFORM_NAMES` registry.

Fix: added the Blender-style 90/180 degree axis transforms to
`server/gsfluent/core/coord_convert.py`, kept the legacy aliases, and restarted
the remote backend. Model orientation validation remains a no-op because
orientation is now user-controlled through the reorient API.

Verification:
- Local frontend build passed with `npm run build`.
- Local recipe-validation tests passed: `6 passed`.
- Local and remote direct transform checks found all expected transform names.
- Remote endpoint probe against a scratch model returned HTTP 200 for
  `{"transform":"rotate_x_neg_90"}`.

Related concern: remote `/api/health` returned HTTP 200 but payload status
`"down"` because `disk_free_pct` was low (`0.48`). This was fixed the same day:
low disk now reports `"degraded"` because restarting the API cannot free disk;
only missing `sim_home` remains `"down"`.

Final health remediation: `/data` was still too full for normal health, and the
visible gsfluent-owned data was not large enough to recover 5% free space. The
remote `.env` now sets `GSFLUENT_WORK_DIR=/tmp/gsfluent_pkg_work`, which is on
the root filesystem with ample space. After backend restart, live health reports
`status="ok"` and `disk_free_pct=88.74`.

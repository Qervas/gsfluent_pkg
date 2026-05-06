"""gsfluent workbench — single-URL browser app for the whole pipeline.

What the user does on one page (default http://localhost:8080):
  1. Upload a 3DGS .ply (or paste a path to an existing model dir).
  2. Pick a recipe from the dropdown; sliders show that recipe's defaults.
  3. Tweak params if they want (n_grid, particles, gravity, Young's modulus...).
  4. Click Run. The page tails sim+fuse stderr in a console panel and the
     3D viewport animates the building live as frames are produced.

What this is NOT: a real-time interactive sim. It's a launcher + viewer; the
sim runs as a subprocess (sim_one.sh --live --no-vkgs-launch under the hood)
and frames stream into the viewport at sim-pace (~1/sec at 200k particles).

Usage:
    python tools/workbench.py --pkg-root /path/to/gsfluent_pkg [--port 8080]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import viser
from plyfile import PlyData

# ----------------------------------------------------------------------- params
# Curated subset of recipe keys the workbench surfaces as sliders/inputs.
# Anything else in the recipe JSON is preserved as-is when we write the
# effective recipe to a temp file before spawning the sim.
EXPOSED_PARAMS = [
    # (key, label, min, max, step, group)
    ("n_grid",       "Grid resolution",       50,    400,   10,   "Solver"),
    ("substep_dt",   "Substep dt (s)",        1e-5,  5e-4,  1e-5, "Solver"),
    ("frame_num",    "Total frames",          30,    600,   10,   "Solver"),
    ("frame_dt",     "Frame dt (s)",          0.005, 0.1,   0.005,"Solver"),
    ("init_azimuthm", "Camera azimuth (deg)", 0.0,   360.0, 1.0,  "Camera"),
    ("init_elevation","Camera elevation",     -45.0, 60.0,  1.0,  "Camera"),
    ("init_radius",   "Camera radius",        1.0,   500.0, 1.0,  "Camera"),
]


# ----------------------------------------------------------------------- runner
@dataclass
class SimState:
    state: str = "IDLE"     # IDLE | RUNNING | DONE | ERROR | CANCELLED
    proc: Optional[subprocess.Popen] = None
    output_name: str = ""
    fused_dir: Optional[Path] = None
    t_started: float = 0.0
    t_finished: float = 0.0
    # Set when the workbench's auto-finish path requests termination because
    # all expected frames are fused. The reader thread treats the eventual
    # SIGTERM exit as DONE (not ERROR / CANCELLED) under this flag.
    intentional_finish: bool = False


class SimRunner:
    """Spawn sim_one.sh, drain stderr+stdout into a ring buffer, expose status."""

    MAX_LOG_LINES = 2000

    def __init__(self, pkg_root: Path):
        self.pkg_root = pkg_root
        self.lock = threading.Lock()
        self.state = SimState()
        self.log: deque[str] = deque(maxlen=self.MAX_LOG_LINES)
        self._reader: Optional[threading.Thread] = None

    def is_running(self) -> bool:
        with self.lock:
            return self.state.state == "RUNNING"

    def append_log(self, line: str) -> None:
        with self.lock:
            self.log.append(line.rstrip())

    def last_lines(self, n: int = 20) -> list[str]:
        with self.lock:
            if n >= len(self.log):
                return list(self.log)
            return list(self.log)[-n:]

    def start(self, model_dir: Path, recipe_path: Path, particles: int,
              output_name: str, env_name: str = "gsfluent") -> Optional[str]:
        """Returns None on success, error message on failure."""
        if self.is_running():
            return "Already running. Cancel first."
        sim_one = self.pkg_root / "tools/sim_one.sh"
        if not sim_one.exists():
            return f"sim_one.sh not found at {sim_one}"
        if not (model_dir / "point_cloud").is_dir():
            return f"{model_dir} is missing point_cloud/iteration_*/point_cloud.ply"

        # Compute fused dir the way run-sim.sh does, so we can watch it.
        fused_dir = self.pkg_root / "work/fused" / output_name
        fused_dir.mkdir(parents=True, exist_ok=True)

        # Spawn run-sim.sh in --no-viewer mode (we ARE the viewer).
        env = os.environ.copy()
        env["GSFLUENT_ENV"] = env_name
        cmd = [
            str(self.pkg_root / "run-sim.sh"),
            str(model_dir),
            "--recipe", recipe_path.stem,           # name only
            "--particles", str(particles),
            "--output", output_name,
            "--no-viewer",
        ]
        # If the recipe is a custom temp recipe (not in tools/recipes), we need
        # to pass it via an alternate path. sim_one.sh supports --config <path>
        # which overrides --recipe. We use --config when the file isn't in
        # tools/recipes/. Detect by checking the parent dir.
        recipes_dir = self.pkg_root / "tools/recipes"
        if recipe_path.parent.resolve() != recipes_dir.resolve():
            # Direct sim_one.sh invocation — bypass run-sim.sh's recipe lookup.
            cmd = [
                str(sim_one),
                str(model_dir),
                "--config", str(recipe_path),
                "--particles", str(particles),
                "--output", output_name,
                "--live",
                "--no-vkgs-launch",
            ]

        self.append_log(f"+ {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, text=True, env=env,
                cwd=str(self.pkg_root),
            )
        except Exception as e:
            return f"failed to spawn: {e}"

        with self.lock:
            self.state = SimState(
                state="RUNNING", proc=proc, output_name=output_name,
                fused_dir=fused_dir, t_started=time.time(),
            )
        # Background thread to drain stdout
        self._reader = threading.Thread(target=self._read_loop, args=(proc,), daemon=True)
        self._reader.start()
        return None

    def cancel(self) -> None:
        with self.lock:
            proc = self.state.proc
            if proc is None or self.state.state != "RUNNING":
                return
        try:
            proc.terminate()
            self.append_log("[workbench] cancel requested (SIGTERM)")
        except Exception as e:
            self.append_log(f"[workbench] cancel failed: {e}")

    def finish(self) -> None:
        """Send SIGTERM to the launcher AND mark the eventual exit as DONE
        (not ERROR / CANCELLED). Used when sim is complete + all expected
        frames are already fused, but sim_one.sh is still in its 10-min
        fuse-drain wait. Skips the unnecessary wait without surfacing an
        error to the user."""
        with self.lock:
            proc = self.state.proc
            if proc is None or self.state.state != "RUNNING":
                return
            self.state.intentional_finish = True
        try:
            proc.terminate()
            self.append_log("[workbench] auto-finish: all frames fused, ending fuse drain")
        except Exception as e:
            self.append_log(f"[workbench] auto-finish failed: {e}")

    def _read_loop(self, proc: subprocess.Popen) -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                # tqdm sends \r-overwritten progress bars; split on either separator.
                for sub in line.replace("\r", "\n").splitlines():
                    if sub.strip():
                        self.append_log(sub)
        except Exception as e:
            self.append_log(f"[workbench] reader error: {e}")
        finally:
            rc = proc.wait()
            with self.lock:
                self.state.t_finished = time.time()
                if self.state.state == "RUNNING":
                    if self.state.intentional_finish:
                        # We asked for termination because everything is done.
                        self.state.state = "DONE"
                    else:
                        self.state.state = "DONE" if rc == 0 else "ERROR"
            self.append_log(f"[workbench] sim exited code={rc}")


# ----------------------------------------------------------------------- viewer
# 3DGS uses 0th-order SH for the diffuse color. Standard reconstruction
# constant (Y_0^0 = 1/(2*sqrt(pi)) ≈ 0.2821).
_SH_C0 = 0.28209479177387814


# Y-up -> Z-up rotation matrix (undoes fuse's --zup_to_yup).
# Takes (x, y, z)_yup to (x, -z, y)_zup.
_M_YUP_TO_ZUP = np.array([[1.0, 0.0, 0.0],
                          [0.0, 0.0, -1.0],
                          [0.0, 1.0,  0.0]], dtype=np.float32)


class FrameStream:
    """Polls a fused dir for new frame_*.ply.

    For each new frame: extract xyz (rotated y-up→z-up).
    First frame additionally yields per-point covariances + RGBs + opacities
    so the workbench can render with viser's 3DGS support — full Gaussian
    splats, not chunky point cloud squares.

    Coordinate fix-up: fuse_to_full_ply.py emits Y-up plys (vkgs convention),
    viser's grid-on-XY scene is Z-up, so we rotate centers AND covariances.
    Color: SH band-0 reconstruction from f_dc_0/1/2.
    Opacity: sigmoid(opacity_field).
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.frames: list[np.ndarray] = []           # list of (N, 3) z-up xyz
        self.colors: Optional[np.ndarray] = None     # (N, 3) float, [0,1]
        self.covariances: Optional[np.ndarray] = None  # (N, 3, 3) float
        self.opacities: Optional[np.ndarray] = None  # (N, 1) float
        self.known: set[str] = set()
        self.dir: Optional[Path] = None

    def reset(self, fused_dir: Optional[Path]) -> None:
        with self.lock:
            self.dir = fused_dir
            self.frames.clear()
            self.colors = None
            self.covariances = None
            self.opacities = None
            self.known.clear()

    def num_frames(self) -> int:
        with self.lock:
            return len(self.frames)

    def get(self, i: int) -> Optional[np.ndarray]:
        with self.lock:
            if 0 <= i < len(self.frames):
                return self.frames[i]
        return None

    def get_static_attrs(self):
        """Returns (covariances, rgbs, opacities) for the loaded run, or None
        if not available (e.g. xyz-only plys without SH/scale/rot)."""
        with self.lock:
            if self.covariances is None or self.colors is None or self.opacities is None:
                return None
            return self.covariances, self.colors, self.opacities

    @staticmethod
    def _yup_to_zup(xyz: np.ndarray) -> np.ndarray:
        return np.stack([xyz[:, 0], -xyz[:, 2], xyz[:, 1]], axis=1)

    @staticmethod
    def _extract_rgb(v) -> Optional[np.ndarray]:
        names = v.dtype.names
        if not all(k in names for k in ("f_dc_0", "f_dc_1", "f_dc_2")):
            return None
        r = (v["f_dc_0"] * _SH_C0 + 0.5).clip(0.0, 1.0)
        g = (v["f_dc_1"] * _SH_C0 + 0.5).clip(0.0, 1.0)
        b = (v["f_dc_2"] * _SH_C0 + 0.5).clip(0.0, 1.0)
        return np.stack([r, g, b], axis=1).astype(np.float32)

    @staticmethod
    def _build_covariances(v) -> Optional[np.ndarray]:
        """Reconstruct 3x3 covariance per particle from 3DGS scale/rot fields,
        rotated from Y-up (fuse output) to Z-up (viser scene)."""
        names = v.dtype.names
        needed = ("scale_0", "scale_1", "scale_2",
                  "rot_0", "rot_1", "rot_2", "rot_3")
        if not all(k in names for k in needed):
            return None
        n = v.shape[0]
        scales = np.stack([np.exp(v["scale_0"]).astype(np.float32),
                           np.exp(v["scale_1"]).astype(np.float32),
                           np.exp(v["scale_2"]).astype(np.float32)], axis=1)
        quats = np.stack([v["rot_0"].astype(np.float32),
                          v["rot_1"].astype(np.float32),
                          v["rot_2"].astype(np.float32),
                          v["rot_3"].astype(np.float32)], axis=1)
        norms = np.linalg.norm(quats, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        quats /= norms
        qw, qx, qy, qz = quats[:, 0], quats[:, 1], quats[:, 2], quats[:, 3]
        R = np.empty((n, 3, 3), dtype=np.float32)
        R[:, 0, 0] = 1.0 - 2.0 * (qy * qy + qz * qz)
        R[:, 0, 1] = 2.0 * (qx * qy - qz * qw)
        R[:, 0, 2] = 2.0 * (qx * qz + qy * qw)
        R[:, 1, 0] = 2.0 * (qx * qy + qz * qw)
        R[:, 1, 1] = 1.0 - 2.0 * (qx * qx + qz * qz)
        R[:, 1, 2] = 2.0 * (qy * qz - qx * qw)
        R[:, 2, 0] = 2.0 * (qx * qz - qy * qw)
        R[:, 2, 1] = 2.0 * (qy * qz + qx * qw)
        R[:, 2, 2] = 1.0 - 2.0 * (qx * qx + qy * qy)
        # Apply Y-up -> Z-up to the rotation: R_zup = M @ R
        R = np.einsum("ij,njk->nik", _M_YUP_TO_ZUP, R)
        # cov = R * diag(scales)^2 * R^T = (R * S) @ (R * S)^T
        RS = R * scales[:, np.newaxis, :]  # (N, 3, 3) — broadcasts col-scaling
        cov = np.matmul(RS, RS.transpose(0, 2, 1))
        return cov.astype(np.float32)

    @staticmethod
    def _extract_opacity(v) -> Optional[np.ndarray]:
        if "opacity" not in v.dtype.names:
            return None
        a = v["opacity"].astype(np.float32)
        # 3DGS stores opacity as the inverse-sigmoid; apply sigmoid to recover [0,1].
        return (1.0 / (1.0 + np.exp(-a))).reshape(-1, 1).astype(np.float32)

    def poll(self) -> int:
        """Returns count of new frames added this call."""
        with self.lock:
            d = self.dir
        if d is None or not d.is_dir():
            return 0
        added = 0
        try:
            for entry in sorted(d.glob("frame_*.ply")):
                stem = entry.stem
                if stem in self.known:
                    continue
                # Skip files still being written (size precheck).
                try:
                    if entry.stat().st_size < 1024:
                        continue
                except FileNotFoundError:
                    continue
                try:
                    v = PlyData.read(str(entry))["vertex"].data
                    xyz = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)
                    xyz = self._yup_to_zup(xyz)
                    # First-frame: extract static gaussian-splat attrs.
                    new_colors  = self._extract_rgb(v)       if self.colors      is None else None
                    new_covs    = self._build_covariances(v) if self.covariances is None else None
                    new_opacity = self._extract_opacity(v)   if self.opacities   is None else None
                except Exception:
                    continue  # partial / unreadable; retry next poll
                with self.lock:
                    if new_colors  is not None and self.colors      is None: self.colors      = new_colors
                    if new_covs    is not None and self.covariances is None: self.covariances = new_covs
                    if new_opacity is not None and self.opacities   is None: self.opacities   = new_opacity
                    self.frames.append(xyz)
                    self.known.add(stem)
                added += 1
        except FileNotFoundError:
            pass
        return added


# ----------------------------------------------------------------------- recipe
def load_recipe(path: Path) -> dict:
    return json.loads(path.read_text())


def write_effective_recipe(base: dict, overrides: dict, dest: Path) -> None:
    """Merge user overrides into a copy of `base` and write to `dest`.

    Preserves int-ness from the base recipe — viser sliders return floats
    even for integer-stepped sliders, but downstream consumers (Taichi
    `dense()`, gs_simulation_building.py grid sizing) require strict ints
    for keys like n_grid / frame_num.
    """
    merged = dict(base)
    for k, v in overrides.items():
        if v is None:
            continue
        if k in base and isinstance(base[k], int) and not isinstance(base[k], bool):
            merged[k] = int(round(v))
        else:
            merged[k] = v
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(merged, indent=2))


# ----------------------------------------------------------------------- main
def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pkg-root", default=str(Path(__file__).resolve().parent.parent),
                   help="gsfluent_pkg root dir (default: parent of this script)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--env", default="gsfluent", help="conda env name for sim_one.sh")
    args = p.parse_args()

    pkg_root = Path(args.pkg_root).resolve()
    recipes_dir = pkg_root / "tools/recipes"
    work_uploads = pkg_root / "work/uploads"
    work_uploads.mkdir(parents=True, exist_ok=True)
    work_recipes = pkg_root / "work/_tmp_recipes"
    work_recipes.mkdir(parents=True, exist_ok=True)

    if not recipes_dir.is_dir():
        raise SystemExit(f"recipes dir not found at {recipes_dir}")

    available_recipes = sorted(p.stem for p in recipes_dir.glob("*.json"))
    if not available_recipes:
        raise SystemExit(f"no recipes found in {recipes_dir}")

    runner = SimRunner(pkg_root)
    stream = FrameStream()

    server = viser.ViserServer(port=args.port)
    server.scene.world_axes.visible = False
    server.scene.add_grid(
        "ground", width=4.0, height=4.0, plane="xy",
        cell_size=0.1, cell_color=(180, 180, 200),
        section_size=1.0, section_color=(80, 80, 110),
    )

    # Real 3DGS rendering — full per-point covariances + SH band-0 colors +
    # opacities. Initialized with a single invisible dummy splat; once the
    # first frame is loaded we swap in real attrs and update centers per frame.
    splat = server.scene.add_gaussian_splats(
        "particles",
        centers=np.zeros((1, 3), dtype=np.float32),
        covariances=np.tile(np.eye(3, dtype=np.float32) * 1e-6, (1, 1, 1)),
        rgbs=np.zeros((1, 3), dtype=np.float32),
        opacities=np.zeros((1, 1), dtype=np.float32),
    )
    # We also keep a small fallback point cloud for the case where a loaded
    # ply lacks SH/scale/rot fields (e.g. xyz-only sim_*.ply). Hidden until used.
    fallback_cloud = server.scene.add_point_cloud(
        "particles_fallback", points=np.zeros((1, 3), dtype=np.float32),
        colors=np.zeros((1, 3), dtype=np.uint8), point_size=0.01,
        visible=False,
    )

    # ---------- GUI ----------
    server.gui.set_panel_label("gsfluent workbench")

    with server.gui.add_folder("Model"):
        upload = server.gui.add_upload_button("Upload .ply", icon=viser.Icon.UPLOAD)
        path_input = server.gui.add_text("...or model path", initial_value="")
        model_status = server.gui.add_text("Model status", initial_value="(no model)")
        model_status.disabled = True

    with server.gui.add_folder("Recipe"):
        recipe_dd = server.gui.add_dropdown("Preset", options=available_recipes,
                                            initial_value=available_recipes[0])
        particles = server.gui.add_slider("Particles", min=20_000, max=2_000_000,
                                          step=10_000, initial_value=200_000)
        # Param widgets are rebuilt whenever the user picks a different recipe.
        param_handles: dict[str, object] = {}
        param_folder = server.gui.add_folder("Recipe parameters")

    with server.gui.add_folder("Run"):
        output_input = server.gui.add_text("Output name", initial_value="(auto)")
        run_btn      = server.gui.add_button("Run sim", color="green")
        cancel_btn   = server.gui.add_button("Cancel")
        status_text  = server.gui.add_text("Status", initial_value="IDLE")
        status_text.disabled = True
        # Progress bar — driven by num_fused_frames / total_expected_frames.
        # 0% until first frame arrives (covers kernel JIT warmup ~30–90s).
        progress_bar = server.gui.add_progress_bar(0.0, animated=False)
        stage_text   = server.gui.add_text("Stage", initial_value="—")
        stage_text.disabled = True
        eta_text     = server.gui.add_text("ETA", initial_value="—")
        eta_text.disabled = True

    with server.gui.add_folder("Past runs"):
        runs_refresh_btn = server.gui.add_button("Refresh list")
        runs_dd          = server.gui.add_dropdown("Run", options=["(none)"],
                                                    initial_value="(none)")
        load_run_btn     = server.gui.add_button("Load selected", color="cyan")
        runs_status      = server.gui.add_text("Info", initial_value="(no runs yet)")
        runs_status.disabled = True

    with server.gui.add_folder("Playback"):
        play_chk = server.gui.add_checkbox("Play", initial_value=True)
        frame_slider = server.gui.add_slider("Frame", min=0, max=0, step=1,
                                             initial_value=0)
        speed_slider = server.gui.add_slider("Speed", min=0.25, max=4.0,
                                             step=0.25, initial_value=1.0)
        target_fps   = server.gui.add_slider("Target fps", min=1.0, max=60.0,
                                             step=1.0, initial_value=24.0)

    # Scrollable HTML console (last 200 lines; user can scroll up to see history).
    # The <img onerror> trick auto-scrolls to bottom on every content update —
    # plain <script> tags don't execute when injected via innerHTML, but image
    # error handlers do. The img itself is hidden.
    LOG_HEAD = ('<div id="wb-log" style="max-height: 280px; overflow-y: auto; '
                'font-family: ui-monospace, Menlo, Consolas, monospace; '
                'font-size: 11px; line-height: 1.35; white-space: pre-wrap; '
                'background: #111; color: #ddd; padding: 8px; border-radius: 4px; '
                'user-select: text; word-break: break-word;">')
    LOG_TAIL = ('<img src style="display:none" '
                'onerror="this.parentElement.scrollTop=this.parentElement.scrollHeight">'
                '</div>')
    log_box = server.gui.add_html(LOG_HEAD + "(no output yet)" + LOG_TAIL)

    # ---------- state ----------
    ui_state = {
        "current_model_dir": None,         # Path or None
        "current_recipe_data": None,       # dict or None
        "current_recipe_path": None,       # Path
        "playing": True,
        "frame": 0,
        "_slider_suppress": False,
        # Progress hooks: set when Run is clicked.
        "expected_frames": 0,              # frame_num from the effective recipe
        "first_frame_t": 0.0,              # time when 1st frame appeared (for ETA)
        # Auto-finish bookkeeping: prevents double-call when sim has already
        # finished and all frames are fused but launcher is in fuse drain.
        "auto_finish_done_for": "",        # output_name we already finished
        # Lookup from "label (N frames)" → output_name for past runs.
        "_run_lookup": {},
    }

    def rebuild_param_widgets():
        """Tear down & rebuild the dynamic recipe-params widgets.

        Picks widget type based on the BASE recipe's value type:
          - int  → integer slider (e.g. n_grid, frame_num)
          - tiny-step float → number input (e.g. substep_dt = 1e-4)
          - regular float → float slider (e.g. camera angles)
        """
        for h in param_handles.values():
            try: h.remove()
            except Exception: pass
        param_handles.clear()
        recipe_data = ui_state["current_recipe_data"] or {}
        with param_folder:
            for key, label, lo, hi, step, group in EXPOSED_PARAMS:
                if key not in recipe_data:
                    continue
                init = recipe_data[key]
                if isinstance(init, (list, tuple)) or not isinstance(init, (int, float)):
                    continue
                is_int = isinstance(init, int) and not isinstance(init, bool)
                if is_int:
                    handle = server.gui.add_slider(
                        label,
                        min=int(lo), max=int(hi),
                        step=int(max(1, step)),
                        initial_value=int(init),
                    )
                elif step < 0.01:
                    handle = server.gui.add_number(
                        label, initial_value=float(init),
                        min=float(lo), max=float(hi), step=float(step),
                    )
                else:
                    handle = server.gui.add_slider(
                        label, min=float(lo), max=float(hi),
                        step=float(step), initial_value=float(init),
                    )
                param_handles[key] = handle

    def load_recipe_data(name: str) -> None:
        path = recipes_dir / f"{name}.json"
        if not path.exists():
            runner.append_log(f"[workbench] recipe missing: {path}")
            return
        ui_state["current_recipe_data"] = load_recipe(path)
        ui_state["current_recipe_path"] = path
        rebuild_param_widgets()
        runner.append_log(f"[workbench] loaded recipe '{name}'")

    load_recipe_data(available_recipes[0])

    def update_model_status() -> None:
        m = ui_state["current_model_dir"]
        if m is None:
            model_status.value = "(no model)"
        else:
            ply = next(m.glob("point_cloud/iteration_*/point_cloud.ply"), None)
            if ply is None:
                model_status.value = f"INVALID: no point_cloud.ply under {m}"
            else:
                size_mb = ply.stat().st_size / 1024**2
                model_status.value = f"OK: {m.name} ({size_mb:.1f} MB ply)"

    # ---------- callbacks ----------
    @upload.on_upload
    def _(_):
        f = upload.value
        if f is None: return
        # f.name is the original filename, f.content is bytes
        if not f.name.lower().endswith(".ply"):
            runner.append_log("[workbench] uploaded file must be .ply")
            return
        token = uuid.uuid4().hex[:8]
        target_dir = work_uploads / f"{Path(f.name).stem}_{token}/point_cloud/iteration_30000"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "point_cloud.ply"
        target.write_bytes(f.content)
        ui_state["current_model_dir"] = target_dir.parent.parent
        runner.append_log(f"[workbench] uploaded {f.name} -> {ui_state['current_model_dir']}")
        path_input.value = str(ui_state["current_model_dir"])
        update_model_status()

    @path_input.on_update
    def _(_):
        v = path_input.value.strip()
        if not v:
            ui_state["current_model_dir"] = None
        else:
            p = Path(v)
            if not p.is_absolute(): p = (pkg_root / p).resolve()
            ui_state["current_model_dir"] = p if p.exists() else None
        update_model_status()

    @recipe_dd.on_update
    def _(_):
        load_recipe_data(recipe_dd.value)

    @run_btn.on_click
    def _(_):
        m = ui_state["current_model_dir"]
        if m is None:
            runner.append_log("[workbench] no model — upload or paste a path first")
            return
        if runner.is_running():
            runner.append_log("[workbench] already running; cancel first")
            return

        # Build the effective recipe with user overrides
        base = ui_state["current_recipe_data"]
        overrides = {k: h.value for k, h in param_handles.items()}
        token = time.strftime("%Y%m%d-%H%M%S")
        out_name = output_input.value if output_input.value not in ("", "(auto)") \
                                      else f"{m.name}_{recipe_dd.value}_{token}"
        eff_recipe = work_recipes / f"{out_name}.json"
        write_effective_recipe(base, overrides, eff_recipe)

        # Reset frame stream + slider + progress hooks
        stream.reset(pkg_root / "work/fused" / out_name)
        ui_state["frame"] = 0
        frame_slider.max = 0
        frame_slider.value = 0
        ui_state["expected_frames"] = int(base.get("frame_num", 150))
        if "frame_num" in param_handles:
            ui_state["expected_frames"] = int(param_handles["frame_num"].value)
        ui_state["first_frame_t"] = 0.0
        progress_bar.value = 0.0
        stage_text.value = "starting (kernel JIT — first run can take 30–90s)"
        eta_text.value = "—"

        err = runner.start(m, eff_recipe, int(particles.value), out_name,
                           env_name=args.env)
        if err:
            runner.append_log(f"[workbench] start failed: {err}")

    @cancel_btn.on_click
    def _(_):
        runner.cancel()

    # ---------- Past runs ----------
    def scan_past_runs():
        fused_root = pkg_root / "work/fused"
        if not fused_root.is_dir():
            return []
        out = []
        for d in fused_root.iterdir():
            if not d.is_dir():
                continue
            n = sum(1 for _ in d.glob("frame_*.ply"))
            if n == 0:
                continue
            mtime = d.stat().st_mtime
            out.append((d.name, n, mtime))
        out.sort(key=lambda e: -e[2])  # newest first
        return out

    def refresh_runs_list():
        entries = scan_past_runs()
        if not entries:
            runs_dd.options = ["(none)"]
            runs_dd.value = "(none)"
            runs_status.value = "(no runs yet — click Run sim to start one)"
            ui_state["_run_lookup"] = {}
            return
        labels = [f"{name}  ({n} frames)" for name, n, _ in entries]
        runs_dd.options = labels
        runs_dd.value = labels[0]
        ui_state["_run_lookup"] = {labels[i]: entries[i][0] for i in range(len(entries))}
        runs_status.value = f"{len(entries)} run(s) found — newest first"

    @runs_refresh_btn.on_click
    def _(_):
        refresh_runs_list()

    @load_run_btn.on_click
    def _(_):
        label = runs_dd.value
        if label == "(none)":
            runs_status.value = "(no run selected)"
            return
        name = ui_state["_run_lookup"].get(label, label)
        d = pkg_root / "work/fused" / name
        if not d.is_dir():
            runs_status.value = f"not found: {d}"
            return
        # Switch the FrameStream to this dir; one poll loads everything that's there.
        stream.reset(d)
        stream.poll()
        n = stream.num_frames()
        ui_state["frame"] = 0
        ui_state["expected_frames"] = n
        ui_state["first_frame_t"] = time.time()
        frame_slider.max = max(0, n - 1)
        frame_slider.value = 0
        progress_bar.value = 100.0 if n > 0 else 0.0
        eta_text.value = "loaded"
        stage_text.value = f"loaded past run: {name}"
        runs_status.value = f"loaded {name}: {n} frames"
        runner.append_log(f"[workbench] loaded past run: {name} ({n} frames)")
        first = stream.get(0)
        static = stream.get_static_attrs()
        if static is not None and first is not None:
            covs, rgbs, ops = static
            splat.covariances = covs
            splat.rgbs        = rgbs
            splat.opacities   = ops
            splat.centers     = first
            splat.visible     = True
            fallback_cloud.visible = False
        elif first is not None and first.shape[0] > 0:
            z = first[:, 2]
            norm = (z - z.min()) / (z.max() - z.min() + 1e-8)
            synth = np.stack([norm * 0.8 + 0.2,
                              0.4 * np.ones_like(norm),
                              1.0 - norm * 0.8], axis=1)
            fallback_cloud.points  = first
            fallback_cloud.colors  = (synth * 255).astype(np.uint8)
            fallback_cloud.visible = True
            splat.visible = False

    refresh_runs_list()  # initial scan

    @play_chk.on_update
    def _(_):
        ui_state["playing"] = play_chk.value

    @frame_slider.on_update
    def _(_):
        if ui_state["_slider_suppress"]:
            return
        ui_state["frame"] = int(frame_slider.value)
        ui_state["playing"] = False
        play_chk.value = False

    # Surface any unhandled exception (in main loop or threads).
    import sys as _sys, traceback as _tb, threading as _th
    def _excepthook(et, ev, tb):
        print("=== UNHANDLED EXCEPTION ===", flush=True)
        _tb.print_exception(et, ev, tb)
    _sys.excepthook = _excepthook
    def _thread_excepthook(args):
        print(f"=== THREAD EXCEPTION in {args.thread.name} ===", flush=True)
        _tb.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
    _th.excepthook = _thread_excepthook

    print(f"\n>>> http://localhost:{args.port} <<<\n", flush=True)
    print(f"pkg_root = {pkg_root}", flush=True)
    print(f"recipes  = {available_recipes}\n", flush=True)
    print("[workbench] entering main loop", flush=True)

    # ---------- main loop ----------
    last_advance = time.perf_counter()
    last_poll    = 0.0
    last_log_dump = 0.0
    while True:
        now = time.perf_counter()
        # Status + progress + log roll-up every 250ms
        if now - last_log_dump >= 0.25:
            with runner.lock:
                st = runner.state.state
                t0 = runner.state.t_started
                t1 = runner.state.t_finished
            n_frames = stream.num_frames()
            expected = ui_state["expected_frames"]

            # ---- Auto-finish: skip the 10-min fuse-drain wait when everything's
            #      already done. Fires exactly once per run.
            recent_short = "\n".join(runner.last_lines(40))
            if (st == "RUNNING" and expected > 0 and n_frames >= expected
                and "[PhaseA-SUMMARY]" in recent_short
                and ui_state["auto_finish_done_for"] != runner.state.output_name):
                ui_state["auto_finish_done_for"] = runner.state.output_name
                runner.finish()  # SIGTERM + flag → exits as DONE not ERROR

            # ---- Stage detection (look at recent log lines for the latest marker) ----
            recent = "\n".join(runner.last_lines(80))
            if st == "RUNNING":
                if "[PhaseA-SUMMARY]" in recent:
                    stage_text.value = "fuse drain (sim done; waiting on fuse quiet timeout)"
                elif "[watch] +frame" in recent and "step 2/3" in recent:
                    stage_text.value = "fuse (matching sim frames to reference)"
                elif "[PhaseA]" in recent or "step 1/3" in recent:
                    stage_text.value = "sim (MPM substeps)"
                else:
                    stage_text.value = "starting (kernel JIT — first run can take 30–90s)"
            elif st == "DONE":
                stage_text.value = "complete"
            elif st == "ERROR":
                stage_text.value = "error"
            elif st == "CANCELLED":
                stage_text.value = "cancelled"
            else:
                stage_text.value = "—"

            # ---- Progress bar + ETA from observed fps since first frame ----
            if expected > 0 and n_frames > 0:
                if ui_state["first_frame_t"] == 0.0:
                    ui_state["first_frame_t"] = time.time()
                progress_bar.value = min(100.0, 100.0 * n_frames / expected)
                elapsed = max(0.001, time.time() - ui_state["first_frame_t"])
                fps_obs = n_frames / elapsed
                if n_frames >= expected:
                    eta_text.value = f"0:00 (done; {fps_obs:.2f} fps avg)"
                elif fps_obs > 0:
                    remaining = (expected - n_frames) / fps_obs
                    eta_text.value = (f"{int(remaining // 60)}:{int(remaining % 60):02d} "
                                      f"(~{fps_obs:.2f} fps)")
                else:
                    eta_text.value = "computing..."
            elif st == "RUNNING":
                progress_bar.value = 0.0
                eta_text.value = "—"
            elif st == "DONE":
                progress_bar.value = 100.0

            # ---- Top-level status line ----
            if st == "RUNNING":
                status_text.value = (
                    f"RUNNING ({int(time.time() - t0) if t0 else 0}s) — "
                    f"{n_frames}/{expected} frames"
                )
            elif st == "DONE":
                status_text.value = f"DONE in {int(t1 - t0)}s ({n_frames} frames)"
            elif st == "ERROR":
                status_text.value = "ERROR (see log)"
            else:
                status_text.value = st

            # On state transition into DONE/ERROR, refresh the past-runs dropdown
            # so the just-finished run shows up at the top without user action.
            if st in ("DONE", "ERROR") and ui_state.get("_last_seen_st") not in ("DONE", "ERROR"):
                refresh_runs_list()
            ui_state["_last_seen_st"] = st

            lines = runner.last_lines(200)
            text = "\n".join(lines) if lines else "(no output yet)"
            log_box.content = LOG_HEAD + html.escape(text) + LOG_TAIL
            last_log_dump = now

        # Frame stream poll every 250ms
        if now - last_poll >= 0.25:
            added = stream.poll()
            if added > 0:
                n = stream.num_frames()
                if n - 1 > frame_slider.max:
                    frame_slider.max = n - 1
                # On first batch of frames: install gaussian splat attrs if the
                # plys carried them. Otherwise show a fallback point cloud.
                if added == n:  # i.e. this poll just added the very first frames
                    static = stream.get_static_attrs()
                    first = stream.get(0)
                    if static is not None and first is not None:
                        covs, rgbs, ops = static
                        splat.covariances = covs
                        splat.rgbs        = rgbs
                        splat.opacities   = ops
                        splat.centers     = first
                        splat.visible     = True
                        fallback_cloud.visible = False
                    elif first is not None:
                        # xyz-only fallback: synthesize colors from height
                        z = first[:, 2]
                        norm = (z - z.min()) / (z.max() - z.min() + 1e-8)
                        synth = np.stack([norm * 0.8 + 0.2,
                                          0.4 * np.ones_like(norm),
                                          1.0 - norm * 0.8], axis=1)
                        fallback_cloud.points  = first
                        fallback_cloud.colors  = (synth * 255).astype(np.uint8)
                        fallback_cloud.visible = True
                        splat.visible = False
            last_poll = now

        # Animation advance
        period = 1.0 / max(target_fps.value, 1e-3) / max(speed_slider.value, 1e-3)
        if ui_state["playing"] and stream.num_frames() > 0 and (now - last_advance) >= period:
            ui_state["frame"] = (ui_state["frame"] + 1) % stream.num_frames()
            ui_state["_slider_suppress"] = True
            frame_slider.value = ui_state["frame"]
            ui_state["_slider_suppress"] = False
            last_advance = now

        f = stream.get(ui_state["frame"])
        if f is not None:
            if splat.visible:
                splat.centers = f
            else:
                fallback_cloud.points = f
        time.sleep(0.02)


if __name__ == "__main__":
    main()

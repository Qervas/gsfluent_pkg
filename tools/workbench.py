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
                    self.state.state = "DONE" if rc == 0 else "ERROR"
            self.append_log(f"[workbench] sim exited code={rc}")


# ----------------------------------------------------------------------- viewer
class FrameStream:
    """Polls a fused dir for new frame_*.ply, holds them in memory as xyz arrays."""

    def __init__(self):
        self.lock = threading.Lock()
        self.frames: list[np.ndarray] = []
        self.known: set[str] = set()
        self.dir: Optional[Path] = None

    def reset(self, fused_dir: Optional[Path]) -> None:
        with self.lock:
            self.dir = fused_dir
            self.frames.clear()
            self.known.clear()

    def num_frames(self) -> int:
        with self.lock:
            return len(self.frames)

    def get(self, i: int) -> Optional[np.ndarray]:
        with self.lock:
            if 0 <= i < len(self.frames):
                return self.frames[i]
        return None

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
                except Exception:
                    continue  # partial / unreadable; retry next poll
                with self.lock:
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
    merged = dict(base)
    merged.update({k: v for k, v in overrides.items() if v is not None})
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

    cloud = server.scene.add_point_cloud(
        "particles", points=np.zeros((1, 3), dtype=np.float32),
        colors=np.zeros((1, 3), dtype=np.uint8), point_size=0.005,
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

    with server.gui.add_folder("Playback"):
        play_chk = server.gui.add_checkbox("Play", initial_value=True)
        frame_slider = server.gui.add_slider("Frame", min=0, max=0, step=1,
                                             initial_value=0)
        speed_slider = server.gui.add_slider("Speed", min=0.25, max=4.0,
                                             step=0.25, initial_value=1.0)
        target_fps   = server.gui.add_slider("Target fps", min=1.0, max=60.0,
                                             step=1.0, initial_value=24.0)

    log_box = server.gui.add_markdown(content="*(no output yet)*")

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
    }

    def rebuild_param_widgets():
        """Tear down & rebuild the dynamic recipe-params widgets."""
        for h in param_handles.values():
            h.remove()
        param_handles.clear()
        recipe_data = ui_state["current_recipe_data"] or {}
        with param_folder:
            for key, label, lo, hi, step, group in EXPOSED_PARAMS:
                if key not in recipe_data:
                    continue
                init = recipe_data[key]
                if isinstance(init, (list, tuple)) or not isinstance(init, (int, float)):
                    continue
                # Number input is more flexible than slider for floats with tiny step
                if step < 0.01:
                    handle = server.gui.add_number(f"{label}", initial_value=float(init),
                                                   min=float(lo), max=float(hi), step=step)
                else:
                    handle = server.gui.add_slider(f"{label}", min=float(lo), max=float(hi),
                                                   step=step, initial_value=float(init))
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

    print(f"\n>>> http://localhost:{args.port} <<<\n")
    print(f"pkg_root = {pkg_root}")
    print(f"recipes  = {available_recipes}\n")

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

            lines = runner.last_lines(20)
            log_box.content = "```\n" + ("\n".join(lines) if lines else "(no output yet)") + "\n```"
            last_log_dump = now

        # Frame stream poll every 250ms
        if now - last_poll >= 0.25:
            added = stream.poll()
            if added > 0:
                n = stream.num_frames()
                # Update the frame slider's max range
                if n - 1 > frame_slider.max:
                    frame_slider.max = n - 1
                # Initialize colors when we first get a frame
                first = stream.get(0)
                if first is not None and added == n:
                    z = first[:, 1]
                    if z.max() > z.min():
                        norm = (z - z.min()) / (z.max() - z.min())
                    else:
                        norm = np.zeros_like(z)
                    colors = np.stack([norm * 0.8 + 0.2,
                                       0.4 * np.ones_like(norm),
                                       1.0 - norm * 0.8], axis=1)
                    cloud.colors = (colors * 255).astype(np.uint8)
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
            cloud.points = f
        time.sleep(0.02)


if __name__ == "__main__":
    main()

"""Realtime gaussian-splat player for pre-baked R7 sims.

mmap-loads all cells at startup: only the OS page cache backs frame data,
so RAM stays bounded to whatever the viewer is actively touching.
Switching the dropdown is instant — the file is already mapped.

Usage:
    python viewer_textured.py --npz_dir <dir> [--cells C1 C2 ...] [--port 8080]
"""
import argparse
import time
from pathlib import Path
import numpy as np
import viser


def mmap_cell(npz_path: Path) -> dict:
    """Load all 4 arrays (frames, cov, rgb, opacity) as mmap'd read-only views.

    The OS pages each frame in on demand when we access frames[i], so peak
    RAM stays bounded regardless of total cell count.
    """
    d = np.load(npz_path, mmap_mode="r")
    return {
        "frames": d["frames"],
        "cov": d["cov"],
        "rgb": d["rgb"],
        "opacity": d["opacity"],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--npz_dir", required=True)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--cells", nargs="+")
    p.add_argument("--target_fps", type=float, default=24.0)
    args = p.parse_args()

    npz_root = Path(args.npz_dir)
    if args.cells:
        npz_paths = [npz_root / f"{c}.npz" for c in args.cells]
    else:
        npz_paths = sorted(p for p in npz_root.glob("*_textured.npz"))
        if not npz_paths:
            npz_paths = sorted(npz_root.glob("*.npz"))
    npz_paths = [p for p in npz_paths if p.exists()]
    if not npz_paths:
        print("ERROR: no cells found"); return

    print(f"mmap-loading {len(npz_paths)} cells...")
    cells = {}
    for path in npz_paths:
        cells[path.stem] = mmap_cell(path)
        print(f"  {path.stem}: frames {cells[path.stem]['frames'].shape}")

    server = viser.ViserServer(port=args.port)
    server.scene.world_axes.visible = False
    server.scene.add_grid(
        "ground", width=4.0, height=4.0, plane="xy",
        cell_size=0.1, cell_color=(180, 180, 200),
        section_size=1.0, section_color=(80, 80, 110),
        position=(1.0, 1.0, 0.5),
    )
    server.scene.add_frame(
        "gizmo", show_axes=True, axes_length=0.5, axes_radius=0.015,
        position=(-0.3, -0.3, 0.5),
    )

    cell_names = list(cells.keys())
    cur_name = cell_names[0]
    cur = cells[cur_name]
    # viser needs contiguous arrays for the splat constructor; copy these
    # one-time static fields so we don't repeatedly hit the mmap.
    splat = server.scene.add_gaussian_splats(
        "building",
        centers=np.ascontiguousarray(cur["frames"][0]),
        covariances=np.ascontiguousarray(cur["cov"]),
        rgbs=np.ascontiguousarray(cur["rgb"]),
        opacities=np.ascontiguousarray(cur["opacity"]),
    )

    cell_dropdown = server.gui.add_dropdown("Cell", cell_names, initial_value=cur_name)
    n_initial = cur["frames"].shape[0]
    play_button = server.gui.add_button("Play / Pause")
    frame_slider = server.gui.add_slider("Frame", 0, n_initial - 1, 1, 0)
    speed_slider = server.gui.add_slider("Speed (x)", 0.25, 4.0, 0.25, 1.0)
    fps_text = server.gui.add_text("FPS", initial_value="—")
    status_text = server.gui.add_text("Status", initial_value=f"{n_initial} frames")

    state = {
        "cell": cur_name, "data": cur, "playing": True,
        "frame": 0, "speed": 1.0,
        "_suppress_slider_cb": False,
    }

    @cell_dropdown.on_update
    def _(_):
        new_cell = cell_dropdown.value
        if new_cell == state["cell"]:
            return
        new_data = cells[new_cell]
        state["cell"] = new_cell
        state["data"] = new_data
        state["frame"] = 0
        n = new_data["frames"].shape[0]
        state["_suppress_slider_cb"] = True
        frame_slider.max = n - 1
        frame_slider.value = 0
        state["_suppress_slider_cb"] = False
        status_text.value = f"{n} frames"
        splat.covariances = np.ascontiguousarray(new_data["cov"])
        splat.rgbs = np.ascontiguousarray(new_data["rgb"])
        splat.opacities = np.ascontiguousarray(new_data["opacity"])

    @frame_slider.on_update
    def _(_):
        if state["_suppress_slider_cb"]:
            return
        state["frame"] = frame_slider.value
        state["playing"] = False

    @play_button.on_click
    def _(_):
        state["playing"] = not state["playing"]

    @speed_slider.on_update
    def _(_):
        state["speed"] = speed_slider.value

    print(f"\n>>> http://localhost:{args.port} <<<\n")

    period = 1.0 / args.target_fps
    last = time.perf_counter()
    last_fps_update = last
    fps_frames = 0
    last_frame_pushed = -1
    last_cell_pushed = None
    while True:
        now = time.perf_counter()
        eff_period = period / max(state["speed"], 1e-3)
        if now - last < eff_period:
            time.sleep(eff_period - (now - last))
            continue
        last = time.perf_counter()
        fps_frames += 1
        if last - last_fps_update >= 1.0:
            fps_text.value = f"{fps_frames / (last - last_fps_update):.1f}"
            fps_frames = 0
            last_fps_update = last

        if state["playing"]:
            n = state["data"]["frames"].shape[0]
            state["frame"] = (state["frame"] + 1) % n
            state["_suppress_slider_cb"] = True
            frame_slider.value = state["frame"]
            state["_suppress_slider_cb"] = False
        if state["frame"] != last_frame_pushed or state["cell"] != last_cell_pushed:
            # mmap slice may not be C-contiguous; copy before sending to viser
            splat.centers = np.ascontiguousarray(state["data"]["frames"][state["frame"]])
            last_frame_pushed = state["frame"]
            last_cell_pushed = state["cell"]


if __name__ == "__main__":
    main()

"""Minimal viser-based point cloud playback for laptop sim runs.

Loads all sim_*.ply (xyz only) from a directory and plays them back as
point clouds in the browser. No reference attrs, no gaussian rendering —
just colored dots that move. Sufficient to verify sim is doing the right
thing without the textured-npz pipeline.

Usage:
    python view_points.py --sim_dir output/laptop_view_100k/simulation_ply
"""
import argparse
import time
from pathlib import Path
import numpy as np
from plyfile import PlyData
import viser


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sim_dir", required=True)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--target_fps", type=float, default=24.0)
    p.add_argument("--point_size", type=float, default=0.005)
    args = p.parse_args()

    plys = sorted(Path(args.sim_dir).glob("sim_*.ply"))
    if not plys:
        print(f"ERROR: no sim_*.ply in {args.sim_dir}"); return
    print(f"loading {len(plys)} frames...")

    frames = []
    for pp in plys:
        v = PlyData.read(str(pp))["vertex"].data
        xyz = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)
        frames.append(xyz)
    print(f"loaded {len(frames)} frames; {frames[0].shape[0]} points each")

    # color by initial height (z is up in sim convention)
    z0 = frames[0][:, 2]
    z_min, z_max = z0.min(), z0.max()
    norm = ((z0 - z_min) / (z_max - z_min + 1e-8))
    colors = np.stack([norm * 0.8 + 0.2, 0.4 * np.ones_like(norm), 1.0 - norm * 0.8], axis=1)
    colors = (colors * 255).astype(np.uint8)

    server = viser.ViserServer(port=args.port)
    server.scene.world_axes.visible = False
    server.scene.add_grid(
        "ground", width=4.0, height=4.0, plane="xy",
        cell_size=0.1, cell_color=(180, 180, 200),
        section_size=1.0, section_color=(80, 80, 110),
        position=(1.0, 1.0, 0.5),
    )

    cloud = server.scene.add_point_cloud(
        "particles",
        points=frames[0],
        colors=colors,
        point_size=args.point_size,
    )

    n = len(frames)
    play = server.gui.add_button("Play / Pause")
    slider = server.gui.add_slider("Frame", 0, n - 1, 1, 0)
    speed = server.gui.add_slider("Speed", 0.25, 4.0, 0.25, 1.0)
    fps_text = server.gui.add_text("FPS", initial_value="—")
    server.gui.add_text("Status", initial_value=f"{n} frames, {frames[0].shape[0]} pts").disabled = True

    state = {"playing": True, "frame": 0, "speed": 1.0, "_suppress": False}

    @slider.on_update
    def _(_):
        if state["_suppress"]:
            return
        state["frame"] = slider.value
        state["playing"] = False

    @play.on_click
    def _(_):
        state["playing"] = not state["playing"]

    @speed.on_update
    def _(_):
        state["speed"] = speed.value

    print(f"\n>>> http://localhost:{args.port} <<<\n")

    period = 1.0 / args.target_fps
    last = time.perf_counter()
    last_fps_t = last
    fps_n = 0
    while True:
        now = time.perf_counter()
        eff = period / max(state["speed"], 1e-3)
        if now - last < eff:
            time.sleep(eff - (now - last))
            continue
        last = time.perf_counter()
        fps_n += 1
        if last - last_fps_t >= 1.0:
            fps_text.value = f"{fps_n / (last - last_fps_t):.1f}"
            fps_n = 0
            last_fps_t = last

        if state["playing"]:
            state["frame"] = (state["frame"] + 1) % n
            state["_suppress"] = True
            slider.value = state["frame"]
            state["_suppress"] = False
        cloud.points = frames[state["frame"]]


if __name__ == "__main__":
    main()

"""
GaussianFluent Building Destruction with Voronoi Pre-Fracture
=============================================================
Based on gs_simulation_building.py with per-particle yield_stress
loaded from the Voronoi preprocessor output.

Usage:
    CUDA_VISIBLE_DEVICES=2 PYTHONPATH=.:gaussian-splatting python \
        gs_simulation/watermelon/gs_simulation_voronoi.py \
        --model_path model/building \
        --output_path output/building_voronoi \
        --config config/building_voronoi_config.json \
        --render_img --compile_video
"""

import sys

sys.path.append("/data/yinshaoxuan/GaussianFluent/gaussian-splatting")
sys.path.append("/data/yinshaoxuan/GaussianFluent")

import argparse
import math
import cv2
import torch
import os
import numpy as np
import json
from tqdm import tqdm
import subprocess
import warp as wp

# Gaussian splatting dependencies
from utils.sh_utils import eval_sh
from scene.gaussian_model import GaussianModel
from diff_gaussian_rasterization import (
    GaussianRasterizationSettings,
    GaussianRasterizer,
)
from sklearn.neighbors import NearestNeighbors
from scene.cameras import Camera as GSCamera
from gaussian_renderer import render, GaussianModel
from utils.system_utils import searchForMaxIteration
from utils.graphics_utils import focal2fov

# MPM dependencies
from mpm_solver_warp.engine_utils import *
from mpm_solver_warp.mpm_solver_warp import MPM_Simulator_WARP

# Particle filling dependencies
from particle_filling.filling import *
import open3d as o3d

# Utils
from utils.decode_param import *
from utils.transformation_utils import *
from utils.camera_view_utils import *
from utils.render_utils import *


wp.init()
wp.config.verify_cuda = True

ti.init(arch=ti.cuda, device_memory_GB=2.0, random_seed=42)


class PipelineParamsNoparse:
    """Same as PipelineParams but without argument parser."""
    def __init__(self):
        self.convert_SHs_python = False
        self.compute_cov3D_python = False
        self.debug = False


def load_checkpoint(model_path, sh_degree=3, iteration=-1):
    checkpt_dir = os.path.join(model_path, "point_cloud")
    if iteration == -1:
        iteration = searchForMaxIteration(checkpt_dir)
    checkpt_path = os.path.join(
        checkpt_dir, f"iteration_{iteration}", "point_cloud.ply"
    )
    gaussians = GaussianModel(sh_degree)
    gaussians.load_ply(checkpt_path)
    return gaussians


"""
Server-side patch for gs_simulation_voronoi.py.
Adds compute_runtime_voronoi() that runs Voronoi on the FULL combined particle cloud
(surface Gaussians + internal fill particles). Fill particles at chunk boundaries
receive the low boundary yield_stress instead of being padded with interior yield.

Insertion point: right above `def load_per_particle_yield_stress`.
"""
import numpy as np
import torch
from scipy.spatial import KDTree


def compute_runtime_voronoi(positions, voronoi_cfg, seeds_file=None, device="cuda:0"):
    """
    Assign each particle in `positions` (N x 3) to a Voronoi chunk,
    then flag particles that have any neighbor in a different chunk within
    `boundary_radius` as boundary particles.

    Returns (yield_stress_tensor, chunk_ids, boundary_mask).
    """
    import os
    n_chunks       = voronoi_cfg.get("n_chunks", 40)
    boundary_radius = voronoi_cfg.get("boundary_radius", 0.05)
    boundary_yield = voronoi_cfg.get("boundary_yield", 300.0)
    interior_yield = voronoi_cfg.get("interior_yield", 5000.0)
    k_neighbors    = voronoi_cfg.get("knn", 24)

    pos_np = positions.detach().cpu().numpy() if hasattr(positions, "detach") else np.asarray(positions)
    n = len(pos_np)
    print(f"[runtime voronoi] {n} particles, {n_chunks} chunks, br={boundary_radius}")

    # Seeds: load if saved, else random in bbox
    seeds = None
    if seeds_file and os.path.exists(seeds_file):
        try:
            seeds_loaded = np.load(seeds_file).astype(np.float32)
            if len(seeds_loaded) == n_chunks:
                seeds = seeds_loaded
                print(f"[runtime voronoi] loaded {n_chunks} seeds from {seeds_file}")
        except Exception as e:
            print(f"[runtime voronoi] seeds load failed ({e}), regenerating")
    if seeds is None:
        rng = np.random.RandomState(42)
        mins, maxs = pos_np.min(axis=0), pos_np.max(axis=0)
        seeds = rng.uniform(mins, maxs, size=(n_chunks, 3)).astype(np.float32)
        print(f"[runtime voronoi] generated {n_chunks} random seeds in particle bbox")

    # Assign chunk ids via nearest seed
    tree_seeds = KDTree(seeds)
    _, chunk_ids = tree_seeds.query(pos_np)
    chunk_ids = chunk_ids.astype(np.int32)
    u, cnts = np.unique(chunk_ids, return_counts=True)
    print(f"[runtime voronoi] chunk sizes: min={cnts.min()} max={cnts.max()} mean={int(cnts.mean())}")

    # Find boundary particles: any neighbor within boundary_radius in a different chunk
    tree_pts = KDTree(pos_np)
    dists, nbr_idx = tree_pts.query(pos_np, k=k_neighbors)  # (N, k)
    nbr_chunks = chunk_ids[nbr_idx]                          # (N, k)
    within_radius = dists < boundary_radius                  # (N, k)
    diff_chunk = nbr_chunks != chunk_ids[:, None]            # (N, k)
    is_boundary = (within_radius & diff_chunk).any(axis=1)   # (N,)

    n_bdy = int(is_boundary.sum())
    print(f"[runtime voronoi] boundary particles: {n_bdy} ({100*n_bdy/n:.1f}%)")

    ys = np.full(n, float(interior_yield), dtype=np.float32)
    ys[is_boundary] = float(boundary_yield)

    return torch.from_numpy(ys).to(device), chunk_ids, is_boundary


def load_per_particle_yield_stress(config_path, sim_params, n_particles, device="cuda:0"):
    """
    Load per-particle yield_stress from a .pt file specified in the config.
    Falls back to uniform yield_stress if no voronoi config is present.
    """
    if "voronoi" not in sim_params:
        return None

    voronoi_cfg = sim_params["voronoi"]
    yield_stress_file = voronoi_cfg.get("yield_stress_file", None)
    if yield_stress_file is None:
        return None

    # Resolve path relative to config file directory
    config_dir = os.path.dirname(os.path.abspath(config_path))
    # Also try relative to GaussianFluent root
    candidates = [
        yield_stress_file,
        os.path.join(config_dir, yield_stress_file),
        os.path.join("/data/yinshaoxuan/GaussianFluent", yield_stress_file),
    ]

    yield_stress_path = None
    for c in candidates:
        if os.path.exists(c):
            yield_stress_path = c
            break

    if yield_stress_path is None:
        print(f"WARNING: yield_stress file not found in any of: {candidates}")
        return None

    yield_stress = torch.load(yield_stress_path, weights_only=True)
    print(f"Loaded per-particle yield_stress from {yield_stress_path}")
    print(f"  Shape: {yield_stress.shape}, min={yield_stress.min():.1f}, max={yield_stress.max():.1f}")
    print(f"  Boundary (low yield) count: {(yield_stress < 500).sum()}")
    print(f"  Interior (high yield) count: {(yield_stress >= 500).sum()}")

    # Return unpadded — the caller will handle masking + padding for fill particles
    return yield_stress


def compile_video_ffmpeg(output_path, width, height, fps=32):
    """Compile rendered frames into video using ffmpeg subprocess."""
    cmd = [
        "/data/yinshaoxuan/miniconda3/bin/ffmpeg",
        "-framerate", str(fps),
        "-i", os.path.join(output_path, "%04d.png"),
        "-c:v", "libx264",
        "-s", f"{width}x{height}",
        "-y",
        "-pix_fmt", "yuv420p",
        os.path.join(output_path, "output.mp4"),
    ]
    subprocess.run(cmd, check=True)
    print(f"Video saved: {os.path.join(output_path, 'output.mp4')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_path", type=str, required=True)
    parser.add_argument("--output_path", type=str, default=None)
    parser.add_argument("--config", type=str, required=True)
    parser.add_argument("--output_ply", action="store_true")
    parser.add_argument("--output_h5", action="store_true")
    parser.add_argument("--render_img", action="store_true")
    parser.add_argument("--compile_video", action="store_true")
    parser.add_argument("--white_bg", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--resolution", type=str, default="1920x1080")
    args = parser.parse_args()

    if not os.path.exists(args.model_path):
        raise AssertionError("Model path does not exist!")
    if not os.path.exists(args.config):
        raise AssertionError("Scene config does not exist!")
    if args.output_path is not None and not os.path.exists(args.output_path):
        os.makedirs(args.output_path)
        import shutil
        config_name = os.path.basename(args.config)
        shutil.copy2(args.config, os.path.join(args.output_path, config_name))

    # Load raw JSON for impulse and voronoi params (not handled by decode_param)
    with open(args.config, 'r') as f:
        raw_config = json.load(f)

    # Load scene config
    print("Loading scene config...")
    (
        material_params,
        bc_params,
        time_params,
        preprocessing_params,
        camera_params,
    ) = decode_param_json(args.config)

    # Pass impulse from raw config into material_params
    if "impulse" in raw_config:
        material_params["impulse"] = raw_config["impulse"]

    # Load gaussians
    print("Loading gaussians...")
    model_path = args.model_path
    gaussians = load_checkpoint(model_path)
    pipeline = PipelineParamsNoparse()
    pipeline.compute_cov3D_python = True
    background = (
        torch.tensor([1, 1, 1], dtype=torch.float32, device="cuda")
        if args.white_bg
        else torch.tensor([0, 0, 0], dtype=torch.float32, device="cuda")
    )

    # Init the scene
    print("Initializing scene and pre-processing...")
    params = load_params_from_gs(gaussians, pipeline)

    init_pos = params["pos"]
    init_cov = params["cov3D_precomp"]
    init_screen_points = params["screen_points"]
    init_opacity = params["opacity"]
    init_shs = params["shs"]

    # Throw away low opacity kernels
    mask = init_opacity[:, 0] > preprocessing_params["opacity_threshold"]
    init_pos = init_pos[mask, :]
    init_cov = init_cov[mask, :]
    init_opacity = init_opacity[mask, :]
    init_screen_points = init_screen_points[mask, :]
    init_shs = init_shs[mask, :]

    gaussians._xyz = gaussians._xyz[mask, :]
    gaussians._features_dc = gaussians._features_dc[mask, :]
    gaussians._features_rest = gaussians._features_rest[mask, :]
    gaussians._opacity = gaussians._opacity[mask, :]
    gaussians._scaling = gaussians._scaling[mask, :]
    gaussians._rotation = gaussians._rotation[mask, :]

    mask_opa = mask

    # Rotate and translate object
    rotation_matrices = generate_rotation_matrices(
        torch.tensor(preprocessing_params["rotation_degree"]),
        preprocessing_params["rotation_axis"],
    )
    rotated_pos = apply_rotations(init_pos, rotation_matrices)

    # Select sim area
    unselected_pos, unselected_cov, unselected_opacity, unselected_shs = (
        None, None, None, None,
    )
    sim_area_mask = None
    if preprocessing_params["sim_area"] is not None:
        boundary = preprocessing_params["sim_area"]
        assert len(boundary) == 6
        sim_area_mask = torch.ones(rotated_pos.shape[0], dtype=torch.bool).to(device="cuda")
        for i in range(3):
            sim_area_mask = torch.logical_and(sim_area_mask, rotated_pos[:, i] > boundary[2 * i])
            sim_area_mask = torch.logical_and(sim_area_mask, rotated_pos[:, i] < boundary[2 * i + 1])

        unselected_pos = init_pos[~sim_area_mask, :]
        unselected_cov = init_cov[~sim_area_mask, :]
        unselected_opacity = init_opacity[~sim_area_mask, :]
        unselected_shs = init_shs[~sim_area_mask, :]

        rotated_pos = rotated_pos[sim_area_mask, :]
        init_cov = init_cov[sim_area_mask, :]
        init_opacity = init_opacity[sim_area_mask, :]
        init_shs = init_shs[sim_area_mask, :]

    transformed_pos, scale_origin, original_mean_pos = transform2origin(rotated_pos)
    transformed_pos = shift2center111(transformed_pos)

    # Modify covariance matrix accordingly
    init_cov = apply_cov_rotations(init_cov, rotation_matrices)
    init_cov = scale_origin * scale_origin * init_cov

    # Particle filling (for solid building — added to support Voronoi + solid fill)
    gs_num = transformed_pos.shape[0]
    device = "cuda:0"
    filling_params = preprocessing_params.get("particle_filling", None)
    if filling_params is not None:
        print("Filling internal particles...")
        mpm_init_pos = fill_particles(
            pos=transformed_pos,
            opacity=init_opacity,
            cov=init_cov,
            grid_n=filling_params["n_grid"],
            max_samples=filling_params["max_particles_num"],
            grid_dx=material_params["grid_lim"] / filling_params["n_grid"],
            density_thres=filling_params["density_threshold"],
            search_thres=filling_params["search_threshold"],
            max_particles_per_cell=filling_params["max_partciels_per_cell"],
            search_exclude_dir=filling_params["search_exclude_direction"],
            ray_cast_dir=filling_params["ray_cast_direction"],
            boundary=filling_params["boundary"],
            smooth=filling_params["smooth"],
        ).to(device=device)
    else:
        mpm_init_pos = transformed_pos.to(device=device)

    # Init the MPM solver
    print("Initializing MPM solver and setting up boundary conditions...")
    mpm_init_vol = get_particle_volume(
        mpm_init_pos,
        material_params["n_grid"],
        material_params["grid_lim"] / material_params["n_grid"],
        unifrom=material_params["material"] == "sand",
    ).to(device=device)

    mpm_init_cov = torch.zeros((mpm_init_pos.shape[0], 6), device=device)
    mpm_init_cov[:gs_num] = init_cov
    shs = init_shs
    opacity = init_opacity

    # Set up the MPM solver
    mpm_solver = MPM_Simulator_WARP(10)

    mpm_solver.load_initial_data_from_torch(
        mpm_init_pos,
        mpm_init_vol,
        mpm_init_cov,
        n_grid=material_params["n_grid"],
        grid_lim=material_params["grid_lim"],
    )
    mpm_solver.set_parameters_dict(material_params)

    # ============================================================
    # VORONOI: Load per-particle yield_stress and apply to solver
    # ============================================================
    # Runtime voronoi on full particle cloud (surface + fill) if requested
    voronoi_cfg = raw_config.get("voronoi", {})
    if voronoi_cfg.get("runtime", False):
        seeds_file = os.path.join("/data/yinshaoxuan/GaussianFluent",
                                  "voronoi_building_textured_data/voronoi_seeds.npy")
        per_particle_ys, _chunk_ids, _bdy = compute_runtime_voronoi(
            mpm_init_pos, voronoi_cfg, seeds_file=seeds_file, device=device)
    else:
        per_particle_ys = load_per_particle_yield_stress(
            args.config, raw_config, mpm_solver.n_particles, device=device
        )

    if per_particle_ys is not None:
        # Runtime-voronoi path already returns per-solver-particle array — skip masking
        if voronoi_cfg.get("runtime", False):
            per_particle_ys_masked = per_particle_ys
        else:
            # Apply opacity mask to yield_stress (same mask we applied to particles)
            if mask_opa is not None:
                per_particle_ys_masked = per_particle_ys[mask_opa.cpu()]
            else:
                per_particle_ys_masked = per_particle_ys
            # Apply sim_area mask if applicable
            if sim_area_mask is not None:
                per_particle_ys_masked = per_particle_ys_masked[sim_area_mask.cpu()]

        # Now match to solver particle count
        n_solver = mpm_solver.n_particles
        if len(per_particle_ys_masked) != n_solver:
            print(f"WARNING: After masking, yield_stress has {len(per_particle_ys_masked)} "
                  f"entries but solver has {n_solver} particles")
            if len(per_particle_ys_masked) > n_solver:
                per_particle_ys_masked = per_particle_ys_masked[:n_solver]
            else:
                pad = torch.full((n_solver - len(per_particle_ys_masked),),
                                 per_particle_ys_masked.max())
                per_particle_ys_masked = torch.cat([per_particle_ys_masked, pad])

        # Modify the existing warp array in-place using .numpy() + .assign()
        # (same pattern as the building script uses for beta)
        ys_warp = mpm_solver.mpm_model.yield_stress.numpy()
        ys_np = per_particle_ys_masked.cpu().numpy().astype(np.float64)
        ys_warp[:] = ys_np
        mpm_solver.mpm_model.yield_stress.assign(ys_warp)
        print(f"Applied per-particle yield_stress to solver ({n_solver} particles)")
        print(f"  Low yield (boundary): {(ys_np < 500).sum()}")
        print(f"  High yield (interior): {(ys_np >= 500).sum()}")
    else:
        print("No per-particle yield_stress found, using uniform value from config.")

    # Apply impact via initial velocity on selected particles
    # (safer than impulse system which can cause CFL violations)
    if "impact" in raw_config:
        impact_cfg = raw_config["impact"]
        impact_point = torch.tensor(impact_cfg["point"], device=device)
        impact_size = torch.tensor(impact_cfg["size"], device=device)
        impact_velocity = torch.tensor(impact_cfg["velocity"], device=device)

        # Select particles in the impact region (in transformed space)
        positions = mpm_solver.export_particle_x_to_torch()
        in_region = torch.ones(positions.shape[0], dtype=torch.bool, device=device)
        for dim in range(3):
            in_region &= (positions[:, dim] - impact_point[dim]).abs() < impact_size[dim]

        n_impact = in_region.sum().item()
        print(f"Impact: {n_impact} particles in region, velocity={impact_velocity.tolist()}")

        # Set initial velocity: impact particles get impact_velocity, rest get zero
        init_v = torch.zeros(positions.shape[0], 3, device=device)
        init_v[in_region] = impact_velocity
        mpm_solver.import_particle_v_from_torch(init_v)
    elif "impulse" in material_params:
        # Fallback to impulse system with force values scaled down
        imp = material_params["impulse"]
        mpm_solver.add_impulse_on_particles(
            force=imp["force"],
            dt=imp["dt"],
            point=imp["point"],
            size=imp["size"],
            num_dt=imp["num_dt"],
            start_time=imp["start_time"],
        )
        print(f"Applied impulse: force={imp['force']}, point={imp['point']}")

    set_boundary_conditions(mpm_solver, bc_params, time_params)
    mpm_solver.finalize_mu_lam()

    # Camera setting
    mpm_space_viewpoint_center = (
        torch.tensor(camera_params["mpm_space_viewpoint_center"]).reshape((1, 3)).cuda()
    )
    mpm_space_vertical_upward_axis = (
        torch.tensor(camera_params["mpm_space_vertical_upward_axis"])
        .reshape((1, 3))
        .cuda()
    )
    (
        viewpoint_center_worldspace,
        observant_coordinates,
    ) = get_center_view_worldspace_and_observant_coordinate(
        mpm_space_viewpoint_center,
        mpm_space_vertical_upward_axis,
        rotation_matrices,
        scale_origin,
        original_mean_pos,
    )

    # Save simulation PLY if requested
    if args.output_ply or args.output_h5:
        directory_to_save = os.path.join(args.output_path, "simulation_ply")
        if not os.path.exists(directory_to_save):
            os.makedirs(directory_to_save)
        save_data_at_frame(
            mpm_solver, directory_to_save, 0,
            save_to_ply=args.output_ply, save_to_h5=args.output_h5,
        )

    # Compute CFL-based substep_dt
    dx = material_params["grid_lim"] / material_params['n_grid']
    E = material_params['E']
    nu = material_params['nu']
    rho = material_params['density']

    def evaluate_sound_speed(E, nu, rho):
        return np.sqrt(E * (1 - nu) / ((1 + nu) * (1 - 2 * nu) * rho))

    cfl = 0.6
    substep_dt = cfl * dx / evaluate_sound_speed(E, nu, rho)
    frame_dt = time_params["frame_dt"]
    frame_num = time_params["frame_num"]
    step_per_frame = int(frame_dt / substep_dt)

    print(f"Simulation parameters:")
    print(f"  substep_dt = {substep_dt:.6e}")
    print(f"  frame_dt = {frame_dt}")
    print(f"  steps/frame = {step_per_frame}")
    print(f"  total frames = {frame_num}")

    opacity_render = opacity
    shs_render = shs
    height = None
    width = None
    ti.reset()

    # Run the simulation
    for frame in tqdm(range(frame_num)):
        current_camera = get_camera_view(
            model_path,
            default_camera_index=camera_params["default_camera_index"],
            center_view_world_space=viewpoint_center_worldspace,
            observant_coordinates=observant_coordinates,
            show_hint=camera_params["show_hint"],
            init_azimuthm=camera_params["init_azimuthm"],
            init_elevation=camera_params["init_elevation"],
            init_radius=camera_params["init_radius"],
            move_camera=camera_params["move_camera"],
            current_frame=frame,
            delta_a=camera_params["delta_a"],
            delta_e=camera_params["delta_e"],
            delta_r=camera_params["delta_r"],
            width=int(args.resolution.split("x")[0]),
            height=int(args.resolution.split("x")[1]),
        )
        rasterize = initialize_resterize(
            current_camera, gaussians, pipeline, background
        )

        # Step the simulation
        for step in range(step_per_frame):
            mpm_solver.p2g2p(step, substep_dt, device=device,
                            flip_pic_ratio=material_params['flip_pic_ratio'])

        if args.output_ply or args.output_h5:
            save_data_at_frame(
                mpm_solver, directory_to_save, frame + 1,
                save_to_ply=args.output_ply, save_to_h5=args.output_h5,
            )

        if args.render_img:
            # Export particle state
            pos = mpm_solver.export_particle_x_to_torch()[:gs_num].to(device)
            cov3D = mpm_solver.export_particle_cov_to_torch()
            rot = mpm_solver.export_particle_R_to_torch()
            cov3D = cov3D.view(-1, 6)[:gs_num].to(device)
            rot = rot.view(-1, 3, 3)[:gs_num].to(device)

            # Apply inverse transforms
            pos = apply_inverse_rotations(
                undotransform2origin(
                    undoshift2center111(pos), scale_origin, original_mean_pos
                ),
                rotation_matrices,
            )
            cov3D = cov3D / (scale_origin * scale_origin)
            cov3D = apply_inverse_cov_rotations(cov3D, rotation_matrices)
            opacity_frame = opacity_render
            shs_frame = shs_render

            # Add unselected particles back
            if preprocessing_params["sim_area"] is not None:
                pos = torch.cat([pos, unselected_pos], dim=0)
                cov3D = torch.cat([cov3D, unselected_cov], dim=0)
                opacity_frame = torch.cat([opacity_render, unselected_opacity], dim=0)
                shs_frame = torch.cat([shs_render, unselected_shs], dim=0)

            # Hide fully damaged particles — only surface Gaussians have opacity
            alpha = mpm_solver.mpm_state.particle_Jp.numpy()
            damage_mask = alpha[:gs_num] > 0.4
            opacity_frame[:gs_num][damage_mask] = 0

            # Convert SH to colors
            colors_precomp = convert_SH(shs_frame, current_camera, gaussians, pos, rot)

            # Rasterize (returns 2 values, not 3)
            rendering, radii = rasterize(
                means3D=pos,
                means2D=init_screen_points,
                shs=None,
                colors_precomp=colors_precomp.float(),
                opacities=opacity_frame,
                scales=None,
                rotations=None,
                cov3D_precomp=cov3D,
            )

            cv2_img = rendering.permute(1, 2, 0).detach().cpu().numpy()
            cv2_img = cv2.cvtColor(cv2_img, cv2.COLOR_BGR2RGB)
            if height is None or width is None:
                height = cv2_img.shape[0] // 2 * 2
                width = cv2_img.shape[1] // 2 * 2
            assert args.output_path is not None
            cv2.imwrite(
                os.path.join(args.output_path, f"{frame:04d}.png"),
                255 * cv2_img,
            )

    if args.render_img and args.compile_video:
        compile_video_ffmpeg(args.output_path, width, height, fps=32)

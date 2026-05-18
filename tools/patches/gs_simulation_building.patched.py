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
# Gaussian splatting dependencies
from utils.sh_utils import eval_sh
from scene.gaussian_model import GaussianModel
from diff_gaussian_rasterization import (
    GaussianRasterizationSettings,
    GaussianRasterizer,
)
from sklearn.neighbors import NearestNeighbors
import numpy as np
from scene.cameras import Camera as GSCamera
from gaussian_renderer import render, GaussianModel
from utils.system_utils import searchForMaxIteration
from utils.graphics_utils import focal2fov
from utils.shadow_utils import *
# MPM dependencies
from mpm_solver_warp.engine_utils import *
from mpm_solver_warp.mpm_solver_warp import MPM_Simulator_WARP
import warp as wp

# Particle filling dependencies
from particle_filling.filling import *
import open3d as o3d
# Utils
from utils.decode_param import *
from utils.transformation_utils import *
from utils.camera_view_utils import *
from utils.render_utils import *
from utils.lighting_utils import *


wp.init()
wp.config.verify_cuda = True

ti.init(arch=ti.cuda, device_memory_GB=2.0, random_seed=42)

import sys # 导入 sys 模块以使用 sys.stdout.flush()

def run_command_realtime(command_to_run):
    """
    执行一个 shell 命令并实时打印其标准输出和标准错误。

    Args:
        command_to_run (str): 要执行的 shell 命令。

    Returns:
        int: 子进程的退出码。
    """
    print(f"--- 开始执行命令: {command_to_run} ---")
    try:
        # 使用 Popen 启动子进程
        # 将 stderr 重定向到 stdout (2>&1)
        # text=True 使 stdout/stderr 成为文本流
        # bufsize=1 设置为行缓冲模式（如果可能）
        # encoding='utf-8' 明确指定编码
        process = subprocess.Popen(
            ["bash", "-c", f"{command_to_run} 2>&1"], # 将 stderr 合并到 stdout
            stdout=subprocess.PIPE,
            # stderr=subprocess.PIPE, # 不再需要单独处理 stderr
            text=True,
            bufsize=1,
            encoding='utf-8',
            errors='replace' # 处理潜在的解码错误
        )

        # 实时读取 stdout 流
        if process.stdout:
            # 使用 iter 和 readline 逐行读取，直到流结束
            for line in iter(process.stdout.readline, ''):
                print(line, end='') # 打印读取到的行，end='' 避免额外换行
                sys.stdout.flush() # 强制刷新缓冲区，确保立即显示

        # 等待子进程结束
        process.wait()

        # 检查子进程的退出码
        return_code = process.returncode
        print(f"\n--- 命令执行完毕，退出码: {return_code} ---")
        if return_code != 0:
            print(f"警告：命令执行可能出错，退出码为 {return_code}")

        return return_code

    except FileNotFoundError:
        print(f"错误：无法找到 'bash' 命令或指定的程序。请检查路径和环境。")
        return -1
    except Exception as e:
        print(f"执行命令时发生错误: {e}")
        return -1


class PipelineParamsNoparse:
    """Same as PipelineParams but without argument parser."""

    def __init__(self):
        self.convert_SHs_python = False
        self.compute_cov3D_python = False
        self.debug = False


def load_checkpoint(model_path, sh_degree=3, iteration=-1):
    # Find checkpoint
    checkpt_dir = os.path.join(model_path, "point_cloud")
    if iteration == -1:
        iteration = searchForMaxIteration(checkpt_dir)
    checkpt_path = os.path.join(
        checkpt_dir, f"iteration_{iteration}", "point_cloud.ply"
    )

    # Load guassians
    gaussians = GaussianModel(sh_degree)
    gaussians.load_ply(checkpt_path)
    return gaussians


def _phasea_print_summary(frame_times, sim_times):
    if frame_times:
        ft = sorted(frame_times)
        med = ft[len(ft)//2]
        mean = sum(ft)/len(ft)
        print(f'[PhaseA-SUMMARY] frame_count={len(ft)} median_frame_s={med:.4f} mean_frame_s={mean:.4f} fps={1.0/med:.3f}')
    if sim_times:
        st = sorted(sim_times)
        med = st[len(st)//2]
        mean = sum(st)/len(st)
        print(f'[PhaseA-SUMMARY] sim-only median={med:.4f}s mean={mean:.4f}s fps={1.0/med:.3f}')

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
    parser.add_argument("--resolution", type=str, default="1920x1080", help="WxH, e.g. 3840x2160 for 4K")
    parser.add_argument("--load_from_saved", action="store_true", help="Load simulation data from saved .pt files instead of running the simulation.")
    parser.add_argument("--no_cfl_override", action="store_true", help="[Phase A] keep substep_dt from config (do not recompute via CFL).")
    parser.add_argument("--graph_capture", action="store_true", help="[Phase A] fuse inner substep loop into a single CUDA graph submission.")
    parser.add_argument("--bench_only", action="store_true", help="[Phase A] skip rendering/loading; only run sim substeps + per-frame timing.")
    parser.add_argument("--async_io", action="store_true", help="[Phase B.3] overlap ply / png writes with next-frame sim via a small ThreadPoolExecutor.")
    parser.add_argument("--target_particles", type=int, default=0, help="[Phase B.1] If >0, importance-subsample post-fill particle set down to this count. 0 = off (default), bit-identical to Phase A.")
    parser.add_argument("--subsample_seed", type=int, default=0, help="[Phase B.1] RNG seed for subsampling (deterministic).")
    parser.add_argument("--fp16", action="store_true", help="[Phase B.2] enable fp16 mixed precision: per-particle x/v/C in fp16 sidecars, grid + stress + F kept fp32. Default off = byte-identical to Phase B.1.")
    parser.add_argument("--sort_p2g", action="store_true", help="[Phase C.2.a] sort particles by cell_id before P2G to reduce atomic contention. Default off = byte-identical to post-B.3.")
    parser.add_argument("--output_cov", action="store_true",
                        help="[particle_F] Also write the 6-float upper-triangular "
                             "covariance per particle into each sim_NNNN.ply, "
                             "alongside x/y/z. Lets the downstream fuse step "
                             "bind each ref splat to a single sim particle and "
                             "inherit its deformation correctly — eliminates the "
                             "K-NN 'ghost' artifact for cracked regions. Adds "
                             "~24 bytes/particle to each frame's ply (~4.8 MB "
                             "extra for 200k particles).")
    args = parser.parse_args()

    if not os.path.exists(args.model_path):
        AssertionError("Model path does not exist!")
    if not os.path.exists(args.config):
        AssertionError("Scene config does not exist!")
    if args.output_path is not None and not os.path.exists(args.output_path):
        os.makedirs(args.output_path)
        # 复制config文件到输出目录
        import shutil
        config_name = os.path.basename(args.config)
        shutil.copy2(args.config, os.path.join(args.output_path, config_name))

    # load scene config
    print("Loading scene config...")
    (
        material_params,
        bc_params,
        time_params,
        preprocessing_params,
        camera_params,
    ) = decode_param_json(args.config)
    with open(args.config) as _f:
        raw_config = json.load(_f)

    # load gaussians
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
    # background = torch.tensor([0, 0, 0], dtype=torch.float32, device="cuda")
    # init the scene
    print("Initializing scene and pre-processing...")
    params = load_params_from_gs(gaussians, pipeline)

    init_pos = params["pos"]
    init_cov = params["cov3D_precomp"]
    init_screen_points = params["screen_points"]
    init_opacity = params["opacity"]
    init_shs = params["shs"]

    # throw away low opacity kernels
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
    
    # rorate and translate object
    if args.debug:
        if not os.path.exists("./log"):
            os.makedirs("./log")
        particle_position_tensor_to_ply(
            init_pos,
            "./log/init_particles.ply",
        )
    rotation_matrices = generate_rotation_matrices(
        torch.tensor(preprocessing_params["rotation_degree"]),
        preprocessing_params["rotation_axis"],
    )
    rotated_pos = apply_rotations(init_pos, rotation_matrices)

    if args.debug:
        particle_position_tensor_to_ply(rotated_pos, "./log/rotated_particles.ply")

    # select a sim area and save params of unslected particles
    unselected_pos, unselected_cov, unselected_opacity, unselected_shs = (
        None,
        None,
        None,
        None,
    )
    if preprocessing_params["sim_area"] is not None:
        boundary = preprocessing_params["sim_area"]
        assert len(boundary) == 6
        mask = torch.ones(rotated_pos.shape[0], dtype=torch.bool).to(device="cuda")
        for i in range(3):
            mask = torch.logical_and(mask, rotated_pos[:, i] > boundary[2 * i])
            mask = torch.logical_and(mask, rotated_pos[:, i] < boundary[2 * i + 1])

        unselected_pos = init_pos[~mask, :]
        unselected_cov = init_cov[~mask, :]
        unselected_opacity = init_opacity[~mask, :]
        unselected_shs = init_shs[~mask, :]

        rotated_pos = rotated_pos[mask, :]
        init_cov = init_cov[mask, :]
        init_opacity = init_opacity[mask, :]
        init_shs = init_shs[mask, :]

        # Keep gaussians._* aligned with the masked init_* arrays. Without
        # this, gaussians._scaling.shape[0] stays at the post-opacity-mask
        # count while init_opacity is now post-sim_area, and the
        # gauss_w = init_opacity * scales_real line in the Phase B.1
        # importance-sampling block below crashes with
        #   RuntimeError: The size of tensor a (N_kept) must match the
        #                 size of tensor b (N_pre_sim_area) at dimension 0.
        # The opacity-threshold filter above already masks gaussians._*
        # — mirroring the same six lines here keeps the two filter
        # passes symmetric.
        gaussians._xyz = gaussians._xyz[mask, :]
        gaussians._features_dc = gaussians._features_dc[mask, :]
        gaussians._features_rest = gaussians._features_rest[mask, :]
        gaussians._opacity = gaussians._opacity[mask, :]
        gaussians._scaling = gaussians._scaling[mask, :]
        gaussians._rotation = gaussians._rotation[mask, :]

    transformed_pos, scale_origin, original_mean_pos = transform2origin(rotated_pos)
    transformed_pos = shift2center111(transformed_pos)

    # modify covariance matrix accordingly
    init_cov = apply_cov_rotations(init_cov, rotation_matrices)
    init_cov = scale_origin * scale_origin * init_cov

    if args.debug:
        particle_position_tensor_to_ply(
            transformed_pos,
            "./log/transformed_particles.ply",
        )

    # fill particles if needed
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

        if args.debug:
            particle_position_tensor_to_ply(mpm_init_pos, "./log/filled_particles.ply")
    else:
        mpm_init_pos = transformed_pos.to(device=device)

    # init the mpm solver
    print("Initializing MPM solver and setting up boundary conditions...")
    mpm_init_vol = get_particle_volume(
        mpm_init_pos,
        material_params["n_grid"],
        material_params["grid_lim"] / material_params["n_grid"],
        unifrom=material_params["material"] == "sand",
    ).to(device=device)

    if filling_params is not None and filling_params["visualize"] == True:
        shs, opacity, mpm_init_cov = init_filled_particles(
            mpm_init_pos[:gs_num],
            init_shs,
            init_cov,
            init_opacity,
            mpm_init_pos[gs_num:],
        )
        gs_num = mpm_init_pos.shape[0]
    else:
        mpm_init_cov = torch.zeros((mpm_init_pos.shape[0], 6), device=device)
        mpm_init_cov[:gs_num] = init_cov
        shs = init_shs
        opacity = init_opacity

    # [Phase B.1] importance-weighted subsampling of post-fill particles.
    # Preserves the layout invariant that mpm_init_pos[:gs_num] are the gaussian-backed
    # render-visible particles and [gs_num:] are filling-only interior particles.
    # Each region is subsampled independently so the split is maintained.
    # Surviving particle volumes are scaled by 1/keep_prob (inverse-prob weighting)
    # so total mass is preserved.
    if args.target_particles > 0 and mpm_init_pos.shape[0] > args.target_particles:
        N_total = mpm_init_pos.shape[0]
        N_target = args.target_particles
        N_gauss = gs_num
        N_inter = N_total - gs_num
        # importance weight per gaussian: opacity * volume(scale_x*scale_y*scale_z).
        # gaussians._scaling is log-space, so exp first; init_opacity is sigmoid-activated.
        scales_real = torch.exp(gaussians._scaling).detach()
        gauss_w = (init_opacity[:, 0] * scales_real[:, 0] * scales_real[:, 1] * scales_real[:, 2]).clamp(min=1e-12)
        # Split target proportionally to current ratio so we don't starve either group.
        target_gauss = max(1, int(round(N_target * (N_gauss / float(N_total)))))
        target_inter = max(0, N_target - target_gauss)
        target_gauss = min(target_gauss, N_gauss)
        target_inter = min(target_inter, N_inter)
        gen = torch.Generator(device='cpu').manual_seed(int(args.subsample_seed))
        # weighted-without-replacement sample of gaussian particles
        if target_gauss < N_gauss:
            probs_g = (gauss_w / gauss_w.sum()).cpu()
            gauss_idx = torch.multinomial(probs_g, target_gauss, replacement=False, generator=gen).to(device)
            keep_prob_g = (probs_g[gauss_idx.cpu()] * target_gauss).clamp(min=1e-12).to(device)
        else:
            gauss_idx = torch.arange(N_gauss, device=device)
            keep_prob_g = torch.ones(N_gauss, device=device)
        # uniform subsample of filled interior particles
        if target_inter < N_inter and N_inter > 0:
            inter_idx_local = torch.randperm(N_inter, generator=gen)[:target_inter].to(device)
            inter_idx = inter_idx_local + N_gauss
            keep_prob_i = torch.full((target_inter,), target_inter / float(N_inter), device=device)
        elif N_inter > 0:
            inter_idx = torch.arange(N_gauss, N_total, device=device)
            keep_prob_i = torch.ones(N_inter, device=device)
        else:
            inter_idx = torch.empty(0, dtype=torch.long, device=device)
            keep_prob_i = torch.empty(0, device=device)

        all_idx = torch.cat([gauss_idx, inter_idx], dim=0)
        keep_probs = torch.cat([keep_prob_g, keep_prob_i], dim=0)
        # rescale volumes by 1/keep_prob to preserve total mass
        vol_scale = (1.0 / keep_probs).to(mpm_init_vol.dtype)

        new_gs_num = gauss_idx.shape[0]
        # apply subsample to all post-fill arrays
        mpm_init_pos = mpm_init_pos[all_idx].contiguous()
        mpm_init_cov = mpm_init_cov[all_idx].contiguous()
        mpm_init_vol = (mpm_init_vol[all_idx] * vol_scale).contiguous()
        # gaussian-only render arrays
        shs = shs[gauss_idx].contiguous() if shs is not None else shs
        opacity = opacity[gauss_idx].contiguous()
        init_cov = init_cov[gauss_idx].contiguous()
        init_shs = init_shs[gauss_idx].contiguous()
        init_opacity = init_opacity[gauss_idx].contiguous()
        # subset the gaussians model in-place so per-frame KNN/feature lookups stay aligned
        _gidx_cpu = gauss_idx.cpu()
        gaussians._xyz = gaussians._xyz[_gidx_cpu] if gaussians._xyz.device.type == 'cpu' else gaussians._xyz[gauss_idx]
        gaussians._features_dc = gaussians._features_dc[_gidx_cpu] if gaussians._features_dc.device.type == 'cpu' else gaussians._features_dc[gauss_idx]
        gaussians._features_rest = gaussians._features_rest[_gidx_cpu] if gaussians._features_rest.device.type == 'cpu' else gaussians._features_rest[gauss_idx]
        gaussians._opacity = gaussians._opacity[_gidx_cpu] if gaussians._opacity.device.type == 'cpu' else gaussians._opacity[gauss_idx]
        gaussians._scaling = gaussians._scaling[_gidx_cpu] if gaussians._scaling.device.type == 'cpu' else gaussians._scaling[gauss_idx]
        gaussians._rotation = gaussians._rotation[_gidx_cpu] if gaussians._rotation.device.type == 'cpu' else gaussians._rotation[gauss_idx]
        gs_num = new_gs_num
        print(f"[PhaseB.1] subsampled: {N_total} -> {mpm_init_pos.shape[0]} (gauss {N_gauss}->{new_gs_num}, inter {N_inter}->{target_inter})")

    if args.debug:
        print("check *.ply files to see if it's ready for simulation")

    # set up the mpm solver
    mpm_solver = MPM_Simulator_WARP(10, mixed_precision=args.fp16)
    if args.fp16:
        print("[PhaseB.2] mixed precision enabled: per-particle x/v/C in fp16, grid+stress+F in fp32")
    
    # mpm_init_pos[:, 2] = mpm_init_pos[:, 2] - 0.5
    # scale_factor =  1 / 3 
    # mpm_init_cov *= scale_factor**2
    # mpm_init_pos =  mpm_init_pos.mean(dim = 0) + scale_factor *(mpm_init_pos -  mpm_init_pos.mean(dim = 0))
    # mpm_init_pos[:, 1] += 1
    mpm_solver.load_initial_data_from_torch(
        mpm_init_pos,
        mpm_init_vol,
        mpm_init_cov,
        n_grid=material_params["n_grid"],
        grid_lim=material_params["grid_lim"],
    )
    mpm_solver.set_parameters_dict(material_params)
    
    beta = mpm_solver.mpm_model.beta.numpy()
    mask1 = (gaussians._features_dc < -0.5).all(axis=2).cpu().numpy().squeeze()
    mask2 = (gaussians._features_dc > -2.5).all(axis=2).cpu().numpy().squeeze()
    mask_ = mask1 & mask2
    # Skip watermelon-specific mask logic for building
    selected_xyz = gaussians._xyz
    mask1 = (gaussians._features_dc > 1.0).all(axis=2).cpu().numpy().squeeze()
    mask2 = (gaussians._features_dc < 4).all(axis=2).cpu().numpy().squeeze()
    mask__ = mask1 & mask2
    
    # Build a KNN radius-search index on all Gaussians.
    all_xyz_np = gaussians._xyz.detach().cpu().numpy()
    knn = NearestNeighbors(radius=0.03, algorithm='ball_tree')
    knn.fit(all_xyz_np)

    # Select candidate "black seed" points by their DC feature range,
    # then collect all neighboring points within a small radius.
    selected_xyz_np = selected_xyz.detach().cpu().numpy()
    neighbors_indices = knn.radius_neighbors(selected_xyz_np, 0.01, return_distance=False)

    # Flatten and deduplicate neighbor indices.
    all_neighbors = np.unique(np.concatenate(neighbors_indices))

    # Create a mask for the seed region and make it less brittle by
    # assigning a very large beta (harder-to-break material behavior).
    new_mask = torch.ones(gaussians._xyz.shape[0], dtype=torch.bool, device=gaussians._xyz.device)  # all particles are interior for building
    new_mask[all_neighbors] = True
    
    
    # beta[new_mask.cpu().numpy()] = 3000000000  # skip watermelon-specific mask
    
    mpm_solver.mpm_model.beta.assign(beta)
    
    
    
    # Note: boundary conditions may depend on mass, so the order cannot be changed!

    # Apply impulse (projectile impact)
    if "impulse" in material_params:
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

    if "impact" in raw_config:
        _cfg = raw_config["impact"]
        _pt = torch.tensor(_cfg["point"], device='cuda')
        _sz = torch.tensor(_cfg["size"], device='cuda')
        _vel = torch.tensor(_cfg["velocity"], device='cuda')
        _pos = mpm_solver.export_particle_x_to_torch()
        _mask = torch.ones(_pos.shape[0], dtype=torch.bool, device='cuda')
        for _d in range(3):
            _mask &= (_pos[:, _d] - _pt[_d]).abs() < _sz[_d]
        n_impact = _mask.sum().item()
        print("Impact: {} particles, v={}".format(n_impact, _cfg["velocity"]))
        _init_v = torch.zeros(_pos.shape[0], 3, device='cuda')
        _init_v += torch.tensor([0.0, 0.0, -6.0], device='cuda')
        _init_v[_mask] = _vel
        mpm_solver.import_particle_v_from_torch(_init_v)
    else:
        mpm_solver.import_particle_v_from_torch(torch.zeros(mpm_init_pos.shape[0], 3, device='cuda').add_(torch.tensor([0.0, 0.0, -6.0], device='cuda')))
    # [PhaseB.2] now that x/v/C are in their final initial state, build fp16 sidecars.
    if args.fp16:
        mpm_solver.init_fp16_mirror(device=device)
        print(f"[PhaseB.2] fp16 sidecars allocated for {mpm_solver.n_particles} particles")
    # camera setting
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

    # run the simulation
    if args.output_ply or args.output_h5:
        directory_to_save = os.path.join(args.output_path, "simulation_ply")
        if not os.path.exists(directory_to_save):
            os.makedirs(directory_to_save)

        save_data_at_frame(
            mpm_solver,
            directory_to_save,
            0,
            save_to_ply=args.output_ply,
            save_to_h5=args.output_h5,
        )
        # [particle_F] Frame 0 was written by save_data_at_frame (the lib
        # helper) which doesn't know about --output_cov. The fuse step
        # detects cov-fields from sim_plys[0] — if it can't find them
        # there, it falls back to the K-NN path for the whole run, which
        # is exactly the ghost-prone behavior --output_cov is meant to
        # replace. Rewrite frame 0 in-place with cov so all frames have
        # a consistent schema.
        if args.output_ply and args.output_cov:
            _f0_path = os.path.join(directory_to_save, "sim_" + "0".zfill(10) + ".ply")
            _f0_pos = mpm_solver.mpm_state.particle_x.numpy().astype(np.float32, copy=True)
            _f0_cov = mpm_solver.export_particle_cov_to_torch().view(-1, 6).detach().cpu().numpy().astype(np.float32, copy=True)
            n_pf = min(_f0_cov.shape[0], _f0_pos.shape[0])
            if _f0_cov.shape[0] != _f0_pos.shape[0]:
                _f0_cov = _f0_cov[:n_pf]; _f0_pos = _f0_pos[:n_pf]
            try:
                if os.path.exists(_f0_path):
                    os.remove(_f0_path)
                with open(_f0_path, "wb") as _f0_fp:
                    _f0_fp.write(
                        f"ply\nformat binary_little_endian 1.0\nelement vertex {len(_f0_pos)}\n"
                        f"property float x\nproperty float y\nproperty float z\n"
                        f"property float cov_00\nproperty float cov_01\nproperty float cov_02\n"
                        f"property float cov_11\nproperty float cov_12\nproperty float cov_22\n"
                        f"end_header\n".encode()
                    )
                    _f0_fp.write(np.concatenate([_f0_pos, _f0_cov], axis=1).astype(np.float32, copy=False).tobytes())
            except Exception as _e:
                print(f"[particle_F] frame-0 cov rewrite failed: {_e}")

    dx = material_params["grid_lim"] / material_params['n_grid']
    substep_dt = time_params["substep_dt"]
    E = material_params['E']
    nu = material_params['nu']
    rho = material_params['density']
    def evaluate_sound_speed_linear_elasticity_analysis(E, nu, rho):
        return np.sqrt(E * (1 - nu) / ((1 + nu) * (1 - 2 * nu) * rho))
    cfl = 0.6
    cfl_dt = cfl * dx / evaluate_sound_speed_linear_elasticity_analysis(E, nu, rho)
    if args.no_cfl_override:
        print(f"[PhaseA] keeping config substep_dt={substep_dt:.3e} (CFL would suggest {cfl_dt:.3e})")
    else:
        # Clamp at the CFL upper bound. The original code did
        #     substep_dt = cfl_dt
        # which silently RELAXED the recipe when cfl_dt > recipe_dt —
        # e.g. for watermelon material the wrapper would overwrite a
        # carefully chosen 1e-4 with 1.307e-4, leaving the sim less
        # stable, not more. The recipe author already knew their scene;
        # we only need to step in when they exceeded CFL.
        new_dt = min(substep_dt, cfl_dt)
        print(f"[PhaseA] substep_dt clamp: recipe={substep_dt:.3e} cfl={cfl_dt:.3e} chosen={new_dt:.3e}")
        substep_dt = new_dt
    frame_dt = time_params["frame_dt"]
    frame_num = time_params["frame_num"]
    step_per_frame = int(frame_dt / substep_dt)
    opacity_render = opacity
    shs_render = shs
    height = None
    width = None
    ti.reset()
    # torch.cuda.empty_cache()

    # color_flag = True
    
    load_color = True
    color_flag = False
    light_flag = False
    end_frame = 23000000
    delta = 0 

    #     opa_mask = torch.load("/data/yinshaoxuan/GaussianFluent/opcity_zero_mask.pt", weights_only=True).cuda()
    #     gaussians2 = load_checkpoint("/data/yinshaoxuan/GaussianFluent/model/garden")
    #     transform_matrix = torch.from_numpy(np.loadtxt("/data/yinshaoxuan/GaussianFluent/model/garden/transform_matrix.txt")).to(device).float()
    #     pos2 = gaussians2._xyz.detach()
    #     pos2 = (pos2  @ transform_matrix[:3, :3].T  + transform_matrix[:3, 3])*3
    #     pos2[:, 2] -= 2.6
    #     pos2[:, 0] += 2.0
    #     pos2[:, 1] += 1.0
    #     cov3D2 = (rotate_flat_covariance(gaussians2.get_covariance(), transform_matrix[:3, :3])*3**2)
    #     rot2 = torch.tensor(transform_matrix[:3, :3], dtype=torch.float32, device="cuda").detach().clone().unsqueeze(0).expand(gaussians2._xyz.shape[0], 3, 3)
    #     opacity_render2 = gaussians2.get_opacity
    #     shs_render2 = 1.0 * gaussians2.get_features
    #     
    #     combined_mask = filter_points_verbose(pos2) 
    #     pos2 = pos2[combined_mask]
    #     cov3D2 = cov3D2[combined_mask]
    #     rot2 = rot2[combined_mask]  # 3D旋转矩阵的mask
    #     opacity_render2 = opacity_render2[combined_mask]
    #     shs_render2 = shs_render2[combined_mask]
    
    
    # Optional cinematic trajectory override (precomputed keyframes)
    _cam_traj = None
    _traj_cfg = raw_config.get("camera_trajectory")
    if _traj_cfg and _traj_cfg.get("keyframes"):
        from utils.camera_trajectory_helper import build_trajectory
        _cam_traj = build_trajectory(frame_num, _traj_cfg["keyframes"])
        print(f"Using cinematic trajectory: {len(_traj_cfg['keyframes'])} keyframes over {frame_num} frames")

    # [PhaseA] timing instrumentation
    import time as _time_mod
    _frame_times = []
    _sim_only_times = []
    try:
        torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass
    print(f"[PhaseA] frame_num={frame_num} step_per_frame={step_per_frame} substep_dt={substep_dt:.3e}")
    print(f"[PhaseA] particle count = {mpm_solver.n_particles}")
    # [PhaseB.3] async-io executor (off by default; byte-identical when off)
    _io_executor = None
    _io_futures = []
    if args.async_io:
        from concurrent.futures import ThreadPoolExecutor
        _io_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="b3-io")
        print("[PhaseB.3] async_io ENABLED: ply/png writes deferred to background thread pool (max_workers=2)")

    def _b3_write_ply(filename, position_np, cov_np=None):
        # Background-safe: writes the supplied host numpy array to disk in PLY format.
        # cov_np (optional): (N, 6) float32 — upper-triangular covariance per
        # particle (order: c00, c01, c02, c11, c12, c22). When provided, the
        # ply gains six extra `property float cov_*` rows after xyz, and
        # particle records become 9 floats wide. The downstream fuse step
        # auto-detects these fields and switches to 1-NN binding with
        # particle-correct deformation.
        try:
            if os.path.exists(filename):
                os.remove(filename)
            num_particles = position_np.shape[0]
            if cov_np is not None:
                assert cov_np.shape == (num_particles, 6), \
                    f"cov_np shape {cov_np.shape} doesn't match position ({num_particles}, 3)"
                _hdr = (
                    f"ply\n"
                    f"format binary_little_endian 1.0\n"
                    f"element vertex {num_particles}\n"
                    f"property float x\n"
                    f"property float y\n"
                    f"property float z\n"
                    f"property float cov_00\n"
                    f"property float cov_01\n"
                    f"property float cov_02\n"
                    f"property float cov_11\n"
                    f"property float cov_12\n"
                    f"property float cov_22\n"
                    f"end_header\n"
                )
                # Interleave xyz + cov into a single (N, 9) array so the file
                # layout matches the property order in the header.
                packed = np.concatenate([position_np, cov_np], axis=1).astype(np.float32, copy=False)
                with open(filename, "wb") as _fp:
                    _fp.write(str.encode(_hdr))
                    _fp.write(packed.tobytes())
            else:
                _hdr = f"""ply\nformat binary_little_endian 1.0\nelement vertex {num_particles}\nproperty float x\nproperty float y\nproperty float z\nend_header\n"""
                with open(filename, "wb") as _fp:
                    _fp.write(str.encode(_hdr))
                    _fp.write(position_np.tobytes())
        except Exception as _e:
            print(f"[PhaseB.3] async ply write failed for {filename}: {_e}")

    def _b3_write_png(path, img_unit):
        # img_unit is a float HxWxC array in [0,1]; the 255* multiply runs in the worker.
        try:
            cv2.imwrite(path, 255 * img_unit)
        except Exception as _e:
            print(f"[PhaseB.3] async png write failed for {path}: {_e}")

    _graph = None
    for frame in tqdm(range(frame_num)):
        _t_frame_start = _time_mod.perf_counter()
        # frame = frame 
        # frame = 21 +frame
        if frame > 30 :
            delta = frame - 30
        if _cam_traj is not None:
            _a, _e, _r = float(_cam_traj[frame, 0]), float(_cam_traj[frame, 1]), float(_cam_traj[frame, 2])
            current_camera = get_camera_view(
                model_path,
                default_camera_index=camera_params["default_camera_index"],
                center_view_world_space=viewpoint_center_worldspace,
                observant_coordinates=observant_coordinates,
                show_hint=camera_params["show_hint"],
                init_azimuthm=_a, init_elevation=_e, init_radius=_r,
                move_camera=False, current_frame=0,
                delta_a=0, delta_e=0, delta_r=0,
                width=int(args.resolution.split("x")[0]),
                height=int(args.resolution.split("x")[1])
            )
        else:
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
                height=int(args.resolution.split("x")[1])
            )
        rasterize = initialize_resterize(
            current_camera, gaussians, pipeline, background
        )
        
        
        
        if not args.load_from_saved :
            _t_sim_start = _time_mod.perf_counter()
            if args.graph_capture:
                if _graph is None:
                    # [Phase C.2.a Pass 2] Wire up the sort-by-cell P2G knob.
                    # Must (a) flip the solver flag BEFORE the warmup substep so
                    # compute_cell_id + radix_sort_pairs are exercised once
                    # outside the captured graph (this is what triggers CUB
                    # scratch alloc in warp.utils.radix_sort_pairs), and
                    # (b) call ensure_sort_buffers() so cell_id/perm exist.
                    if args.sort_p2g:
                        print('[PhaseC] sort_p2g ON -- allocating scratch + warmup')
                        mpm_solver.sort_p2g = True
                        mpm_solver.ensure_sort_buffers()
                    print(f'[PhaseA] capturing CUDA graph for {step_per_frame} substeps...')
                    # warm up kernels (force module load) before capture
                    mpm_solver.p2g2p_capture_safe(0, substep_dt, device=device, flip_pic_ratio=material_params['flip_pic_ratio'])
                    wp.synchronize()
                    _orig_verify = wp.config.verify_cuda
                    wp.config.verify_cuda = False
                    wp.capture_begin(device=device)
                    try:
                        for step in range(step_per_frame):
                            mpm_solver.p2g2p_capture_safe(step, substep_dt, device=device, flip_pic_ratio=material_params['flip_pic_ratio'])
                    finally:
                        _graph = wp.capture_end(device=device)
                        wp.config.verify_cuda = _orig_verify
                    print('[PhaseA] graph captured.')
                else:
                    wp.capture_launch(_graph)
            else:
                if args.sort_p2g and not getattr(mpm_solver, 'sort_p2g', False):
                    print('[PhaseC] sort_p2g ON (non-capture path)')
                    mpm_solver.sort_p2g = True
                    mpm_solver.ensure_sort_buffers()
                for step in range(step_per_frame):
                    mpm_solver.p2g2p(step, substep_dt, device=device, flip_pic_ratio=material_params['flip_pic_ratio'])
            wp.synchronize()
            _sim_only_times.append(_time_mod.perf_counter() - _t_sim_start)
            # [PhaseB.2] sync fp16 sidecars back to fp32 struct so export / save_data /
            # downstream KNN see consistent particle_x/v/C. NOT in the timed sim
            # window since this is bookkeeping, not part of the substep loop.
            if args.fp16:
                mpm_solver.sync_fp32_from_fp16(device=device)
                wp.synchronize()

        if args.output_ply or args.output_h5:
            if args.async_io and args.output_ply and not args.output_h5:
                # [PhaseB.3] snapshot host buffer on main thread (cheap memcpy),
                # then defer disk write to the executor.
                _ply_dir = directory_to_save
                os.umask(0)
                os.makedirs(_ply_dir, 0o777, exist_ok=True)
                _ply_filename = _ply_dir + "/sim_" + str(frame + 1).zfill(10) + ".ply"
                _pos_host = mpm_solver.mpm_state.particle_x.numpy().astype(np.float32, copy=True)
                # [particle_F] Also snapshot the per-particle covariance when
                # the user asks for it. export_particle_cov_to_torch returns
                # (N*6,) flattened; reshape to (N, 6) — upper-triangle order.
                # We only emit cov for the gaussian-bound subset [:gs_num] to
                # match what the fuse step's nearest-neighbor lookup actually
                # needs (filler particles are interior volume only, not
                # bound to any ref splat).
                _cov_host = None
                if args.output_cov:
                    _cov_flat = mpm_solver.export_particle_cov_to_torch()
                    _cov_host = _cov_flat.view(-1, 6).detach().cpu().numpy().astype(
                        np.float32, copy=True
                    )
                    # The solver tensor covers ALL post-fill particles. Match
                    # the position snapshot's row count — particle_x already
                    # holds (gs + filler) entries; cov should match 1:1.
                    if _cov_host.shape[0] != _pos_host.shape[0]:
                        # Defensive: clip to the shorter to avoid downstream
                        # shape mismatches. Logged so we notice if it ever
                        # diverges materially.
                        n = min(_cov_host.shape[0], _pos_host.shape[0])
                        if _cov_host.shape[0] != _pos_host.shape[0]:
                            print(
                                f"[particle_F] cov rows {_cov_host.shape[0]} != "
                                f"pos rows {_pos_host.shape[0]} at frame {frame+1}; "
                                f"clipping to {n}"
                            )
                        _cov_host = _cov_host[:n]
                        _pos_host = _pos_host[:n]
                _io_futures.append(_io_executor.submit(_b3_write_ply, _ply_filename, _pos_host, _cov_host))
            else:
                save_data_at_frame(
                    mpm_solver,
                    directory_to_save,
                    frame + 1,
                    save_to_ply=args.output_ply,
                    save_to_h5=args.output_h5,
                )
            
        if args.render_img:
            # Watermelon-specific "black seed" mask. Originally computed
            # every frame outside this `if` block, but `args.render_img`
            # is the only consumer and the call uses a hard-coded
            # watermelon-shaped ellipsoid that's meaningless for other
            # scenes (e.g. cluster_6_15 buildings). Calling it every
            # frame from outside this block ALSO surfaced a CUDA
            # illegal-memory-access — likely an async-error from a prior
            # MPM op being caught at the next CUDA call — that took down
            # earthquake / metal recipes. Moving inside `if args.render_img`
            # both fixes the wasted work and stops the crash.
            select_id = filter_gaussian_points_by_ellipsoid(tensor=mpm_init_pos, ellipsoid_center=torch.tensor([-0.1, 0.0, 0.0]), ellipsoid_axes=torch.tensor([0.22, 0.22, 0.22]), ellipsoid_greater=False)[1]
            # Define a new base directory within args.output_path for detailed tensor data
            per_frame_tensor_output_base_dir = os.path.join(args.output_path, "gaussian_frame_data")
            os.makedirs(per_frame_tensor_output_base_dir, exist_ok=True)

            # Create a subdirectory for the current frame's tensors
            current_frame_tensor_dir = os.path.join(per_frame_tensor_output_base_dir, f"frame_{frame-delta:05d}") # e.g., frame_00000, frame_00001
            os.makedirs(current_frame_tensor_dir, exist_ok=True)
            # 获取初始数据
            if not args.load_from_saved :
                pos = mpm_solver.export_particle_x_to_torch()[:gs_num].to(device)
                cov3D = mpm_solver.export_particle_cov_to_torch()
                rot = mpm_solver.export_particle_R_to_torch()
                cov3D = cov3D.view(-1, 6)[:gs_num].to(device)
                rot = rot.view(-1, 3, 3)[:gs_num].to(device)

                # 应用变换
                pos = apply_inverse_rotations(
                    undotransform2origin(
                        undoshift2center111(pos), scale_origin, original_mean_pos
                    ),
                    rotation_matrices,
                )
                cov3D = cov3D / (scale_origin * scale_origin)
                cov3D = apply_inverse_cov_rotations(cov3D, rotation_matrices)
                opacity = opacity_render
                shs =  shs_render



                # 如果有sim_area,添加未选择的点
                if preprocessing_params["sim_area"] is not None:
                    pos = torch.cat([pos, unselected_pos], dim=0)
                    cov3D = torch.cat([cov3D, unselected_cov], dim=0)
                    opacity = torch.cat([opacity_render, unselected_opacity], dim=0)
                    shs = torch.cat([shs_render, unselected_shs], dim=0)

                alpha = mpm_solver.mpm_state.particle_Jp.numpy()
                mask = alpha[:gs_num] > 0.4
                opacity[:gs_num][mask] = 0
                
            else:
                # 从当前帧的目录加载保存的张量数据
                tensors_to_load = {
                    "pos.pt": None,
                    "rot.pt": None,
                    "cov3D.pt": None, 
                    "shs.pt": None,
                    "opacity.pt": None
                }

                # 加载每个张量
                for filename, _ in tensors_to_load.items():
                    load_path = os.path.join(current_frame_tensor_dir, filename)
                    if os.path.exists(load_path):
                        tensors_to_load[filename] = torch.load(load_path, weights_only=True).to(device)
                    else:
                        print(f"警告: 找不到文件 {filename} ,帧 {frame}")

                # 将加载的张量赋值给对应变量
                pos = tensors_to_load["pos.pt"]
                rot = tensors_to_load["rot.pt"]
                cov3D = tensors_to_load["cov3D.pt"]
                shs = tensors_to_load["shs.pt"]
                opacity = tensors_to_load["opacity.pt"]

                
            light_output_base_dir = os.path.join(args.output_path, "normal_and_light")
            os.makedirs(light_output_base_dir, exist_ok=True)
            
            current_frame_light_dir = os.path.join(light_output_base_dir, f"frame_{frame-delta:05d}") # e.g., frame_00000, frame_00001
            os.makedirs(current_frame_light_dir, exist_ok=True)
            
            # if frame >= 5 :
            #     opacity[:gaussians._xyz.shape[0]][opa_mask] = 0
            
            if color_flag:
                pos = pos
                cov3D = cov3D
                rot = rot
                opacity = opacity
                shs = shs
                
                npy_path = os.path.join(current_frame_tensor_dir, 'pos.pt')
                opacity_path = os.path.join(current_frame_tensor_dir, 'opacity.pt')
                shs_path = os.path.join(current_frame_tensor_dir, 'shs.pt')
                cov_path = os.path.join(current_frame_tensor_dir, 'cov3D.pt')
                rot_path = os.path.join(current_frame_tensor_dir, 'rot.pt')
                
                colors_precomp = convert_SH(shs, current_camera, gaussians, pos, rot)
                color_path = os.path.join(current_frame_tensor_dir, 'color.pt')
                
                torch.save(pos.detach().cpu(), npy_path)
                torch.save(opacity.detach().cpu(), opacity_path)
                torch.save(shs.detach().cpu(), shs_path)
                torch.save(colors_precomp.detach().cpu(), color_path)
                torch.save(cov3D.detach().cpu(), cov_path)
                torch.save(rot.detach().cpu(), rot_path)

                output_folder = current_frame_light_dir
                normal_path = os.path.join(output_folder, 'pos_valid_with_normals.ply')
                valid_indice_path = os.path.join(output_folder, "pos_valid_indice.npy")
                attenuation_constant = 3
                if  light_flag : 


                    command = f'cd /data/yinshaoxuan/GaussianFluent/ && source /data/yinshaoxuan/miniconda3/etc/profile.d/conda.sh && conda activate GaussianFluent && python normal_vector_proc_nan.py --npy_path {npy_path} --output_folder {output_folder}'
                    run_command_realtime(command)


                    command = f'cd /data/yinshaoxuan/GaussianFluent/ && source /data/yinshaoxuan/miniconda3/etc/profile.d/conda.sh && conda activate GaussianFluent && python phong_model_wm_shs_15.py \
                                --npy_path {normal_path} --output_folder {output_folder}  --opacity_path {opacity_path} --valid_indice_path {valid_indice_path} \
                                    --shs_path {shs_path} --color_path {color_path} --attenuation_constant {attenuation_constant}'
                    run_command_realtime(command)
                    
                    

                # pcd_combined = o3d.io.read_point_cloud(normal_path)
                # normal = torch.from_numpy(np.array(pcd_combined.normals)).to("cuda")
                valid_indice = torch.from_numpy(np.load(os.path.join(output_folder, "pos_valid_indice.npy"))).to("cuda")
                colors = torch.from_numpy(np.load(os.path.join(output_folder, "phong_colors.npy"))).to("cuda").reshape(-1 , 3).float()

            

        
            # pos_ = pos
            # cov3D_ = cov3D
            # rot_ = rot
            # opacity_ =  opacity
            # shs_ = shs
            

        

            pos_ = pos
            cov3D_ = cov3D
            rot_ = rot
            opacity_ = opacity
            shs_ = shs
            

            
            
            colors_precomp = convert_SH(shs_, current_camera, gaussians, pos_, rot_)
            if color_flag:
                colors_precomp[ valid_indice ]  = colors.clone()



            
            
            rendering, raddi = rasterize(
                means3D=pos_,
                means2D=init_screen_points,
                shs=None,
                colors_precomp=colors_precomp.float(),
                opacities=opacity_, 
                scales=None,
                rotations=None,
                cov3D_precomp=cov3D_,
            )
            
            # scale = 0.5 
            # new_colors =  calculate_colors_per_point_optimized_torch(
            #         point_xy,                   # (N, 2) 所有点的原始2D坐标 (y, x)
            #         pos_,              # (N, 3) 所有点的原始3D坐标
            #         normal.float(),          # (N, 3) 所有点的原始3D法向量 (应预先归一化)
            #         valid_indice,          # (N,) 初始有效点布尔掩码
            #         colors_precomp,            # (N, 3) 所有点的基础漫反射颜色 (albedo) [0,1]
            #         current_camera2.camera_center,     # (3,) 全局点光源的3D位置
            #         opacity_,     # (N,) 所有点的原始基础不透明度 (alpha) [0,1]
            #         current_camera.camera_center,  # (3,) 全局相机/观察者的3D位置
            #         w=800 * scale, h=800 * scale,               # 投影平面的宽高 (用于分组)
            #         ka=0.8, kd=0.4, ks=0.1, shininess=32.0, # Blinn-Phong材质属性
            #         ambient_light_color_global= torch.Tensor([1.0, 1, 1]).cuda(),   # 全局环境光颜色
            #         light_color_intensity_global= torch.Tensor([1.0, 1.0, 1.0]).cuda() # 全局点光源的颜色/强度
            #     )
            
            
                        
            rendering, raddi = rasterize(
                means3D=pos_,
                means2D=init_screen_points,
                shs=None,
                colors_precomp=colors_precomp.float(),
                opacities=opacity_, 
                scales=None,
                rotations=None,
                cov3D_precomp=cov3D_,
            )
            


            cv2_img = rendering.permute(1, 2, 0).detach().cpu().numpy()
            cv2_img = cv2.cvtColor(cv2_img, cv2.COLOR_BGR2RGB)
            if height is None or width is None:
                height = cv2_img.shape[0] // 2 * 2
                width = cv2_img.shape[1] // 2 * 2
            assert args.output_path is not None
            _png_path = os.path.join(args.output_path, f"{frame }.png".rjust(8, "0"))
            if args.async_io:
                # The 255* multiply runs inside the worker (saves ~5-6ms of main-thread numpy work).
                # cv2_img is freshly allocated each frame so the buffer is owned by us.
                _io_futures.append(_io_executor.submit(_b3_write_png, _png_path, cv2_img))
            else:
                cv2.imwrite(_png_path, 255 * cv2_img)
            _frame_times.append(_time_mod.perf_counter() - _t_frame_start)
            if frame > end_frame:
                light_flag = False
    if _io_executor is not None:
        # [PhaseB.3] flush all pending writes before reporting / shutdown.
        import time as _time_mod_b3
        _t_drain = _time_mod_b3.perf_counter()
        for _f in _io_futures:
            _f.result()
        _io_executor.shutdown(wait=True)
        _drain_s = _time_mod_b3.perf_counter() - _t_drain
        print(f'[PhaseB.3] drained {len(_io_futures)} io futures in {_drain_s:.3f}s')
    _phasea_print_summary(_frame_times, _sim_only_times)
    try:
        _peak_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
        _reserved_mb = torch.cuda.max_memory_reserved() / (1024 * 1024)
        print(f"[PhaseB.1-VRAM] target_particles={args.target_particles} n_particles={mpm_solver.n_particles} peak_alloc_MB={_peak_mb:.1f} peak_reserved_MB={_reserved_mb:.1f}")
    except Exception as _e:
        print(f"[PhaseB.1-VRAM] could not query peak VRAM: {_e}")
    if args.render_img and args.compile_video:
        fps = 32
        os.system(
            f"ffmpeg -framerate {fps} -i {args.output_path}/%04d.png -c:v libx264 -s {width}x{height} -y -pix_fmt yuv420p {args.output_path}/output.mp4"
        )


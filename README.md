# gsfluent — 建筑物理仿真 + 浏览器实时回放

针对 3D Gaussian Splatting 场景的物理仿真工具。给一个 3DGS 训练好的建筑（或其他物体），选一个材料配方（jelly、demolition...），在浏览器里看着它实时形变。底层是 MPM（物质点法）仿真 + viser/WebGL 渲染。

> 状态：Linux + NVIDIA GPU 已验证，其他平台可尝试。仿真核心是 Python（PyTorch + NVIDIA Warp + Taichi），渲染走浏览器端 WebGL（viser），所以回放在任何带浏览器的机器上都能看。

[English version](README.en.md)

## 安装（一次性，约 5 分钟）

```bash
git clone https://github.com/Qervas/gsfluent_pkg
cd gsfluent_pkg
./setup.sh
```

`setup.sh` 会创建 conda 环境 `gsfluent`，装好对应版本的 PyTorch / Warp / Taichi，并从内置的 gaussian-splatting 子模块里编译两个 CUDA 扩展（`diff_gaussian_rasterization` 和 `simple-knn`）。重复运行安全。

依赖：

- conda（Miniconda / Anaconda / Mambaforge）
- NVIDIA GPU + 较新驱动（CUDA 12.x 运行时——通过 conda 或系统包管理器装）
- 约 5 GB 磁盘空间给 conda 环境

## 用法

### 仿真自己的建筑

```bash
./run-sim.sh <building_path> --recipe demolition
```

`<building_path>` 可以是：

- 一个 `.ply` 文件（脚本自动包装成仿真器要的目录结构）
- 一个 3DGS 模型目录（含 `point_cloud/iteration_*/point_cloud.ply` 的标准训练输出）

跑完之后浏览器自动打开 `localhost:8080`，仿真一边算，建筑一边形变。笔记本 GPU + 200k 粒子约 1 frame/sec，150 帧大概两分钟。

例子：

```bash
./run-sim.sh ~/projects/scan_3dgs/                 --recipe jelly
./run-sim.sh /tmp/quick_scan.ply                   --recipe demolition --particles 100000
./run-sim.sh ~/data/building_a/point_cloud.ply     --recipe jelly --output building_a_test
```

### 自带的配方

| 名字          | 效果                              | 备注                            |
| ------------- | --------------------------------- | ------------------------------- |
| `jelly`       | 软体晃动 / 缓慢回弹               | 第一次跑用这个                  |
| `demolition`  | 通过粒子顺序释放实现的塌楼        | 视觉冲击大；RTX 5070 约 2 分钟  |

### 改 / 加自己的配方

配方就是 `tools/recipes/` 下的 JSON 文件。每个配方包含材料参数、边界条件、相机角度、积分步长。改配方流程：

```bash
# 复制现有配方开始改
cp tools/recipes/jelly.json tools/recipes/my_recipe.json
# 编辑参数
vim tools/recipes/my_recipe.json
# 用新配方跑
./run-sim.sh <building_path> --recipe my_recipe
```

可以改的关键参数：

- `n_grid` — MPM 网格分辨率，越高越细，显存按平方增长
- `substep_dt` — 积分子步长，越小越稳但越慢
- `frame_num` — 总帧数（每帧 `frame_dt` 秒）
- `boundary_conditions` — 边界条件列表。目前验证过的两条路径：`release_particles_sequentially`（塌楼）、`particle_damping`（软体）
- 材质参数：密度、杨氏模量、泊松比、屈服应力等

完整参数说明见 [`tools/recipes/RECIPES.md`](tools/recipes/RECIPES.md)。

### 回放之前的结果（不重新仿真）

```bash
./run-viewer.sh work/fused/<run_name>/
```

把浏览器查看器接到任意一个装满 `frame_NNNN.ply` 的目录。

## 性能

| 组件         | 速度                                       | 实时？                |
| ------------ | ------------------------------------------ | --------------------- |
| 仿真         | 200k 粒子在 RTX 5070 上约 1 frame/sec      | 否——物理是瓶颈        |
| 浏览器渲染   | 24 fps 回放目标，渲染端有 200+ fps 余量    | 是——回放端实时        |

`--live` 模式给的是「仿真算到哪儿展示到哪儿」的渐进预览，不是「物理实时」。浏览器滑块随新帧到达自动延长（约每秒一帧）。

## 常用 CLI 参数

```
./run-sim.sh <input> [options]
  --recipe NAME         配方名，见 tools/recipes/
  --particles N         MPM 粒子数（默认 200000，越少越快）
  --output NAME         输出目录名（默认 <model>_<recipe>_<date>）
  --no-viewer           只仿真不开浏览器
  --port N              查看器端口（默认 8080）
  --dry-run             只打印命令不执行

./run-viewer.sh <dir> [--port N]
```

## 常见问题

`./setup.sh` 报 CUDA 不匹配 / 扩展编译失败
- `env.yml` 锁的是 PyTorch + CUDA 12.4。驱动不一致就改里面的 `--extra-index-url`（`cu121` / `cu128` / Blackwell sm_120 用 `cu132` nightly），删掉 conda 环境后重跑 `./setup.sh`。

第一次跑仿真卡很久
- 第一次启动 Warp + Taichi 要编译内核，30–90 秒是正常的。第二次起内核缓存在 `~/.cache/warp/`，启动很快。

Taichi 1.7.4 在 Blackwell（sm_120）上 `densify_grids` 卡死
- `sim_one.sh` 默认导出 `GSFLUENT_TI_ARCH=cpu`，把 Taichi 的（很轻量的）粒子填充步骤强制走 CPU。仿真主体仍由 Warp 跑在 CUDA 上。

浏览器一直空白
- 等 30 秒，等第一帧融合好；查看器只是在轮询目录。如果一直没东西，看 `work/output/<run>/fuse.log` 和 `work/output/<run>/viewer.log`。

## 目录结构

```
gsfluent_pkg/
├── README.md          # 中文版（默认）
├── README.en.md       # English
├── setup.sh           # 一次性安装（conda 环境 + CUDA 扩展）
├── run-sim.sh         # 主入口：丢模型，开浏览器
├── run-viewer.sh      # 回放已有结果
├── env.yml            # conda 环境定义
├── core/              # 仿真代码（gs_simulation, mpm_solver_warp, ...）
├── tools/
│   ├── sim_one.sh         # 仿真+融合编排（被 run-sim.sh 调用）
│   ├── fuse_to_full_ply.py
│   ├── view_points.py     # 浏览器查看器（点云）
│   ├── viewer_textured.py # 浏览器查看器（高斯泼溅，更重）
│   └── recipes/           # JSON 配方
└── work/              # 运行时生成：每次跑的仿真和融合输出
```

## 引用

- 3D Gaussian Splatting：Kerbl et al. 2023
- MPM 仿真：基于 NVIDIA Warp + Taichi
- 浏览器查看器：viser

# gsfluent — 动画 3DGS 序列的工作台

针对物理仿真后的 3D Gaussian Splatting 序列的浏览器工作台。挑序列、
拖时间轴、在点云和 splat 两种渲染模式间切换、绕模型转动相机。

仿真跑在服务器（`your-server`），本机只是查看器 + 任务接入层。本机
不需要 CUDA、PyTorch、Warp、Taichi——纯 Python 轻依赖。

[English README](README.en.md)

## 安装

纯 pip，不用 conda。用 PATH 上的任意 Python 即可，下面七个依赖装到
那个环境里。

```bash
git clone <repo>
cd gsfluent_pkg
./setup-view.sh
```

`setup-view.sh` 安装：

```
fastapi  uvicorn  pydantic  watchfiles  plyfile  numpy  viser
```

外加 `pip install -e ./server`，把 `gsfluent` CLI 装到 PATH 上。指定
Python 版本：

```bash
PYTHON=python3.11 ./setup-view.sh
```

构建生产版 SPA（不需要 HMR 的场景）：

```bash
cd frontend && npm install && npm run build
cp -r frontend/dist/* server/gsfluent/static/
```

## 运行

服务器（your-server）一次：

```bash
./setup-server.sh
./run-server.sh                    # 后端在 :8080
```

笔记本：

```bash
./setup-view.sh
GSFLUENT_SERVER=http://your-server:8080 ./run-laptop.sh
```

启动两个协作服务：

```
┌──────────────────┐  HTTP   ┌─────────────────────┐
│ gsfluent serve   │ ←─────→ │  React workbench    │
│ :8080            │         │  (浏览器)           │
│  - SPA + REST    │         │  ┌───────────────┐  │
│  - /api/stream   │         │  │ iframe :8091  │ ←─┐
│    (WS, Points)  │         │  │  viser splat  │   │  /set, /camera
└──────────────────┘         │  └───────────────┘   │  → :8092
                             └─────────────────────┘
                                                      │
                             ┌────────────────────────┴─┐
                             │  tools/viser_headless.py │
                             │  viser :8091, ctl :8092  │
                             └──────────────────────────┘
```

打开 `http://localhost:8080`（dev 模式打开 `:5173`）。左侧大纲选序列，
底部拖时间轴，右上角切换 **Points** 渲染（R3F + WebSocket 上的 int16
量化 xyz）和 **Splats** 渲染（viser iframe，由控制 API 驱动）。

## 数据放在哪

```
work/
├── library/
│   └── sequences/<name>/
│       ├── frames/frame_NNNN.ply   # 融合后的 3DGS 每帧（静止姿态 Z-up）
│       ├── frames.bin              # GSSQ 打包的 int16 xyz（Points 模式用）
│       ├── manifest.json
│       └── _meta.json
└── cache/
    └── viser/<name>.npz            # Splats 模式的播放缓存
```

序列是核心资产：融合后的每帧 splat ply，加上可选的二进制打包文件和
viser 缓存。两种填充方式：

1. **从服务器拉** — `rsync your-server:.../sequences/<name>/` 到
   `work/library/sequences/`，再 `python tools/batch_convert_to_npz.py`
   生成 viser 缓存。
2. **本地融合 sim_*.ply** — 把服务器的 sim 输出 rsync 下来后跑
   `python tools/fuse_to_full_ply.py`，依赖只有 numpy + plyfile（加
   `--knn_rotation` 时还需要 torch）。

服务器端的 `runner.py` 在每次 sim 跑完之后自动调用 `batch_convert_to_npz.py`
（幂等——只重建过期的 .npz）。笔记本上的 `sync_daemon` 随后把新生成的
.npz mirror 到本地。

## 渲染模式

| 模式 | 渲染器 | 传输 | 用途 |
|---|---|---|---|
| **Points** | R3F（three.js） | `/api/stream` WS, `PackedReader` 解 int16 xyz | 轻量检视；没有 viser 缓存也能用 |
| **Splats** | viser iframe | `POST /set`、`POST /camera` 到 `:8092` | 高质量 splat 渲染，做评审或 demo |

两种模式共用 Zustand store 里的 `currentFrameIdx` 和 `simRunName`，
时间轴和大纲切的是当前渲染器。模式切换不会重置播放状态。

## 配方（仿真参数）

`tools/recipes/*.json` 描述材料 + 边界 + 积分参数，由服务器端的仿真
脚本消费。schema 与 `your-server` 上的 `gs_simulation_building.py`
匹配。

```bash
ls tools/recipes/
# cluster_6_15_smash.json  demolition.json  jelly.json  earthquake.json  ...
cp tools/recipes/jelly.json tools/recipes/my_recipe.json
```

本地改完配方提交仿真要经过服务器——sim 提交流程见
`docs/ARCHITECTURE.md`（开发中）。

## 目录结构

```
gsfluent_pkg/
├── README.md                README.en.md      # 中英双语
├── setup-view.sh / run-laptop.sh         # laptop side
├── setup-server.sh / run-server.sh       # server side
├── docs/ARCHITECTURE.md     # 架构细节
├── server/                  # FastAPI + SPA 服务
│   └── gsfluent/
│       ├── api/             # /api/recipes, /api/runs, /api/sequences, /api/stream
│       └── core/            # library 扫描、manifest、runner、frame_stream
├── frontend/                # React + Vite + R3F SPA
│   └── src/components/viewport/
│       ├── SplatScene.tsx       # Points 模式（R3F）
│       └── ViserSplatScene.tsx  # Splats 模式（viser iframe）
├── tools/
│   ├── viser_headless.py        # viser + 控制 API（Splats 后端）
│   ├── batch_convert_to_npz.py  # 生成 work/cache/viser/*.npz
│   ├── sequence_to_viser_npz.py # 单序列转换器
│   ├── fuse_to_full_ply.py      # sim_*.ply + 参考 3DGS → frame_*.ply
│   ├── pack_sequence.py         # frame_*.ply → frames.bin（GSSQ int16）
│   ├── migrate_to_library.py    # 旧布局迁移到 work/library/
│   ├── vkgs_play.py             # 启动 vkgs fork 播一个序列
│   └── recipes/                 # JSON 配方
└── work/                    # 运行时数据（library / cache / uploads）
```

## 引用

- 3D Gaussian Splatting：Kerbl et al. 2023
- MPM 物理：NVIDIA Warp + Taichi（服务器端）
- Splat 播放：viser
- 工作台：React + Vite + React Three Fiber

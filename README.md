# gsfluent — 动画 3DGS 序列的工作台

针对物理仿真后的 3D Gaussian Splatting 序列的浏览器工作台。挑序列、
拖时间轴、在点云和 splat 两种渲染模式间切换、绕模型转动相机。

仿真跑在远端服务器（GPU 机），客户端只是查看器 + 任务接入层。客户端
不需要 CUDA、PyTorch、Warp、Taichi——纯 Python 轻依赖。

[English README](README.en.md)

## 架构：严格前后端分离

| | 服务器（GPU 机） | 客户端（你的机器） |
|---|---|---|
| 代码 | `server/`（FastAPI + 仿真 runner） | `frontend/`（React SPA） + `tools/`（viser、sync、Points WS） |
| 安装 | `./setup-server.sh` | `./setup-client.sh` |
| 运行 | `./run-server.sh` | `./run-client.sh` |
| Python 环境 | `server/.venv`（uv 管理），纯 API 依赖 | 同一份 lockfile + `[client]` extras（viser、numpy） |
| Node | 不需要 | 需要（Vite 构建） |

Python 依赖统一用 [uv](https://docs.astral.sh/uv/) 管理，`server/uv.lock`
入库——每次安装都解析到完全相同的版本。新接手的人装一次 uv 即可
（`curl -LsSf https://astral.sh/uv/install.sh | sh`），其余 setup
脚本会处理。

## 安装 + 运行

**服务器（首次）：**

```bash
ssh <server-host>
cd gsfluent_pkg && ./setup-server.sh
```

**服务器（每次）：**

```bash
./run-server.sh                    # API 在 :8080
```

**客户端（首次）：**

```bash
cd gsfluent_pkg && ./setup-client.sh
```

**客户端（每次）：**

```bash
SERVER_SSH=mygpu ./run-client.sh
```

### `SERVER_SSH` 是什么（一个例子）

`SERVER_SSH` 就是你 `ssh ` 后面那个**主机别名**——读自
`~/.ssh/config`：

```ssh-config
# ~/.ssh/config
Host mygpu
    HostName 10.20.30.40        # 或 gpu.lab.example.com
    User alice
    IdentityFile ~/.ssh/lab_key
    Port 22
```

配好这一段后，`ssh mygpu` 就能直接进服务器。
`SERVER_SSH=mygpu ./run-client.sh` 复用同一个别名，在后台开一条
**端口转发**的 SSH 连接：

```text
客户端（你的机器）                              服务器（mygpu）
───────────────────────                        ──────────────────
http://localhost:4173  ◄── vite preview        gsfluent serve
                            （SPA）               监听 :8080
                                                       ▲
                                                       │
                            ssh -N -L 8080:localhost:8080 mygpu
http://localhost:8080  ──────────────────────────────► 隧道出口
   （SPA 把 /api 代理到这里）                       （服务器的 loopback）
```

笔记本的 `:8080` 和服务器的 `:8080` 通过 SSH 焊在一起。SPA 调
`/api/*`，被 vite 代理到 `localhost:8080`——也就是隧道的客户端
端——也就是服务器的 `gsfluent serve`。整条链路全在 SSH 里跑，
不开公网端口。Ctrl-C 时连同隧道一起清理。

已有隧道或后端在 LAN 上直连？跳过 `SERVER_SSH`：

```bash
GSFLUENT_SERVER=http://server.lan:8080 ./run-client.sh
```

启动两个协作服务：

```
   SERVER (run-server.sh)             CLIENT (run-client.sh)

  ┌──────────────────┐               ┌──────────────────────────┐
  │ gsfluent serve   │   /api  HTTP  │ vite preview  :4173      │
  │ :8080            │ ◀────────────▶│  (serves frontend/dist/) │
  │  - REST + /api   │   走 SSH 隧道 │                          │
  │  - /api/stream   │               │ React workbench 在       │
  │    (WS, Points)  │               │  浏览器  ┌─────────────┐ │
  │                  │               │          │ iframe :8091│◀┐
  │ runner.py 起 MPM │               │          │ viser splat │ │
  │ 仿真             │               │          └─────────────┘ │
  └──────────────────┘               │                          │
         ▲                           │ tools/viser_headless.py  │
         │                           │   :8091 + ctl :8092 ─────┘
         │ sync_daemon 轮询          │ tools/sync_daemon.py
         │ /api/sequences            │ tools/local_stream.py
         └───────────────────────────┴─────  /set, /camera, /sync-status
```

`./run-client.sh` 之后打开 `http://localhost:4173`。左侧大纲选序列，
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
viser 缓存。正常流程下你不用手动填充：

1. `POST /api/runs` 触发仿真（或在 workbench 里点 Run）。
2. 服务器的 `runner.py` 依次跑 sim → fuse → `batch_convert_to_npz.py`，
   并写出 `_meta.json`。
3. 客户端的 `sync_daemon` 下一轮轮询时把 `.npz` + `_meta.json` mirror
   到本地 `work/` 目录。
4. 大纲自动出现新序列，viser 自动 reload。

## 渲染模式

| 模式 | 渲染器 | 传输 | 用途 |
|---|---|---|---|
| **Points** | R3F（three.js） | `/api/stream` WS, `PackedReader` 解 int16 xyz | 轻量检视；没有 viser 缓存也能用 |
| **Splats** | viser iframe | `POST /set`、`POST /camera` 到 `:8092` | 高质量 splat 渲染，做评审或 demo |

两种模式共用 Zustand store 里的 `currentFrameIdx` 和 `simRunName`，
时间轴和大纲切的是当前渲染器。模式切换不会重置播放状态。

## 配方（仿真参数）

`tools/recipes/*.json` 描述材料 + 边界 + 积分参数，由服务器端的仿真
脚本消费。schema 与服务器上配置的 sim 脚本（`$GSFLUENT_SIM_SCRIPT_RUNNER`
指向）匹配。

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
├── setup-client.sh / run-client.sh       # client side
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

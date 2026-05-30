# gsfluent

GaussianFluent 物理仿真工作台,前后端分离部署。

后端和 GPU 仿真跑在服务器上,前端跑在每个团队成员自己的客户端机器上,
splat 直接在浏览器内渲染(Spark + three.js,下载后播放)。后端通过
公网 NAT 端口对外暴露。

英文版见 [README.en.md](README.en.md)。

---

## 快速上手(团队成员)

本机要有 Python 3.10+ 和 Node 18+。不用 conda,不用 sudo。

```bash
git clone <repo> gsfluent_pkg
cd gsfluent_pkg/frontend
npm install      # 装 JS 依赖、构建 dist/
npm start        # 起 vite preview
```

浏览器会自动打开 `http://localhost:5173/`,Ctrl-C 一并停掉整套栈。

`npm install` 背后走的是 `frontend/scripts/install.mjs`(postinstall 钩子):
装 JS 依赖,然后跑 `vite build`。`npm start` 走的是
`frontend/scripts/start.mjs`,启动 vite preview(把 `/api/*` 代理到服务器),
共用一个 Ctrl-C 关停。不需要额外的 Python 进程。

后端地址默认走 `.env` 里的 `BACKEND_URL`。要换的话:

```bash
GSFLUENT_BACKEND_URL=http://your.host:port npm start
```

---

## 创作仿真:材料 × 场景 × 建筑

配方不再手写,而是由三个正交输入 **MATERIAL × SCENARIO × BUILDING**
合成。前端 Properties 面板顶部的 **Composer** 就是入口:选场景、选材料、
选建筑,后端 `POST /api/compose` 生成可直接跑的扁平配方。选好场景 + 材料,
点 **Run** 就行。

五个精选场景(都已用渲染视频验证过,在推荐的软材料 `watermelon` 下会有
明显的「楼塌了」效果):

| 场景         | 效果                                   |
| ---          | ---                                   |
| `earthquake` | 地基震动 → 整楼塌成废墟                 |
| `wrecking`   | 中部侧向撞击(地基固定)→ 解体          |
| `topple`     | 顶部沿薄轴拖拽 → 像多米诺一样倒下       |
| `burst`      | 核心四块向外炸开 → 结构爆裂             |
| `demolish`   | 两侧对撞切断底部 → 直接砸塌并碎裂       |

每个场景带 `recommended_material`;剧烈场景对刚性材料(jelly/plasticine)
会数值爆掉(出网格 → CUDA 崩溃,这是物理本身,不是 bug),所以都推荐软的
`watermelon`。换场景时 UI 会自动把材料切到推荐值,不匹配时给提示。原来的
扁平参数面板(Material / Solver / Forces / …)还在,作为合成配方之上的
折叠「高级覆盖」。

> 合成的配方只存在内存里(带 `_composed_from` 溯源块),**不是**已保存的
> 服务端配方。已保存的配方是扁平材料 demo + `★` 用户预设。完整 HTTP
> 参考(含 compose 端点):[`docs/API.md`](docs/API.md)。

---

## 架构

```
┌─────── 团队成员客户端 ─────────────────────┐
│                                            │
│  浏览器  →  http://localhost:5173/         │
│             (vite preview 提供 dist/)      │
│                                            │
│  vite preview :5173                        │
│   ├─ /api/*  → proxy → 服务器 :24701       │
│   └─ /       → frontend/dist/              │
│                                            │
│  SPA (SplatScene)                          │
│   浏览器内下载 + 渲染 splat                │
│   (Spark + three.js,无额外进程)           │
│                                            │
└────────────────┬───────────────────────────┘
                 │ HTTP /api/*  (公网 NAT)
                 ▼
┌─────── GPU 服务器 ─────────────────────────┐
│                                            │
│  公网入口  your-backend:port               │
│             │ (NAT)                        │
│             ▼                              │
│  v1 backend  0.0.0.0:7869                  │
│   ├─ /api/*       (REST,见 docs/API.md)   │
│   └─ runner       (启动 MPM 仿真子进程)    │
│                                            │
│  GaussianFluent 仿真栈 (torch + warp +     │
│   taichi,A100)                             │
│                                            │
│  work/library/sequences/<run>/*.ply        │
│                                            │
└────────────────────────────────────────────┘
```

splat 数据通过和 REST API 同一条 HTTP 通道从服务器下载,由浏览器内的
`SplatScene` 本地渲染。仿真结果以 PLY 帧序列存在服务器上,前端按需拉取。

---

## 服务端运维

后端进程由 systemd unit 托管,unit 文件在 `deploy/`(生产用
`gsfluent-backend.service`,开发机用 `gsfluent-backend.dev.service`)。
`Type=notify` + `WatchdogSec=30s` 能检测到卡死;每次启动会调用
`recover_on_boot()` 把中断的 run 标成 INTERRUPTED。安装步骤见
[`deploy/README.md`](deploy/README.md)。

```bash
# 生产
sudo systemctl enable --now gsfluent-backend.service
sudo systemctl status gsfluent-backend.service

# 开发机(per-user systemd)
systemctl --user enable --now gsfluent-backend.service
systemctl --user status gsfluent-backend.service
```

端口绑定:

| 进程        | 监听              | 公网映射                  |
|-------------|-------------------|---------------------------|
| v1 backend  | `0.0.0.0:7869`    | `your-backend:port` (NAT) |

仿真后处理(把 ply 转 gsq 缓存、打包 frames.bin)在 `server/tools/` 下,
按需 ssh 进服务器手动跑。

日志走 journald:`journalctl -u gsfluent-backend -f -o json | jq -r '.MESSAGE | fromjson?'`(开发机加 `--user`)。后端写的是
JSON event,可直接 `jq` 过滤 `run_id` / `event`。

Python 解释器在 `.env` 里通过 `GSFLUENT_API_PYTHON` / `GSFLUENT_SIM_PYTHON`
配,需要改就改 `.env`。

---

## 限额配置(防止跑飞)

后端在 API 边界对 recipe 做 cap 校验,违规直接 422 拒绝,不会拉起子进程。
默认值在 `server/gsfluent/core/limits.py:DEFAULT_*`,可通过环境变量改写:

| 环境变量                          | 默认值      | 含义                                 |
|-----------------------------------|-------------|--------------------------------------|
| `GSFLUENT_MAX_PARTICLE_COUNT`     | `500000`    | recipe 单次允许的最大粒子数          |
| `GSFLUENT_MAX_WALL_TIME_SEC`      | `3600`      | sim 最长 wall-time 秒数(超时 PG-kill)|
| `GSFLUENT_MAX_RECIPE_BYTES`       | `16384`     | recipe JSON 体积上限(防 DoS)       |

cap 触发返回的错误结构:

```json
{
  "error": {
    "kind": "cap_exceeded.particle_count",
    "message": "Particle count 800000 exceeds limit 500000",
    "details": { "requested": 800000, "limit": 500000 },
    "trace_id": "01H8K2P..."
  }
}
```

---

## 组件分层

后端按六层 Protocol 切分,每层一个 `typing.Protocol` 接口 + 一个
当前的具体实现,在 `server/gsfluent/composition.py` 一次性接装:

| 层 | Protocol                                    | 当前实现                                       |
|----|---------------------------------------------|------------------------------------------------|
| L0 | (HTTP)                                      | `server/gsfluent/api/*.py`                     |
| L1 | `protocols/runs.py:RunManager`              | `core/run_manager.py:AsyncioRunManager`        |
| L2 | `protocols/sim.py:SimulationEngine`         | `core/sim_engines/mpm.py:MPMSimulationEngine`  |
| L3 | `protocols/fuse.py:Fuser`                   | `core/fusers/knn_kabsch.py:KNNKabschFuser`     |
| L4 | `protocols/cache.py:CacheCodec`             | `core/codecs/gsq.py:GSQCodec`                  |
| L5 | `protocols/storage.py:Storage`              | `storage/filesystem.py:FilesystemStorage`      |
| L6 | `protocols/observability.py:EventEmitter`   | `observability/jsonlog.py:StdlibJSONEmitter`   |

每个 Protocol 有一套 conformance 测试(`server/tests/protocols/test_*_conformance.py`),
任何新实现替换进来时跑一次就能确认契约。详细架构说明见
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## 仓库结构

| 路径                | 作用                                                                 |
|---------------------|----------------------------------------------------------------------|
| `frontend/`         | React + Vite SPA。`npm install` / `npm start` 入口都在这。           |
| `frontend/scripts/` | Node 启动脚本 `install.mjs` / `start.mjs` / `clean.mjs`。            |
| `frontend/python/`  | 客户端 Python(历史遗留):`vkgs_play.py`。(`viser_headless.py`、`sync_daemon.py` 已移除) |
| `frontend/patches/` | 上游渲染补丁(no-cull、point precision)。                            |
| `server/`           | FastAPI v1 backend,只在服务器跑。六层 Protocol + composition root 在 `gsfluent/`。 |
| `server/tools/`     | 仿真包装薄壳(`run_sim.sh` 现在 ≈20 行 conda-activate),其余 PLY/打包脚本是 `core/` 实现的 CLI 包装。 |
| `server/recipes/`   | 内置仿真 recipe JSON。                                               |
| `server/patches/`   | 上游 GaussianFluent 仿真补丁。                                       |
| `deploy/`           | systemd unit (`gsfluent-backend.service` + `gsfluent-backend.dev.service`) 和部署手册 (`README.md`)。 |
| `docs/`             | API 参考、架构文档。                                                 |
| `work/`             | 运行时数据(已 gitignore):`library/sequences/<run>/`、`cache/splats/*.gsq`。 |

---

## API 参考

后端一共 31 个 REST 接口 + 1 个 WS:

- 英文:[`docs/API.md`](docs/API.md)
- 中文:[`docs/API.zh.md`](docs/API.zh.md)

主要端点:`/api/health`、`/api/recipes`、`/api/models`、`/api/sequences`、
`/api/runs`、`/api/runs/{name}/log`、`/api/stream` (WS) 等等。没有 auth,
谁连得到端口就能调。

架构详细描述见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## 故障排查

| 现象                                | 一句话排查                                                                 |
|-------------------------------------|----------------------------------------------------------------------------|
| 前端打不开 / `:5173` 报错           | 端口被占,`lsof -i :5173`;或者 `UI_PORT=5174 npm start`                   |
| Splat 没画面                        | 看浏览器控制台报错;确认 `/api/sequences/{name}/cache/splats.gsq` 可访问      |
| `/api/*` 全部 502 / connection refused | 服务器后端挂了,跑 `systemctl status gsfluent-backend.service`(或加 `--user`)         |
| 提交仿真后立刻 error                | `curl <backend>/api/runs/<name>/log?offset=0` 看 stdout                    |
| 本机 `.venv/` 坏了                  | `rm -rf .venv/ frontend/dist/ && cd frontend && npm install`               |

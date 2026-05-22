# gsfluent

GaussianFluent 物理仿真工作台,前后端分离部署。

后端和 GPU 仿真跑在服务器上,前端和 viser splat 渲染跑在每个团队成员
自己的客户端机器上。后端通过公网 NAT 端口对外暴露;splat 数据走本地
loopback,不进网络。

英文版见 [README.en.md](README.en.md)。

---

## 快速上手(团队成员)

本机要有 Python 3.10+ 和 Node 18+。不用 conda,不用 sudo。

```bash
git clone <repo> gsfluent_pkg
cd gsfluent_pkg/frontend
npm install      # 自动建 .venv/、装 pip 依赖、构建 dist/
npm start        # 起 viser_headless + vite preview
```

浏览器会自动打开 `http://localhost:5173/`,Ctrl-C 一并停掉整套栈。

`npm install` 背后走的是 `frontend/scripts/install.mjs`(postinstall 钩子):
在仓库根建 `.venv/`,pip 装 `viser`、`fastapi`、`uvicorn`、`httpx`、
`eval_type_backport`,然后 `vite build`。`npm start` 走的是
`frontend/scripts/start.mjs`,用 `concurrently` 拉起 viser_headless + vite
preview,共用一个 Ctrl-C 关停。

后端地址默认走 `.env` 里的 `BACKEND_URL`。要换的话:

```bash
GSFLUENT_BACKEND_URL=http://your.host:port npm start
```

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
│  viser_headless                            │
│   ├─ 127.0.0.1:8091 (splat WS)             │
│   └─ 127.0.0.1:8092 (control API)          │
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

splat WebSocket 全程走本机 127.0.0.1,不吃公网带宽。仿真结果以 PLY
帧序列存在服务器上,前端通过 REST API 按需拉。

---

## 服务端运维

后端进程由 `server/supervise.sh` 托管,在服务器上跑:

```bash
bash server/supervise.sh up      # 启动 v1 backend,挂了自动重启
bash server/supervise.sh status  # 看当前 PID
bash server/supervise.sh stop    # 停掉
```

端口绑定:

| 进程        | 监听              | 公网映射                  |
|-------------|-------------------|---------------------------|
| v1 backend  | `0.0.0.0:7869`    | `your-backend:port` (NAT) |

仿真后处理(把 ply 转 gsq 缓存、打包 frames.bin)在 `server/tools/` 下,
按需 ssh 进服务器手动跑。

日志在 `/path/to/gsfluent_pkg/work/logs/{v1,supervisor}.log`。

Python 解释器在 `.env` 里通过 `GSFLUENT_API_PYTHON` / `GSFLUENT_SIM_PYTHON`
配,需要改就改 `.env`。

---

## 仓库结构

| 路径                | 作用                                                                 |
|---------------------|----------------------------------------------------------------------|
| `frontend/`         | React + Vite SPA。`npm install` / `npm start` 入口都在这。           |
| `frontend/scripts/` | Node 启动脚本 `install.mjs` / `start.mjs` / `clean.mjs`。            |
| `frontend/python/`  | 客户端跑的 Python:`viser_headless.py`、`sync_daemon.py`、`vkgs_play.py`。 |
| `frontend/patches/` | 上游 viser 包的渲染补丁(no-cull、point precision)。                |
| `server/`           | FastAPI v1 backend,只在服务器跑。REST 路由 + runner 在 `gsfluent/`。 |
| `server/tools/`     | 仿真包装(`run_sim.sh`)、PLY → gsq 转换(`pack_splats.py`)、fuse、迁移等服务端脚本。   |
| `server/recipes/`   | 内置仿真 recipe JSON。                                               |
| `server/patches/`   | 上游 GaussianFluent 仿真补丁。                                       |
| `docs/`             | API 参考、架构文档。                                                 |
| `work/`             | 运行时数据(已 gitignore):`library/sequences/<run>/`、`cache/viser/*.gsq`。 |

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
| Splat 没画面                        | viser 没起,`curl http://127.0.0.1:8092/state`                             |
| `/api/*` 全部 502 / connection refused | 服务器后端挂了,在服务器跑 `bash server/supervise.sh status`            |
| 提交仿真后立刻 error                | `curl <backend>/api/runs/<name>/log?offset=0` 看 stdout                    |
| 本机 `.venv/` 坏了                  | `rm -rf .venv/ frontend/dist/ && cd frontend && npm install`               |

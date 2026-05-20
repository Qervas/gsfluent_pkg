# gsfluent

GaussianFluent 物理仿真工作台,前后端分离部署。

后端 + GPU 仿真跑在 your server,前端 + viser splat 渲染跑在每位团队成员
自己的电脑上。后端通过公网 NAT 端口对团队暴露;splat 数据走电脑
本地 loopback,不进网络。

英文版:[README.en.md](README.en.md)。

---

## 快速上手(团队成员)

需要本机有 Python 3.10+、Node 18+。无需 conda,无需 sudo。

```bash
git clone <repo> gsfluent_pkg
cd gsfluent_pkg/frontend
npm install      # 自动建 .venv/、装 pip 依赖、构建 dist/
npm start        # 起 viser_headless + vite preview
```

浏览器自动打开 `http://localhost:5173/`。Ctrl-C 一并停掉整套栈。

`npm install` 调用 `scripts/_install.sh`:在仓库根建 `.venv/`
并 pip 装 `viser`, `fastapi`, `uvicorn`, `httpx`, `eval_type_backport`,
然后 `npm ci` + `vite build`。`npm start` 调用 `scripts/_start.sh`,
拉起两个本地进程并代理 `/api/*` 到 your server 后端。

默认后端地址为 `${BACKEND_URL}`,需要覆盖时:

```bash
GSFLUENT_BACKEND_URL=http://your.host:port npm start
```

---

## 架构

```
┌─────── 团队成员电脑 ───────────────────────┐
│                                            │
│  浏览器  →  http://localhost:5173/         │
│             (vite preview 提供 dist/)      │
│                                            │
│  vite preview :5173                        │
│   ├─ /api/*  → proxy → your server :24701        │
│   └─ /       → frontend/dist/              │
│                                            │
│  viser_headless                            │
│   ├─ 127.0.0.1:8091 (splat WS)             │
│   └─ 127.0.0.1:8092 (control API)          │
│                                            │
└────────────────┬───────────────────────────┘
                 │ HTTP /api/*  (公网 NAT)
                 ▼
┌─────── your server GPU 主机 ─────────────────────┐
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

splat WebSocket 全程在 laptop 127.0.0.1,不占公网带宽。
仿真结果以 PLY 帧序列保存在 your server,通过 REST API 按需下发。

---

## 服务端运维(your server)

后端进程由 `tools/supervise.sh` 托管,在 your server 上执行:

```bash
bash tools/supervise.sh up      # 启动 viser_headless + v1 backend,守护重启
bash tools/supervise.sh status  # 看当前 PID
bash tools/supervise.sh stop    # 停掉
```

绑定:

| 进程            | 监听                     | 公网映射                     |
|-----------------|--------------------------|------------------------------|
| v1 backend      | `0.0.0.0:7869`           | `your-backend:port` (NAT)    |
| viser_headless  | `127.0.0.1:8091` / `:8092` | 不暴露(仅 your server 本机回调) |

日志:`/path/to/gsfluent_pkg/work/logs/{v1,viser_headless,supervisor}.log`。

仿真 Python 在 `supervise.sh` 顶部以常量配置,改路径请在那里改。

---

## 仓库结构

| 路径          | 作用                                                                 |
|---------------|----------------------------------------------------------------------|
| `frontend/`   | React + Vite SPA。`npm install` / `npm start` 入口都在这里。         |
| `server/`     | FastAPI v1 backend,服务于 your server。包含 REST 路由 + runner。          |
| `tools/`      | 仿真包装、PLY → npz 转换、`viser_headless.py`、`supervise.sh`。     |
| `scripts/`    | 本地启动脚本 `_install.sh` / `_start.sh`(由 npm 调用)。            |
| `docs/`       | API 参考、架构文档、补丁说明。                                       |
| `patches/`    | 上游 viser 包的渲染补丁(no-cull、point precision)。                |
| `work/`       | 运行时数据:`library/sequences/<run>/`、`cache/viser/*.npz` 等。     |

---

## API 参考

后端共 31 个 REST 端点 + 1 个 WS:

- 英文:[`docs/API.md`](docs/API.md)
- 中文:[`docs/API.zh.md`](docs/API.zh.md)

总览:`/api/health`、`/api/recipes`、`/api/models`、`/api/sequences`、
`/api/runs`、`/api/runs/{name}/log`、`/api/stream` (WS) 等。无鉴权,
按 IP 可达性放行。

架构详述:[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## 故障排查

| 现象                                | 一句话排查                                                                 |
|-------------------------------------|----------------------------------------------------------------------------|
| 前端打不开 / `:5173` 报错           | 端口被占,`lsof -i :5173`;或 `UI_PORT=5174 npm start`                     |
| Splat 不出画面                      | viser 没起,`curl http://127.0.0.1:8092/state`;your server 看 `supervise.sh status` |
| `/api/*` 全部 502 / connection refused | your server 后端挂了,your server 执行 `bash tools/supervise.sh status`                |
| 提交仿真后立刻 error                | `curl <backend>/api/runs/<name>/log?offset=0` 看 stdout                    |
| 本机 `.venv/` 损坏           | `rm -rf .venv/ frontend/dist/ && cd frontend && npm install`        |

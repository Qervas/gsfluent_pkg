# gsfluent — 部署与使用指南

GaussianFluent 物理仿真的工作台。提供：

- **3DGS 模型管理**（上传、列表、删除）
- **配方驱动的仿真提交**（材料、边界条件、积分参数 → 远端 MPM 仿真）
- **序列回放**（仿真结果作为 3DGS 序列，浏览器内交互式播放）
- **HTTP REST API**（团队成员可通过 IP 直接调用，无需 SSH）

**部署模式：本地渲染（推荐）。** 服务端跑 API + SPA + 仿真；
viser splat 渲染器和 sync_daemon 跑在团队成员各自的电脑上。
splat 数据通过 sync_daemon 一次性拉到本地（每个序列 ~2.8 GB），
之后浏览器播放走 loopback，不再吃网络带宽。

服务端只对外开 **一个端口**（API），不暴露 viser；viser 绑在每个
团队成员电脑的 127.0.0.1，无安全暴露。

如果团队全员都在数据中心同一个 LAN 上，可以走"服务端渲染"模式
（viser 也跑服务端、对外开 8091/8092 端口），更简单但需要 1 Gbps
带宽支撑 splat WebSocket。本文档以推荐的本地渲染模式为主。

---

## 系统架构

```
┌──────────── 服务器（GPU 机） ────────────────┐
│                                             │
│   gsfluent serve         :18080  [对外]      │
│   ├─ REST API            /api/*             │
│   ├─ 内置 SPA            /                  │
│   └─ runner（启动仿真）  内部调用            │
│                                             │
│   仿真环境               GaussianFluent      │
│   (MPM + Warp + Taichi)                     │
│                                             │
│   work/cache/viser/*.npz  [仿真结果，被拉取] │
│                                             │
└──────────────▲──────────────────────▲───────┘
               │                       │
               │ /api/* (HTTP)         │ /api/sequences/<n>/cache/viser.npz
               │                       │ （sync_daemon 下载）
               │                       │
┌──────────────┴─────────┐  ┌──────────┴───────────────┐
│ 团队成员的电脑          │  │ 团队成员的电脑            │
│                        │  │                          │
│ 浏览器                  │  │ viser_headless           │
│  ├─ http://server:18080│  │  127.0.0.1:8091 (WS)     │
│  │  (SPA + API 代理)   │  │  127.0.0.1:8092 (控制)   │
│  └─ iframe localhost:  │  │  读 work/cache/viser/*   │
│     8091 (splat 渲染)  │  │                          │
│                        │  │ sync_daemon              │
│ SPA 调用 :8092 控制    │  │  轮询服务器 /api/sequences│
│                        │  │  下载新 .npz 到本地       │
└────────────────────────┘  └──────────────────────────┘
```

数据流：服务器跑仿真 → 写 .npz → sync_daemon 拉到本地 →
viser_headless 读本地 .npz → 浏览器 WebSocket 接 viser → WebGL 渲染。

只有 `:18080`（服务器）需要对团队成员开放。viser 全程在 127.0.0.1。

---

## 一、服务端部署

### 1.1 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| OS | Linux | 已验证 Ubuntu 22.04 |
| Python（API） | 3.11+ | API 服务运行环境 |
| Python（仿真） | 3.9 | GaussianFluent 仿真环境（torch + warp + taichi） |
| GPU | NVIDIA, CC ≥ 8.0 | A100 已验证 |
| CUDA Toolkit | 11.5+ | 驱动 ≥ 525 |
| 磁盘 | 50 GB+ | 每个仿真序列约 2.8 GB（.npz 缓存） |

### 1.2 安装

仓库已经放置在服务器：`$GSFLUENT_PKG_ROOT_tmp/`
（包含已构建的前端 `frontend/dist/`、Python 虚拟环境 `server/.venv/`）。

如果从零部署：

```bash
git clone <repo> /opt/gsfluent_pkg
cd /opt/gsfluent_pkg

# 1. 创建 API 用的 conda env（已有则跳过）
conda create -n gsfluent-api python=3.11 -y
conda activate gsfluent-api
pip install -e ./server[client]   # 含 viser、numpy 等客户端依赖

# 2. 构建前端
cd frontend && npm ci && npm run build && cd ..

# 3. 配置 GaussianFluent 仿真环境
#    （torch + warp 0.10 + taichi 1.5，需要 CUDA 编译）
#    详见 GaussianFluent 自身的 README
```

### 1.3 启动服务

仓库根目录已提供启动脚本 `start-gsfluent-server.sh`：

```bash
cd $GSFLUENT_PKG_ROOT_tmp
./start-gsfluent-server.sh
```

该脚本会：
1. 导出 `GSFLUENT_SIM_HOME` 和 `GSFLUENT_SIM_PYTHON` 环境变量
2. 后台启动 `gsfluent serve --host 0.0.0.0 --port 18080 --no-browser`
3. 日志写到 `/tmp/gsfluent_server.log`

**服务端只跑 API + SPA + 仿真。** 不在服务端启 viser_headless
（推荐的本地渲染模式下，viser 跑在团队成员电脑上，见第二章）。

### 1.4 端口与防火墙

| 端口 | 用途 | 必须开放 |
|---|---|---|
| 18080 | API + SPA | ✓ 团队访问 |

防火墙开放（示例 `ufw`）：

```bash
sudo ufw allow 18080/tcp
```

viser 的 8091 / 8092 **不暴露**——它们绑在团队成员各自电脑的
127.0.0.1。

### 1.5 验证服务

```bash
curl http://<服务器IP>:18080/api/health
# 期望返回：{"status":"ok","pkg_root":"$GSFLUENT_PKG_ROOT_tmp"}
```

---

## 二、客户端使用

### 2.1 仅 API 调用（不需要 splat 回放）

只要提交仿真、查询状态、下载结果 .ply：浏览器或 curl/Python 直接打
`http://<服务器IP>:18080/`，不用装任何东西。见 2.3 节的脚本示例。

### 2.2 完整工作台（含 splat 实时回放，推荐）

要在浏览器里交互式播放仿真序列，需要在团队成员**自己的电脑上**
跑两个本地服务：

- `tools/sync_daemon.py` — 从服务器拉新生成的 .npz 到本地缓存
- `tools/viser_headless.py` — 把 .npz 喂给浏览器的 WebGL 渲染器

整个流程封装在 `run-client.sh`：

```bash
# 一次性设置：装 Python 依赖、构建前端
cd gsfluent_pkg && ./setup-client.sh

# 每次运行：指向服务器，启动客户端栈
GSFLUENT_SERVER=http://<服务器IP>:18080 ./run-client.sh
```

启动后：

- 浏览器自动打开 `http://localhost:4173/`（vite 在本地预览构建好的 SPA）
- 后台跑：vite preview、viser_headless（绑 127.0.0.1:8091/8092）、sync_daemon
- SPA 的 `/api/*` 请求经 vite 代理到 `<服务器IP>:18080`
- splat iframe 接 `localhost:8091`，走 loopback，无网络延迟

Ctrl-C 一并关闭整个客户端栈。

依赖：
- Python 3.11+（运行 viser / sync_daemon）
- Node.js 18+（仅首次 `setup-client.sh` 用，跑过一次后可以不留）
- 磁盘空间：每个完成的仿真序列约 2.8 GB 本地缓存

支持的浏览器：Chrome / Edge / Firefox 最新版（需 WebGL 2.0 + WebSocket）。

### 2.3 API（脚本化）

工作台所有功能都对应一个 HTTP 接口。常用：

#### 列出可用配方

```bash
curl http://<服务器IP>:18080/api/recipes
```

返回：
```json
[
  {"name": "jelly",       "source": "builtin"},
  {"name": "metal",       "source": "builtin"},
  {"name": "sand",        "source": "builtin"},
  {"name": "foam",        "source": "builtin"},
  {"name": "plasticine",  "source": "builtin"},
  {"name": "earthquake",  "source": "builtin"},
  {"name": "demolition",  "source": "builtin"},
  {"name": "wrecking",    "source": "builtin"}
]
```

#### 列出已注册的模型

```bash
curl http://<服务器IP>:18080/api/models
```

#### 列出已完成的仿真序列

```bash
curl http://<服务器IP>:18080/api/sequences
```

#### 获取配方的完整参数

```bash
curl http://<服务器IP>:18080/api/recipes/jelly
```

#### 提交一次仿真

```bash
# 1. 读取配方
curl -s http://<服务器IP>:18080/api/recipes/jelly -o /tmp/recipe.json

# 2. 构造请求体
python3 -c '
import json
recipe = json.load(open("/tmp/recipe.json"))
print(json.dumps({
    "run_name": "my_test_run_001",
    "model_path": "$GSFLUENT_SIM_HOME/model/cluster_6_15",
    "recipe_data": recipe["data"],
    "recipe_source": "jelly",
    "particles": 200000
}))' > /tmp/req.json

# 3. 提交
curl -X POST http://<服务器IP>:18080/api/runs \
     -H "Content-Type: application/json" \
     -d @/tmp/req.json
# 返回：{"run_id":"<id>","run_name":"my_test_run_001"}
```

#### 查询活跃运行

```bash
curl http://<服务器IP>:18080/api/runs
# 返回所有运行的列表，含 state（running/done/error）
```

#### 实时跟踪日志

```bash
RUN=my_test_run_001
curl "http://<服务器IP>:18080/api/runs/${RUN}/log?offset=0"
# 返回 {"content": "...", "offset": N, "size": N}
# 下次轮询时把上次的 offset 作为新的 offset 参数即可获得增量
```

#### 取消运行

```bash
curl -X DELETE http://<服务器IP>:18080/api/runs/<run_id>
```

#### 下载某帧的 PLY

```bash
curl "http://<服务器IP>:18080/api/runs/${RUN}/frame/0.ply" -o frame_0000.ply
```

### 2.4 Python 客户端示例

```python
import requests, json, time

API = "http://<服务器IP>:18080"

# 1. 拿配方
recipe = requests.get(f"{API}/api/recipes/jelly").json()

# 2. 提交仿真
resp = requests.post(f"{API}/api/runs", json={
    "run_name": "py_demo_001",
    "model_path": "$GSFLUENT_SIM_HOME/model/cluster_6_15",
    "recipe_data": recipe["data"],
    "recipe_source": "jelly",
    "particles": 200000,
})
print("submitted:", resp.json())

# 3. 跟踪日志
offset = 0
while True:
    runs = requests.get(f"{API}/api/runs").json()
    me = next((r for r in runs if r["name"] == "py_demo_001"), None)
    if me is None:
        break
    log = requests.get(f"{API}/api/runs/py_demo_001/log",
                       params={"offset": offset}).json()
    if log["content"]:
        print(log["content"], end="")
    offset = log["offset"]
    if me["state"] != "running":
        print(f"\n[final state: {me['state']}]")
        break
    time.sleep(1)
```

---

## 三、可用配方

服务器仿真脚本 `gs_simulation_building.py` 跑实际 MPM。
工作台只是发请求 + 看结果。

### 材料类（同一栋楼、不同物理）

| 名称 | 物理行为 | 关键参数 |
|---|---|---|
| `jelly` | 软体晃动、轻微反弹 | E=5000, density=1 |
| `metal` | 刚性、受载凹陷、保持形状 | E=50000, density=3 |
| `sand` | 颗粒堆积，无内聚力 | Drucker-Prager 塑性 |
| `foam` | 软泡沫，缓慢回弹 | E=1000, density=0.3 |
| `plasticine` | 塑形粘土，永久变形 | yield_stress=500 |

### 场景类（带外力/碰撞器作用于建筑）

| 名称 | 行为 | 实现 |
|---|---|---|
| `demolition` | 顶部粒子顺序释放，建筑自上而下坍塌 | `release_particles_sequentially` |
| `earthquake` | 4 个 cuboid 碰撞器在地基横向往返推 | 4× `cuboid` 横向速度交替 |
| `wrecking` | 中部高度横向冲击（破坏球模式） | 1× `cuboid` 横向速度 |

### 已删除的配方

`meteor`（垂直冲击）和 `uplift`（地面上顶）在 cluster_6_15 模型上
触发 MPM 求解器的 `Warp CUDA error 700: illegal memory access`，
原因是它们的 cuboid 边界在 t=0 时刻与现有几何重叠，瞬间速度注入
导致应力集中数值爆破。当前版本不再提供。

---

## 四、运行时数据布局

```
work/
├── library/sequences/<run_name>/
│   ├── frames/frame_NNNN.ply   ← 融合后的每帧 3DGS（Z-up，规范化）
│   ├── manifest.json           ← 仿真元数据（起止时间、状态、粒子数）
│   ├── _meta.json              ← 序列展示元数据（帧数、bbox、模型来源）
│   ├── _effective_recipe.json  ← 服务器实际使用的配方（含坐标平移）
│   └── run.log                 ← 服务器仿真完整日志
└── cache/viser/<run_name>.npz  ← 浏览器 splat 模式播放缓存
```

正常使用流程下不需要手动操作这些文件：

1. `POST /api/runs` 触发仿真
2. `runner.py` 串行跑 sim → fuse → npz 缓存构建
3. 客户端浏览器自动看到新序列

---

## 五、自定义配方

```bash
# 复制现有配方
cp tools/recipes/jelly.json tools/recipes/my_recipe.json

# 编辑 — 调材料参数（E, nu, density, yield_stress）或边界条件
vim tools/recipes/my_recipe.json
```

工作台下次启动会自动列出。也可以通过 API 直接提交带 `recipe_data`
的请求，跳过持久化配方文件。

配方关键字段：

- `n_grid`（默认 150）：MPM 网格分辨率，越高细节越多但显存呈平方增长
- `substep_dt`（默认 1e-4）：内积分步长，越小越稳但越慢。当前版本会自动 clamp 到 `min(recipe, CFL)`，所以稍大也不会立刻爆
- `frame_num`（默认 150）：总帧数（约 5 秒 @ 30 fps）
- `g`：重力，默认 `[0, 0, -15]`
- `material`：`jelly` | `metal` | `sand` | `foam` | `snow` | `plasticine` | `watermelon`
- `boundary_conditions`：列表。`bounding_box` 和 `surface_collider` 是固定的；场景配方在此加 `cuboid`（移动碰撞器）或 `release_particles_sequentially`

---

## 六、故障排查

### "ERROR: sim interpreter not on PATH: $GSFLUENT_SIM_PYTHON=python"

服务器启动时没设仿真环境的 Python。用 `start-gsfluent-server.sh`
启动会自动设好。手动启动时确保：

```bash
export GSFLUENT_SIM_PYTHON=$CONDA_ROOT/envs/GaussianFluent/bin/python
export GSFLUENT_SIM_HOME=$GSFLUENT_SIM_HOME
```

### "no sim environment installed at $GSFLUENT_SIM_HOME"

`GSFLUENT_SIM_HOME` 指向不存在的目录。检查路径，或确认 GaussianFluent
仓库已 clone 到对应位置。

### 浏览器打开 `http://<服务器IP>:18080/` 显示不出来

1. 服务是否启动：`curl http://<服务器IP>:18080/api/health`
2. 防火墙是否开放：`sudo ufw status | grep 18080`
3. `gsfluent serve` 是否绑定到 `0.0.0.0`（不是 `127.0.0.1`）

### Splat 模式不出画面

viser_headless 只在客户端栈（`run-client.sh`）启动后才存在，且绑
本地 127.0.0.1。检查：

```bash
curl http://localhost:8092/state
# 应返回 viser 状态 JSON
```

如果失败：
1. 确认跑了 `run-client.sh`，看终端有没有报错
2. 单独启 viser 调试：
   ```bash
   python tools/viser_headless.py \
       --npz_dir work/cache/viser \
       --viser_port 8091 --control_port 8092
   ```
3. .npz 还没下到本地？看 sync_daemon 状态：
   ```bash
   cat /run/user/$(id -u)/gsfluent_sync_status.json | python3 -m json.tool
   ```

### 仿真启动后立刻 error

打开日志：

```bash
RUN=<run_name>
curl "http://<服务器IP>:18080/api/runs/${RUN}/log?offset=0" | python3 -c 'import json,sys; print(json.load(sys.stdin)["content"])'
```

最常见两种：

- **`tensor a (X) vs tensor b (Y) 形状不匹配`** — 仿真脚本对 `gaussians._scaling` 与 `init_opacity` 长度同步不全。当前版本已修复，如复发说明仿真脚本被覆写过，需重新打补丁。
- **`Warp CUDA error 700`** — 数值不稳定或边界条件触发非法内存访问。多数情况是配方过激（cuboid 速度太大、起点与几何重叠）。降速度、抬起点、减小 `substep_dt`，或换 `plasticine` 材料（有屈服）。

### .npz 缓存太大 / 磁盘满

每个仿真序列约 2.8 GB（683k splats × 151 帧 × per-frame cov）。
清理旧序列：

```bash
curl -X DELETE http://<服务器IP>:18080/api/runs/history/<run_name>
```

---

## 七、目录结构

```
gsfluent_pkg/
├── README.md                       ← 本文档（中文）
├── README.en.md                    ← 英文版（开发流程，未必同步）
├── start-gsfluent-server.sh        ← 服务器启动脚本
├── server/                         ← FastAPI 后端
│   └── gsfluent/
│       ├── api/                    ← /api/{recipes,runs,models,sequences,schemas}
│       └── core/                   ← runner、library、manifest、recipes
├── frontend/                       ← React + Vite SPA
│   └── dist/                       ← 已构建产物（gsfluent serve 直接服务）
├── tools/
│   ├── viser_headless.py           ← splat 渲染服务（端口 8091/8092）
│   ├── fuse_to_full_ply.py         ← sim_*.ply + 参考 3DGS → 每帧 3DGS
│   ├── sequence_to_viser_npz.py    ← 每帧 ply → .npz 缓存
│   ├── batch_convert_to_npz.py     ← 批量转换工具
│   ├── run_sim.sh                  ← 服务器仿真包装脚本
│   └── recipes/                    ← JSON 配方
└── work/                           ← 运行时数据
    ├── library/                    ← 持久化序列
    └── cache/                      ← .npz 播放缓存
```

---

## 八、扩展接口

完整接口列表在 `server/gsfluent/api/` 各文件顶部的 docstring。
当前提供的 endpoint：

| Method | Path | 作用 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/recipes` | 列出配方 |
| GET | `/api/recipes/{name}` | 获取配方详情 |
| GET | `/api/models` | 列出已注册模型 |
| POST | `/api/models/upload` | 上传新模型 |
| POST | `/api/models/register` | 注册已有模型路径 |
| DELETE | `/api/models/{name}` | 删除模型 |
| GET | `/api/sequences` | 列出仿真序列 |
| DELETE | `/api/sequences/{name}` | 删除序列 |
| GET | `/api/runs` | 列出活跃运行 |
| POST | `/api/runs` | 提交新运行 |
| DELETE | `/api/runs/{run_id}` | 取消运行 |
| GET | `/api/runs/history` | 列出历史运行 |
| DELETE | `/api/runs/history/{name}` | 删除历史 |
| GET | `/api/runs/{name}/log` | 增量获取日志 |
| GET | `/api/runs/{name}/frame/{idx}.ply` | 下载某帧 PLY |
| GET | `/api/schemas/materials` | 材料默认值 |
| GET | `/api/schemas/boundaries` | 边界类型 schema |

---

## 九、参考

- 3D Gaussian Splatting: Kerbl et al. 2023
- MPM 物理: NVIDIA Warp + Taichi（服务器仿真栈）
- Splat 播放: viser
- 工作台前端: React + Vite + React Three Fiber

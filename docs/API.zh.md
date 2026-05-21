# gsfluent v1 Backend — HTTP API

这份文档写给需要直接对接 gsfluent 后端的人:curl 调试、Python 脚本、浏览器前端、本地同步进程都算在内。每个接口的描述都对着 `server/gsfluent/api/` 下的路由源码,以及散落在各路由文件里的 Pydantic 模型(`api/schemas.py` 现在只是聚合层,真正的请求模型并不在那)。

文档和实际行为对不上时以代码为准,文档是错的,欢迎提 PR。

## 总览

### Base URL

公共部署地址:`${BACKEND_URL}`。所有 HTTP 路由都挂在 `/api/` 前缀下,例如 `${BACKEND_URL}/api/health`。SPA 走根路径 `/`,而 `/api/*` 比 SPA 路由先注册,所以路径冲突时 API 永远先吃到请求。

### Auth

没有。没有 auth header,没有 API key,也没有 session cookie。只要能连到这个端口的人就能调任意接口,所以部署地址当作内网处理。

### 版本

URL 里没有版本号。FastAPI 在 OpenAPI 里报 `"version": "0.1.0"`(`/docs` 和 `/openapi.json` 都看得到),但路由本身不带版本。破坏性改动会直接落到接口上,客户端最稳的做法是锁某个测过的 commit。

### Content-Type

- 请求体:除了文件上传都是 `application/json`。上传(model ply、cameras.json、npz)走 `multipart/form-data`。
- 响应体:除了文件下载(frame ply、npz 缓存、frames.bin)是 `application/octet-stream`,其余都是 `application/json`。

### 错误响应

FastAPI 默认结构。任何非 2xx 响应都是:

```json
{ "detail": "human-readable message" }
```

状态码区分错误类别(400 / 404 / 409 / 413 / 422 / 500)。Pydantic 体校验失败也走 422,但 `detail` 会变成 FastAPI 标准的错误列表,每个字段一条。

### CORS

正则匹配 `^https?://(localhost|127\.0\.0\.1)(:\d+)?$` 的来源放行。启动时可以用环境变量 `GSFLUENT_EXTRA_CORS_ORIGINS`(逗号分隔)再加来源。不允许携带凭据;methods 和 headers 都是通配符。

### 约定

- 全链路坐标系是 **Z-up**。Y-up 输入在 import 阶段会被转过去,`_meta.json` 里记一条 `converted_from: "y-up"`。
- 运行名(run name)要满足 `^[A-Za-z0-9_.\-]+$`。recipe 名是 `^[A-Za-z0-9_\-]+$`,model 名是 `^[A-Za-z0-9_.\-]+$`。任何路径穿越尝试都会被打成 400 或 422。

---

## Health

### GET /api/health

存活探针,进程没挂就一直返回 200。

**Response**

```json
{
  "status": "ok",
  "pkg_root": "/path/to/gsfluent_pkg"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 永远是 `"ok"`。 |
| `pkg_root` | string | 服务端 package 根目录的绝对路径,只用于诊断。 |

**curl**

```bash
curl ${BACKEND_URL}/api/health
```

### GET /api/gpu-check

调 `nvidia-smi` 看主机 GPU,部署时用来做握手。这里不会碰 CUDA,只是 shell 出去把 CSV 解出来。

**Response (success)**

```json
{
  "ok": true,
  "gpus": [
    "0, NVIDIA A100-SXM4-80GB, 565.57.01, 81920 MiB, 58765 MiB"
  ]
}
```

**Response (failure)**

```json
{
  "ok": false,
  "error": "nvidia-smi not on PATH",
  "hint": "If running in Docker: was the container started with `--gpus all` ..."
}
```

其他失败形态:`{"ok": false, "error": "nvidia-smi timed out (>5s)"}`、`{"ok": false, "error": "nvidia-smi exit <N>", "stderr": "..."}`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ok` | bool | 至少枚举到一块 GPU 时为 true。 |
| `gpus` | string[] | CSV 行:`index, name, driver_version, memory.total, memory.free`。仅 `ok=true` 时存在。 |
| `error` | string | 单行失败摘要,仅 `ok=false` 时存在。 |
| `hint` | string | 给运维的修复提示,可选。 |
| `stderr` | string | nvidia-smi 的 stderr,可选。 |

不管成功失败,HTTP 状态都是 200;判断走 JSON 里的 `ok` 字段。

**curl**

```bash
curl ${BACKEND_URL}/api/gpu-check
```

### GET /api/system

主机和容器的自省信息。不会暴露敏感字段。

**Response**

```json
{
  "hostname": "jy-r308-f01-7",
  "platform": "Linux-5.15.0-171-generic-x86_64-with-glibc2.35",
  "python": "3.11.15",
  "pkg_root": "/path/to/gsfluent_pkg",
  "sim_script": "<default>",
  "sim_home": "<default>",
  "in_container": false
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `hostname` | string | `socket.gethostname()`。 |
| `platform` | string | `platform.platform()`。 |
| `python` | string | 解释器版本(如 `3.11.15`)。 |
| `pkg_root` | string | 服务端 package 根。 |
| `sim_script` | string | 环境变量 `GSFLUENT_SIM_SCRIPT_RUNNER` 的值,未设则是 `"<default>"`。 |
| `sim_home` | string | 环境变量 `GSFLUENT_SIM_HOME` 的值,未设则是 `"<default>"`。 |
| `in_container` | bool | `/.dockerenv` 存在时为 true。 |

**curl**

```bash
curl ${BACKEND_URL}/api/system
```

---

## Recipes

一份 recipe 就是驱动一次仿真用的 JSON 配置(sim_area、n_grid、材料参数、边界条件等等)。内置 recipe 放在 `server/recipes/*.json`,只读;用户保存的写到 `work/_user_recipes/`。名字要满足 `^[A-Za-z0-9_\-]+$`。

### GET /api/recipes

列出服务端能看见的所有 recipe(内置 + 用户)。

**Response**

```json
[
  { "name": "demolition", "source": "builtin" },
  { "name": "earthquake", "source": "builtin" },
  { "name": "my_run",     "source": "user"    }
]
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | recipe 标识符(文件名去扩展名)。 |
| `source` | string | `"builtin"` 或 `"user"`。 |

内置先列,然后是用户,组内按字典序。

**curl**

```bash
curl ${BACKEND_URL}/api/recipes
```

### GET /api/recipes/{name}

按名取一个 recipe。同名 recipe 同时存在于内置和用户里时,内置优先。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 满足 `^[A-Za-z0-9_\-]+$`。 |

**Response**

```json
{
  "name": "demolition",
  "source": "builtin",
  "data": {
    "sim_area": [-30, 30, -10, 10, -2, 45],
    "n_grid": 150,
    "material": "plasticine",
    "E": 50000.0,
    "nu": 0.2,
    "...": "..."
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 回显请求名。 |
| `source` | string | `"builtin"` 或 `"user"`。 |
| `data` | object | 完整 recipe 体,字段随 recipe 而定;字段并集见内置 JSON。 |

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 404 | recipe 不存在。 |
| 409 | 文件存在但读取或解析失败(`RecipeReadError`)。 |
| 422 | name 没过正则。 |

**curl**

```bash
curl ${BACKEND_URL}/api/recipes/demolition
```

### PUT /api/recipes/{name}

写一个用户 recipe,有同名的会被覆盖。内置 recipe 改不了:用同名保存只会落一份用户副本,但读取(GET)时依然先返回内置那一份。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 目标用户 recipe 名,满足 `^[A-Za-z0-9_\-]+$`。 |

**Request body** (`application/json`)

```json
{
  "data": { "sim_area": [-30, 30, -10, 10, -2, 45], "...": "..." },
  "based_on": "demolition"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `data` | object | **必填。** 要存的 recipe 体。原样落盘,会自动注入一个 `_provenance` 块。 |
| `based_on` | string\|null | 可选。来源 recipe 名,写到 `_provenance.based_on`。缺省记成 `"(unknown)"`。 |

**Response**

```json
{
  "name": "my_run",
  "source": "user",
  "data": {
    "sim_area": [-30, 30, -10, 10, -2, 45],
    "_provenance": {
      "based_on": "demolition",
      "saved_at": "2026-05-20T10:34:20Z"
    }
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 回显。 |
| `source` | string | 永远是 `"user"`。 |
| `data` | object | 持久化后的 payload,含注入的 `_provenance`。 |

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 422 | name 不合规,或 `data` 缺失。 |

**curl**

```bash
curl -X PUT ${BACKEND_URL}/api/recipes/my_run \
  -H 'Content-Type: application/json' \
  -d '{"data":{"sim_area":[-30,30,-10,10,-2,45]},"based_on":"demolition"}'
```

### DELETE /api/recipes/{name}

删用户 recipe。内置删不掉。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | recipe 名。 |

**Response**

```json
{ "deleted": "my_run" }
```

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 403 | 名字命中内置 recipe。 |
| 404 | 用户 recipe 不存在。 |
| 422 | name 不合规。 |
| 500 | 文件 unlink 失败。 |

**curl**

```bash
curl -X DELETE ${BACKEND_URL}/api/recipes/my_run
```

---

## Models

一份 model 就是一份 3D Gaussian Splatting 扫描,目录结构固定是 `<dir>/point_cloud/iteration_<N>/point_cloud.ply`。library 放在 `work/library/models/`;外部注册的路径记在 `work/library/models/_registered.json` 里,文件本身不拷贝。

### GET /api/models

列出 library 里所有 model(库内 + 外部注册)。

**Response**

```json
[
  {
    "name": "cluster_6_15",
    "kind": "model",
    "source": "register",
    "source_path": "/path/to/GaussianFluent/model/cluster_6_15",
    "n_splats": 683741,
    "bbox": [[3443.6, 29036.1, -19.9], [3474.1, 29054.1, 30.5]],
    "coord_convention": "z-up",
    "imported_at": "2026-05-18T01:03:34Z",
    "converted_from": null,
    "sha256": null,
    "path": "/path/to/GaussianFluent/model/cluster_6_15"
  }
]
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | model 标识(库内就是目录名,外部就是注册名)。 |
| `kind` | string | 永远是 `"model"`。 |
| `source` | string | `"upload"` / `"register"` / `"import"`。 |
| `source_path` | string\|null | 原盘路径(register / import 才有),upload 是 `null`。 |
| `n_splats` | int\|null | 最高 iteration ply 的顶点数。 |
| `bbox` | float[2][3]\|null | `[[xmin,ymin,zmin],[xmax,ymax,zmax]]`,从 ply 算出来。 |
| `coord_convention` | string | 合法条目恒为 `"z-up"`。 |
| `imported_at` | string\|null | ISO-8601 UTC 时间戳。 |
| `converted_from` | string\|null | 来源是 Y-up 时为 `"y-up"`。 |
| `sha256` | string\|null | 上传 ply 字节的 SHA-256(仅 upload)。 |
| `path` | string | model 目录的绝对路径。 |

按 `imported_at` 倒序;没这个字段的条目落到末尾按字典序。

**curl**

```bash
curl ${BACKEND_URL}/api/models
```

### POST /api/models/check_hash

查内容 hash 一致的 model 是不是已经在了。前端上传前会先调一下,命中就跳过传输,直接复用现有 model。

**Request body** (`application/json`)

```json
{ "sha256": "abc123...64hex", "filename": "scene.ply" }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `sha256` | string | **必填。** 原始 .ply 字节的 64 字符小写十六进制 SHA-256。 |
| `filename` | string\|null | 可选,只做诊断日志用,不持久化。 |

**Response (命中)**

```json
{
  "exists": true,
  "name": "scene_a1b2c3d4",
  "path": "/data/.../library/models/scene_a1b2c3d4",
  "n_splats": 683741
}
```

**Response (未命中)**

```json
{ "exists": false }
```

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 422 | `sha256` 缺失或长度不是 64。 |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/models/check_hash \
  -H 'Content-Type: application/json' \
  -d '{"sha256":"'$(sha256sum scene.ply | cut -c1-64)'"}'
```

### POST /api/models/upload

上传一个 `.ply`(可以附带一份 `cameras.json`)。服务端会按 3DGS 目录布局包好,算 bbox 和 splat 数,写 `_meta.json`。内容 hash 已经存在时直接返回现有 model 的 meta。

**Request body** (`multipart/form-data`)

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ply` | file | **必填。** `.ply` 文件(校验扩展名),上限 8 GiB。 |
| `cameras_json` | file | 可选,`.json` 格式,即原始 COLMAP camera 列表。 |
| `convert_y_up` | bool (form) | 为 `true` 时在 import 阶段把位置、四元数、法线从 Y-up 改写为 Z-up。默认 `false`。 |
| `ply_encoding` | string (form) | `"identity"`(默认)或 `"gzip"`。`gzip` 模式下服务端在校验前 gunzip(同样 8 GiB 上限防 gzip 炸弹)。 |

**Response (新上传)**

```json
{
  "name": "scene_a1b2c3d4",
  "path": "/data/.../library/models/scene_a1b2c3d4"
}
```

**Response (按解压后内容去重命中)**

返回和 `GET /api/models` 单条相同的完整 meta dict。

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 413 | 原始字节或 gunzip 之后字节数超过 8 GiB。 |
| 422 | `ply` 扩展名不对、文件过小(<64 B)、缺 `ply\n` magic、`cameras.json` 不是 JSON 列表、`ply_encoding` 取值非法、gunzip 失败、Y-up 转换解析失败,任一种。 |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/models/upload \
  -F 'ply=@scene.ply' \
  -F 'cameras_json=@cameras.json' \
  -F 'convert_y_up=false' \
  -F 'ply_encoding=identity'
```

### POST /api/models/register

把盘上已有的一份 3DGS 目录注册进来,**不拷贝**。路径下必须有 `point_cloud/iteration_<N>/point_cloud.ply`。`convert_y_up=true` 时会把这套目录拷进 library 并改写成 Z-up,响应里的 `mode` 字段标明走了哪条分支。

**Request body** (`application/json`)

```json
{ "path": "/data/scans/my_scene", "convert_y_up": false }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `path` | string | **必填。** 服务端绝对路径。 |
| `convert_y_up` | bool | 可选,默认 `false`。 |

**Response**

```json
{
  "name": "my_scene",
  "path": "/data/scans/my_scene",
  "mode": "registered"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | model 名(目录的 basename)。 |
| `path` | string | 最终盘上路径:`registered` 时就是源路径,`copied-and-converted` 时是 `library/models/<name>`。 |
| `mode` | string | `"registered"` 或 `"copied-and-converted"`。 |

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 409 | `convert_y_up=true` 而 library 内已有同名 model。 |
| 422 | 路径不存在 / 不是目录 / 缺 `point_cloud/iteration_*/point_cloud.ply` 结构 / 目录名不安全(要满足 `^[A-Za-z0-9_.\-]+$`)。 |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/models/register \
  -H 'Content-Type: application/json' \
  -d '{"path":"/data/scans/my_scene","convert_y_up":false}'
```

### GET /api/models/file?path=&lt;abs-path&gt;

### GET /api/models/file/{filename}?path=&lt;abs-path&gt;

流式下载已注册 model 里最高 iteration 的 `point_cloud.ply`。`{filename}` 段只是装饰,作用是让 URL 以 `.ply` 结尾,这样浏览器侧的 splat 库(比如 `@mkkellogg/gaussian-splats-3d`)能正确路由到 ply parser;实际服务的文件永远是 `<path>/point_cloud/iteration_<max N>/point_cloud.ply`。

**Query params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `path` | string | **必填。** 绝对路径。要精确命中 `GET /api/models` 某条记录的 `path`(白名单校验)。 |

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `filename` | string | 可选,服务端忽略。 |

**Response**

`application/octet-stream`,原始 .ply 字节。FastAPI 的 `FileResponse` 原生支持 HTTP `Range`,断了可以续。

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 404 | path 不在已注册 model 白名单内、不是目录、或缺 `point_cloud/iteration_*/point_cloud.ply`。 |

**curl**

```bash
curl -OJ "${BACKEND_URL}/api/models/file/scene.ply?path=/data/scans/my_scene"
```

### DELETE /api/models/{name}

把 model 从 library 里去掉。库内的 model 会 `rmtree` 整个目录;外部注册的只删注册表条目,**不会**去碰用户盘上的文件。引用这个 model 的 sequence(通过 `model_ref`)不会被级联删,会变成孤儿。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | model 名。 |

**Response**

```json
{ "deleted": "scene_a1b2c3d4" }
```

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 404 | model 不在 library 中。 |
| 500 | 删除失败(比如 rmtree 权限错误)。 |

**curl**

```bash
curl -X DELETE ${BACKEND_URL}/api/models/scene_a1b2c3d4
```

---

## Runs

一个 run 就是一次仿真任务。活动 run 由进程内的 `core.runner` 跟踪,归档 run 落到 `work/library/sequences/<run_name>/`。runs 路由把这两边都暴露出来。

### GET /api/runs

只列出当前活动的 run(state == `"running"`)。历史 run 走 `GET /api/runs/history`。

**Response**

```json
[
  { "id": "1a2b3c", "name": "cluster_6_15_eq_v1", "state": "running" }
]
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 进程内 run id(不透明)。只 `DELETE /api/runs/{run_id}` 用。 |
| `name` | string | 启动时指定的 run name。其余接口(`/log`、`/frame`、`/history/...`)都用这个值。 |
| `state` | string | 这个列表里永远是 `"running"`。 |

**curl**

```bash
curl ${BACKEND_URL}/api/runs
```

### POST /api/runs

起一次新仿真,或者只做 dry-run 校验。

**Request body** (`application/json`)

```json
{
  "run_name": "cluster_6_15_eq_v1",
  "model_path": "/data/.../library/models/cluster_6_15",
  "recipe_data": { "sim_area": [-30, 30, -10, 10, -2, 45], "...": "..." },
  "recipe_source": "earthquake",
  "particles": 200000,
  "dry_run": false
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_name` | string | **必填。** 输出 sequence 名,要唯一。 |
| `model_path` | string | **必填。** model 目录绝对路径(已注册或 library 内)。 |
| `recipe_data` | object | **必填。** 完整 recipe 体(和 `GET /api/recipes/{name}` 里的 `data` 同构)。 |
| `recipe_source` | string | **必填。** 来源 recipe 名,写到 run manifest。 |
| `particles` | int | 可选,默认 `200000`。 |
| `dry_run` | bool | 可选,为 `true` 时只跑校验器(model_path 存在、sim_area 和 model bbox 相交等等),不真起仿真。默认 `false`。 |

**Response (实跑)**

```json
{
  "run_id": "1a2b3c",
  "run_name": "cluster_6_15_eq_v1"
}
```

**Response (dry run)**

```json
{ "dry_run": true, "valid": true, "run_name": "cluster_6_15_eq_v1" }
```

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 422 | `model_path` 缺失或不是目录;sim_area 和 model bbox 不相交;`runner.start_run` 抛 `ValueError`。 |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/runs \
  -H 'Content-Type: application/json' \
  -d @start.json
```

### DELETE /api/runs/{run_id}

取消活动 run。这里用的是进程内 **`run_id`**,不是 run name。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_id` | string | `GET /api/runs` 返回的 run id。 |

**Response**

```json
{ "status": "cancelled" }
```

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 404 | `run_id` 当前不活跃。 |

**curl**

```bash
curl -X DELETE ${BACKEND_URL}/api/runs/1a2b3c
```

### GET /api/runs/history

列出 library 里所有历史 run,按 `started_at` 倒序。先扫 `library/sequences/`,迁移前的旧数据回退到 `runner.FUSED_DIR`。

**Response**

```json
[
  {
    "run_name": "cluster_6_15_eq_v3",
    "status": "done",
    "started_at": 1779266060.81,
    "finished_at": 1779266270.89,
    "particles": 200000,
    "recipe_source": "earthquake",
    "model_ref": "cluster_6_15",
    "frame_count": 151,
    "sequence_source": "sim"
  }
]
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_name` | string | sequence 名。 |
| `status` | string | `"done"` / `"error"` / `"cancelled"` / `"running"` / `"unknown"`。 |
| `started_at` | float | Unix epoch 秒。优先取 `manifest.json:started_at`,其次 `_meta.json:created_at` 解析,最后退化到目录 mtime。 |
| `finished_at` | float | 可选。`manifest.json` 里有这条字段才返回。 |
| `particles` | int | 可选,从 `manifest.json` 读。 |
| `recipe_source` | string | 可选,从 `manifest.json` 读。 |
| `model_ref` | string | 可选,父 model 名;来自 `_meta.json` 或 `manifest.json:model_dir` 的 basename。 |
| `frame_count` | int | 可选,来自 `_meta.json` 或当场算的 live 计数。 |
| `sequence_source` | string | 可选,来自 `_meta.json:source`。 |
| `_synthetic` | bool | 只在 legacy 目录回退分支、且没有 manifest 时带这个字段。 |

**curl**

```bash
curl ${BACKEND_URL}/api/runs/history
```

### DELETE /api/runs/history/{run_name}

按名删一条历史 run。还在跑的 run 拒删。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_name` | string | run / sequence 名。 |

**Response**

```json
{ "deleted": "cluster_6_15_eq_v3" }
```

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 400 | 路径穿越,或 legacy 条目不是目录。 |
| 404 | run 不存在。 |
| 409 | run 还在跑,先取消。 |
| 500 | rmtree 或 library delete 失败。 |

**curl**

```bash
curl -X DELETE ${BACKEND_URL}/api/runs/history/cluster_6_15_eq_v3
```

### GET /api/runs/{run_name}/log

增量拉 run 的 `run.log`。前端大约 500 ms 一次。活动 run 和归档 run 都支持(先看 `runner.FUSED_DIR/<name>/run.log`,再看 `library.SEQUENCES_DIR/<name>/run.log`)。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_name` | string | run 名,要满足 `^[A-Za-z0-9_.\-]+$`。 |

**Query params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `offset` | int | 可选,默认 `0`。从该字节偏移开始读;`offset > size` 或 `< 0` 时服务端重置回 `0`(兼容 log 轮转和截断)。 |

**Response**

```json
{
  "content": "=== run_sim.sh plan ===\n  model         : /data/...\n  ...",
  "offset": 14460,
  "size": 14460
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `content` | string | 请求的 `offset` 到 `size` 之间的字节,UTF-8 解码,`errors="replace"`。 |
| `offset` | int | 当前文件大小。下一轮直接把它当 `offset` 传回来就行。 |
| `size` | int | 同 `offset`。 |

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 400 | run name 不合规。 |
| 404 | active 和 library 两处都找不到 `run.log`。 |

**curl**

```bash
curl "${BACKEND_URL}/api/runs/cluster_6_15_eq_v3/log?offset=0"
```

### GET /api/runs/{run_name}/frame/{frame_idx}.ply

下载某个 sequence 的某一帧 `.ply`。splat 回放模式靠这个接口拉到完整属性的帧来初始化浏览器侧 splat mesh(WebSocket 推送只发 xyz)。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_name` | string | run / sequence 名。 |
| `frame_idx` | int | 文件名里是 4 位补零,但这里传普通整数就行(`0`、`1`、`42` 这样)。 |

**Response**

`application/octet-stream`,原始帧 ply 字节。

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 400 | legacy 回退路径上的路径穿越尝试。 |
| 404 | canonical 和 legacy 两处都找不到 sequence 或对应帧。 |

**curl**

```bash
curl -OJ "${BACKEND_URL}/api/runs/cluster_6_15_eq_v3/frame/0.ply"
```

---

## Sequences

一个 sequence 就是一组按时间采样的 `.ply`,要么是仿真自己产的(`source: "sim"`),要么是从外部目录导进来的(`source: "import"`)。全部落在 `work/library/sequences/<name>/`。

### GET /api/sequences

列出 library 里所有 sequence,按 `created_at` 倒序。

**Response**

```json
[
  {
    "name": "cluster_6_15_eq_v3",
    "kind": "sequence",
    "source": "sim",
    "source_path": "host:/data/.../sequences/cluster_6_15_eq_v3",
    "model_ref": "cluster_6_15",
    "frame_count": 151,
    "fps_hint": 24,
    "n_splats": 683741,
    "bbox_initial": [[-15.2, -9.0, -25.2], [15.2, 9.0, 25.2]],
    "coord_convention": "z-up",
    "first_frame_full": true,
    "created_at": "2026-05-20T08:38:17Z",
    "converted_from": null,
    "is_broken": false,
    "cache": {
      "viser_npz_mtime": 1779266297.33,
      "viser_npz_bytes": 2910003294,
      "frames_bin_mtime": null,
      "frames_bin_bytes": null
    }
  }
]
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | sequence 名。 |
| `kind` | string | 永远是 `"sequence"`。 |
| `source` | string | `"sim"` / `"import"`;`_meta.json` 缺失时是 `"unknown"`。 |
| `source_path` | string\|null | 原文件夹路径(import)或 `host:/abs/path`(sim)。 |
| `model_ref` | string\|null | 父 model 名。仿真在跑的过程中可能从 `manifest.json:model_dir` 临时补出来。 |
| `frame_count` | int | 优先看 `_meta.json`;meta 缺失或仿真还在写就取 live 文件计数。 |
| `fps_hint` | int | 默认 `24`。 |
| `n_splats` | int\|null | 首帧 splat 数。 |
| `bbox_initial` | float[2][3]\|null | 首帧 bbox。 |
| `coord_convention` | string | 恒为 `"z-up"`。 |
| `first_frame_full` | bool | 帧 0 是否带完整 3DGS 属性集。 |
| `created_at` | string\|null | ISO-8601 UTC。 |
| `converted_from` | string\|null | import 时做了坐标转换就是 `"y-up"`。 |
| `is_broken` | bool | 导入 sequence 的 `frames/` 符号链接悬挂时为 true。 |
| `cache.viser_npz_mtime` | float\|null | `work/cache/viser/<name>.npz` 的 mtime,文件不存在就是 `null`。 |
| `cache.viser_npz_bytes` | int\|null | 同上,文件大小。 |
| `cache.frames_bin_mtime` | float\|null | `library/sequences/<name>/frames.bin` 的 mtime。 |
| `cache.frames_bin_bytes` | int\|null | 同上,文件大小。 |

服务端文件系统路径(`path`)会在响应前被剥掉,避免暴露服务器目录布局。

**curl**

```bash
curl ${BACKEND_URL}/api/sequences
```

### POST /api/sequences/import

把一个外部的 `frame_*.ply` 目录注册成 sequence,用的是软链,不拷贝。

**Request body** (`application/json`)

```json
{
  "folder_path": "/data/external/my_seq",
  "name": "my_seq",
  "convert_y_up": false
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `folder_path` | string | **必填。** 含 `frame_*.ply` 的目录绝对路径。 |
| `name` | string\|null | 可选,sequence 名,缺省取文件夹 basename。 |
| `convert_y_up` | bool | 可选,默认 `false`。 |

**Response**

和 `GET /api/sequences` 单条同构。

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 409 | 同名 sequence 已存在。 |
| 422 | 文件夹缺失 / 不是目录 / ply 解析失败 / `plyfile` 依赖缺失。 |
| 500 | import 过程中盘错误。 |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/sequences/import \
  -H 'Content-Type: application/json' \
  -d '{"folder_path":"/data/external/my_seq","name":"my_seq"}'
```

### POST /api/sequences/upload-npz

上传一份预生成的回放 `.npz` 缓存(由 `server/tools/batch_convert_to_npz.py` 产),注册成 library 里的 sequence。落盘是流式的,不会把整个文件一次性塞进内存。

**Request body** (`multipart/form-data`)

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `file` | file | **必填。** `.npz` 扩展名,8 GB 上限,要含一个形状为 `(T, N, 3)` 的 `frames` 数组。 |
| `name` | string (form) | 可选,缺省取文件名去掉 `.npz`。含 `/` 或以 `.` 开头会被拒。 |

**Response**

和 `GET /api/sequences` 单条同构(sequence 注册为 `source: "import"`、`source_path: "upload:<orig_filename>"`)。

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 409 | 同名 sequence 已存在。 |
| 413 | 文件超过 8 GB。 |
| 422 | 扩展名错;sequence 名不合规;缺 zip magic;无 `frames` 键;`frames` 形状不是 `(T, N, 3)`;npz 解析失败。 |
| 500 | `write_meta` 成功但重新 `Sequence.load` 返回 None。 |

**curl**

```bash
curl -X POST ${BACKEND_URL}/api/sequences/upload-npz \
  -F 'file=@my_seq.npz' \
  -F 'name=my_seq'
```

### GET /api/sequences/{name}/frame/{frame_idx}.ply

`GET /api/runs/{run_name}/frame/{frame_idx}.ply` 的别名,字节和状态码一样。这个 URL 形态存在的意义是让前端 WebSocket bootstrap 不挑路径都能拿到数据。

**curl**

```bash
curl -OJ "${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3/frame/0.ply"
```

### GET /api/sequences/{name}/cache/viser.npz

下载 sequence 的 `.npz` viser 缓存。客户端侧的同步守护进程拿它把服务端缓存镜像到本地。`FileResponse` 原生支持 HTTP `Range`,断了能续。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | sequence 名。 |

**Response**

`application/octet-stream`,原始 `.npz` 字节。

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 400 | 解析后路径逃出 cache 根(防御性)。 |
| 404 | sequence 不存在,或还没跑过 `server/tools/batch_convert_to_npz.py <name>`。 |

**curl**

```bash
curl -OJ "${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3/cache/viser.npz"
```

### GET /api/sequences/{name}/cache/frames.bin

下载 GSSQ 打包过的 `frames.bin`(每帧 int16 量化的 xyz,出自 `server/tools/pack_sequence.py`)。客户端侧 Points 模式 WS 服务器拿它做本地的快速流播。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | sequence 名。 |

**Response**

`application/octet-stream`,原始 `frames.bin` 字节。

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 400 | 解析后路径逃出 library 根。 |
| 404 | sequence 不存在,或还没跑过 `server/tools/pack_sequence.py <name>`。 |

**curl**

```bash
curl -OJ "${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3/cache/frames.bin"
```

### DELETE /api/sequences/{name}

把 sequence 从 library 里去掉。import 进来的 sequence(`frames/` 是软链)只删 library 条目和软链,不动源目录;sim 产的 sequence(真目录)整个 `rmtree`。还在写入的 sequence 拒删。

**Path params**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | sequence 名。 |

**Response**

```json
{ "deleted": "cluster_6_15_eq_v3" }
```

**状态码**

| 状态码 | 触发原因 |
| --- | --- |
| 400 | 路径穿越。 |
| 404 | sequence 不存在。 |
| 409 | 仿真还在往这个 sequence 写,先取消。 |
| 500 | 删除失败。 |

**curl**

```bash
curl -X DELETE ${BACKEND_URL}/api/sequences/cluster_6_15_eq_v3
```

---

## Schemas

React BC 编辑器和材料编辑器用到的静态 schema。两个接口都从内存表里读,不碰磁盘。

### GET /api/schemas/boundaries

按 BC 类型给字段 schema。

**Response**

```json
{
  "bounding_box": [],
  "surface_collider": [
    { "name": "point",        "type": "vec3",   "default": [0.0, 0.0, 0.0], "hint": "Plane origin" },
    { "name": "normal",       "type": "vec3",   "default": [0.0, 0.0, 1.0], "hint": "Plane normal (unit)" },
    { "name": "surface_type", "type": "string", "default": "sticky",        "hint": "sticky | slip | separate" },
    { "name": "friction",     "type": "float",  "default": 0.0,             "hint": "0..1" }
  ],
  "cuboid": [ "..." ],
  "release_particles_sequentially": [ "..." ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `<bc_type>` | array | 字段 schema 列表。空列表表示这个 BC 没参数。 |
| `<bc_type>[].name` | string | 字段名。 |
| `<bc_type>[].type` | string | `"vec3"` / `"float"` / `"string"`。 |
| `<bc_type>[].default` | any | 默认值(类型对应 `type`)。 |
| `<bc_type>[].hint` | string | UI 提示。 |

**curl**

```bash
curl ${BACKEND_URL}/api/schemas/boundaries
```

### GET /api/schemas/materials

每种材料的默认参数。

**Response**

```json
{
  "jelly":      { "E": 5000.0,  "nu": 0.38, "density": 1, "yield_stress": 0.0,    "friction_angle": 45.0, "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0 },
  "metal":      { "E": 50000.0, "nu": 0.30, "density": 3, "yield_stress": 1000.0, "friction_angle": 0.0,  "beta": 1.0, "xi": 3.0,  "hardening": 1.0, "alpha_0": -0.04, "plastic_viscosity": 0.0 },
  "sand":       { "...": "..." },
  "foam":       { "...": "..." },
  "snow":       { "...": "..." },
  "plasticine": { "...": "..." },
  "watermelon": { "...": "..." }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `<material>` | object | 该材料的 MPM 默认参数集。 |
| `<material>.E` | float | 杨氏模量。 |
| `<material>.nu` | float | 泊松比。 |
| `<material>.density` | float | 相对密度。 |
| `<material>.yield_stress` | float | 塑性屈服应力(纯弹性材料是 `0`)。 |
| `<material>.friction_angle` | float | Drucker-Prager 摩擦角(度)。 |
| `<material>.beta` | float | snow 模型参数。 |
| `<material>.xi` | float | snow 模型硬化指数。 |
| `<material>.hardening` | float | 硬化乘子。 |
| `<material>.alpha_0` | float | snow 模型初始 alpha。 |
| `<material>.plastic_viscosity` | float | 塑性粘度。 |

**curl**

```bash
curl ${BACKEND_URL}/api/schemas/materials
```

---

## WebSocket:/api/stream

实时帧数据和 run log 的流式通道。后端逻辑和 runs 路由共用,只是从轮询换成了推送。下面给客户端足够的消息形状参考,完整定义看 `server/gsfluent/api/stream.py`。

**Connect**

```
ws://your-backend:port/api/stream
```

**Client → server (JSON)**

| Type | 字段 | 作用 |
| --- | --- | --- |
| `subscribe` | `run_name: string` | 开始为 `run_name` 推送帧 + log。同一连接上的旧订阅会先被取消。 |
| `unsubscribe` | — | 停止推送。 |
| `load_model` | `path: string` | 把 `<path>/point_cloud/iteration_<N>/point_cloud.ply`(最大 N)当成一帧静态快照渲染。`path` 必须在已注册 model 白名单里。 |

**Server → client (JSON,中间夹一种二进制)**

| Type | 字段 | 备注 |
| --- | --- | --- |
| `static_attrs` | `run_name`, `n`, `R_b64`, `scales_b64`, `rgb_b64`, `opacity_b64` | 每次订阅只发一次。当前只有 `rgb_b64` 有内容,其余是空串(原因看源码注释)。 |
| `frame_meta` | `run_name`, `frame_idx`, `n` | 后面紧跟一条二进制消息。 |
| *(binary)* | `Float32Array`,shape `(n, 3)` 的 xyz | 帧负载。 |
| `log` | `run_name`, `line` | `run.log` 的回放 + 实时追加。 |
| `status` | `run_name`, `state` | manifest 进终态(`done` / `error` / `cancelled`)时触发。 |
| `error` | `code`, `message`, `run_name` 或 `path` | code:`run_not_found` / `snapshot_failed` / `watch_failed` / `model_not_found` / `model_parse_failed`。 |

---

## 常见工作流

### 1. 列 recipe,选一个

```bash
curl ${BACKEND_URL}/api/recipes
curl ${BACKEND_URL}/api/recipes/earthquake
```

第二个调用返回的 `data` 就是要回传给 `POST /api/runs` 当 `recipe_data` 用的。

### 2. 端到端起一次仿真

```bash
# 1. 选 model
MODEL_PATH=$(curl -s ${BACKEND_URL}/api/models \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['path'])")

# 2. 取 recipe
RECIPE=$(curl -s ${BACKEND_URL}/api/recipes/earthquake)

# 3. 拼启动 payload(recipe_data === recipe.data)
python3 -c "
import json, sys
r = json.loads('''$RECIPE''')
print(json.dumps({
    'run_name': 'cluster_6_15_eq_demo',
    'model_path': '$MODEL_PATH',
    'recipe_data': r['data'],
    'recipe_source': r['name'],
    'particles': 200000,
    'dry_run': False,
}))
" > start.json

# 4. 提交
curl -X POST ${BACKEND_URL}/api/runs \
  -H 'Content-Type: application/json' -d @start.json
```

响应里 `run_id` 是给 cancel 用的,`run_name` 给 log / frame / history 用。

### 3. 轮询 run 状态和日志

```bash
RUN=cluster_6_15_eq_demo
OFFSET=0
while true; do
  RESP=$(curl -s "${BACKEND_URL}/api/runs/$RUN/log?offset=$OFFSET")
  CONTENT=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['content'])")
  OFFSET=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['size'])")
  [ -n "$CONTENT" ] && printf '%s' "$CONTENT"
  # 看 run 还在不在活跃列表里
  ACTIVE=$(curl -s ${BACKEND_URL}/api/runs)
  echo "$ACTIVE" | grep -q "\"name\":\"$RUN\"" || break
  sleep 1
done
```

UI 客户端建议直接走 WebSocket:一条 `subscribe` 消息就够,log 回放、实时追加、终态 `status`、帧流都拿得到。

### 4. 拉生成出来的 .npz 缓存

run 跑完之后,缓存要在服务端先构一次:

```bash
ssh ${GSFLUENT_SSH_HOST} \
  '${CONDA_ROOT}/bin/python /path/to/gsfluent_pkg/server/tools/batch_convert_to_npz.py cluster_6_15_eq_demo'
```

然后下载:

```bash
curl -OJ "${BACKEND_URL}/api/sequences/cluster_6_15_eq_demo/cache/viser.npz"
```

`GET /api/sequences` 里的 `viser_npz_mtime` / `viser_npz_bytes` 是给客户端的同步守护进程用的,不发 HEAD 也能判断本地副本过没过期。下载支持 HTTP `Range`,可以断点续传。

---
marp: true
theme: default
paginate: true
backgroundColor: #fff
header: 'gsfluent · fuse 阶段'
footer: '2026-05-18'
style: |
  section {
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 26px;
  }
  h1 { font-size: 1.8em; color: #1a2533; }
  h2 { font-size: 1.4em; color: #1a2533; margin-top: 0; }
  code { font-size: 0.85em; }
  pre { font-size: 0.7em; line-height: 1.35; }
  table { font-size: 0.78em; }
  .deepdive {
    background: #f4f6fa;
    border-left: 4px solid #4a90e2;
    padding: 0.5em 0.9em;
    margin-top: 0.5em;
    font-size: 0.78em;
    line-height: 1.45;
  }
  .deepdive::before {
    content: "技术深入";
    display: block;
    font-weight: 600;
    color: #4a90e2;
    margin-bottom: 0.3em;
    letter-spacing: 0.05em;
  }
---

# Fuse 阶段是什么

#### 物理仿真 → 可动 3DGS 序列

把 MPM 仿真的每帧粒子位置，融合回参考 3DGS 的完整属性
最终结果供浏览器实时回放

---

## 输入与输出

**输入：**
- 参考 .ply — 建筑静止状态的 3DGS（683k splat，含 xyz / scale / rotation / opacity / rgb / SH）
- 每帧 sim ply — `sim_NNNN.ply`（仿真粒子位置约 200k，particle_F 模式下额外带 cov）

**输出：**
- 每帧融合 ply — `frame_NNNN.ply`（完整 3DGS 格式，按时间演化）

<div class="deepdive">

3DGS 每个 splat 有约 60 个浮点数（位置、形状、朝向、颜色、SH）。
MPM 只算位置和形变 —— 颜色跟物理方程无关，硬塞进去只会拖慢仿真。
所以让仿真专心算物理，把视觉属性留给 fuse 补。

</div>

---

## 在整体流水线中的位置

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ MPM 仿真  │ →  │   fuse   │ →  │   npz    │ →  │ 浏览器渲染 │
│ 200k 粒子 │    │  上采样   │    │  缓存构建 │    │  WebGL   │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
```

<div class="deepdive">

为什么不让仿真直接出可渲染的 3DGS？
- 仿真不知道颜色（只跟踪位置、速度、形变梯度）
- 仿真为速度跑 200k，渲染要保持原模型 683k 的细节密度
- 模块化：fuse 是纯几何映射，换 PBD / SPH 也能用同一套

</div>

---

## 为什么需要 fuse

| 阶段 | 粒子 / splat 数 | 原因 |
|---|---|---|
| MPM 仿真 | 200k | 邻居搜索 O(N²)，1M 粒子要 25× 时间 |
| 渲染目标 | 683k | 参考模型训练时的精细密度 |

直接拿 200k 渲染：丢掉 70% 视觉细节
直接跑 683k 仿真：每次 25 分钟，用户体验崩

**fuse = 用 200k 个物理样本，"补"出 683k 个渲染样本**

<div class="deepdive">

图形学的经典套路：**粗物理 + 精渲染**。
Pixar 毛发用 ~10k 引导曲线驱动 ~1M 渲染细丝；
游戏角色用 ~100 蒙皮骨骼驱动 ~50k 顶点。
共同点：物理在粗粒度上算，渲染在细粒度上补。

</div>

---

## 步骤总览

1. 读取参考 ply
2. 计算 bbox 归一化参数
3. 按帧索引排序 sim ply
4. 读取首帧 sim ply（rest 状态）
5. 建立 rest 对应映射 ← **核心设计选择**
6. 预生成 rest 模板 `full_attrs`
7. 每帧循环：融合位置 + 旋转
8. 归一化坐标变换到输出世界系
9. 原子写入 `frame_NNNN.ply`
10. 下游：构建 npz 缓存

---

## 步骤 1：读取参考 ply

```python
ref_ply = PlyData.read(args.reference_ply)
ref_v = ref_ply["vertex"].data
```

**字段构成：** xyz × 3 · scale × 3 · rot × 4 · opacity × 1 · SH × 48

<div class="deepdive">

3DGS 把不透明度存成 **logit**、把尺度存成 **log** —— 训练时希望参数是无界实数（梯度下降友好），渲染前再用 sigmoid / exp 映回 [0, 1] 和正数。直接存物理量会在饱和区让梯度消失。

SH（球谐函数）= 跟视角有关的颜色。0 阶就是基础 RGB，高阶是反光、半透明等修正项。

</div>

---

## 步骤 2：bbox 归一化

```python
center = (aabb_min + aabb_max) / 2.0
extent = float((aabb_max - aabb_min).max())
ref_xyz_norm = (ref_xyz_raw - center) / extent + 1.0
```

世界坐标 → [0, 2]³ 立方体

<div class="deepdive">

为什么仿真要跑在 [0, 2]³ 里？
MPM 内部有 N×N×N 背景网格。如果让仿真直接用世界坐标，建筑 50m / dx 0.013 → 3800 格，显存爆炸。
归一化把网格参数和场景大小**解耦** —— 同一套 grid_lim=2、n_grid=150 跑任何大小的模型。

</div>

---

## 步骤 3-4：排序 + 读首帧

```python
sim_plys = sorted(sim_dir.glob("sim_*.ply"))
first_data = PlyData.read(str(sim_plys[0]))["vertex"].data
sim_xyz_t0 = np.stack([first_data["x"], ...], axis=1)
```

**显式 sort：** 不依赖 glob 顺序
**读首帧：** 仿真静止状态，对应关系的"零参考点"

<div class="deepdive">

任何蒙皮/绑定算法都要选一个 rest pose（静止姿态）作基线，所有后续帧的形变都相对它计算。
frame 0 时刻每个仿真粒子的位置 = 它代表的那块材料体积的几何中心 —— 这就是"未受力时的自然状态"。

</div>

---

## 步骤 5：rest 对应映射（核心选择）

**两条路线：**

- **K-NN（旧）：** 每个参考 splat 跟踪最近 8 个仿真粒子的**加权平均**
- **1-NN（particle_F 新）：** 每个参考 splat **刚性绑定**到 1 个仿真粒子

<div class="deepdive">

整个 fuse 阶段**最重要的设计决策**。两种路线对应两种失败方式：

- K-NN 偏"过度平滑" → 裂缝被涂抹，出现鬼影
- 1-NN 偏"过度刚性" → 多个 splat 共享同一粒子的粗形状

物理仿真里有专门的名字：**连续介质 vs 离散单元**。

</div>

---

## 步骤 5 · K-NN 路径

```python
sim_tree = cKDTree(sim_xyz_t0_kept)
dists, knn_idx = sim_tree.query(ref_xyz_norm, k=8)
inv_d = 1.0 / (dists + 1e-6)
knn_weights = inv_d / inv_d.sum(axis=1, keepdims=True)
```

类比：角色动画里的**线性混合蒙皮（LBS）**

<div class="deepdive">

K 太小（4）：邻居覆盖不够，加权场不连续。
K 太大（16）：邻居跨远距离，一个 splat 听到整面墙。
K=8 是 3D 空间 LBS 的经验最佳点。

反距离权重无需选超参；高斯权重要调带宽 σ，对粒子密度敏感。

</div>

---

## 步骤 5 · particle_F 1-NN 路径

```python
sim_tree_pf = cKDTree(sim_xyz_t0_kept)
_, pf_1nn_idx = sim_tree_pf.query(ref_xyz_norm, k=1)
pf_ref_rest_offset = ref_xyz_norm - sim_xyz_t0_kept[pf_1nn_idx]
```

每个参考 splat 刚性绑定到一个仿真粒子，记录 rest 偏移

<div class="deepdive">

这是有限元法（FEM）的标准做法 —— 节点之间用 shape function 插值，最简单的就是 nearest-node。

为什么要记 rest 偏移？
如果直接把 splat 吸到粒子位置，~3 个共享同粒子的 splat 会塌缩成一个点。
带上偏移，splat 就像顶点蒙皮上骨骼一样，**飘**在它代表的那块材料里。

</div>

---

## 步骤 6：预生成 rest 模板

```python
full_attrs = np.empty(len(ref_v), dtype=out_dtype)
for field in out_dtype.names:
    full_attrs[field] = ref_v[field]
rest_xyz = _transform_sim_xyz(ref_xyz_norm, args)
full_attrs["x"] = rest_xyz[:, 0]; ...
```

每帧只 `.copy()` 一份再覆盖位置/旋转

<div class="deepdive">

参考 ply 约 161 MB，plyfile 解析约 800ms。
151 帧重读 = 121s 纯 I/O，占总 fuse 时间的 60%。
预生成一次 + 每帧 numpy copy（连续内存 memcpy，~20 GB/s）比磁盘 I/O 快 100×。

</div>

---

## 步骤 7：每帧融合入口

```python
def fuse_one(sp, idx):
    v = PlyData.read(str(sp))["vertex"].data
    sim_xyz = np.stack([v["x"], v["y"], v["z"]], axis=1)
    out_path = out_dir / f"frame_{idx:04d}.ply"
```

读当前帧仿真位置 → 应用映射 → 写输出

<div class="deepdive">

每帧成本拆解：
- 读 sim ply：~30 ms
- K-NN / 1-NN 应用：~50 ms
- particle_F 的 eigendecomp（683k × 3×3）：~150 ms ← **瓶颈**
- 写 frame ply：~80 ms

可压到 ~20ms：用 cupy 批量 SVD。暂未做，整体不是瓶颈。

</div>

---

## 步骤 7a · K-NN 加权位移

```python
sim_disp = sim_kept - sim_xyz_t0_kept           # 每个粒子的位移
neighbors = sim_disp[knn_idx]                    # (n_ref, 8, 3)
ref_disp = (knn_weights * neighbors).sum(axis=1) # 加权平均
ref_xyz_displaced = ref_xyz_norm + ref_disp
```

**关键：平均"位移"，不是"位置"**

<div class="deepdive">

例子：8 个邻居静止时聚在 (0,0,0)，splat 在 (0.1, 0, 0)。

- 平均**位置** → 粒子塌缩到 (0,0,0)，splat 也被拉过去，**细节丢失**
- 平均**位移** → 粒子位移都是 0，splat 仍在 (0.1, 0, 0)，**细节保留**

位移对常数项不敏感，叠加不破坏 splat 跟粒子的相对位置。

</div>

---

## 步骤 7b · particle_F 刚性绑定

```python
# 位置：被绑粒子当前 xyz + rest 偏移
ref_pos_norm = sim_kept[pf_1nn_idx] + pf_ref_rest_offset

# 协方差：被绑粒子当前 cov，乘 extent² 转世界尺度
ref_cov_t = sim_cov6_kept[pf_1nn_idx] * extent**2

# eigh 分解出 quaternion + log_scale
new_quat, log_s = _cov6_to_quat_logscale(ref_cov_t)
```

---

## particle_F · 数学细节

线性变换 `x' = A·x` 作用在协方差上：`Σ' = A·Σ·Aᵀ`

各向同性放缩 `A = extent·I` →
- 位置：一阶，乘 `extent`
- 协方差：二阶，乘 `extent²`

<div class="deepdive">

为什么要 eigendecomp？

3DGS 不直接存协方差矩阵，存的是 (rotation, scale)，合成时算 `Σ = R · diag(s²) · Rᵀ`。

逆过程就是 eigh：给定对称正定 Σ，eigvecs 就是 R 的列，eigvals 就是 s²。
从 R 抽 quaternion，对 s² 开方取 log 得到 log_scale。

</div>

---

## 步骤 8：坐标变换到输出空间

```python
out_xyz_world = _transform_sim_xyz(ref_xyz_displaced, args)
# (x - 1) * extent + center  反归一化
# 可选 Rx(-π/2)  Y-up → Z-up
```

<div class="deepdive">

Y-up 是图形学传统（OpenGL / Blender）；Z-up 是 3D 扫描、建筑、地理信息的默认。
参考 3DGS 是 Y-up 训练的，工作台希望统一展示成 Z-up（建筑站立感更自然），用 Rx(-π/2) 一行修正。

</div>

---

## 步骤 9：原子写入

```python
out = full_attrs.copy()          # 必须 copy，不能改模板本身
out["x"] = ...; out["rot_0"] = ...

tmp_path = Path(str(out_path) + ".tmp")
PlyData([PlyElement.describe(out, "vertex")]).write(tmp_path)
os.replace(tmp_path, out_path)
```

<div class="deepdive">

vkgs / workbench 在 watch 目录实时轮询。如果直接写 `frame_0042.ply`，写一半时 watcher 读到半截文件 → 解析报错或显示乱码。

POSIX 的 `os.replace()` 保证 **rename 是原子的**。读者只会看到旧文件或完整新文件，不存在中间态。

</div>

---

## 步骤 10：下游 npz 缓存

`sequence_to_viser_npz.py` 把 151 个 ply 打包成一个 npz：

```
positions[T, N, 3]   float32   每帧位置
quats[T, N, 4]       float32   每帧旋转
scales²[N, 3]        float32   静态尺度
rgb[N, 3]            uint8     静态颜色
opacity[N, 1]        uint8     静态不透明度
```

<div class="deepdive">

直接读 151 个 ply：23 GB，浏览器没法 mmap，每次切帧解析 200ms+。
打包 npz：2.8 GB，numpy 内存映射，OS 按页加载，切帧零解析。

这是 3DGS 序列流式播放的标准模式 —— NeRF Studio、Inria 3DGS viewer 都这么干。

</div>

---

## 各步骤"为什么"汇总

| 步骤 | 存在的理由 | 不做会怎样 |
|---|---|---|
| 1. 读参考 ply | 仿真不带视觉属性 | 输出黑色无形状 |
| 2. 归一化 bbox | 仿真与渲染坐标系不同 | 网格爆炸或精度损失 |
| 3. 按帧排序 | 时间顺序 | 帧序混乱 |
| 4. 读首帧 sim | 建立 rest 对应 | 蒙皮无参考点 |
| 5. 建立对应映射 | 决定驱动方式 | 无法跨数量映射 |
| 6. 预生成模板 | 摊销 ply 重读成本 | 总时间 ×3 |
| 7. 每帧融合 | 真正的上采样 | 没有动画 |
| 8. 坐标变换 | 渲染器要世界系 | splat 太小或位置错 |
| 9. 原子写入 | watcher 不能看半写 | 间歇性崩溃 |
| 10. npz 缓存 | 浏览器读 1 文件 | 内存爆 / 切帧卡顿 |

---

## 核心取舍：K-NN vs particle_F

|   | **K-NN（旧）** | **particle_F（新）** |
|---|---|---|
| 绑定 | 8 粒子加权平均 | 1 粒子刚性 |
| 适合 | 连续形变 | 含断裂场景 |
| 鬼影（裂缝） | 出现 | 不出现 |
| 形状细节 | 保留 splat 自己的精细形状 | ~3 splat 共享粒子的粗形状 |
| 旋转跟随 | 不跟随 | 跟随 |

**核心矛盾：平滑 ↔ 不连续**
K-NN 等价于低通滤波 —— 把噪声磨平的同时也磨平了裂缝。

---

## 下一步：F-skinning

把两种路线的优点**结合**起来。

splat 保留自己的 rest cov；从仿真粒子的形变中提取一个张量 F：

$$
F = \sqrt{\Sigma_{\text{particle},t} \cdot \Sigma_{\text{particle},rest}^{-1}}
$$

把 F 作用到 splat 自己的 rest cov 上：

$$
\Sigma_{\text{splat},t} = F \cdot \Sigma_{\text{splat},rest} \cdot F^T
$$

**结果：** splat 保留细节，又能正确跟随形变。

---

## 总结

**fuse 本质 = 坐标桥 + 上采样规则 + 每帧属性覆盖**

```
sim 输出 (200k 粒子, 归一化空间)
    │
    ▼
[ 坐标桥：归一化 ↔ 世界 ]
    │
    ▼
[ 上采样规则：K-NN / 1-NN+cov ]   ◀── 视觉保真度的核心取舍
    │
    ▼
[ 属性覆盖：rest 模板 + 每帧位置/旋转 ]
    │
    ▼
frame_NNNN.ply (683k splat, 世界空间, 可渲染)
```

视觉效果好坏 ≈ 上采样规则的选择。

---

## 谢谢

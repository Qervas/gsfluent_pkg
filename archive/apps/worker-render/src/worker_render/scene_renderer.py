"""Splat scene rasterizer.

Tasks 5.5 + 5.7 from the rebuild plan.

Loads a 3DGS scene (PLY) from MinIO and rasterizes frames given a
camera pose. Two backends, tried in order:

1. `gsplat` (Nerfstudio's CUDA rasterizer) — preferred. Returns frames
   directly on GPU memory; we host-copy to numpy uint8 for the aiortc
   pipeline.
2. Test pattern — animated gradient. Used when gsplat / torch aren't
   importable so the container still boots in dev environments.

Camera state is held by the renderer; the WebRTC data channel handler
in peer.py calls `set_camera(T, R)` between frames.
"""

from __future__ import annotations

import asyncio
import math
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger("scene")

DEFAULT_WIDTH = 960
DEFAULT_HEIGHT = 540


@dataclass
class CameraPose:
    """World-to-camera. Translation in world units; quat as (w, x, y, z)."""
    translation: tuple[float, float, float] = (0.0, 0.0, 3.0)
    rotation_quat: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    fov_y_deg: float = 60.0


class _TestPatternRenderer:
    """Fallback: animated RGB gradient. Same shape as the real renderer
    so the peer code doesn't branch."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.camera = CameraPose()
        self.frame_idx = 0
        self.lock = threading.Lock()

    def set_camera(self, pose: CameraPose) -> None:
        with self.lock:
            self.camera = pose

    def render(self) -> np.ndarray:
        with self.lock:
            t = self.frame_idx
            self.frame_idx += 1
            arr = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            arr[:, :, 0] = (t * 2) % 256
            arr[:, :, 1] = (t * 3) % 256
            arr[:, :, 2] = (t * 5) % 256
            return arr


class _GsplatRenderer:
    """gsplat-backed renderer. Loads the scene once on construction.

    The rasterization call is `gsplat.rasterization` (or `project_gaussians`
    + alpha composite in older API versions). We use the simplest 'forward'
    path; differentiable / training paths aren't needed for playback.
    """

    def __init__(self, model_ply_path: Path, width: int, height: int) -> None:
        # Lazy-imports so the worker can boot without torch/gsplat in dev.
        import torch  # type: ignore[import-untyped]
        from gsplat.rendering import rasterization  # type: ignore[import-not-found]
        from plyfile import PlyData

        self._torch = torch
        self._rasterize = rasterization
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.width = width
        self.height = height
        self.camera = CameraPose()
        self.lock = threading.Lock()

        ply = PlyData.read(str(model_ply_path))
        v = ply["vertex"].data

        # 3DGS PLY columns vary by exporter. Common: x,y,z, scale_0..2,
        # rot_0..3 (quat), opacity, f_dc_0..2 (DC color), f_rest_0..N (SH).
        xyz = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)
        scales = np.stack(
            [v["scale_0"], v["scale_1"], v["scale_2"]], axis=1
        ).astype(np.float32)
        # gsplat expects exp(scale) → so v1 PLYs ship log-scale, just exp on render.
        scales = np.exp(scales)
        rots = np.stack(
            [v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=1
        ).astype(np.float32)
        opacity = (1.0 / (1.0 + np.exp(-np.asarray(v["opacity"], dtype=np.float32))))
        # DC SH coefficient -> RGB via 0.5 + C0*coeff per 3DGS convention.
        c0 = 0.28209479177387814
        rgb = 0.5 + c0 * np.stack(
            [v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=1
        ).astype(np.float32)
        rgb = np.clip(rgb, 0.0, 1.0)

        self.means = torch.from_numpy(xyz).to(self.device)
        self.scales = torch.from_numpy(scales).to(self.device)
        self.quats = torch.from_numpy(rots).to(self.device)
        self.opacities = torch.from_numpy(opacity).to(self.device)
        self.colors = torch.from_numpy(rgb).to(self.device)

        log.info("scene.loaded",
                 splats=int(self.means.shape[0]),
                 device=str(self.device))

    def set_camera(self, pose: CameraPose) -> None:
        with self.lock:
            self.camera = pose

    def render(self) -> np.ndarray:
        torch = self._torch
        with self.lock:
            cam = self.camera

        # View matrix from CameraPose.
        # Quat (w, x, y, z) -> 3x3 rotation.
        w, x, y, z = cam.rotation_quat
        n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
        w, x, y, z = w / n, x / n, y / n, z / n
        rot = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ], dtype=np.float32)
        t = np.asarray(cam.translation, dtype=np.float32)

        viewmat = np.eye(4, dtype=np.float32)
        viewmat[:3, :3] = rot
        viewmat[:3, 3] = -rot @ t
        viewmat_t = torch.from_numpy(viewmat).to(self.device)[None, ...]

        fy = (self.height / 2) / math.tan(math.radians(cam.fov_y_deg) / 2)
        fx = fy
        cx = self.width / 2
        cy = self.height / 2
        K = np.array([
            [fx, 0, cx],
            [0, fy, cy],
            [0, 0, 1.0],
        ], dtype=np.float32)
        K_t = torch.from_numpy(K).to(self.device)[None, ...]

        renders, _, _ = self._rasterize(
            self.means,
            self.quats,
            self.scales,
            self.opacities,
            self.colors,
            viewmats=viewmat_t,
            Ks=K_t,
            width=self.width,
            height=self.height,
            packed=False,
        )
        img = renders[0].clamp(0.0, 1.0)
        np_img = (img.detach().cpu().numpy() * 255).astype(np.uint8)
        if np_img.shape[2] == 4:
            np_img = np_img[..., :3]
        return np_img


class SceneRenderer:
    """Public renderer. Picks the best backend available."""

    def __init__(
        self,
        model_ply_path: Path | None,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
    ) -> None:
        self.width = width
        self.height = height
        self._impl: _TestPatternRenderer | _GsplatRenderer

        if model_ply_path is None:
            log.warning("scene.no_model — using test pattern")
            self._impl = _TestPatternRenderer(width, height)
            return

        try:
            self._impl = _GsplatRenderer(model_ply_path, width, height)
            log.info("scene.backend", backend="gsplat")
        except Exception as e:  # noqa: BLE001
            log.warning("scene.gsplat_unavailable",
                        error=str(e)[:200],
                        fallback="test_pattern")
            self._impl = _TestPatternRenderer(width, height)

    def set_camera(self, pose: CameraPose) -> None:
        self._impl.set_camera(pose)

    def render_frame(self) -> np.ndarray:
        return self._impl.render()


async def load_model_to_temp(model_minio_path: str) -> Path:
    """Download a 3DGS PLY from MinIO into a temp file for the renderer."""
    from gsfluent_api.storage import get_minio_client

    bucket, _, key = model_minio_path.partition("/")
    tmp = Path(tempfile.NamedTemporaryFile(
        suffix=".ply", delete=False, prefix="gsfluent-model-",
    ).name)

    def _download() -> None:
        get_minio_client().fget_object(bucket, key, str(tmp))

    await asyncio.to_thread(_download)
    log.info("scene.model_downloaded", path=str(tmp),
             size=tmp.stat().st_size)
    return tmp

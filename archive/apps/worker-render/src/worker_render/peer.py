"""Single render-session peer. Handles SDP signaling + ICE + a video track
with a real splat scene rasterizer (gsplat when available, test-pattern
fallback otherwise).

Tasks 5.3 + 5.5 + 5.7 + 5.8 from the rebuild plan.

Camera control: data channel "camera" — client sends {"type": "setPose",
"T": [x,y,z], "R": [w,x,y,z]} between frames; the renderer applies the
pose on its next render() call.

Throttle: subscribes to the `gpu.sim_running` Redis channel; when sim is
running (worker-sim publishes "1") we cut framerate in half so the
single A100 doesn't melt (spec §8.2).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
import uuid
from pathlib import Path

import av  # type: ignore[import-untyped]
import redis.asyncio as aioredis
import structlog
from aiortc import (
    RTCConfiguration,
    RTCIceCandidate,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamTrack

from .config import get_settings
from .scene_renderer import CameraPose, SceneRenderer

log = structlog.get_logger("peer")

SIM_RUNNING_CHANNEL = "gpu.sim_running"


class SplatSceneTrack(MediaStreamTrack):
    """Video track that pulls frames from a SceneRenderer."""

    kind = "video"

    def __init__(
        self,
        renderer: SceneRenderer,
        fps: int = 30,
    ) -> None:
        super().__init__()
        self.renderer = renderer
        self.base_fps = fps
        self.fps = fps
        self._counter = 0
        self._start = time.monotonic()

    def set_fps(self, fps: int) -> None:
        self.fps = max(1, fps)

    async def recv(self) -> av.VideoFrame:  # type: ignore[name-defined]
        # Pace to target fps. fps may have changed mid-stream (throttle).
        target = self._start + self._counter / self.fps
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

        arr = await asyncio.to_thread(self.renderer.render_frame)
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        self._counter += 1
        return frame


async def _resolve_target_model(session_id: uuid.UUID) -> str | None:
    """Look up the RenderSession row + return the linked model's MinIO path."""
    import os

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from gsfluent_api.models.model import Model
    from gsfluent_api.models.render_session import RenderSession
    from gsfluent_api.models.run import Run

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.warning("scene.no_db_url")
        return None
    engine = create_async_engine(db_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            rs = await s.get(RenderSession, session_id)
            if rs is None:
                return None
            if rs.model_id is not None:
                model = await s.get(Model, rs.model_id)
                return model.minio_path if model else None
            if rs.run_id is not None:
                run = await s.get(Run, rs.run_id)
                if run is None:
                    return None
                model = await s.get(Model, run.model_id)
                return model.minio_path if model else None
    finally:
        await engine.dispose()
    return None


async def _throttle_watcher(track: SplatSceneTrack, redis: aioredis.Redis,
                            stop: asyncio.Event) -> None:
    """Watches gpu.sim_running channel; halves track fps while sim is running."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(SIM_RUNNING_CHANNEL)
    try:
        async for message in pubsub.listen():
            if stop.is_set():
                return
            if message.get("type") != "message":
                continue
            data = message["data"]
            running = (data.decode() if isinstance(data, bytes) else data).strip() == "1"
            new_fps = track.base_fps // 2 if running else track.base_fps
            track.set_fps(new_fps)
            log.info("track.throttle",
                     sim_running=running, fps=new_fps)
    finally:
        try:
            await pubsub.unsubscribe(SIM_RUNNING_CHANNEL)
        except Exception:  # noqa: BLE001
            pass
        await pubsub.aclose()


def _handle_camera_message(renderer: SceneRenderer, raw: str) -> None:
    """Parse a 'setPose' data channel message and apply to the renderer."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    if msg.get("type") != "setPose":
        return
    t = msg.get("T")
    r = msg.get("R")
    if not (isinstance(t, list) and len(t) == 3 and isinstance(r, list) and len(r) == 4):
        return
    pose = CameraPose(
        translation=(float(t[0]), float(t[1]), float(t[2])),
        rotation_quat=(float(r[0]), float(r[1]), float(r[2]), float(r[3])),
        fov_y_deg=float(msg.get("fov", 60.0)),
    )
    renderer.set_camera(pose)


async def run_peer(session_id: uuid.UUID, redis: aioredis.Redis) -> None:
    """Spin up a peer for one render session and signal until it closes."""
    s = get_settings()
    offer_ch = f"render:session:{session_id}:offer"
    answer_ch = f"render:session:{session_id}:answer"
    cand_in = f"render:session:{session_id}:candidate-in"
    events_ch = f"events:render-session:{session_id}"

    # Resolve the scene's model + load it (gsplat backend if torch/gsplat
    # are importable; test pattern otherwise).
    model_minio_path = await _resolve_target_model(session_id)
    ply_path: Path | None = None
    if model_minio_path:
        from .scene_renderer import load_model_to_temp
        try:
            ply_path = await load_model_to_temp(model_minio_path)
        except Exception as e:  # noqa: BLE001
            log.warning("scene.load_failed", error=str(e)[:200])
    renderer = SceneRenderer(ply_path)
    track = SplatSceneTrack(renderer, fps=30)

    config = RTCConfiguration(iceServers=[])
    pc = RTCPeerConnection(configuration=config)
    pc.addTrack(track)

    @pc.on("icecandidate")
    def _on_local_candidate(candidate: RTCIceCandidate | None) -> None:
        if candidate is None:
            return
        body = json.dumps({
            "type": "webrtc.candidate",
            "session_id": str(session_id),
            "candidate": {
                "candidate": candidate.candidate,
                "sdpMid": candidate.sdpMid,
                "sdpMLineIndex": candidate.sdpMLineIndex,
            },
            "timestamp": dt.datetime.now(dt.UTC).isoformat(),
        })
        asyncio.create_task(redis.publish(events_ch, body))

    @pc.on("datachannel")
    def _on_dc(channel: object) -> None:
        name = getattr(channel, "label", "")
        log.info("dc.open", session=str(session_id), label=name)

        @channel.on("message")  # type: ignore[union-attr]
        def _on_msg(msg: object) -> None:
            if isinstance(msg, (bytes, bytearray)):
                msg = msg.decode("utf-8", errors="replace")
            if isinstance(msg, str):
                _handle_camera_message(renderer, msg)

    pubsub = redis.pubsub()
    await pubsub.subscribe(offer_ch, cand_in)
    log.info("peer.start", session=str(session_id), worker=s.worker_id)

    stop_throttle = asyncio.Event()
    throttle_task = asyncio.create_task(
        _throttle_watcher(track, redis, stop_throttle),
        name=f"throttle-{session_id}",
    )

    last_activity = time.monotonic()

    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            channel = message["channel"].decode() if isinstance(message["channel"], bytes) else message["channel"]
            data = message["data"].decode() if isinstance(message["data"], bytes) else message["data"]

            if channel == offer_ch:
                offer = json.loads(data)
                await pc.setRemoteDescription(
                    RTCSessionDescription(sdp=offer["sdp"], type=offer["type"])
                )
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                await redis.publish(
                    answer_ch,
                    json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}),
                )
                last_activity = time.monotonic()
            elif channel == cand_in:
                cand = json.loads(data)
                rtc_cand = RTCIceCandidate(
                    candidate=cand["candidate"],
                    sdpMid=cand["sdpMid"],
                    sdpMLineIndex=cand["sdpMLineIndex"],
                )
                await pc.addIceCandidate(rtc_cand)
                last_activity = time.monotonic()

            if time.monotonic() - last_activity > s.idle_seconds:
                log.info("peer.idle_timeout", session=str(session_id))
                break

            if pc.connectionState in ("failed", "closed"):
                log.info("peer.terminal",
                         session=str(session_id), state=pc.connectionState)
                break
    finally:
        stop_throttle.set()
        throttle_task.cancel()
        await asyncio.gather(throttle_task, return_exceptions=True)
        await pubsub.aclose()
        await pc.close()
        if ply_path:
            try:
                ply_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        log.info("peer.end", session=str(session_id))

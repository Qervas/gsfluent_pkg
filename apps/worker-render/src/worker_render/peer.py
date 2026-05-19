"""Single render-session peer. Handles SDP signaling + ICE + a video track.

Phase 5 scaffold uses TestPatternTrack so the wiring can be validated
end-to-end (browser ↔ aiortc) without viser / NVENC / engine.

Task 5.5 swaps in a ViserSceneTrack that rasterizes a real 3DGS scene.
Task 5.6 swaps the SW encoder for h264_nvenc.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import av  # type: ignore[import-untyped]
import numpy as np
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

log = structlog.get_logger("peer")


class TestPatternTrack(MediaStreamTrack):
    """Animated RGB gradient. Replaced by viser rasterizer in Task 5.5."""

    kind = "video"

    def __init__(self, width: int = 640, height: int = 480, fps: int = 30) -> None:
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self._counter = 0
        self._start = time.monotonic()

    async def recv(self) -> av.VideoFrame:  # type: ignore[name-defined]
        # Pace to target fps.
        target = self._start + self._counter / self.fps
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

        t = self._counter
        arr = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        arr[:, :, 0] = (t * 2) % 256
        arr[:, :, 1] = (t * 3) % 256
        arr[:, :, 2] = (t * 5) % 256

        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        self._counter += 1
        return frame


async def run_peer(session_id: uuid.UUID, redis: aioredis.Redis) -> None:
    """Spin up a peer for one render session and signal until it closes."""
    import datetime as dt  # noqa: PLC0415

    s = get_settings()
    offer_ch = f"render:session:{session_id}:offer"
    answer_ch = f"render:session:{session_id}:answer"
    cand_in = f"render:session:{session_id}:candidate-in"
    # Local candidates fan out via the WS-routed events channel so the
    # client receives them through its existing /v1/stream subscription.
    events_ch = f"events:render-session:{session_id}"

    config = RTCConfiguration(iceServers=[])
    pc = RTCPeerConnection(configuration=config)
    pc.addTrack(TestPatternTrack())

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
        # Fire-and-forget publish; aiortc invokes this synchronously.
        asyncio.create_task(redis.publish(events_ch, body))

    @pc.on("datachannel")
    def _on_dc(channel: object) -> None:
        @channel.on("message")  # type: ignore[union-attr]
        def _on_msg(msg: object) -> None:
            log.debug("dc.msg", session=str(session_id), msg_len=len(str(msg)))

    pubsub = redis.pubsub()
    await pubsub.subscribe(offer_ch, cand_in)
    log.info("peer.start", session=str(session_id), worker=s.worker_id)

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

            # Idle timeout — close peer if no signaling activity for too long.
            if time.monotonic() - last_activity > s.idle_seconds:
                log.info("peer.idle_timeout", session=str(session_id))
                break

            if pc.connectionState in ("failed", "closed"):
                log.info("peer.terminal", session=str(session_id), state=pc.connectionState)
                break
    finally:
        await pubsub.aclose()
        await pc.close()
        log.info("peer.end", session=str(session_id))

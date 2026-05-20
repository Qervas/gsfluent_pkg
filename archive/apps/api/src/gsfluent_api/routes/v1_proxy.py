"""Reverse-proxy /api/* (and /api/stream WS) to v1 backend on loopback.

The v1 frontend SPA hits /api/* which doesn't exist in v2 — implementing
all 25 endpoints natively is a multi-day effort. For the immediate demo
we run v1 backend on an internal port (7870 by default) and proxy its
surface through v2 api. The public hostname stays on v2's :7869.

v2's own /v1/* routes are unaffected — they register first and the
proxy only matches /api/*.

The WS proxy supports v1's /api/stream protocol (mixed JSON control
messages + binary frame data); both directions stream through this hop.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import Response, StreamingResponse
from starlette.websockets import WebSocketDisconnect

V1_BASE = os.environ.get("V1_API_BASE", "http://127.0.0.1:7870")
V1_WS_BASE = V1_BASE.replace("http://", "ws://").replace("https://", "wss://")

# Strip hop-by-hop headers per RFC 7230 §6.1 + content-encoding (we don't
# rewrite the body so any encoding the upstream chose stays valid).
_DROP_REQ_HEADERS = {"host", "content-length", "connection", "keep-alive"}
_DROP_RESP_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "proxy-authenticate",
    "proxy-authorization",
    "trailers",
}

router = APIRouter()


@router.api_route(
    "/api/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_api(full_path: str, request: Request) -> Response:
    target = f"{V1_BASE}/api/{full_path}"
    qs = urlencode(list(request.query_params.multi_items()))
    if qs:
        target = f"{target}?{qs}"

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _DROP_REQ_HEADERS
    }
    body = await request.body()

    # Generous timeout — sequence-list with metadata reads can take a
    # moment, and the upload-npz / sim-start endpoints may block on disk I/O.
    timeout = httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=5.0)
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)

    req = client.build_request(
        request.method, target, headers=headers, content=body,
    )
    upstream = await client.send(req, stream=True)
    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _DROP_RESP_HEADERS
    }

    async def passthrough() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        passthrough(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


# ---------- /api/stream WS proxy --------------------------------------


@router.websocket("/api/stream")
async def proxy_ws(client_ws: WebSocket) -> None:
    """Bidirectionally proxy the v1 stream WebSocket.

    v1 sends mixed text (JSON control) + binary (frame xyz arrays).
    The starlette WS API exposes both via receive()/send_bytes/send_text;
    we mirror that to the upstream Python `websockets` client.
    """
    await client_ws.accept()

    # Lazy import — `websockets` is shipped with uvicorn[standard].
    from websockets.asyncio.client import connect as ws_connect
    from websockets.exceptions import ConnectionClosed

    upstream_url = f"{V1_WS_BASE}/api/stream"
    upstream = None
    try:
        upstream = await ws_connect(upstream_url, max_size=None)

        async def client_to_upstream() -> None:
            try:
                while True:
                    msg = await client_ws.receive()
                    mtype = msg.get("type")
                    if mtype == "websocket.disconnect":
                        return
                    if "text" in msg and msg["text"] is not None:
                        await upstream.send(msg["text"])
                    elif "bytes" in msg and msg["bytes"] is not None:
                        await upstream.send(msg["bytes"])
            except (WebSocketDisconnect, ConnectionClosed):
                return

        async def upstream_to_client() -> None:
            try:
                async for msg in upstream:
                    if isinstance(msg, bytes | bytearray):
                        await client_ws.send_bytes(bytes(msg))
                    else:
                        await client_ws.send_text(msg)
            except (WebSocketDisconnect, ConnectionClosed):
                return

        await asyncio.gather(
            client_to_upstream(),
            upstream_to_client(),
            return_exceptions=True,
        )
    except Exception:
        pass
    finally:
        if upstream is not None:
            try:
                await upstream.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            await client_ws.close()
        except Exception:  # noqa: BLE001
            pass

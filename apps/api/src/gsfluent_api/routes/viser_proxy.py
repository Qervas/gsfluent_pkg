"""Reverse-proxy /viser-iframe/* (HTTP + WS) + /viser-ctrl/* (HTTP) to
viser_headless running on the sxyin loopback.

viser_headless binds 127.0.0.1:8091 (the splat renderer SPA + WS) and
127.0.0.1:8092 (the FastAPI control plane). The sxyin host only exposes
the v2 api's public port :24701 → :7869, so the only way the browser
can reach viser is through this proxy.

Two endpoints:

* /viser-iframe/    GET serves the viser SPA's index.html (single self-
                    contained file, ~2.8 MB with all CSS/JS/wasm inlined
                    and zstd-compressed). WS upgrade is on the SAME
                    path; the viser client constructs its WS URL from
                    `location.href` with http→ws + trailing-slash strip,
                    so it ends up connecting to `/viser-iframe` (no
                    slash) — and we register the WS route there.

* /viser-ctrl/*     Proxied HTTP for the headless control API
                    (/state, /sync-status, /set, /camera, /load, ...).
                    No WS here.
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

VISER_HTTP = os.environ.get("VISER_HTTP_BASE", "http://127.0.0.1:8091")
VISER_WS = VISER_HTTP.replace("http://", "ws://").replace("https://", "wss://")
CTRL_HTTP = os.environ.get("VISER_CTRL_BASE", "http://127.0.0.1:8092")

_DROP_REQ_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
}
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


async def _proxy_http(target: str, request: Request) -> Response:
    qs = urlencode(list(request.query_params.multi_items()))
    if qs:
        target = f"{target}?{qs}"
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _DROP_REQ_HEADERS
    }
    body = await request.body()
    timeout = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)
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


# ---------- viser SPA (HTTP) ------------------------------------------


# Style overlay we inject into the viser SPA's <head>. Hides Mantine's
# Paper-root floating panels (the "Connected / Save Canvas / Reset View /
# Orbit Origin Tool / Dev Settings / Scene tree" side panel) so the
# customer-facing pitch view is a clean viewport. The selector only
# matches Mantine Paper containers — buttons, inputs and the rest of
# viser's UI use different Mantine components and are untouched.
_VISER_STYLE_OVERLAY = (
    b"<style>"
    b".mantine-Paper-root{display:none!important;}"
    b"</style></head>"
)


@router.api_route(
    "/viser-iframe/",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def viser_iframe_root(request: Request) -> Response:
    """Fetch viser's root HTML, inject the style overlay, return it.

    The HTML is small enough (~2.2 MB, single self-contained file with
    all JS/CSS inlined) that buffering once is fine; there are no
    follow-up asset requests. HEAD is passed through unmodified.
    """
    if request.method == "HEAD":
        return await _proxy_http(f"{VISER_HTTP}/", request)

    timeout = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _DROP_REQ_HEADERS
    }
    # Force identity encoding from viser — we want plain HTML so the
    # byte-string injection works. viser respects Accept-Encoding and
    # returns the uncompressed file_cache[] when gzip isn't requested.
    headers["accept-encoding"] = "identity"
    async with httpx.AsyncClient(timeout=timeout) as client:
        upstream = await client.get(f"{VISER_HTTP}/", headers=headers)
        body = upstream.content
        resp_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in _DROP_RESP_HEADERS
        }
    # Inject right before </head>. If we can't find it (viser refactor
    # could change the markup), just pass the body through unchanged.
    if b"</head>" in body:
        body = body.replace(b"</head>", _VISER_STYLE_OVERLAY, 1)
    resp_headers["content-length"] = str(len(body))
    return Response(
        content=body,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


# Catch-all for any sub-paths the viser SPA might fetch later (none
# today — it's a single self-contained HTML file — but registered for
# forward-compat). MUST appear after the `/viser-iframe/` route above
# so FastAPI tries the more specific match first.
@router.api_route(
    "/viser-iframe/{full_path:path}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def viser_iframe(full_path: str, request: Request) -> Response:
    return await _proxy_http(f"{VISER_HTTP}/{full_path}", request)


# ---------- viser SPA (WS) --------------------------------------------
#
# viser client computes its WS URL by taking location.href, rewriting
# http→ws / https→wss, and stripping any trailing slash. The iframe
# loads at /viser-iframe/ so the WS URL ends up at /viser-iframe.


@router.websocket("/viser-iframe")
async def viser_ws(client_ws: WebSocket) -> None:
    """Bidirectional WS proxy with subprotocol negotiation.

    viser uses subprotocols `viser-v<X.Y.Z>` for version-pinning; if we
    drop them the server refuses the handshake. Starlette gives us the
    list of requested subprotocols via `client_ws['subprotocols']` and
    accepts with a chosen one via `await client_ws.accept(subprotocol=...)`.
    We forward all candidates upstream and re-use the one upstream picks.
    """
    requested = client_ws.scope.get("subprotocols") or []

    from websockets.asyncio.client import connect as ws_connect
    from websockets.exceptions import ConnectionClosed

    upstream_url = f"{VISER_WS}/"
    upstream = None
    try:
        try:
            upstream = await ws_connect(
                upstream_url,
                max_size=50 * 1024 * 1024,  # match viser's server-side cap
                subprotocols=requested or None,
            )
        except Exception as e:  # noqa: BLE001
            # Surface as a 1011 close so the browser console shows a
            # clear error instead of a silent "Server returned WebSocket
            # error" line with no detail.
            await client_ws.close(code=1011, reason=f"upstream: {e}"[:120])
            return

        chosen = upstream.subprotocol
        await client_ws.accept(subprotocol=chosen)

        async def client_to_upstream() -> None:
            try:
                while True:
                    msg = await client_ws.receive()
                    if msg.get("type") == "websocket.disconnect":
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


# ---------- viser control plane (HTTP only) ---------------------------


@router.api_route(
    "/viser-ctrl/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def viser_ctrl(full_path: str, request: Request) -> Response:
    return await _proxy_http(f"{CTRL_HTTP}/{full_path}", request)

# CDN setup for `splats.gsq` edge caching

The `splats.gsq` cache is the largest single artifact the gsfluent backend
serves — typically 0.4-1 GB per sequence — and it's strictly immutable
once a sequence has been packed (a re-pack produces a new file with a
new mtime; the `(size, mtime)` ETag changes, so any conditional GET
naturally re-fetches). Putting a CDN in front of the backend lets the
first user warm the edge POP nearest them, and everyone after that
streams from POP instead of from the GPU host.

This is a pure operational change — **no code changes needed**. The
endpoint already emits the right headers:

| Header | Value |
|---|---|
| `Cache-Control` | `public, immutable, max-age=31536000` |
| `ETag` | `"<size>-<mtime>"` (weak) |
| `Accept-Ranges` | `bytes` (FileResponse default) |

So any compliant CDN can keep the body for a year and revalidate via
the ETag when the sequence is re-packed.

## Why a CDN is worth it

Without a CDN, every viser_headless boot on a new client hits the
origin GPU host directly. Latency math, very rough:

| Path | Round-trip time | First-byte | 1 GB transfer (100 Mbit) |
|---|---|---|---|
| Client ↔ GPU origin (cross-region) | 100-200 ms | ~250 ms | 90 s + RTT |
| Client ↔ nearest POP (Cloudflare) | 5-20 ms | ~30 ms | 90 s + 20 ms |

For first-byte the win is ~200 ms; for the cold-cell decode the
TTFF (time to first frame) shifts from "RTT-bound on every byte" to
"throughput-bound on a short hop." For a warmed edge, repeat fetches
from a different team member skip the GPU entirely.

## Recommended providers

Pick one based on operational fit, not features — they all do the
basics correctly for our use case (long-lived immutable bodies + range
requests).

| Provider | Free tier | Range requests | Caches `Cache-Control: immutable` | Notes |
|---|---|---|---|---|
| Cloudflare | yes (generous) | yes, but with a caveat (below) | yes | Easiest DNS setup; default proxy mode |
| Bunny CDN | no, ~$0.01/GB | yes | yes | Per-POP control; cheap if traffic isn't huge |
| Fastly | dev tier free | yes | yes | VCL config; most powerful but biggest learning curve |

Below the doc shows Cloudflare (most common starter). Bunny + Fastly
work identically as far as the gsfluent contract is concerned.

## Cloudflare setup

### 1. Point DNS at Cloudflare

Add the backend hostname as a `CNAME` or `A` record under your
Cloudflare zone, with the orange-cloud proxy ON:

```
backend.your-domain.com    CNAME    backend-origin.your-domain.com    (proxied)
backend-origin.your-domain.com    A    <GPU host public IP>           (DNS-only)
```

The origin record (`backend-origin.*`) stays DNS-only so the Caddy
deploy on your GPU host can fetch a Let's Encrypt cert without
fighting Cloudflare's edge cert.

### 2. Cache rules

In Cloudflare's dashboard, **Caching → Cache Rules**, add a rule:

| Field | Value |
|---|---|
| Name | gsfluent splats.gsq edge cache |
| Match | `URI Path` starts with `/api/sequences/` AND `URI Path` ends with `/cache/splats.gsq` |
| Cache eligibility | Eligible for cache |
| Edge TTL | Use cache-control header from origin |
| Browser TTL | Use cache-control header from origin |
| Respect strong ETags | Yes |

This activates **Cache Everything** for splats.gsq URLs, defers to
the origin's `Cache-Control: immutable, max-age=31536000`, and uses
the ETag for revalidation.

A second rule explicitly **bypasses** the cache for the rest of the
API surface (small JSON responses that aren't worth caching and
shouldn't be cached because they're per-tenant + dynamic):

| Field | Value |
|---|---|
| Name | gsfluent API bypass |
| Match | `URI Path` starts with `/api/` AND `URI Path` does not end with `/cache/splats.gsq` |
| Cache eligibility | Bypass cache |

Order matters: place the bypass rule BELOW the splats.gsq rule. Cloudflare
processes rules top-to-bottom and the first match wins.

### 3. Verify it works

After the rules propagate (~1 min):

```bash
# First fetch warms the edge for this POP.
curl -sI 'https://backend.your-domain.com/api/sequences/<name>/cache/splats.gsq'
# → look for cf-cache-status: MISS (or EXPIRED on the first hit after deploy)

# Second fetch should be HIT.
curl -sI 'https://backend.your-domain.com/api/sequences/<name>/cache/splats.gsq'
# → cf-cache-status: HIT

# Bypassed endpoints should NOT be cached.
curl -sI 'https://backend.your-domain.com/api/sequences'
# → cf-cache-status: BYPASS  (or DYNAMIC)
```

If the first fetch is HIT immediately, the cache was warmed by another
user — that's the goal. If it's stuck on MISS even on repeat fetches,
the rule didn't take effect; double-check the path-match pattern.

### 4. Cache invalidation

The .gsq is immutable per (name, size, mtime), so **invalidation is
generally unnecessary**:

- Repacking a sequence (`server/tools/pack_splats.py <name>`) produces
  a new size+mtime → new ETag. Browsers + the viser_headless client
  send `If-None-Match: <old-etag>`; the CDN sees the new ETag from
  origin, replaces its cache entry, and the next fetch returns the new
  body. No purge call needed.
- Deleting a sequence (`DELETE /api/sequences/<name>`) leaves a stale
  cached body at the edge until the entry's max-age expires. If you
  want to free the edge slot immediately, run a single-URL purge:
  - Cloudflare dashboard → Caching → Configuration → Purge by URL → paste the splats.gsq URL.
  - or `curl -X POST -H "Authorization: Bearer $CF_TOKEN" \
    -d '{"files":["https://backend.your-domain.com/api/sequences/<name>/cache/splats.gsq"]}' \
    https://api.cloudflare.com/client/v4/zones/$ZONE_ID/purge_cache`

## The Range-request gotcha

Cloudflare's **free + Pro tiers do NOT honor Range requests for cached
files larger than 1 GB**: the first request gets the full body cached
in chunks, but subsequent partial-content requests fall back to the
origin instead of being served from cache slices. See [Cloudflare's
docs on Range request support](https://developers.cloudflare.com/cache/concepts/cache-behavior/#range-requests).

For our `.gsq` files this matters because:

- **Cold fetches (no .partial on disk)**: the viser_headless client
  issues a single full-body GET. Works fine on all tiers; the CDN
  caches the full body and serves it to the next client from POP.
- **Resumed fetches (a .partial from a prior interrupted run)**:
  viser_headless sends `Range: bytes=<n>-`. On free/Pro tiers, this
  bypasses the cache and hits origin. Functionally correct (the
  download still works) but the CDN win is lost on that specific
  resume.

What to do:

- **Stay on free/Pro**: accept the perf gap on resumes. The win on
  fresh fetches (the common case) is still huge.
- **Move to Business tier**: $200/mo, gets cached range support.
- **Move to Enterprise tier**: gets per-byte-range caching and prefetch
  hints. Overkill for most deployments.
- **Switch to Bunny CDN**: honors range requests from cache on all
  tiers, at ~$0.01/GB. Better fit if you're sensitive to resume cost
  but not ready for CF Business pricing.

For most users on free/Pro the resume case is rare enough (only
happens when a previous viser_headless boot was interrupted mid-download)
that the loss is academic.

## Operational notes

- **The CDN doesn't help WebSocket traffic** (port 8092, viser's own
  WS). Those connections still hit the GPU host directly. The
  WS payload is small (~1 KB/frame for the per-frame /set + camera
  pushes), so this is fine.
- **The CDN doesn't help the FastAPI control plane**: `/api/runs`,
  `/api/sequences` list, `/api/recipes`, etc. all return tiny
  per-tenant dynamic JSON; caching them would cause staleness without
  meaningful win. The bypass rule above ensures they always hit origin.
- **Set up Caddy first** (`deploy/caddy.example.conf`). Caddy gives
  you HTTP/2 + TLS at the origin. The CDN sits in FRONT of Caddy and
  speaks HTTP/2 to clients regardless. Don't put a CDN in front of
  raw uvicorn (HTTP/1.1 only) unless your CDN handles HTTP/2 ↔ HTTP/1.1
  protocol translation (most do).

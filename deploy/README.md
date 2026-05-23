# gsfluent backend — systemd deployment

This directory contains the systemd unit files that supervise the
gsfluent FastAPI backend. They replace the old `server/supervise.sh`
shell loop with a proper notify-mode service that:

- restarts the backend automatically if it crashes
- kills the backend if it stops responding to systemd's watchdog (30s)
- routes structured JSON logs through journald
- propagates SIGTERM to sim subprocesses via the backend's own
  PG-signal logic on graceful shutdown

Two unit files are provided:

| File | When to use |
|---|---|
| `gsfluent-backend.service` | Production. Runs as a dedicated `gsfluent` system user under `/opt/gsfluent`. |
| `gsfluent-backend.dev.service` | Dev box / single-operator. Runs as the current user from the repo checkout. |

Both expect Python 3.10+, the unified `uv`-managed virtualenv at
`<repo>/.venv` (single venv for both server and client stacks; see
`frontend/scripts/install.mjs` or `server/Makefile`), and the env
vars `GSFLUENT_SIM_HOME` + `GSFLUENT_SIM_PYTHON` set (typically via
an `.env` file loaded by `EnvironmentFile=`).

## Install — production form

Replace `/opt/gsfluent` with your actual checkout path if different.

```bash
# 1. Create the system user.
sudo useradd --system --shell /usr/sbin/nologin --home /opt/gsfluent gsfluent

# 2. Lay down the code and the unified venv.
sudo mkdir -p /opt/gsfluent
sudo chown gsfluent:gsfluent /opt/gsfluent
sudo -u gsfluent git clone https://example.invalid/gsfluent_pkg.git /opt/gsfluent
cd /opt/gsfluent
# Production GPU box: skip the client extra (no renderer) and just
# install the server stack + dev tools into the unified <repo>/.venv.
sudo -u gsfluent uv venv .venv --python 3.12
sudo -u gsfluent uv pip install --python .venv/bin/python -e 'server[dev]'

# 3. Make sure work/ is writable by the service user.
sudo -u gsfluent mkdir -p /opt/gsfluent/work
sudo chown -R gsfluent:gsfluent /opt/gsfluent/work

# 4. Drop your environment vars into /opt/gsfluent/.env (mode 0600).
sudo -u gsfluent cp .env.example .env
sudo -u gsfluent chmod 600 .env
sudoedit /opt/gsfluent/.env   # set GSFLUENT_SIM_HOME, GSFLUENT_SIM_PYTHON, etc.

# 5. Link the unit into systemd's search path and enable it.
sudo systemctl link /opt/gsfluent/deploy/gsfluent-backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now gsfluent-backend.service

# 6. Confirm it came up.
systemctl status gsfluent-backend.service
```

Expected: `Active: active (running)` and `Status:` showing
`ready (reattached=0 interrupted=0 terminal_already=N)`.

## Install — dev-box form

Useful when one operator on a workstation wants systemd to keep the
backend up without granting root or creating a system user. Runs as the
current user under per-user systemd.

```bash
# 1. Copy the dev unit into your per-user systemd directory.
mkdir -p ~/.config/systemd/user
cp deploy/gsfluent-backend.dev.service \
   ~/.config/systemd/user/gsfluent-backend.service

# 2. Edit the WorkingDirectory= / Environment= / ExecStart= paths so they
#    match your actual checkout location. The committed file uses
#    /home/frankyin/Desktop/work/gsfluent_pkg/ as an example - replace
#    with your real path.
$EDITOR ~/.config/systemd/user/gsfluent-backend.service

# 3. Make sure the unified root venv has uvicorn + gsfluent installed.
cd /path/to/your/gsfluent_pkg
# If you already ran `cd frontend && npm install`, the unified .venv
# is already built. Otherwise, materialise it directly:
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e 'server[dev,client]'

# 4. Reload + start.
systemctl --user daemon-reload
systemctl --user enable --now gsfluent-backend.service

# 5. Optional: make the service start at boot (without a login session).
sudo loginctl enable-linger "$USER"

# 6. Confirm.
systemctl --user status gsfluent-backend.service
```

Note: per-user services use `default.target` instead of
`multi-user.target` in `WantedBy=`. The dev unit file already accounts
for this.

## What replaced `server/supervise.sh`?

The old `server/supervise.sh` shell loop (83 lines) is gone. It was a
parent watcher that polled the backend every 5 seconds and respawned
on crash. It had three problems:

1. No watchdog detection — a wedged backend with a live PID stayed up forever.
2. No structured logging integration — it wrote plain text to `work/logs/supervisor.log`.
3. No crash recovery hook — in-flight runs were lost across restarts.

The systemd unit in this directory solves all three:

- `Type=notify` + `WatchdogSec=30s` detects wedged backends within ~30s.
- stdout goes to journald, where the backend's JSON event format is queryable with `journalctl -o json | jq`.
- The FastAPI lifespan calls `AsyncioRunManager.recover_on_boot()` on every startup. Runs that were mid-flight when the backend died are marked `interrupted` (with `error.kind = "internal.backend_restarted"`); runs whose sim subprocess is still alive on the original PG get re-attached.

If you were running `bash server/supervise.sh up` before, replace it with the install steps above.

## Restart the backend gracefully

```bash
# Production:
sudo systemctl restart gsfluent-backend.service

# Dev:
systemctl --user restart gsfluent-backend.service
```

Graceful restart flow:

1. systemd sends SIGTERM to the backend's main process.
2. uvicorn drains in-flight HTTP requests and enters lifespan shutdown.
3. The backend cancels its watchdog task and sends `STATUS=shutting down`.
4. Any in-flight runs stay in their `running` / `started` state on disk
   (the sim subprocess is in its own process group; `KillMode=mixed`
   leaves it alone until `TimeoutStopSec=60s` expires).
5. systemd starts a fresh backend process within `RestartSec=5` seconds.
6. The new backend's `recover_on_boot()` sees the still-alive sim PID
   (starttime matches) and re-attaches; the run continues without loss.

If the backend hangs and stops sending watchdog pings, systemd will
SIGKILL it after `WatchdogSec=30s` and restart automatically.

## View logs

The backend emits one JSON event per line to stdout, which systemd
captures into journald. Two recipes:

```bash
# Pretty-print all events from this boot.
journalctl -u gsfluent-backend -b -o json | jq -r '.MESSAGE | fromjson?'

# Tail live events, filtered to a single run.
journalctl -u gsfluent-backend -f -o json \
  | jq -r '.MESSAGE | fromjson? | select(.run_id == "RUN_ID_HERE")'

# Show only error events.
journalctl -u gsfluent-backend -o json \
  | jq -r '.MESSAGE | fromjson? | select(.event | startswith("error."))'

# Per-user equivalent for the dev-box install.
journalctl --user -u gsfluent-backend -f -o json | jq -r '.MESSAGE | fromjson?'
```

## Troubleshooting

### Watchdog fires (`Watchdog timeout` in `systemctl status`)

systemd killed the backend because no `WATCHDOG=1` arrived within 30s.
This means `/api/health` is no longer being reached by the lifespan's
watchdog task — typically the event loop is blocked on synchronous I/O
or stuck on an `await` that never completes.

1. Check `/api/health` from a separate shell while the backend is
   running (before the kill). If it hangs, the event loop is wedged.
2. Search journalctl for the last `backend.watchdog.ping` event before
   the death: `journalctl -u gsfluent-backend -o json
   | jq -r '.MESSAGE | fromjson? | select(.event=="backend.watchdog.ping")
   | .ts' | tail -5`
3. Look for blocking operations near that timestamp — file I/O on the
   main loop, a synchronous DNS lookup, an `asyncio.run_until_complete`
   inside a coroutine, etc.

### Backend restarts in a loop (`Start-limit hit`)

`StartLimitBurst=5` within `StartLimitIntervalSec=300s` is the cap. If
you see this, the backend is crashing on startup. Get the first crash
log:

```bash
journalctl -u gsfluent-backend -o json --no-pager | tail -30
```

Common causes:

- `GSFLUENT_SIM_HOME` unset -> `AppConfig.from_env()` fails or
  preflight errors.
- `work/` directory not writable by the service user (production: did
  you `chown gsfluent:gsfluent /opt/gsfluent/work`?).
- `.venv/bin/uvicorn` missing -> the unified venv wasn't built;
  run `uv pip install --python .venv/bin/python -e 'server[dev]'`
  (or `cd frontend && npm install` on a workstation).

Once fixed, clear the start limit: `systemctl reset-failed
gsfluent-backend.service` then `systemctl restart gsfluent-backend.service`.

### Crash recovery reports surprising counts

After a restart, look for `boot.recovery_complete`:

```bash
journalctl -u gsfluent-backend -b -o json \
  | jq -r '.MESSAGE | fromjson? | select(.event=="boot.recovery_complete")'
```

Expected fields: `reattached`, `interrupted`, `terminal_already`. If
`interrupted` is non-zero unexpectedly, individual runs each emit a
`boot.run.interrupted` event with the previous state and pid — use those
to investigate which runs lost their subprocess and why (sim crashed,
backend killed mid-spawn before pid was persisted, etc.).

### Sim subprocesses survive a backend restart

This is expected when systemd's `KillMode=mixed` plus the sim's
`start_new_session=True` (Phase 3) leaves the sim PG running. On the
next backend start, `recover_on_boot()` reattaches that run if PID +
starttime match.

If you need to nuke everything (e.g. dev box stuck state):

```bash
sudo systemctl stop gsfluent-backend.service
# Then kill any orphaned sim PGs by hand:
pgrep -fa 'run_sim|gsfluent.core.sim_engines' | awk '{print $1}' \
  | xargs -r -I{} kill -TERM -{}
```

### `sd_notify` not reaching systemd (`READY=1` never received)

If `systemctl status` hangs at `activating: start` and never reaches
`active (running)`:

1. Confirm `NotifyAccess=main` is present in the unit (it is in the
   committed files).
2. Confirm the backend code path actually calls `notify_ready()`.
   Check the `backend.ready` event in journalctl.
3. Check `$NOTIFY_SOCKET` is set inside the service: `systemctl
   show gsfluent-backend.service | grep NOTIFY` or add a one-shot debug
   line at the top of `composition.build_app`.

## HTTP/2 via Caddy (production-recommended)

uvicorn does NOT speak HTTP/2 natively (as of late 2025). For
production we run Caddy as a reverse proxy in front of uvicorn — Caddy
terminates HTTP/2 + TLS at the edge and talks plain HTTP/1.1 over
loopback to uvicorn. The browser + SPA see HTTP/2 (multiplexed
streams, header compression, no head-of-line blocking on the
`splats.gsq` body while a `/state` poll fires).

Why not switch uvicorn to hypercorn (which does support HTTP/2)?
Hypercorn would work but is operational churn: different CLI args,
different startup-notification semantics, and our existing systemd
units assume uvicorn. Caddy is a 5-line config and gives HTTP/2 +
Let's Encrypt for free.

The committed `deploy/gsfluent-backend.service` keeps `uvicorn` in
`ExecStart=` so direct-access debugging (`curl
http://127.0.0.1:7869/api/health`) still works. Caddy fronts it for
public traffic.

Install:

```bash
# 1. Install Caddy. Debian/Ubuntu:
sudo apt install -y caddy
# macOS:
brew install caddy

# 2. Drop the example config in place and edit the hostname.
sudo cp deploy/caddy.example.conf /etc/caddy/Caddyfile
sudoedit /etc/caddy/Caddyfile      # replace backend.example.com

# 3. Reload Caddy. systemd's caddy.service is already installed by the
#    package; `reload` re-reads the Caddyfile without dropping connections.
sudo systemctl reload caddy

# 4. Verify Caddy is healthy.
sudo systemctl status caddy
sudo caddy validate --config /etc/caddy/Caddyfile  # parses + lints
```

Verify HTTP/2 is live end-to-end:

```bash
# Browser-facing should be HTTP/2.
curl -sIv https://backend.example.com/api/health 2>&1 | grep -E '^(HTTP|>) '
# → expect `HTTP/2 200`.

# Splats.gsq body should also be HTTP/2 (and benefit from the
# Phase-5 Cache-Control: immutable + ETag header).
curl -o /dev/null -w '%{http_version} %{time_total}\n' \
  https://backend.example.com/api/sequences/<name>/cache/splats.gsq
# → expect `2 <seconds>`.
```

Where the latency win comes from:

- **TCP/TLS handshake amortization**: HTTP/2 multiplexes many requests
  over one connection. The SPA's per-tick `/state` poll + the
  per-frame WS-style `/set` calls + the splats.gsq body all share
  the same TLS connection. With HTTP/1.1 the browser would either
  open 6 parallel TCP connections (each with its own ~100 ms handshake)
  or serialize them.
- **HPACK header compression**: per-request overhead drops from ~600 B
  to ~50 B for a typical `/state` poll. Across 30 polls/s the savings
  add up to a few KB/s saved on cellular.
- **No head-of-line blocking**: a long-running splats.gsq stream over
  HTTP/2 doesn't block a `/state` poll on the same connection.

What HTTP/2 doesn't help:

- WebSocket frames (viser's own WS port 8092) are HTTP/1.1 Upgrade and
  stay that way under Caddy. The HTTP/2 win is for the FastAPI sidecar
  + the `splats.gsq` body, not the viser WS.

See `deploy/caddy.example.conf` for the full annotated config (Range
passthrough, timeout tuning, access log path).

## Uninstall

```bash
# Production:
sudo systemctl disable --now gsfluent-backend.service
sudo systemctl unlink gsfluent-backend.service  # if linked via `systemctl link`
sudo userdel gsfluent  # only if you want to remove the user

# Dev:
systemctl --user disable --now gsfluent-backend.service
rm ~/.config/systemd/user/gsfluent-backend.service
systemctl --user daemon-reload
```

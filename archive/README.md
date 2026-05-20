# `archive/` — code we don't run anymore

Everything in this directory is intentionally not part of the live
system. Two reasons something lands here:

1. **Abandoned rewrite.** The `apps/` + `infra/` + `docker/` trees
   were the v2 FastAPI rewrite (Postgres + MinIO + Redis + Arq +
   Caddy). Got through Phase 9 but the sim worker was never wired
   up; on the demo deadline we kept the v1 backend and the v2 api
   was reduced to a reverse proxy. Today neither is running — v1 is
   the only backend (see `server/gsfluent/` at the repo root).

2. **Legacy deployment scripts.** The `*-client.sh` / `*-server.sh`
   launchers ran the SSH-tunnel pattern (server on your-server, laptop
   tunnels to it). Replaced by `scripts/install-local.sh` +
   `scripts/start-local.sh` (or `npm install` + `npm start` from
   `frontend/`), which package the SPA + viser as a single
   teammate-laptop install.

   `migrate_v1_to_v2.py` was a one-shot migration tool from the v2
   transition; obsolete now that v2 is dead.

## Should I delete this?

Probably eventually. Kept here for two reasons:

- Some of the v2 code (notably the `routes/v1_proxy.py` and the
  `viser_proxy.py` reverse-proxy patterns) may be worth referencing
  if we ever need to put viser behind a public NAT again.
- The Caddy/Grafana/Loki/Prometheus configs in `infra/` are
  reasonable starting points if the team ever needs proper
  observability.

If a fresh `rm -rf archive/` ever happens, nothing in `server/`,
`frontend/`, `tools/`, `scripts/`, or `docs/` references it.

## What's still alive at the repo root

- `server/gsfluent/` — the v1 FastAPI backend (canonical)
- `frontend/` — React/Vite SPA
- `tools/` — sim/fuse/viser scripts the backend orchestrates
- `scripts/` — laptop-local install + start launchers
- `docs/` — API reference + architecture
- `patches/` — patches for upstream GaussianFluent
- `work/` — runtime state (gitignored)

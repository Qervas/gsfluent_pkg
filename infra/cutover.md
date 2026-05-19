# gsfluent v2 cutover runbook

Move team from v1 (current `gsfluent serve` on `:7869` + laptop-side
`viser_headless`) to v2 (full container stack on `:8869`).

Estimated downtime: ~5 minutes for the port swap. Old containers remain
paused for 30 days for rollback.

---

## 0. Pre-flight (week before)

- [ ] Phase 9 dogfooding: team has been on v2 (`:8869`) for ≥ 5 days.
- [ ] All P0/P1 reports resolved or accepted.
- [ ] `apps/api` tests green in CI on `rebuild`.
- [ ] Backup pipeline running nightly (`pg_dump` + `mc mirror`).
- [ ] Dry-run migration: `python tools/migrate_v1_to_v2.py --v1-root ... --dry-run`
      report counts match expectations.

## 1. Freeze v1 writes (~T-30 min)

```bash
# Stop the v1 api from accepting writes by killing its container.
# Keep filesystem intact for the final migration pass.
ssh sxyin-host 'docker stop gsfluent-v1-api'
# (Or whatever the v1 container is named — check `docker ps`.)
```

Post to team Slack: "v1 frozen at <time>; final migration starting."

## 2. Final migration pass

```bash
ssh sxyin-host
cd /data/yinshaoxuan/gsfluent_pkg
git fetch && git checkout rebuild && git pull

# v2 db should already be running from the parallel-run period.
export $(grep -v '^#' infra/.env | xargs)
export DATABASE_URL="postgresql+asyncpg://gsfluent:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT:-15432}/gsfluent_v2"
export MINIO_ENDPOINT="localhost:${MINIO_API_PORT:-19000}"
export MINIO_ACCESS_KEY="${MINIO_ROOT_USER:-gsfluent}"
export MINIO_SECRET_KEY="$MINIO_ROOT_PASSWORD"

# Final migration — picks up anything new since the last dogfooding pass.
uv run python tools/migrate_v1_to_v2.py --v1-root /data/yinshaoxuan/gsfluent_pkg

# Validate.
docker compose --profile v2 exec postgres psql -U gsfluent -d gsfluent_v2 -c \
  "SELECT (SELECT count(*) FROM models) AS models,
          (SELECT count(*) FROM recipes) AS recipes,
          (SELECT count(*) FROM runs) AS runs,
          (SELECT count(*) FROM artifacts) AS artifacts;"
```

Counts must match (or exceed) v1 — confirm with `ls runs/ | wc -l` etc.

## 3. Port swap (~T-5 min)

The bookmarked URL teammates use today is `:7869`. Make `:8869` (or
the company-allocated port) the canonical entry.

Option A — change Caddy in v2 stack to bind on `:7869`:

```bash
# Edit infra/.env on the server.
sed -i 's/^GSFLUENT_V2_PORT=.*/GSFLUENT_V2_PORT=7869/' infra/.env
docker compose --profile v2 up -d caddy   # recreate caddy with new port
```

Option B — front everything with the existing reverse proxy and switch
the upstream there. Depends on company network setup.

## 4. Pause v1 (~T-0)

```bash
docker stop gsfluent-v1-api gsfluent-v1-viser gsfluent-v1-sync 2>/dev/null || true
# Keep the containers (do NOT `docker rm`). Filesystem state retained.
```

Post to team Slack: "v2 live at <url>. v1 paused; rollback available
for 30 days."

## 5. Smoke (T+5 min)

```bash
curl -fsS http://localhost:7869/v1/system/health | jq
# Expect: status=ok, postgres.ok, redis.ok, minio.ok, gpu.ok (or gpu
# 'not on PATH' if the api container isn't given nvidia runtime —
# that's fine; the *worker* containers are.)
```

Team should test:
- [ ] Open the URL — SPA loads, no errors in console.
- [ ] Existing recipes + models visible in their pages.
- [ ] Existing runs visible in /runs.
- [ ] Submit a small new run from /sim/new — completes.
- [ ] Click into a completed run → viewer loads (server mode).

## 6. Rollback (if needed, T+anything within 30d)

```bash
# Bring v1 back up:
docker compose --profile v2 stop caddy   # free :7869
docker start gsfluent-v1-api gsfluent-v1-viser gsfluent-v1-sync
# Or restart whatever the v1 entrypoint was. The original
# `server/gsfluent/server.py` is unchanged on main; just docker run it.
```

V1 filesystem state was preserved (we never wrote *into* v1's recipes/
runs/models). New writes since cutover live in v2; rollback loses them
unless you also reverse-migrate.

## 7. 30-day cleanup

- [ ] Delete paused v1 containers: `docker rm gsfluent-v1-*`.
- [ ] On `rebuild` branch, `git mv frontend frontend-v1-retired && git mv frontend2 frontend`. Push, watch CI rename paths.
- [ ] Delete `frontend-v1-retired/` after the rename PR merges.
- [ ] Update `README.md` to point at the new stack.
- [ ] Close issues that were "blocked on v2 cutover".

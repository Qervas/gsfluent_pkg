#!/usr/bin/env bash
# Convenience wrappers for the sxyin native deploy without jq.
# Source: `source infra/scripts/cli-helpers.sh`

GSFLUENT_API="${GSFLUENT_API:-http://127.0.0.1:18000}"
PY="${GSFLUENT_PY:-python3}"

gsf_health() {
  curl -fsS "$GSFLUENT_API/v1/system/health" | "$PY" -m json.tool
}

gsf_health_short() {
  curl -fsS "$GSFLUENT_API/v1/system/health" \
    | "$PY" -c "import json,sys;d=json.load(sys.stdin);print(d['status'],'pg:',d['postgres']['ok'],'redis:',d['redis']['ok'],'minio:',d['minio']['ok'],'gpu:',d['gpu']['ok'])"
}

gsf_models() {
  curl -fsS "$GSFLUENT_API/v1/models" | "$PY" -m json.tool
}

gsf_recipes() {
  curl -fsS "$GSFLUENT_API/v1/recipes" | "$PY" -m json.tool
}

gsf_runs() {
  curl -fsS "$GSFLUENT_API/v1/runs" \
    | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
for r in d['items']:
    print(r['id'][:8], r['name'], r['status'],
          'arts=?', f\"queued={r['queued_at'][:19]}\")
"
}

gsf_run() {
  # usage: gsf_run <run_id_prefix>
  if [ -z "$1" ]; then echo "usage: gsf_run <run_id or prefix>"; return 1; fi
  curl -fsS "$GSFLUENT_API/v1/runs/$1" | "$PY" -m json.tool
}

gsf_artifacts() {
  if [ -z "$1" ]; then echo "usage: gsf_artifacts <run_id>"; return 1; fi
  curl -fsS "$GSFLUENT_API/v1/runs/$1/artifacts" \
    | "$PY" -c "
import json, sys
arts = json.load(sys.stdin)
for a in arts:
    print(a['kind'], 'frame=' + str(a.get('frame_idx')), a['size_bytes'], 'B', a['id'][:8])
print('total:', len(arts))
"
}

gsf_fetch() {
  # usage: gsf_fetch <artifact_id> [out_path]
  if [ -z "$1" ]; then echo "usage: gsf_fetch <artifact_id> [out_path]"; return 1; fi
  URL="$(curl -fsS "$GSFLUENT_API/v1/artifacts/$1/url" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["url"])')"
  OUT="${2:-/tmp/gsf-artifact-$1}"
  curl -fsS -o "$OUT" "$URL"
  echo "saved -> $OUT  ($(stat -c %s "$OUT") bytes)"
}

gsf_submit() {
  # usage: gsf_submit <name> <model_id> <recipe_id>
  if [ -z "$3" ]; then echo "usage: gsf_submit <name> <model_id> <recipe_id>"; return 1; fi
  curl -fsS -X POST "$GSFLUENT_API/v1/runs" \
    -H "content-type: application/json" \
    -d "{\"name\":\"$1\",\"model_id\":\"$2\",\"recipe_id\":\"$3\"}" \
    | "$PY" -m json.tool
}

echo "gsf_* helpers loaded. Try: gsf_health_short | gsf_models | gsf_runs"

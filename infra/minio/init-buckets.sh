#!/bin/sh
# Idempotent bucket setup. Runs as the minio-init sidecar; exits on completion.
set -eu

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

for bucket in gsfluent-models gsfluent-runs gsfluent-misc; do
  if ! mc ls "local/$bucket" >/dev/null 2>&1; then
    mc mb "local/$bucket"
    echo "created bucket: $bucket"
  else
    echo "bucket already exists: $bucket"
  fi
done

# Versioning on runs bucket so artifacts can be retained across re-runs.
mc version enable local/gsfluent-runs >/dev/null 2>&1 || true

echo "minio init done"

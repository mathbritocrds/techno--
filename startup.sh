#!/bin/sh
# Restart contract: start Flux Gestão (FastAPI) on 0.0.0.0:8080 if it is down.
set -eu
cd /workspace
if curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8080/docs; then
  exit 0
fi
mkdir -p /workspace/data /workspace/app/static
if ! python3 -c "import fastapi,uvicorn" 2>/dev/null; then
  pip3 install -q 'fastapi>=0.110' 'uvicorn[standard]>=0.29'
fi
export ADMIN_USER="${ADMIN_USER:-admin}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"
export TZ_OFFSET_HOURS="${TZ_OFFSET_HOURS:--3}"
export DB_PATH="${DB_PATH:-/workspace/data/flux.db}"
nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload \
  >/tmp/flux-gestao.log 2>&1 &
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -sf -o /dev/null --max-time 1 http://127.0.0.1:8080/docs; then
    exit 0
  fi
  sleep 0.4
done
exit 0

#!/usr/bin/env bash
# Start the DealPilot web app and every stage service. Safe to run twice.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p output/logs

PY="${PYTHON:-python3}"
STAGES=(intake credit committee product committed closed)

is_up() {
  ps ax -o command= | grep -F -- "$1" >/dev/null 2>&1
}

port_open() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

start_bg() {
  local log="$1"
  local needle="$2"
  shift 2
  if is_up "$needle"; then
    echo "already up: $needle"
    return 0
  fi
  echo "starting: $needle"
  nohup "$PY" -u "$@" >> "output/logs/$log" 2>&1 &
}

if port_open 8000; then
  echo "already up: web on http://127.0.0.1:8000/"
else
  echo "starting: web on http://127.0.0.1:8000/"
  nohup "$PY" -u server.py >> output/logs/web.log 2>&1 &
fi

for stage in "${STAGES[@]}"; do
  start_bg "parser-$stage.log" "-m services.parser --stage $stage" -m services.parser --stage "$stage"
  start_bg "rules-$stage.log" "-m services.rules --stage $stage" -m services.rules --stage "$stage"
  start_bg "human-$stage.log" "-m services.human --stage $stage" -m services.human --stage "$stage"
  start_bg "upload-$stage.log" "-m services.upload --stage $stage" -m services.upload --stage "$stage"
  start_bg "sla-$stage.log" "-m services.sla --stage $stage" -m services.sla --stage "$stage"
done

start_bg "history.log" "-m services.history" -m services.history

if port_open 8765; then
  echo "already up: sim clock on http://127.0.0.1:8765/"
else
  echo "starting: sim clock on http://127.0.0.1:8765/"
  nohup "$PY" -u sim/clock.py serve 8765 >> output/logs/clock.log 2>&1 &
fi

sleep 0.4
echo
echo "Web:   http://127.0.0.1:8000/"
echo "Clock: http://127.0.0.1:8765/now"
echo "Human: http://127.0.0.1:8201/ through :8206/ (intake, credit, committee, product, committed, closed)"
echo "Upload: http://127.0.0.1:8101/ through :8106/ (same stage order)"
echo "Logs:  $ROOT/output/logs/"

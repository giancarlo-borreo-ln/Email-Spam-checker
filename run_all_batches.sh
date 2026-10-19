#!/usr/bin/env bash
# Continue 10k batches automatically until the mailbox is exhausted.
#
# It waits for any current run to finish, then processes offset 10000,
# 20000, ... into the same OUT_DIR. Each batch appends to mailbox.jsonl and
# the passes skip messages already classified.
#
#   ./run_all_batches.sh
#
# Ctrl-C stops after the current batch. Rerun to resume.
#
# Overrides:
#   BATCH_SIZE=10000
#   OUT_DIR=triage-10k
#   LOG_FILE=logs.txt
#   EMAIL_ADDRESS=you@example.com

set -euo pipefail
cd "$(dirname "$0")"

BATCH_SIZE="${BATCH_SIZE:-10000}"
OUT_DIR="${OUT_DIR:-triage-10k}"
LOG_FILE="${LOG_FILE:-logs.txt}"
POLL_SECONDS="${POLL_SECONDS:-30}"

if [ -z "${EMAIL_ADDRESS:-}" ] && [ -t 0 ]; then
  read -r -p "Email address: " EMAIL_ADDRESS
  export EMAIL_ADDRESS
fi
if [ -z "${EMAIL_ADDRESS:-}" ]; then
  echo "EMAIL_ADDRESS is not set and stdin is not interactive." >&2
  exit 1
fi

# Wait for the currently running 10k job, if any.
while pgrep -f "[r]un_triage.sh" >/dev/null 2>&1; do
  echo "[batches] waiting for the current run to finish..."
  sleep "$POLL_SECONDS"
done

offset=$(wc -l < "$OUT_DIR/mailbox.jsonl" 2>/dev/null || echo 0)
offset=${offset//[[:space:]]/}
batch=$(( offset / BATCH_SIZE + 1 ))

echo "[batches] resuming at offset $offset (batch $batch), size $BATCH_SIZE"

while :; do
  echo "[batches] starting batch $batch: offset $offset limit $BATCH_SIZE"
  FRESH=1 \
    LIMIT="$BATCH_SIZE" \
    OFFSET="$offset" \
    OUT_DIR="$OUT_DIR" \
    ENABLE_PASS_C="${ENABLE_PASS_C:-1}" \
    VERBOSE="${VERBOSE:-1}" \
    LOG_FILE="$LOG_FILE" \
    ./run_triage.sh

  new=$("$HOME/.local/bin/uv" run python - "$OUT_DIR" <<'PY'
import json
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "mailbox.meta.json"
try:
    print(int(json.loads(p.read_text()).get("new_records", 0)))
except Exception:
    print(0)
PY
)

  total=$(wc -l < "$OUT_DIR/mailbox.jsonl")
  echo "[batches] batch $batch added $new new messages; $total total"

  if [ "$new" -lt "$BATCH_SIZE" ]; then
    echo "[batches] done — last batch added $new messages"
    break
  fi

  offset=$((offset + BATCH_SIZE))
  batch=$((batch + 1))
done

echo "[batches] all batches complete. Results in $OUT_DIR/"

#!/usr/bin/env bash
# run_triage.sh - read-only two-pass email triage orchestrator.
#
# It never moves, deletes, or flags mail. All output is JSONL under $OUT_DIR.
#
#   ./run_triage.sh                 # run all phases, resuming where possible
#   ./run_triage.sh export
#   ./run_triage.sh pass-a
#   ./run_triage.sh pass-b
#   ./run_triage.sh pass-c          # tie-break only review.jsonl
#   ./run_triage.sh merge
#   ./run_triage.sh report
#   ./run_triage.sh preflight
#
# Useful environment variables:
#   OUT_DIR=triage              output directory
#   PASS_A_MODEL=qwen3.5:4b     first classifier
#   PASS_B_MODEL=qwen3.5:4b     second classifier (or a different model)
#   PASS_C_MODEL=qwen3.5:4b     tie-breaker for disagreements
#   ENABLE_PASS_C=0             set to 1 to include pass-c in "all"
#   SNIPPET_CHARS=600           body characters included per email (0 = headers)
#   LOG_FILE=logs.txt           where to tee the full run log
#   FOLDERS="INBOX"             space-separated folders to export
#   LIMIT=500                   pilot on the first N messages
#   MAX_ERROR_RATE=0.01         abort if a pass exceeds this error rate
#   FORCE=1                     ignore .done markers and resume anyway
#   FRESH=1                     clear .done markers (does NOT delete data)

set -euo pipefail
cd "$(dirname "$0")"

UV="${UV:-uv}"
if ! command -v "$UV" >/dev/null 2>&1; then
  UV="$HOME/.local/bin/uv"
fi
if ! command -v "$UV" >/dev/null 2>&1; then
  echo "uv not found. Install it or set UV=/path/to/uv" >&2
  exit 1
fi
UV="$(command -v "$UV")"

OLLAMA_BIN="${OLLAMA_BIN:-$HOME/.local/ollama/bin/ollama}"
if [ ! -x "$OLLAMA_BIN" ]; then
  OLLAMA_BIN="$(command -v ollama || true)"
fi

PY=("$UV" run python)
OUT_DIR="${OUT_DIR:-triage}"
# Keep a pilot from contaminating the full run's mailbox.jsonl/done markers.
if [ -n "${LIMIT:-}" ] && [ "$OUT_DIR" = "triage" ]; then
  OUT_DIR="triage-pilot-${LIMIT}"
fi
PASS_A_MODEL="${PASS_A_MODEL:-qwen3.5:4b}"
PASS_B_MODEL="${PASS_B_MODEL:-qwen3.5:4b}"
PASS_C_MODEL="${PASS_C_MODEL:-qwen3.5:4b}"
ENABLE_PASS_C="${ENABLE_PASS_C:-0}"
SNIPPET_CHARS="${SNIPPET_CHARS:-600}"
MAX_ERROR_RATE="${MAX_ERROR_RATE:-0.01}"

read -r -a FOLDER_LIST <<< "${FOLDERS:-INBOX}"

mkdir -p "$OUT_DIR" logs
LOG="${LOG_FILE:-logs/triage-$(date +%Y%m%d-%H%M%S).log}"
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

# One run at a time.
exec 9>"$OUT_DIR/.triage.lock"
if ! flock -n 9; then
  die "another triage run holds $OUT_DIR/.triage.lock"
fi

trap 'log "interrupted"; exit 130' INT TERM

if [ "${FRESH:-0}" = "1" ]; then
  rm -f "$OUT_DIR"/*.done
  log "cleared .done markers (existing JSONL is kept and resumed)"
fi

preflight() {
  log "preflight"
  "${PY[@]}" triage_pipeline.py --help >/dev/null

  if ! curl -fsS "http://127.0.0.1:11434/api/version" >/dev/null 2>&1; then
    log "Ollama is not responding; starting the user service"
    systemctl --user start ollama.service >/dev/null 2>&1 || true
    sleep 2
  fi
  curl -fsS "http://127.0.0.1:11434/api/version" >/dev/null \
    || die "Ollama is not reachable at 127.0.0.1:11434"

  [ -n "$OLLAMA_BIN" ] && [ -x "$OLLAMA_BIN" ] || die "ollama binary not found"
  "$OLLAMA_BIN" show "$PASS_A_MODEL" >/dev/null 2>&1 \
    || die "model $PASS_A_MODEL is not installed (ollama pull $PASS_A_MODEL)"
  if [ "$PASS_B_MODEL" != "$PASS_A_MODEL" ]; then
    "$OLLAMA_BIN" show "$PASS_B_MODEL" >/dev/null 2>&1 \
      || die "model $PASS_B_MODEL is not installed (ollama pull $PASS_B_MODEL)"
  fi
  if [ "${ENABLE_PASS_C:-0}" = "1" ]; then
    "$OLLAMA_BIN" show "$PASS_C_MODEL" >/dev/null 2>&1 \
      || die "model $PASS_C_MODEL is not installed (ollama pull $PASS_C_MODEL)"
  fi

  local free_kb
  free_kb="$(df -Pk "$OUT_DIR" | awk 'NR==2 {print $4}')"
  if [ -n "$free_kb" ] && [ "$free_kb" -lt 2097152 ]; then
    die "less than 2 GB free in $OUT_DIR"
  fi
  log "preflight OK (A=$PASS_A_MODEL B=$PASS_B_MODEL folders=${FOLDER_LIST[*]})"
}

run_phase() {
  local name="$1"; shift
  if [ -f "$OUT_DIR/$name.done" ] && [ "${FORCE:-0}" != "1" ]; then
    log "skip $name (already done)"
    return 0
  fi
  log "start $name"
  "$@"
  log "finished $name"
}

mark_done() {
  touch "$OUT_DIR/$1.done"
  log "marked $1 done"
}

validate_phase() {
  "${PY[@]}" triage_pipeline.py validate \
    --out-dir "$OUT_DIR" --phase "$1" --max-error-rate "$MAX_ERROR_RATE"
}

do_export() {
  local args=(export --out-dir "$OUT_DIR" --snippet-chars "$SNIPPET_CHARS")
  local folder
  for folder in "${FOLDER_LIST[@]}"; do
    args+=(--folder "$folder")
  done
  if [ -n "${LIMIT:-}" ]; then
    args+=(--limit "$LIMIT")
  fi
  "${PY[@]}" triage_pipeline.py "${args[@]}"
}

do_pass_a() {
  local args=(pass-a --out-dir "$OUT_DIR" --model "$PASS_A_MODEL")
  [ -n "${LIMIT:-}" ] && args+=(--limit "$LIMIT")
  "${PY[@]}" triage_pipeline.py "${args[@]}"
}

do_pass_b() {
  local args=(pass-b --out-dir "$OUT_DIR" --model "$PASS_B_MODEL")
  [ -n "${LIMIT:-}" ] && args+=(--limit "$LIMIT")
  "${PY[@]}" triage_pipeline.py "${args[@]}"
}

do_pass_c() {
  local args=(pass-c --out-dir "$OUT_DIR" --model "$PASS_C_MODEL")
  [ -n "${LIMIT:-}" ] && args+=(--limit "$LIMIT")
  "${PY[@]}" triage_pipeline.py "${args[@]}"
}

do_merge() {
  "${PY[@]}" triage_pipeline.py merge --out-dir "$OUT_DIR"
}

do_report() {
  "${PY[@]}" triage_pipeline.py report --out-dir "$OUT_DIR"
}

unload_first_model() {
  if [ "$PASS_A_MODEL" != "$PASS_B_MODEL" ]; then
    log "unloading $PASS_A_MODEL before pass-b"
    "$OLLAMA_BIN" stop "$PASS_A_MODEL" >/dev/null 2>&1 || true
    sleep 2
  fi
}

unload_before_pass_c() {
  if [ "$PASS_B_MODEL" != "$PASS_C_MODEL" ]; then
    log "unloading $PASS_B_MODEL before pass-c"
    "$OLLAMA_BIN" stop "$PASS_B_MODEL" >/dev/null 2>&1 || true
    sleep 2
  fi
}

all_phases() {
  preflight

  run_phase export do_export
  validate_phase export
  mark_done export

  run_phase pass-a do_pass_a
  validate_phase pass-a
  mark_done pass-a

  unload_first_model

  run_phase pass-b do_pass_b
  validate_phase pass-b
  mark_done pass-b

  if [ "$ENABLE_PASS_C" = "1" ]; then
    unload_before_pass_c
    run_phase pass-c do_pass_c
    mark_done pass-c
  fi

  run_phase merge do_merge
  mark_done merge

  do_report
  log "all done. Review $OUT_DIR/review.jsonl; nothing was changed in the mailbox."
}

case "${1:-all}" in
  all)
    all_phases
    ;;
  preflight)
    preflight
    ;;
  export)
    preflight
    run_phase export do_export
    validate_phase export
    mark_done export
    ;;
  pass-a)
    preflight
    run_phase pass-a do_pass_a
    validate_phase pass-a
    mark_done pass-a
    ;;
  pass-b)
    preflight
    unload_first_model
    run_phase pass-b do_pass_b
    validate_phase pass-b
    mark_done pass-b
    ;;
  pass-c)
    ENABLE_PASS_C=1
    preflight
    unload_before_pass_c
    run_phase pass-c do_pass_c
    mark_done pass-c
    ;;
  merge)
    run_phase merge do_merge
    mark_done merge
    do_report
    ;;
  report)
    do_report
    ;;
  *)
    echo "usage: $0 [all|preflight|export|pass-a|pass-b|pass-c|merge|report]" >&2
    exit 2
    ;;
esac

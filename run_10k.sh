#!/usr/bin/env bash
# Simple foreground 10k runner.
#
#   ./run_10k.sh
#
# It prompts for your email address (if not already set), then prints the full
# verbose log to the terminal and to logs.txt. Nothing is moved or deleted.
#
# Ctrl-C stops it; run it again to resume.
#
# Override any of these:
#   LIMIT=500 OUT_DIR=triage-pilot ./run_10k.sh
#   ENABLE_PASS_C=0 ./run_10k.sh
#   VERBOSE=0 ./run_10k.sh

set -euo pipefail
cd "$(dirname "$0")"

export LIMIT="${LIMIT:-10000}"
export OUT_DIR="${OUT_DIR:-triage-10k}"
export ENABLE_PASS_C="${ENABLE_PASS_C:-1}"
export VERBOSE="${VERBOSE:-1}"
export LOG_FILE="${LOG_FILE:-logs.txt}"

exec ./run_triage.sh "$@"

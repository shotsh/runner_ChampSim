#!/usr/bin/env bash
set -euo pipefail

# This script sits next to champsim_e2e.py
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E="$HERE/champsim_e2e.py"

# Configs via env if needed
LABEL_MAP="${LABEL_MAP:-resche2:schedcost_on,resche_:schedcost_off,ChampSim:latest}"
BASELINE="${BASELINE:-latest}"

summarize_one() {
  local run_dir="$1"
  local res_dir="$run_dir/results"
  local out_dir="$res_dir/summary_out"

  if [[ ! -d "$res_dir" ]]; then
    echo "skip: $run_dir (no results dir)"
    return
  fi

  shopt -s nullglob
  local files=( "$res_dir"/*.txt )
  if (( ${#files[@]} == 0 )); then
    echo "skip: $run_dir (no .txt logs)"
    return
  fi

  mkdir -p "$out_dir"
  echo "Summarizing $run_dir"
  python3 "$E2E" \
    --glob "$res_dir/*.txt" \
    --outdir "$out_dir" \
    --baseline "$BASELINE" \
    --label-map "$LABEL_MAP" \
    --img-formats "svg"

  echo "Wrote: $out_dir/summary.csv"
  echo "Wrote: $out_dir/normalized_ipc.csv"
  echo "Wrote: $out_dir/ipc_normalized_bar.svg"
  echo "Wrote: $out_dir/ipc_vs_llc_mpki.svg"
}

if (( $# >= 1 )); then
  summarize_one "$1"
else
  # Try *_my_run first, then any directory under runs as a fallback
  for d in runs/*_my_run runs/*; do
    [[ -d "$d" ]] || continue
    summarize_one "$d"
  done
fi

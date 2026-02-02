#!/bin/bash
#SBATCH --job-name=csim_matrix
#SBATCH --time=08:00:00
#SBATCH --nodes=1 # Request 1 node
#SBATCH --ntasks-per-node=2                # Request n tasks(cores) per node
#SBATCH --cpus-per-task=1
#SBATCH --ntasks=1
#SBATCH --mem=8G
# #SBATCH --partition=grace                   # [00] Enable only when needed (no speed difference)
#SBATCH --output=logs/%x.%A.%a.out            # [00] Standard output log
#SBATCH --error=logs/%x.%A.%a.err             # [00] Standard error log

## OPTIONAL JOB SPECIFICATIONS
#SBATCH --mail-type=ALL                      # Send email on all job events
#SBATCH --mail-user=sshintani@tamu.edu       # Send all emails to email_address

# === Added section (.bashrc is not read in batch, so explicitly set same environment) ===
# module may not exist on Olympus, so don't fail if it does
if command -v module >/dev/null 2>&1; then
  module -q load GCCcore/13.2.0 2>/dev/null || true
  module -q load Python/3.11.5  2>/dev/null || true
fi

# Priority: CSIM_VENV > VIRTUAL_ENV > SCRATCH(or HOME)
VENV_PATH="${CSIM_VENV:-${VIRTUAL_ENV:-${SCRATCH:-$HOME}/venvs/csim}}"

if [[ -f "${VENV_PATH}/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source "${VENV_PATH}/bin/activate"
else
  echo "[INFO] venv not found, skipping: ${VENV_PATH}/bin/activate" >&2
fi

export MPLBACKEND=Agg
# === End of added section ===

set -euo pipefail

# [01] Get row for array task from matrix.tsv (0-based to 1-based)
LINE=$((SLURM_ARRAY_TASK_ID + 1))
ROW=$(sed -n "${LINE}p" matrix.tsv)

# [02] Extract fields (1:BIN 2:TRACE 3:ARGS 4:ARGS_IDX)
BIN=$(printf "%s" "$ROW" | cut -f1)
TRACE=$(printf "%s" "$ROW" | cut -f2)
ARGS=$(printf "%s" "$ROW" | cut -f3)         # OK with spaces (tab-delimited)
ARGIDX=$(printf "%s" "$ROW" | cut -f4 2>/dev/null || true)
[[ -z "${ARGIDX:-}" ]] && ARGIDX=0           # Support old matrix.tsv without 4th column

# [03] Result save directory
mkdir -p results

# [04] Repository name and binary name
#      .../ChampSim/bin/champsim -> REPO="ChampSim", BIN_NAME="champsim"
REPO=$(basename "$(dirname "$(dirname "$BIN")")")
BIN_NAME=$(basename "$BIN")
TRACE_BASE=$(basename "$TRACE")

# [05] Dynamically determine zero-padding width from total task count
TOTAL=$(wc -l < matrix.tsv)                  # Total task count
MAXIDX=$(( TOTAL - 1 ))
PADW=${#MAXIDX}                              # e.g., 0..59 -> width 2
printf -v AID_PAD "%0*d" "$PADW" "$SLURM_ARRAY_TASK_ID"

# [06] JobID is appended with j prefix at the end
JID=${SLURM_JOB_ID:-NA}
JTAG="j${JID}"

# [07] Naming convention:
#      <array_id(zero-padded)>_<trace_name>_<REPO>_<BIN_NAME>_<ARGS_number>_j<JobID>.txt
OUT="results/${AID_PAD}_${TRACE_BASE}_${REPO}_${BIN_NAME}_${ARGIDX}_${JTAG}.txt"

# [08] Output diagnostic info to stderr (viewable in .err log)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] START task=${SLURM_ARRAY_TASK_ID} node=$(hostname)" >&2
echo "[INFO] BIN=${BIN}" >&2
echo "[INFO] TRACE=${TRACE}" >&2
echo "[INFO] ARGS=${ARGS}" >&2
echo "[INFO] OUT=${OUT}" >&2

# Check binary exists
if [[ ! -x "$BIN" ]]; then
  echo "[ERROR] Binary not found or not executable: $BIN" >&2
  exit 1
fi

# Check trace file exists
if [[ ! -f "$TRACE" ]]; then
  echo "[ERROR] Trace file not found: $TRACE" >&2
  exit 1
fi

# [09] Execute
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running: srun $BIN $ARGS $TRACE" >&2
srun "$BIN" $ARGS "$TRACE" > "$OUT"
EXIT_CODE=$?

echo "[$(date '+%Y-%m-%d %H:%M:%S')] DONE task=${SLURM_ARRAY_TASK_ID} exit_code=${EXIT_CODE}" >&2

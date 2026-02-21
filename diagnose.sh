#!/bin/bash
# diagnose.sh - Job Diagnostic Script
# Usage: ./diagnose.sh [job_id] [run_dir]
#   job_id  : Diagnose a specific job ID (auto-detects running jobs if omitted)
#   run_dir : Diagnose a specific run directory (uses latest runs if omitted)

set -euo pipefail

# Color codes (if terminal supports them)
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[0;33m'
  BLUE='\033[0;34m'
  NC='\033[0m' # No Color
else
  RED='' GREEN='' YELLOW='' BLUE='' NC=''
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="${SCRIPT_DIR}/runs"

# === 1. Current Job Queue ===
echo -e "${BLUE}=== 1. Job Queue (squeue) ===${NC}"
QUEUE_OUTPUT=$(squeue -u "$USER" -o "%.18i %.9P %.30j %.8T %.10M %.9l %.6D %R" 2>/dev/null || true)
if [[ -z "$QUEUE_OUTPUT" || "$QUEUE_OUTPUT" == *"JOBID"* && $(echo "$QUEUE_OUTPUT" | wc -l) -eq 1 ]]; then
  echo -e "${YELLOW}No running jobs${NC}"
  RUNNING_JOBS=""
else
  echo "$QUEUE_OUTPUT"
  # Extract running job IDs (base ID for array jobs)
  RUNNING_JOBS=$(squeue -u "$USER" -h -o "%i" 2>/dev/null | sed 's/_.*//g' | sort -u | head -5)
fi
echo ""

# === 2. Identify Latest Run Directory ===
if [[ -n "${2:-}" ]]; then
  RUN_DIR="$2"
elif [[ -d "$RUNS_DIR" ]]; then
  RUN_DIR=$(ls -1td "$RUNS_DIR"/*/ 2>/dev/null | head -1 || true)
else
  RUN_DIR=""
fi

if [[ -z "$RUN_DIR" || ! -d "$RUN_DIR" ]]; then
  echo -e "${YELLOW}Run directory not found${NC}"
  exit 0
fi

RUN_NAME=$(basename "$RUN_DIR")
echo -e "${BLUE}=== 2. Diagnostic Target: ${RUN_NAME} ===${NC}"
echo "Path: $RUN_DIR"
echo ""

# === 3. Identify Job ID ===
JOB_ID="${1:-}"
if [[ -z "$JOB_ID" && -f "${RUN_DIR}/sbatch_jobs.txt" ]]; then
  JOB_ID=$(head -1 "${RUN_DIR}/sbatch_jobs.txt" 2>/dev/null || true)
fi

if [[ -n "$JOB_ID" ]]; then
  echo -e "${BLUE}=== 3. Job Details (job_id=${JOB_ID}) ===${NC}"

  # Check job status with sacct
  echo -e "${GREEN}--- sacct (Job Status) ---${NC}"
  sacct -j "$JOB_ID" --format=JobID,JobName%20,State,ExitCode,Elapsed,MaxRSS -n 2>/dev/null | head -20 || echo "No sacct info"
  echo ""

  # Check resource usage with sstat (only for running jobs)
  if [[ -n "$RUNNING_JOBS" ]] && echo "$RUNNING_JOBS" | grep -q "^${JOB_ID}$"; then
    echo -e "${GREEN}--- sstat (Resource Usage) ---${NC}"
    sstat -j "$JOB_ID" --format=JobID,MaxRSS,MaxVMSize,AveCPU 2>/dev/null | head -10 || echo "No sstat info (job may have completed)"
    echo ""
  fi
else
  echo -e "${YELLOW}Could not identify job ID${NC}"
fi
echo ""

# === 4. Result Files Status ===
echo -e "${BLUE}=== 4. Result Files (results/) ===${NC}"
RESULTS_DIR="${RUN_DIR}/results"
if [[ -d "$RESULTS_DIR" ]]; then
  TOTAL_FILES=$(find "$RESULTS_DIR" -name "*.txt" -type f 2>/dev/null | wc -l)
  ZERO_BYTE=$(find "$RESULTS_DIR" -name "*.txt" -type f -empty 2>/dev/null | wc -l)
  NON_ZERO=$(( TOTAL_FILES - ZERO_BYTE ))

  echo "Total files: $TOTAL_FILES"
  echo -e "Completed (>0 bytes): ${GREEN}${NON_ZERO}${NC}"
  echo -e "Incomplete (0 bytes): ${YELLOW}${ZERO_BYTE}${NC}"

  if [[ $ZERO_BYTE -gt 0 ]]; then
    echo ""
    echo "Zero-byte files (max 5):"
    find "$RESULTS_DIR" -name "*.txt" -type f -empty 2>/dev/null | head -5 | while read -r f; do
      echo "  - $(basename "$f")"
    done
  fi
else
  echo -e "${YELLOW}results/ directory not found${NC}"
fi
echo ""

# === 5. Log Files Status ===
echo -e "${BLUE}=== 5. Log Files (logs/) ===${NC}"
LOGS_DIR="${RUN_DIR}/logs"
if [[ -d "$LOGS_DIR" ]]; then
  # Check .err files with content
  ERR_WITH_CONTENT=$(find "$LOGS_DIR" -name "*.err" -type f ! -empty 2>/dev/null | wc -l)
  ERR_EMPTY=$(find "$LOGS_DIR" -name "*.err" -type f -empty 2>/dev/null | wc -l)

  echo ".err files: with content=${ERR_WITH_CONTENT}, empty=${ERR_EMPTY}"

  # Check diagnostic output (latest .err file)
  LATEST_ERR=$(ls -1t "$LOGS_DIR"/*.err 2>/dev/null | head -1 || true)
  if [[ -n "$LATEST_ERR" && -s "$LATEST_ERR" ]]; then
    echo ""
    echo -e "${GREEN}--- Latest .err log ($(basename "$LATEST_ERR")) ---${NC}"
    tail -15 "$LATEST_ERR"
  elif [[ -n "$LATEST_ERR" ]]; then
    echo ""
    echo -e "${YELLOW}Latest .err log is empty: $(basename "$LATEST_ERR")${NC}"
    echo "-> Script may not have written diagnostic output yet, or buffering in progress"
  fi
else
  echo -e "${YELLOW}logs/ directory not found${NC}"
fi
echo ""

# === 6. Diagnostic Summary ===
echo -e "${BLUE}=== 6. Diagnostic Summary ===${NC}"

# Detect issues
ISSUES=()

if [[ -n "$RUNNING_JOBS" && $ZERO_BYTE -gt 0 ]]; then
  # Job is running and there are zero-byte files
  if [[ $NON_ZERO -eq 0 ]]; then
    ISSUES+=("All result files are 0 bytes - binary may be hanging")
  else
    ISSUES+=("Some result files are 0 bytes - still running, or some may be hanging")
  fi
fi

if [[ -z "$RUNNING_JOBS" && $ZERO_BYTE -gt 0 ]]; then
  ISSUES+=("Job has finished but zero-byte files remain - possible execution error")
fi

if [[ ${#ISSUES[@]} -eq 0 ]]; then
  echo -e "${GREEN}No issues detected${NC}"
else
  for issue in "${ISSUES[@]}"; do
    echo -e "${RED}⚠ ${issue}${NC}"
  done
  echo ""
  echo "Remediation steps:"
  echo "  1. Check logs/*.err for error messages"
  echo "  2. Verify exit codes with sacct -j <job_id>"
  echo "  3. Verify binary and trace paths in matrix.tsv"
fi

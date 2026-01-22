#!/bin/bash
#SBATCH --job-name=csim_matrix
#SBATCH --time=08:00:00
#SBATCH --nodes=1 # Request 1 node
#SBATCH --ntasks-per-node=2                # Request n tasks(cores) per node
#SBATCH --cpus-per-task=1
#SBATCH --ntasks=1
#SBATCH --mem=8G
# #SBATCH --partition=grace                   # [00] 必要な時だけ有効化（速度は変わらない）
#SBATCH --output=logs/%x.%A.%a.out            # [00] 標準出力ログ
#SBATCH --error=logs/%x.%A.%a.err             # [00] 標準エラーログ

## OPTIONAL JOB SPECIFICATIONS
#SBATCH --mail-type=ALL                      # Send email on all job events
#SBATCH --mail-user=sshintani@tamu.edu       # Send all emails to email_address

# === ここから追加（.bashrc はバッチで読まれないため同じ環境を明示）===
# module は Olympus だと無い場合があるので、失敗しても落とさない
if command -v module >/dev/null 2>&1; then
  module -q load GCCcore/13.2.0 2>/dev/null || true
  module -q load Python/3.11.5  2>/dev/null || true
fi

# 優先度: CSIM_VENV > VIRTUAL_ENV > SCRATCH(or HOME)
VENV_PATH="${CSIM_VENV:-${VIRTUAL_ENV:-${SCRATCH:-$HOME}/venvs/csim}}"

if [[ -f "${VENV_PATH}/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source "${VENV_PATH}/bin/activate"
else
  echo "[INFO] venv not found, skipping: ${VENV_PATH}/bin/activate" >&2
fi

export MPLBACKEND=Agg
# === 追加ここまで ===

set -euo pipefail

# [01] 配列タスクの行を matrix.tsv から取得（0始まり→+1で1始まりへ）
LINE=$((SLURM_ARRAY_TASK_ID + 1))
ROW=$(sed -n "${LINE}p" matrix.tsv)

# [02] フィールド抽出（1:BIN 2:TRACE 3:ARGS 4:ARGS_IDX）
BIN=$(printf "%s" "$ROW" | cut -f1)
TRACE=$(printf "%s" "$ROW" | cut -f2)
ARGS=$(printf "%s" "$ROW" | cut -f3)         # 空白を含んでもOK（タブ区切り前提）
ARGIDX=$(printf "%s" "$ROW" | cut -f4 2>/dev/null || true)
[[ -z "${ARGIDX:-}" ]] && ARGIDX=0           # 4列が無い古いmatrix.tsvにも対応

# [03] 結果保存ディレクトリ
mkdir -p results

# [04] バイナリ系統名とバイナリ名
#      …/ChampSim/bin/champsim → REPO="ChampSim", BIN_NAME="champsim"
REPO=$(basename "$(dirname "$(dirname "$BIN")")")
BIN_NAME=$(basename "$BIN")
TRACE_BASE=$(basename "$TRACE")

# [05] 配列IDのゼロ埋め幅を総タスク数から動的決定
TOTAL=$(wc -l < matrix.tsv)                  # 総タスク数
MAXIDX=$(( TOTAL - 1 ))
PADW=${#MAXIDX}                              # 例: 0..59 → 幅2
printf -v AID_PAD "%0*d" "$PADW" "$SLURM_ARRAY_TASK_ID"

# [06] JobID は末尾に j プレフィックスで付与
JID=${SLURM_JOB_ID:-NA}
JTAG="j${JID}"

# [07] 命名規則:
#      <配列ID(ゼロ埋め)>_<trace名>_<REPO>_<BIN_NAME>_<ARGS番号>_j<JobID>.txt
OUT="results/${AID_PAD}_${TRACE_BASE}_${REPO}_${BIN_NAME}_${ARGIDX}_${JTAG}.txt"

# [08] 診断情報を stderr に出力（.err ログで確認可能）
echo "[$(date '+%Y-%m-%d %H:%M:%S')] START task=${SLURM_ARRAY_TASK_ID} node=$(hostname)" >&2
echo "[INFO] BIN=${BIN}" >&2
echo "[INFO] TRACE=${TRACE}" >&2
echo "[INFO] ARGS=${ARGS}" >&2
echo "[INFO] OUT=${OUT}" >&2

# バイナリの存在確認
if [[ ! -x "$BIN" ]]; then
  echo "[ERROR] Binary not found or not executable: $BIN" >&2
  exit 1
fi

# トレースファイルの存在確認
if [[ ! -f "$TRACE" ]]; then
  echo "[ERROR] Trace file not found: $TRACE" >&2
  exit 1
fi

# [09] 実行
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running: srun $BIN $ARGS $TRACE" >&2
srun "$BIN" $ARGS "$TRACE" > "$OUT"
EXIT_CODE=$?

echo "[$(date '+%Y-%m-%d %H:%M:%S')] DONE task=${SLURM_ARRAY_TASK_ID} exit_code=${EXIT_CODE}" >&2

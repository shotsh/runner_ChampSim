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

set -euo pipefail

# === 環境セットアップ（Grace/Olympus両対応の安全版） ===
# - module が使える場合のみロードを試す（無ければスキップ）
if command -v module >/dev/null 2>&1; then
  # 必要なら modules.sh を読む環境もある（無くてもOK）
  # source /etc/profile.d/modules.sh 2>/dev/null || true

  module -q load GCCcore/13.2.0 2>/dev/null || true
  module -q load Python/3.11.5  2>/dev/null || true
fi

# - venv は存在する場合だけ有効化（Olympus側で無くても落ちない）
VENV_PATH="${CSIM_VENV:-${SCRATCH:-}/venvs/csim}"
if [[ -n "${VENV_PATH}" && -f "${VENV_PATH}/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source "${VENV_PATH}/bin/activate"
else
  # ここはエラーにせず情報だけ
  echo "[INFO] venv not found, skipping: ${VENV_PATH}/bin/activate" >&2
fi

# matplotlib は headless 用に Agg（既にOK）
export MPLBACKEND=Agg
# === ここまで ===

# [01] 配列タスクの行を matrix.tsv から取得（0始まり→+1で1始まりへ）
LINE=$((SLURM_ARRAY_TASK_ID + 1))
ROW=$(sed -n "${LINE}p" matrix.tsv)

if [[ -z "${ROW:-}" ]]; then
  echo "ERROR: matrix.tsv has no line ${LINE} (array_id=${SLURM_ARRAY_TASK_ID})" >&2
  exit 2
fi

# [02] フィールド抽出（1:BIN 2:TRACE 3:ARGS 4:ARGS_IDX）
BIN=$(printf "%s" "$ROW" | cut -f1)
TRACE=$(printf "%s" "$ROW" | cut -f2)
ARGS=$(printf "%s" "$ROW" | cut -f3)         # 空白を含んでもOK（タブ区切り前提）
ARGIDX=$(printf "%s" "$ROW" | cut -f4 2>/dev/null || true)
[[ -z "${ARGIDX:-}" ]] && ARGIDX=0           # 4列が無い古いmatrix.tsvにも対応

# [03] 結果保存ディレクトリ
mkdir -p results logs

# [04] バイナリ系統名（…/ChampSim/bin/champsim → "ChampSim"）
REPO=$(basename "$(dirname "$(dirname "$BIN")")")
TRACE_BASE=$(basename "$TRACE")

# [05] 配列IDの**ゼロ埋め**幅を総タスク数から動的決定
TOTAL=$(wc -l < matrix.tsv)                  # 総タスク数
MAXIDX=$(( TOTAL - 1 ))
PADW=${#MAXIDX}                              # 例: 0..59 → 幅2
printf -v AID_PAD "%0*d" "$PADW" "$SLURM_ARRAY_TASK_ID"

# [06] JobID は末尾に j プレフィックスで付与
JID=${SLURM_JOB_ID:-NA}
JTAG="j${JID}"

# [07] 望む命名規則:
#      <配列ID(ゼロ埋め)>_<trace名>_<REPO>_<ARGS番号>_j<JobID>.txt
OUT="results/${AID_PAD}_${TRACE_BASE}_${REPO}_${ARGIDX}_${JTAG}.txt"

echo "[INFO] BIN=${BIN}"
echo "[INFO] TRACE=${TRACE}"
echo "[INFO] ARGS=${ARGS}"
echo "[INFO] OUT=${OUT}"

# [08] 実行
# NOTE: ARGS が空でも壊れないように bash -lc 経由で組む
if [[ -n "${ARGS}" ]]; then
  srun "$BIN" $ARGS "$TRACE" > "$OUT"
else
  srun "$BIN" "$TRACE" > "$OUT"
fi

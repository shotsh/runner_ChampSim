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
#SBATCH --mail-user=sshintani@tamu.edu # Send all emails to email_address

# === ここから追加（.bashrc はバッチで読まれないため同じ環境を明示）===
module load GCCcore/13.2.0 Python/3.11.5
source "$SCRATCH/venvs/csim/bin/activate"
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

# [08] 実行（最小構成：プリステージやヘッダ付与は省略）
srun "$BIN" $ARGS "$TRACE" > "$OUT"

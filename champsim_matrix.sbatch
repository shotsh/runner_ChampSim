#!/bin/bash
#SBATCH --job-name=csim_matrix
#SBATCH --time=08:00:00
#SBATCH --cpus-per-task=1
#SBATCH --ntasks=1                 # [14] 各配列タスク=1プロセス（ChampSimは単スレッドで十分）
#SBATCH --mem=8G
# #SBATCH --partition=grace        # [15] 必要なら有効化（速度は変わらず、制限時間/キューが変わるだけ）
#SBATCH --output=logs/%x.%A.%a.out # [15] 標準出力ログ（%x=job名, %A=JobID, %a=配列ID）
#SBATCH --error=logs/%x.%A.%a.err  # [15] 標準エラーログ
set -euo pipefail

# [10] Slurmの配列ID（0始まり）を取得。matrix.tsvは1始まりなので +1 して使う
LINE=$((SLURM_ARRAY_TASK_ID + 1))

# [12] matrix.tsv の対象行を取り出す（タブ区切り: 1列目BIN, 2列目TRACE, 3列目ARGS, 4列目ARGS_IDX）
ROW=$(sed -n "${LINE}p" matrix.tsv)
BIN=$(printf "%s" "$ROW" | cut -f1)
TRACE=$(printf "%s" "$ROW" | cut -f2)
ARGS=$(printf "%s" "$ROW" | cut -f3)     # ARGSは1フィールド（内部は空白だがタブは含めない想定）
ARGIDX=$(printf "%s" "$ROW" | cut -f4)   # ARGSの番号(0,1,2,...)

# [13] 結果保存用ディレクトリの用意
mkdir -p results

# …/ChampSim/bin/champsim → REPO="ChampSim"
REPO=$(basename "$(dirname "$(dirname "$BIN")")")
TRACE_BASE=$(basename "$TRACE")

# [10] 配列IDとJobID（結果ファイル名に埋め込む）
AID=${SLURM_ARRAY_TASK_ID:-NA}
JID=${SLURM_JOB_ID:-NA}

# [14] 結果ファイル名の規約: <配列ID>_<JobID>_<trace名>_<REPO>_<ARGS番号>.txt
OUT="results/${AID}_${JID}_${TRACE_BASE}_${REPO}_${ARGIDX}.txt"

# [14] ChampSim を実行（最小：プリステージやヘッダ付与は省略）
srun "$BIN" $ARGS "$TRACE" > "$OUT"

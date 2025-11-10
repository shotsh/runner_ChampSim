#!/bin/bash
#SBATCH --job-name=csim_matrix
#SBATCH --time=08:00:00
#SBATCH --cpus-per-task=1
#SBATCH --ntasks=1                 # ← これを追加（各配列タスク=1タスクのシリアル実行）
#SBATCH --mem=8G
# #SBATCH --partition=grace        # 必要なら有効化
#SBATCH --output=logs/%x.%A.%a.out   # [15] 標準出力はここへ
#SBATCH --error=logs/%x.%A.%a.err    # [15] 標準エラーはここへ
set -euo pipefail

# [10] Slurm が各タスクに与える配列インデックス（0始まり）
# [12] sed の行番号は 1 始まりなので +1 する
LINE=$((SLURM_ARRAY_TASK_ID + 1))

# [12] matrix.tsv の対象行を取り出す
ROW=$(sed -n "${LINE}p" matrix.tsv)

# [13] タブ区切りで 1列目 BIN, 2列目 TRACE, 3列目以降 ARGS
BIN=$(printf "%s" "$ROW" | cut -f1)
TRACE=$(printf "%s" "$ROW" | cut -f2)
ARGS=$(printf "%s" "$ROW" | cut -f3-)

mkdir -p results

# [14] 割り当てられた資源の中で ChampSim を実行
#      結果は配列インデックスを先頭に付けて保存
set -o pipefail
srun "$BIN" $ARGS "$TRACE" > "results/${SLURM_ARRAY_TASK_ID}__$(basename "$TRACE").txt"


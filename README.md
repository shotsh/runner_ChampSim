# runner README

ChampSimの大量実行をGraceなどのSlurm環境で回すための最小ランナーです。
設定は1枚のYAMLを書き、`submit.py`で配列ジョブを投げ、結果はランごとのフォルダにまとまります。
コード内コメントと同じ番号を使って処理の流れを対応付けています。

---

## ディレクトリ構成

```
~/champsim-work/runner/
  submit.py                 # 送信スクリプト
  champsim_matrix.sbatch    # 実行テンプレ（Slurmバッチ）
  recipes/
    runspec.yaml            # 実験レシピ（編集するのは基本ここ）
  runs/
    <日時>_<name>/          # 送信ごとに自動生成（結果が入る）
      matrix.tsv
      sbatch_cmd.txt
      logs/
      results/
```

---

## 前提

* Python 3 系
* PyYAML

  ```
  pip install --user pyyaml
  ```
* Slurm 環境でジョブ投入が可能
  `squeue -u $USER` などが動くこと

---

## クイックスタート

1. `recipes/runspec.yaml` を編集する `[1]`
2. 送信

   ```
   cd ~/champsim-work/runner
   python3 submit.py --recipe recipes/runspec.yaml   # [2]
   ```
3. 状況確認

   ```
   squeue -u $USER                                   # 監視は手動
   ```
4. 結果確認

   ```
   runs/<日時>_<name>/results/*.txt
   runs/<日時>_<name>/logs/*.out, *.err
   ```

---

## 全体パイプライン（番号付き）

1. あなたが `recipes/runspec.yaml` を編集
2. `python3 submit.py --recipe ...` を実行
3. `submit.py` が YAML を読み込む
4. `submit.py` がトレースのパターンを glob 展開
5. ラン用フォルダ `runs/<日時>_<name>/` を作る
6. BIN×TRACE×ARGS の直積を `matrix.tsv` に書く
7. 総タスク数 N を数える
8. N を既定1000件ごとに分割
9. 各チャンクを `sbatch --array=<start>-<end>` で投入し `sbatch_cmd.txt` に記録
10. Slurm が配列をキューイングし各タスクに `SLURM_ARRAY_TASK_ID` を付与
11. 計算ノードで `champsim_matrix.sbatch` が起動
12. 配列インデックスから `matrix.tsv` の対象行を読む
13. タブ区切りを BIN, TRACE, ARGS に分解
14. `srun "$BIN" $ARGS --traces "$TRACE"` を実行し結果を `results/` へ
15. Slurm の標準ログを `logs/` へ
16. 進捗は必要に応じて `squeue` で確認

---

## ファイル別の番号対応

### recipes/runspec.yaml

* `[1]` 編集対象
* `[4]` `traces:` は glob 展開される
* `[8][9]` `resources.chunk` で分割幅を変更可
* `partition` は必要時のみ指定
* `time`, `mem`, `cpus_per_task` はそのまま `sbatch` に渡される

### champsim_matrix.sbatch

* `[10]` `SLURM_ARRAY_TASK_ID` はSlurmが自動で与える配列インデックス
* `[12]` 対象行を `sed` で取り出す
* `[13]` `cut -f1,2,3-` で BIN, TRACE, ARGS を取得
* `[14]` `srun` で ChampSim を実行し `results/` に保存
* `[15]` `%x.%A.%a` でログファイルを一意化

### submit.py

* `[3]` runspec を読み込み
* `[4]` glob 展開で実在ファイルに解決
* `[5]` ラン用フォルダと `logs/` `results/` を作成
* `[6]` 直積を `matrix.tsv` に書く
* `[7]` 総タスク数を算出
* `[8][9]` 1000件ずつに自動分割し配列で投入、`sbatch_cmd.txt` に記録

---

## 出力物の見方

```
runs/<日時>_<name>/
  matrix.tsv          # 1行が1タスク（BIN<TAB>TRACE<TAB>ARGS）
  sbatch_cmd.txt      # 投入に使った sbatch コマンドの記録
  logs/               # Slurmの標準ログ（%x.%A.%a 形式）
  results/            # ChampSimの出力
    0__<trace名>.txt
    1__<trace名>.txt
```

* `results/00__*.txt` の先頭番号は配列インデックス
* 対応する行は `sed -n '<インデックス+1>p' matrix.tsv` で確認できる

---

## よくある質問

* **TSVとは**
  Tab Separated Values の略。CSVに似たタブ区切りのテキスト表
* **globとは**
  `*` や `?` `[]` を使うファイル名パターン展開のこと
  例 `/path/gap/bc-*.trace.gz`
* **SLURM_ARRAY_TASK_IDは自分で定義するのか**
  いいえ。配列ジョブを使うとSlurmが各タスクに自動で渡す
* **大量投入は大丈夫か**
  既定で1000件ずつに分割して配列投入するので、サイトの配列上限と整合する
  上限が小さいサイトでは `resources.chunk` を下げる

---

## 監視と運用ヒント

* 監視

  ```
  squeue -u $USER
  squeue -u $USER -o "%.18i %.9P %.8j %.8T %.10M %.9l %.6D %R" | grep <name>
  ```
* キュー状態

  ```
  sinfo
  ```
* サイトの配列上限

  ```
  scontrol show config | grep -i MaxArraySize
  ```

---

## トラブルシュート

* `bins が空です / traces が空です / args が空です`
  runspec.yaml の該当セクションを確認。絶対パス推奨
* `テンプレートがありません`
  `champsim_matrix.sbatch` が `submit.py` と同じディレクトリにあるか確認
* 投入はされたが結果が無い
  `logs/*.err` を確認。トレースパスや権限を見直す
* 配列上限に当たる
  `resources.chunk` を上限以下の値に下げる

---

## レシピ例

```yaml
name: spec_sample
bins:
  - /home/sshintani/champsim-work/ChampSim/bin/champsim
traces:
  - /scratch/user/sshintani/traces/speccpu/403.gcc-*.trace.gz
  - /scratch/user/sshintani/traces/gap/bfs-3.trace.gz
args:
  - "--warmup_instructions 100000000 --simulation_instructions 100000000"
resources:
  time: 08:00:00
  mem: 8G
  cpus_per_task: 1
  chunk: 1000
```

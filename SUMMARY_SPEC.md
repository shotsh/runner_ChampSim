# SUMMARY_SPEC.md

## 1. 目的

本仕様は、ChampSim 実行ログを集計するサマライザの挙動を定義する。  
対象は以下の 2 系統を含む。

- normal ChampSim 形式ログ
- Wrong-Path 対応 ChampSim（以下 WP ChampSim）形式ログ

目標は次の通り。

- 手動でスクリプトを切り替えずに集計できること
- `submit.py` の運用を変更しないこと（同じ summarize 呼び出しを維持）
- 人間向け要約と深掘り向け構造化データを同時に出力すること

---

## 2. スコープ

本仕様が扱う範囲:

- ログ形式の自動判定
- メトリクス抽出ルール
- CSV 出力仕様（`full_metrics.csv` / `summary.csv`）
- 警告・エラー処理
- 列安定性と可読性ルール

本仕様の範囲外:

- ジョブ投入ロジックそのもの（`submit.py` の本体仕様）
- ログ外情報（レシピ等）に依存した必須判定
- multi-core 集計（現版は `cpu0` のみ対象）

---

## 3. 入力

- 入力ファイル: `results/` 配下の `*.txt` ログ
- ログは 1 ファイルずつ独立に解析する
- 判定はログ本文のみで完結させる（レシピ参照を必須にしない）
- ベンチ名・設定名の抽出優先順位:
  - 1位: ファイル名
  - 2位: ログ本文（将来実装時の拡張）
  - 3位: `unknown`

---

## 4. ログ判定ルール

各ログについて、以下 2 軸を判定する。

### 4.1 `log_format`

判定は排他的。normal と wp_capable は同一ログに共存しない（バイナリが異なるため）。

- `normal`
  - `cpu0->cpu0_` 形式の統計行が存在
- `wp_capable`
  - `WRONG-PATH` 統計行が存在（WP ON/OFF 問わず WP ChampSim バイナリ由来ログに必ず出現）
- `unknown`
  - 上記いずれにも該当しない

判定補足:

- `WRONG-PATH` 統計行（`ACCESS:` 等を含む完全形式）の存在を判定に使う。文字列単独ヒットは不可
- `unknown` の場合は `parse_errors.csv` に `unknown_format` を記録して行スキップ

### 4.2 `wp_mode`

- `on`
  - `Wrong path enabled` 行が存在
- `off`
  - 上記以外（`wp_capable` ログで WP を無効にして実行した場合を含む）

重要:

- `wp_capable` かつ `wp_mode=off` は正当状態（WP 対応バイナリを通常モード実行）
- `log_format=normal` かつ `wp_mode=on` の組み合わせは構造上発生しない

---

## 5. ROI 抽出ルール

- ROI 基本行: `CPU <id> cumulative IPC: ... instructions: ... cycles: ...`
- 複数候補がある場合は「最後に出現した行」を採用
- warmup 関連行は ROI 行として扱わない
- 途中打ち切りログで ROI 行がない場合はハードエラー扱い（行スキップ）

---

## 6. 出力

出力先ディレクトリ: `results/summary_out/`

### 6.1 `full_metrics.csv`

- 取得可能な項目を可能な限り全て出力
- 欠損は空欄
- 人間と機械の両方が読む前提
- 生値だけでなく派生値（例: `ipc`, `*_mpki`）も含める

### 6.2 `summary.csv`

- 比較に必要な最小項目のみ出力
- 人が日常確認する軽量サマリ
- 原則として `full_metrics.csv` の部分集合

### 6.3 補助出力

- `parse_errors.csv`
- `normalized_ipc.csv`（既存機能）
- 図表ファイル（有効化時）

---

## 7. 共通メタ列

`full_metrics.csv` と `summary.csv` に最低限含める。

- `bench`
- `config`
- `file`
- `log_format`
- `wp_mode`
- `parse_warnings`

---

## 8. メトリクス定義

定義はログ表示値を優先し、必要時のみ算出する。

- `inst`
  - ROI committed instructions
- `cycles`
  - ROI cycles
- `ipc`
  - 原則: ROI 行のログ値を採用
  - 補助検算式: `inst / cycles`
- `*_mpki`
  - `miss_count / inst * 1000`
  - 分母は ROI instructions
- `branch_acc_percent`
  - 原則: ログ値を採用
- `llc_miss_lat`
  - 単位: cycles
  - 意味: ログが示す平均ミスレイテンシ
  - サンプル 0 相当時（`-nan` 等）は空欄
- `wp_cycles`
  - ログ出力の定義値をそのまま転記
  - `cycles` との加算関係を仮定しない

miss カウントの範囲:

- `l1d_load_miss` / `l2_load_miss` / `llc_load_miss` は `LOAD` 行由来（demand load 系）
- `TOTAL` / `RFO` / `WRITE` / `PREFETCH` miss とは別概念として扱う

---

## 9. 列設計と安定性

### 9.1 命名規約

- 率: `_percent`
- MPKI: `_mpki`
- 平均レイテンシ: `_avg_lat_cycles` を推奨（互換期間は既存名を許容）

### 9.2 列順

`full_metrics.csv` は以下順でグループ化して並べる。

- 識別: `bench, config, file`
- 判定: `log_format, wp_mode, parse_warnings`
- ROIコア: `cycles, wp_cycles, inst, ipc`
- Branch
- Cache（L1D/L2/LLC）
- TLB（DTLB/ITLB/STLB）
- その他

### 9.3 互換性

- 列順は固定
- 新規列は原則末尾追加
- 欠損表現は空文字 `""` で統一
- `summary.csv` は原則 `full_metrics.csv` の部分集合

---

## 10. 実行モデル

- 各ログを 1 回だけパースしてメモリ上の `rows` を作る
- 同じ `rows` から `full_metrics.csv` と `summary.csv` を書き出す
- 片方の CSV を再読込してもう片方を作る方式は採用しない

理由:

- ロジック重複を防ぐ
- 2 CSV の整合を維持しやすい

---

## 11. 警告・エラー処理

### 11.1 `parse_warnings` 形式

- 1セルに `|` 区切りコード列で格納
- 例: `missing_wp_cycles|conflict_signature_resolved_wp_capable`

推奨コード:

- `missing_<field>`
- `wp_signature_weak_match`

### 11.2 `parse_errors.csv` 形式

CSV ヘッダ固定:

- `file,bench,config,error_code,detail`

代表 `error_code`:

- `missing_roi`
- `unreadable_file`
- `unknown_format`

挙動:

- `missing_roi` / `unknown_format` は原則行スキップ
- ROI 取得済みの部分欠損はスキップせず警告のみ

---

## 12. summary 最小列

`summary.csv` には最低限、以下を含める。

- `bench, config, log_format, wp_mode, parse_warnings`
- `cycles, wp_cycles, inst, ipc`
- `branch_mpki`
- `llc_load_miss, llc_load_mpki, llc_miss_lat`

`log_format=wp_capable` のログにのみ追加する列:

- `wp_access, wp_useful, wp_useful_percent`
- `cp_miss`

除外の根拠:

- `file`: bench + config から推測可能、日常確認には不要
- `dtlb_*`: これまでの分析で一貫して無視できるレベル。full_metrics には残す
- `l2_load_*`: LLC で代表させる。詳細は full_metrics 参照
- `branch_acc_percent`: MPKI で代表させる（率と頻度は二重）

---

## 13. スキーマバージョン（将来対応）

現時点では未実装。仕様が安定した時点で検討する。

候補列: `summary_schema_version`, `parser_version`

---

## 14. テスト観点（最低限）

- normal ログ（完全）
- wp_capable + wp_mode=on（完全）
- wp_capable + wp_mode=off（完全）
- ROI 欠損ログ（スキップ）
- ROI あり・一部統計欠損ログ（行保持）
- 途中打ち切りログ
- 空ファイル

---

## 15. 運用上の整合

- 手動モード指定なしで両形式を自動処理する
- `submit.py` 側で summarize スクリプト切替を要求しない
- 既存運用に対する利用者操作を増やさない

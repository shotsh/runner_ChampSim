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
- `*_miss_lat`
  - 単位: cycles
  - `-nan` / `-` 等は空欄（`safe_float` で処理）
- `wp_cycles`
  - ログ出力の定義値をそのまま転記
  - `cycles` との加算関係を仮定しない
- `{lv}_pollution`
  - POLLUTION 行の先頭数値（WP_MISS / CP_FILL 比）
  - 「CP fill 1 回あたりの WP DRAM アクセス数」に相当
- `{lv}_pf_useful` / `{lv}_pf_useless`
  - PREFETCH REQUESTED 行由来
  - L1D/L2C は実値あり。LLC は WP 形式で常に 0（LLC 独自 prefetcher なし）
- `{lv}_wp_useful`
  - WRONG-PATH 行 `USEFULL:` 由来（ログ typo のまま保持）
  - WP demand fill のうち CP が後で使ったライン数
- `{lv}_pol_cp_miss`
  - POLLUTION 行 `CP_MISS:` 由来
  - correct-path の need による miss 数（WP miss を除く）
  - LLC では WP 研究の主要メトリクスの一つ
- `{lv}_data_wp_req` / `{lv}_data_wp_hit` / `{lv}_data_wp_miss`
  - DATA REQ 行由来（WP ChampSim のみ）
  - WP ロードが当該レベルに要求した総数・ヒット・ミス

miss カウントの範囲:

- `{lv}_load_miss` は `LOAD` 行由来（demand load のみ）
- `TOTAL` / `RFO` / `WRITE` / `PREFETCH` miss とは別概念として扱う
- `{lv}_data_miss` は DATA REQ 行由来（demand + WP 合算）

---

## 9. 列設計と安定性

### 9.1 命名規約

- 率: `_percent`
- MPKI: `_mpki`
- 平均レイテンシ: `_miss_lat`（サイクル単位）
- WP 分離レイテンシ: `_wp_miss_lat` / `_cp_miss_lat`
- Pollution 由来フィールド: `_pol_` プレフィックス

### 9.2 列順・グループ構成

`full_metrics.csv` は 183 列、以下の順でグループ化する。

| グループ | 列数 | 対象フォーマット |
|---------|------|----------------|
| 識別 | 6 | 両方 |
| ROI コア + WP insts | 11 | 一部 WP のみ |
| Branch | 8 | 両方 |
| Pipeline / Execute | 10 | WP のみ |
| L1D キャッシュ | 29 | 一部 WP のみ |
| L1I キャッシュ | 29 | 一部 WP のみ |
| L2C キャッシュ | 29 | 一部 WP のみ |
| LLC キャッシュ | 29 | 一部 WP のみ |
| DTLB | 10 | 一部 WP のみ |
| ITLB | 10 | 一部 WP のみ |
| STLB | 10 | 一部 WP のみ |
| DRAM | 2 | 両方 |
| **合計** | **183** | |

### 9.3 互換性

- 列順は固定
- 新規列は原則末尾追加
- 欠損・非対応（normal 形式の WP 専用列等）は空文字 `""` で統一
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

**識別（全形式）**
- `bench, config, log_format, wp_mode, parse_warnings`

**ROI コア（全形式）**
- `cycles, wp_cycles, inst, ipc`

**Branch（全形式）**
- `branch_mpki`

**LLC（全形式）**
- `llc_load_miss, llc_load_mpki, llc_miss_lat`
- `llc_pf_useful, llc_pf_useless`（prefetch quality）

**WP 専用（`log_format=wp_capable` のみ）**
- `llc_wp_access, llc_wp_useful`（WP demand fill）
- `llc_pol_cp_miss`（correct-path LLC miss）
- `l2c_pf_useful, l2c_pf_useless`（L2C prefetch quality）
- `l2c_pollution`（L2C 汚染比率）

除外の根拠:

- `file`: bench + config から推測可能、日常確認には不要
- TLB 系: 研究対象外。full_metrics には残す
- Pipeline stats: 詳細分析時のみ参照。full_metrics に存在
- `branch_acc_percent`: MPKI で代表
- DATA REQ 詳細: full_metrics 参照

注: `wp_useful_percent` は `llc_wp_useful / llc_wp_access * 100` として summary 側で算出可。
現版では full_metrics.csv の値を参照することを推奨。

---

## 16. full_metrics.csv 全列定義

### 凡例

- **対象**: `both`=両形式, `wp`=WP ChampSim のみ（normal 形式は空欄）
- **ソース**: ログ行の種別
- キャッシュレベル変数 `{lv}` = `l1d` / `l1i` / `l2c` / `llc`
- TLB レベル変数 `{tlv}` = `dtlb` / `itlb` / `stlb`

---

### G1. 識別（6列）

| 列名 | 対象 | 内容 |
|------|------|------|
| `bench` | both | ファイル名から抽出したベンチ名（インデックス・拡張子除去済み） |
| `config` | both | `--label-map` で割り当てた設定ラベル |
| `file` | both | ログファイルのベース名 |
| `log_format` | both | `wp_capable` / `normal` / `unknown` |
| `wp_mode` | both | `on` / `off` |
| `parse_warnings` | both | `\|` 区切り警告コード列 |

---

### G2. ROI コア + WP insts（11列）

| 列名 | 対象 | ソース行 |
|------|------|---------|
| `cycles` | both | ROI 行 `cycles:` |
| `inst` | both | ROI 行 `instructions:` |
| `ipc` | both | ROI 行 `cumulative IPC:` |
| `wp_cycles` | wp | ROI 行 `wp_cycles:` |
| `wp_insts_total` | wp | `wrong_path_insts:` |
| `wp_insts_skipped` | wp | `wrong_path_insts_skipped:` |
| `wp_insts_executed` | wp | `wrong_path_insts_executed:` |
| `instr_footprint` | wp | `instr_foot_print:` |
| `data_footprint` | wp | `data_foot_print:` |
| `is_prefetch_insts` | wp | `is_prefetch_insts:` |
| `is_prefetch_skipped` | wp | `is_prefetch_skipped:` |

---

### G3. Branch（8列）

| 列名 | 対象 | ソース行 |
|------|------|---------|
| `branch_acc_percent` | both | `Branch Prediction Accuracy:` |
| `branch_mpki` | both | `MPKI:` （同行） |
| `br_direct_jump_mpki` | both | `BRANCH_DIRECT_JUMP:` |
| `br_indirect_mpki` | both | `BRANCH_INDIRECT:` |
| `br_conditional_mpki` | both | `BRANCH_CONDITIONAL:` |
| `br_direct_call_mpki` | both | `BRANCH_DIRECT_CALL:` |
| `br_indirect_call_mpki` | both | `BRANCH_INDIRECT_CALL:` |
| `br_return_mpki` | both | `BRANCH_RETURN:` |

---

### G4. Pipeline / Execute stats（10列、WP のみ）

| 列名 | ソース行 |
|------|---------|
| `exec_only_wp_cycles` | `Execute Only WP Cycles` |
| `exec_only_cp_cycles` | `Execute Only CP Cycles` |
| `exec_cp_wp_cycles` | `Execute CP WP Cycles` |
| `rob_full_cycles` | `ROB Full Cycles` |
| `rob_empty_cycles` | `ROB Empty Cycles` |
| `rob_full_events` | `ROB Full Events` |
| `rob_empty_events` | `ROB Empty Events` |
| `resteer_events` | `Resteer Events` |
| `resteer_penalty_pct` | `Resteer Penalty` |
| `wp_not_avail_cycles_pct` | `WP Not Available Count ... Cycles` のパーセント部 |

---

### G5. キャッシュ per level（29列 × 4 = 116列）

`{lv}` = `l1d`, `l1i`, `l2c`, `llc` の順

| 列名パターン | 対象 | ソース行 |
|------------|------|---------|
| `{lv}_load_access` | both | `{LV} LOAD ACCESS:` |
| `{lv}_load_hit` | both | 同行 `HIT:` |
| `{lv}_load_miss` | both | 同行 `MISS:` |
| `{lv}_load_mpki` | both | 算出: `load_miss / inst * 1000` |
| `{lv}_pf_access` | both | `{LV} PREFETCH ACCESS:` |
| `{lv}_pf_hit` | both | 同行 `HIT:` |
| `{lv}_pf_miss` | both | 同行 `MISS:` |
| `{lv}_pf_requested` | both | `{LV} PREFETCH REQUESTED:` |
| `{lv}_pf_issued` | both | 同行 `ISSUED:` |
| `{lv}_pf_useful` | both | 同行 `USEFUL:` |
| `{lv}_pf_useless` | both | 同行 `USELESS:` |
| `{lv}_wp_access` | wp | `{LV} WRONG-PATH ACCESS:` |
| `{lv}_wp_useful` | wp | 同行 `USEFULL:` （typo はログ準拠） |
| `{lv}_wp_fill` | wp | 同行 `FILL:` |
| `{lv}_wp_useless` | wp | 同行 `USELESS:` |
| `{lv}_pollution` | wp | `{LV} POLLUTION:` の比率値 |
| `{lv}_pol_wp_fill` | wp | 同行 `WP_FILL:` |
| `{lv}_pol_wp_miss` | wp | 同行 `WP_MISS:` |
| `{lv}_pol_cp_fill` | wp | 同行 `CP_FILL:` |
| `{lv}_pol_cp_miss` | wp | 同行 `CP_MISS:` |
| `{lv}_data_req` | wp | `{LV} DATA REQ:` の total |
| `{lv}_data_hit` | wp | 同行 `HIT:` |
| `{lv}_data_miss` | wp | 同行 `MISS:` |
| `{lv}_data_wp_req` | wp | 同行 `WP_REQ:` |
| `{lv}_data_wp_hit` | wp | 同行 `WP_HIT:` |
| `{lv}_data_wp_miss` | wp | 同行 `WP_MISS:` |
| `{lv}_miss_lat` | both | `{LV} AVERAGE MISS LATENCY:` または `AVERAGE DATA MISS LATENCY:` |
| `{lv}_wp_miss_lat` | wp | `{LV} AVERAGE WP DATA MISS LATENCY:` |
| `{lv}_cp_miss_lat` | wp | `{LV} AVERAGE CP DATA MISS LATENCY:` |

注: `{lv}_miss_lat` は WP 形式では `AVERAGE DATA MISS LATENCY:`、normal 形式では `AVERAGE MISS LATENCY:` を使用。

---

### G6. TLB per level（10列 × 3 = 30列）

`{tlv}` = `dtlb`, `itlb`, `stlb` の順

| 列名パターン | 対象 | ソース行 |
|------------|------|---------|
| `{tlv}_access` | both | `{TLV} LOAD ACCESS:` |
| `{tlv}_hit` | both | 同行 `HIT:` |
| `{tlv}_miss` | both | 同行 `MISS:` |
| `{tlv}_mpki` | both | 算出: `miss / inst * 1000` |
| `{tlv}_wp_access` | wp | `{TLV} WRONG-PATH ACCESS:` |
| `{tlv}_wp_useful` | wp | 同行 `USEFULL:` |
| `{tlv}_wp_useless` | wp | 同行 `USELESS:` |
| `{tlv}_miss_lat` | both | `{TLV} AVERAGE DATA MISS LATENCY:` または `AVERAGE MISS LATENCY:` |
| `{tlv}_wp_miss_lat` | wp | `{TLV} AVERAGE WP DATA MISS LATENCY:` |
| `{tlv}_cp_miss_lat` | wp | `{TLV} AVERAGE CP DATA MISS LATENCY:` |

---

### G7. DRAM（2列）

| 列名 | 対象 | ソース行 |
|------|------|---------|
| `dram_rq_row_hit` | both | `Channel 0 RQ ROW_BUFFER_HIT:` |
| `dram_rq_row_miss` | both | `ROW_BUFFER_MISS:` （RQ ブロック内） |

注: WQ・REFRESHES・AVG DBUS 等は現版では対象外。

---

### 列数確認

```
G1 識別:          6
G2 ROI:          11
G3 Branch:        8
G4 Pipeline:     10
G5 Cache×4:    4×29 = 116
G6 TLB×3:      3×10 =  30
G7 DRAM:          2
─────────────────────
合計:            183
```

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

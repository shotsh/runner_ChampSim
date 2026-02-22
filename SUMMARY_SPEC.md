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
- シグネチャの具体パターン（Python 正規表現）:
  ```
  ^(?:cpu0_\w+|LLC) WRONG-PATH\s+ACCESS:
  ```
  `LLC WRONG-PATH ACCESS:` のみでは不十分。WP 形式ログでは `cpu0_L1D WRONG-PATH ACCESS:` 等、各キャッシュレベルにも同行が存在するため、いずれかのレベルへのマッチで判定する。
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

`full_metrics.csv` の列数・列定義は、処理対象ログの形式に応じて自動切り替えする。

#### パース後の自動選択ルール

- パース対象ログに `wp_capable` 行が **1行でも含まれる** → **フルスキーマ（183列）**
- パース対象ログが **全行 `normal`** → **ノーマルスキーマ（82列）**

この判定はスクリプト実行時に自動で行われ、ユーザ操作は不要。

#### フルスキーマ（183列、wp_capable ログ用）

| グループ | 列数 | 備考 |
|---------|------|------|
| 識別 | 6 | 全行共通 |
| ROI コア + WP insts | 11 | WP 専用フィールドは normal 行で空欄 |
| Branch | 8 | 全行共通 |
| Pipeline / Execute | 10 | WP バイナリのみ（normal 行で空欄） |
| L1D / L1I / L2C / LLC キャッシュ | 29 × 4 = 116 | WP 専用フィールドは WP OFF / normal で空欄 |
| DTLB / ITLB / STLB | 10 × 3 = 30 | 同上 |
| DRAM | 2 | 全行共通 |
| **合計** | **183** | |

#### ノーマルスキーマ（82列、normal ログ専用）

WP 専用列（G2 WP insts・G4 Pipeline・G5/G6 の WP フィールド）を**列ごと出力しない**。
ノーマルログに実際に存在するフィールドのみで構成する。

| グループ | 列数 | 内容 |
|---------|------|------|
| 識別 | 6 | |
| ROI コア | 3 | `cycles`, `inst`, `ipc`（`wp_cycles` 等は列自体なし） |
| Branch | 8 | 全フィールド |
| Pipeline | 0 | 列なし |
| L1D / L1I / L2C / LLC キャッシュ | 12 × 4 = 48 | `load_*`, `pf_*`, `miss_lat` のみ |
| DTLB / ITLB / STLB | 5 × 3 = 15 | `access`, `hit`, `miss`, `mpki`, `miss_lat` のみ |
| DRAM | 2 | |
| **合計** | **82** | |

### 9.3 互換性

- 列順は各スキーマ内で固定
- 新規列は原則末尾追加
- スキーマ選択は自動（`log_format` による判定）
- `summary.csv` は `full_metrics.csv` と同じスキーマ判定に従う部分集合

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

`summary.csv` は §9.2 のスキーマ自動選択と連動し、**フルスキーマ時**と**ノーマルスキーマ時**で列定義が変わる。

### フルスキーマ時（wp_capable ログあり）

**識別**
- `bench, config, log_format, wp_mode, parse_warnings`

**ROI コア**
- `cycles, wp_cycles, inst, ipc`

**Branch**
- `branch_mpki`

**LLC**
- `llc_load_miss, llc_load_mpki, llc_miss_lat`
- `llc_pf_useful, llc_pf_useless`

**WP 専用**
- `llc_wp_access, llc_wp_useful`（WP demand fill）
- `llc_pol_cp_miss`（correct-path LLC miss）
- `l2c_pf_useful, l2c_pf_useless`（L2C prefetch quality）
- `l2c_pollution`（L2C 汚染比率）

### ノーマルスキーマ時（全行 normal）

WP 専用列は列ごと出力しない。

**識別**
- `bench, config, log_format, wp_mode, parse_warnings`

**ROI コア**
- `cycles, inst, ipc`（`wp_cycles` は列なし）

**Branch**
- `branch_mpki`

**LLC**
- `llc_load_miss, llc_load_mpki, llc_miss_lat`
- `llc_pf_useful, llc_pf_useless`

**L2C**
- `l2c_pf_useful, l2c_pf_useless`

除外の根拠:

- `file`: bench + config から推測可能、日常確認には不要
- TLB 系: 研究対象外。full_metrics には残す
- Pipeline stats: 詳細分析時のみ参照。full_metrics に存在
- `branch_acc_percent`: MPKI で代表
- DATA REQ 詳細: full_metrics 参照

---

## 13. full_metrics.csv 全列定義

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
| `{lv}_pollution` | wp | `{LV} POLLUTION:` の比率値（WP OFF 時は **空欄**） |
| `{lv}_pol_wp_fill` | wp | 同行 `WP_FILL:`（WP OFF 時は空欄） |
| `{lv}_pol_wp_miss` | wp | 同行 `WP_MISS:`（WP OFF 時は空欄） |
| `{lv}_pol_cp_fill` | wp | 同行 `CP_FILL:`（WP OFF 時は 0 を保持） |
| `{lv}_pol_cp_miss` | wp | 同行 `CP_MISS:`（WP OFF 時も保持、CP-path miss の主要指標） |
| `{lv}_data_req` | wp | `{LV} DATA REQ:` の total（WP OFF 時も実値を保持） |
| `{lv}_data_hit` | wp | 同行 `HIT:`（WP OFF 時も実値を保持） |
| `{lv}_data_miss` | wp | 同行 `MISS:`（WP OFF 時も実値を保持） |
| `{lv}_data_wp_req` | wp | 同行 `WP_REQ:`（WP OFF 時は空欄） |
| `{lv}_data_wp_hit` | wp | 同行 `WP_HIT:`（WP OFF 時は空欄） |
| `{lv}_data_wp_miss` | wp | 同行 `WP_MISS:`（WP OFF 時は空欄） |
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

注: WP ログの TLB WRONG-PATH 行には `FILL:` フィールドも存在するが、現版スキーマでは意図的に除外している（§17.5 参照）。
| `{tlv}_miss_lat` | both | WP 形式: `{TLV} AVERAGE DATA MISS LATENCY:`、normal 形式: `{TLV} AVERAGE MISS LATENCY:` |
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

## 14. スキーマバージョン（将来対応）

現時点では未実装。仕様が安定した時点で検討する。

候補列: `summary_schema_version`, `parser_version`

---

## 15. テスト観点（最低限）

| テスト観点 | 実施状況 |
|-----------|---------|
| normal ログ（完全） | ✓ 確認済み（10 ファイル、2026-01-21 run） |
| wp_capable + wp_mode=on（完全） | ✓ 確認済み（8 ベンチ、2026-02-11 run） |
| wp_capable + wp_mode=off（完全） | ✓ 確認済み（8 ベンチ、2026-02-11 run） |
| ROI 欠損ログ（スキップ） | コードレベルで処理あり（実ログ未確認） |
| ROI あり・一部統計欠損ログ（行保持） | コードレベルで処理あり（実ログ未確認） |
| 途中打ち切りログ | コードレベルで処理あり（実ログ未確認） |
| 空ファイル | コードレベルで処理あり（実ログ未確認） |

確認済み項目の検証内容:
- 183 列数の一致を assert で保証
- 14〜18 項目の raw ログ値 vs CSV 値スポットチェック（全 OK）
- WP OFF 時の抑制ルール（WP 活動フィールド空欄、CP-path フィールド保持）を確認

---

## 16. 運用上の整合

- 手動モード指定なしで両形式を自動処理する
- `submit.py` 側で summarize スクリプト切替を要求しない
- 既存運用に対する利用者操作を増やさない

---

## 17. ログ構造差分：Normal ChampSim vs WP ChampSim

WP ログは Normal に対して「WP 命令がキャッシュ階層をどう通過したか」と「パイプラインが WP のせいでどう詰まったか」の 2 種類の情報が階層ごとに丸ごと追加されている。Normal ログにあるものはすべて WP ログにも含まれており、**上位互換**の関係にある。

```
差分の出所（82列 → 183列 = +101列）

G2  ROI 拡張         :  +8   (wp_cycles, wp_insts_*, footprint, is_prefetch_*)
G4  パイプライン統計  : +10   ← Normal には存在しないセクション
G5  キャッシュ × 4   : +68   (17列/レベル × 4)
G6  TLB × 3          : +15   (5列/TLB × 3)
────────────────────────────
合計                  : +101列
```

---

### 17.1 概観

| 項目 | Normal ChampSim | WP ChampSim |
|------|:---:|:---:|
| ログ行数（目安） | ~112 行 | ~743 行 |
| CSV 列数（スキーマ） | 82 列 | 183 列 |
| 統計ラインプレフィックス | `cpu0->cpu0_{LV}` / `cpu0->LLC` | `cpu0_{LV}` / `LLC` |
| WP 専用セクション | なし | G2 WP insts, G4 Pipeline, 各レベルの WP 統計行 |

両形式はバイナリが異なる（`log_format` で自動判別）。
WP ChampSim バイナリは WP OFF 実行でも WP 統計行を全レベルに出力する（値は 0、詳細は §17.4）。

---

### 17.2 各セクションの対応表

| セクション | Normal | WP（ON/OFF 共通） |
|-----------|:------:|:-----------------:|
| ROI 行（IPC / inst / cycles） | ✓ | ✓ |
| `wp_cycles` / `wrong_path_insts` 等 | — | ✓（WP ON 時のみ非ゼロ） |
| `instr_foot_print` / `data_foot_print` | — | ✓ |
| Branch 統計 | ✓ | ✓ |
| Execute Only WP/CP Cycles 等（G4） | — | ✓ |
| ROB Full/Empty Cycles/Events | — | ✓ |
| Resteer Events / Penalty | — | ✓ |
| `{LV} LOAD/PREFETCH ACCESS:` 等 | ✓ | ✓ |
| `{LV} PREFETCH REQUESTED:` | ✓ | ✓ |
| `{LV} WRONG-PATH ACCESS:` | — | ✓（WP OFF 時は全フィールド 0） |
| `{LV} POLLUTION:` | — | ✓（WP OFF 時: WP_FILL/WP_MISS/比率=0、CP_MISS=実値） |
| `{LV} INSTR REQ:` / `DATA REQ:` | — | ✓（WP OFF 時: WP_* = 0、CP 側は実値） |
| `AVERAGE MISS LATENCY:` | ✓（1 行のみ） | ✓（WP 形式でも出力、ただしスキーマ採用外） |
| `AVERAGE DATA MISS LATENCY:` | — | ✓（サマライザの `miss_lat` に採用） |
| `AVERAGE WP DATA MISS LATENCY:` | — | ✓（WP OFF 時は -nan） |
| `AVERAGE CP DATA MISS LATENCY:` | — | ✓ |
| DRAM Statistics | ✓ | ✓ |

---

### 17.3 キャッシュ統計行の詳細比較（1 レベルあたり）

#### Normal ChampSim の主要行（例: L1D）

```
cpu0->cpu0_L1D LOAD         ACCESS: N  HIT: N  MISS: N
cpu0->cpu0_L1D PREFETCH     ACCESS: N  HIT: N  MISS: N
cpu0->cpu0_L1D PREFETCH REQUESTED: N  ISSUED: N  USEFUL: N  USELESS: N
cpu0->cpu0_L1D AVERAGE MISS LATENCY: N cycles
（TOTAL / RFO / WRITE / TRANSLATION 行は集計対象外）
```

#### WP ChampSim の主要行（例: L1D、WP 専用行を追記）

```
cpu0_L1D LOAD         ACCESS: N  HIT: N  MISS: N
cpu0_L1D PREFETCH     ACCESS: N  HIT: N  MISS: N
cpu0_L1D PREFETCH REQUESTED: N  ISSUED: N  USEFUL: N  USELESS: N
cpu0_L1D WRONG-PATH ACCESS: N  LOAD: N  USEFULL: N  FILL: N  USELESS: N   ← WP専用
cpu0_L1D POLLUTION: R  WP_FILL: N  WP_MISS: N  CP_FILL: N  CP_MISS: N     ← WP専用
cpu0_L1D INSTR REQ: N  HIT: N  MISS: N  WP_REQ: N  WP_HIT: N  WP_MISS: N ← WP専用
cpu0_L1D DATA REQ:  N  HIT: N  MISS: N  WP_REQ: N  WP_HIT: N  WP_MISS: N ← WP専用
cpu0_L1D AVERAGE MISS LATENCY: N cycles            （集計対象外: WP 含む全体平均）
cpu0_L1D AVERAGE DATA MISS LATENCY: N cycles       ← miss_lat に採用
cpu0_L1D AVERAGE WP DATA MISS LATENCY: N cycles    ← wp_miss_lat に採用
cpu0_L1D AVERAGE CP DATA MISS LATENCY: N cycles    ← cp_miss_lat に採用
（AVERAGE INSTR MISS / WP INSTR MISS / CP INSTR MISS LATENCY は集計対象外）
```

---

### 17.4 WP OFF 時の WP 統計行の値（WP ChampSim バイナリ使用時）

WP ChampSim バイナリを WP 無効で実行した場合、WP 統計行は出力されるが値は次の通り：

| ログ行 | WP OFF 時の実値 |
|--------|----------------|
| `WRONG-PATH ACCESS:` | 全フィールド 0 |
| `POLLUTION:` | 比率 = 0、`WP_FILL` = 0、`WP_MISS` = 0、`CP_FILL` = 0、**`CP_MISS` = 実値** |
| `DATA REQ:` | `data_req/hit/miss` = 実値、`WP_*` = 0 |
| `AVERAGE WP DATA MISS LATENCY:` | `-nan`（ゼロ除算） |

サマライザの抑制ルール（§13 G5）との対応：

| フィールド群 | WP OFF 時のサマライザ動作 |
|-------------|--------------------------|
| `wp_access/useful/fill/useless` | → 空欄（None に抑制） |
| `pollution / pol_wp_fill / pol_wp_miss` | → 空欄（None に抑制） |
| `pol_cp_fill` | → 0 を保持（ログ実値が 0） |
| `pol_cp_miss` | → 実値を保持（CP-path miss の主要指標） |
| `data_req / data_hit / data_miss` | → 実値を保持（WP OFF でも出力される） |
| `data_wp_req / data_wp_hit / data_wp_miss` | → 空欄（None に抑制） |

---

### 17.5 TLB 統計の差分

キャッシュと同様に WP 統計行が追加されるが、G6 スキーマは省略版を採用している：

| フィールド | キャッシュ（G5） | TLB（G6） | 備考 |
|-----------|:---:|:---:|------|
| `wp_access` | ✓ | ✓ | |
| `wp_useful` | ✓ | ✓ | |
| `wp_fill` | ✓ | **除外** | WP ログの WRONG-PATH 行に `FILL:` は存在するが現版スキーマ対象外 |
| `wp_useless` | ✓ | ✓ | |
| `pollution` / `pol_*` | ✓ | **除外** | WP ログに POLLUTION 行は存在するが現版スキーマ対象外 |
| `data_req` / `data_*` | ✓ | **除外** | WP ログに DATA REQ 行は存在するが現版スキーマ対象外 |

TLB の POLLUTION・DATA REQ・`wp_fill` は現版では集計対象外。必要な場合は G6 スキーマ拡張を検討する。

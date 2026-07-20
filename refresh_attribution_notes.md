# AD-DQ1 修正: 720hアトリビューション成熟の再取得(refresh_attribution)

## 問題(AD-DQ1)
RPP「商品別」「キーワード別」レポートは **クリックから720時間(30日)** のアトリビューション窓を持つ。
ある日のクリックに紐づく受注は、その後最大30日かけて確定・追加されていく。
既存パイプラインは対象日の**翌朝1回だけ取得**し、`upload_to_bigquery.py` が
**日付単位のdedup(既にBQに在る日付はスキップ)＋WRITE_APPEND** で追記するだけなので、
未成熟な値が恒久的に固定される → 受注/売上が**恒久的に過少**。

実測(2026-07-21, SKU=`1dayp`, 6/21〜7/19):
- BQ `raw_shohin_betsu.売上件数合計720時間` 合計 = **39件**（凍結スナップショット）
- RMS実画面（常に最新の成熟値）= **51件**  → 約23%過少
- clicks は 630（BQ）= 630（RMS）で**完全一致**。受注のみ過少 ＝ AD-DQ1の典型シグネチャ
  （クリックは当日確定、受注のみ720hかけて成熟するため）。
- この過少はKW別ACOS（広告費÷過少売上）を**実際より悪く**見せる（系統誤差）。

## 修正方針（最小差分・完全追加型）
既存ファイル（`Download-All-Reports.ps1` / `upload_to_bigquery.py` / `run_all.ps1` /
`check_raw_completeness.py`）は**一切変更しない**。以下2ファイル＋1タスクを**追加**するだけ。

- `refresh_attribution_window.ps1`（オーケストレータ）
  1. 対象日リスト（既定=直近30日 / `-Dates` で明示指定可）を作る。
  2. 既存の `Download-All-Reports.ps1 -Phase 1 -Dates <window>` を呼ぶ。
     楽天はレポートを都度**新規生成**するため、CSVには現時点の**成熟した720h値**が入る。
     （＝過去日付を指定して再ダウンロードでき、成熟値が取れることを確認済み。
       レポートは start=end の単日指定で日別行を得る設計＝1日1ダウンロード。）
  3. `refresh_attribution.py` を呼び、`raw_shohin_betsu` / `raw_keyword` を対象日だけUPSERT。
- `refresh_attribution.py`（UPSERTアップローダ）
  - `upload_to_bigquery.py` の既存ヘルパー（`normalize_date`/`sanitize_columns`/`get_client`等）を
    import 再利用。
  - **staging テーブルへ全量ロード → 単一トランザクション内で DELETE(対象日)+INSERT**。
    → 原子的（消したまま入れ直せず空になる瞬間が無い）・冪等・二重計上なし。

## 既知の地雷への対応
1. `日付`列2形式混在: DELETE側は `COALESCE(SAFE.PARSE_DATE('%Y年%m月%d日',...),
   SAFE.PARSE_DATE('%Y-%m-%d',SUBSTR(...,1,10)))` で両形式に対応（`check_raw_completeness.py`と同一）。
   → 統合テストで jp形式/iso形式 両方の旧行が正しく消えることを確認済み。
2. 重複計上防止: 「対象日の全行を消してから入れ直す」ため二重計上不可。
   洗い替え対象は直近窓のみ（3/16-18の完全重複などの旧データには触れない）。
3. `商品CPC`列は空 → 使わない（受注列 `売上件数合計720時間` 等を扱う）。
4. VPS用ps1のcp932誤デコード: `.ps1` は **UTF-8 BOM付き**で保存（`run_all.ps1`等と同じ）。
   Japaneseコメントは書かず本notesに分離。
5. completeness checker（取込失敗 vs 正常0円の非区別）との衝突回避:
   トランザクションで原子的に洗い替えるため、対象日が一瞬でも「行ゼロ」になることが無い。
   さらに**「今日再取得できたファイルが在る日付」だけ**を洗い替える（再DL失敗日は触らない＝
   `--any-mtime` 未指定時）。よって欠損誤検知を誘発しない。

## metric_sanity_check.py との整合
「KW分>商品全体」の内数整合warnは、`raw_shohin_betsu`（全体）と `raw_keyword`（KW内数）を
**必ず同じ窓で同時に**洗い替えるため、両者が揃って成熟し整合は維持/改善される。

## 運用
- 定期実行タスク `RakutenAttributionRefresh`（Interactive/S4U・日次）。
  ※ `Download-All-Reports.ps1` は Windows資格情報マネージャからRMS資格情報を読むため、
    SSH直実行では error 1312 で失敗する。**必ずInteractive/S4Uのタスク経由**で実行する。
- 手動バックフィル: `refresh_attribution_window.ps1 -Days 30`（またはピンポイントで `-Dates`）。
- 既に落としたCSVで洗い替えだけしたい: `-SkipDownload`（→ `--any-mtime` が自動付与）。
- コスト: 商品別+KW別を対象日ぶんだけ history フロー再取得（1日=2レポート）。
  日次の重い `run_all.ps1` からは**分離**（部分失敗しても本流を止めない・冪等で自己回復）。
  本番の窓Nは効果測定の成熟カーブに応じて調整可（`-Days`）。

# -*- coding: utf-8 -*-
"""
refresh_attribution.py  (AD-DQ1 fix)

問題(AD-DQ1): RPP商品別/KW別レポートは「クリックから720時間(30日)」の
アトリビューション窓を持つ。ある日のクリックに紐づく受注はその後最大30日かけて
確定していく。しかし既存の upload_to_bigquery.py は日付単位のdedup(既にBQに在る
日付はスキップ)で追記(WRITE_APPEND)するだけなので、翌朝1回取得した未成熟な値が
恒久的に固定され、受注/売上が約2割過少になる(clicksは当日確定なので一致、ordersのみ過少)。

本スクリプトの役割: 直近N日(=720h窓をカバー)の商品別/KW別CSVを「再ダウンロード済み
(=成熟した最新値)」の前提で受け取り、その日付分だけを BQ の raw_shohin_betsu /
raw_keyword に UPSERT(洗い替え)する。再ダウンロード自体は
  Download-All-Reports.ps1 -Phase 1 -Dates <window>
が担う(本スクリプトは呼ばない。orchestrator = refresh_attribution_window.ps1 が両者を順に実行)。

安全設計:
  - upload_to_bigquery.py の既存ヘルパー(normalize_date / sanitize_columns 等)を import 再利用。
    upload_to_bigquery.py / run_all.ps1 / Download-All-Reports.ps1 は一切変更しない(完全追加型)。
  - UPSERT は「staging テーブルへ全量ロード → 単一トランザクション内で
    DELETE(対象日) + INSERT」で原子的に行う。ゆえに「消したが入れ直せず空になる」瞬間が無い
    (completeness checker が対象日を欠損誤検知しない = landmine#5対策)。
  - 洗い替えは常に「対象日の全行を消してから入れ直す」ため二重計上が起きない(landmine#2対策)。
  - 対象日は「今日ダウンロードし直したファイルが実在する日付」だけ(--any-mtime で解除可)。
    再取得に失敗した日は触らない(既存の値をそのまま残す)。
  - 日付列は2形式混在(landmine#1)。DELETE側は COALESCE(SAFE.PARSE_DATE...) で両形式に対応。

使い方(C:\\rakuten-automation\\楽天広告分析マスター で実行):
  python refresh_attribution.py --days 30        # 直近30日を洗い替え(既定)
  python refresh_attribution.py --days 7         # 直近7日
  python refresh_attribution.py --days 30 --any-mtime   # mtime当日縛りを外す(手動バックフィル用)
  python refresh_attribution.py --dry-run --days 30     # 対象日と件数だけ表示(BQ更新なし)
"""
import argparse
import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
from google.cloud import bigquery

import upload_to_bigquery as u  # 既存ヘルパー再利用(本体は変更しない)

JST = timezone(timedelta(hours=9))

PROJECT = u.PROJECT_ID
DATASET = u.DATASET_ID
DATE_COL = '日付'

# 720hアトリビューションで成熟する「日別」レポート2種のみ対象(店舗全体の日次サマリーや
# RPPEXPは対象外 = AD-DQ1のスコープ = raw_shohin_betsu / raw_keyword)。
TARGETS = {
    'raw_shohin_betsu': r'C:\csv_out\rpp_reports\RPP商品別12h_*.csv',
    'raw_keyword':      r'C:\csv_out\rpp_reports\RPPキーワード別12h_*.csv',
}

# MAIN側の日付パース(2形式混在対応)。_adq1_rootcause.py / check_raw_completeness.py と同一ロジック。
PARSED_MAIN = (
    "COALESCE("
    "SAFE.PARSE_DATE('%Y年%m月%d日', REGEXP_EXTRACT(`日付`, r'^(\\d{4}年\\d{2}月\\d{2}日)')),"
    "SAFE.PARSE_DATE('%Y-%m-%d', SUBSTR(`日付`,1,10)))"
)
# staging側は normalize 済み(YYYY-MM-DD)なので単純パースでよい。
PARSED_STG = "SAFE.PARSE_DATE('%Y-%m-%d', SUBSTR(`日付`,1,10))"


def file_target_date(path: str):
    """ファイル名から対象日(YYYYMMDD)を取り出す。
    RPP商品別12h_20260720_0930.csv -> 20260720 / RPPキーワード別12h_20260720_0930.csv -> 20260720"""
    m = re.search(r'_(\d{8})_', os.path.basename(path))
    return m.group(1) if m else None


def latest_file_per_date(files):
    """対象日ごとに mtime 最新のファイルを1つ選ぶ。"""
    by = {}
    for f in files:
        d = file_target_date(f)
        if not d:
            continue
        if d not in by or os.path.getmtime(f) > os.path.getmtime(by[d]):
            by[d] = f
    return by


def window_dates(days: int):
    """直近 days 日(昨日〜days日前)の YYYYMMDD 集合を返す。"""
    today = datetime.now(JST).date()
    return {(today - timedelta(days=i)).strftime('%Y%m%d') for i in range(1, days + 1)}


def parse_explicit_dates(raw: str):
    """--dates で渡された日付列(空白/カンマ区切り, YYYY-MM-DD or YYYYMMDD)を YYYYMMDD 集合に。"""
    out = set()
    for tok in re.split(r'[,\s]+', (raw or '').strip()):
        if not tok:
            continue
        digits = tok.replace('-', '').replace('/', '')
        if re.fullmatch(r'\d{8}', digits):
            out.add(digits)
        else:
            print(f"  WARN: 日付として解釈できないトークンを無視: {tok!r}")
    return out


def is_today_mtime(path: str) -> bool:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), JST).date() == datetime.now(JST).date()
    except OSError:
        return False


def build_window_df(glob_pat: str, wanted_dates: set, any_mtime: bool):
    """window内かつ(既定)本日再取得済みのCSVを結合し、日付正規化済みDataFrameを返す。
    戻り値: (df, used_dates(YYYY-MM-DD set)) / 対象なしは (None, set())。"""
    by_date = latest_file_per_date(glob.glob(glob_pat))
    use = {}
    for d8, path in by_date.items():
        if d8 not in wanted_dates:
            continue
        if not any_mtime and not is_today_mtime(path):
            continue  # 今日取得し直していない日は触らない(再DL失敗日を上書きしない)
        use[d8] = path
    if not use:
        return None, set()
    frames = []
    used = set()
    for d8, path in sorted(use.items()):
        df = pd.read_csv(path, encoding='utf-8-sig', sep=',', dtype=str, header=0).fillna('')
        df['__norm__'] = df[DATE_COL].apply(
            lambda x: u.normalize_date(x, 'rpp_range') or u.normalize_date(x, 'simple')
        )
        df = df.dropna(subset=['__norm__'])
        if df.empty:
            continue
        df[DATE_COL] = df['__norm__']
        df = df.drop(columns=['__norm__'])
        frames.append(df)
        used.update(df[DATE_COL].unique().tolist())
    if not frames:
        return None, set()
    return pd.concat(frames, ignore_index=True), used


def upsert(client: bigquery.Client, table: str, df: pd.DataFrame, dry_run: bool):
    """staging へ全量ロード → 単一トランザクションで DELETE(対象日)+INSERT。原子的・冪等・二重計上なし。"""
    tid = f"{PROJECT}.{DATASET}.{table}"
    stg = f"{PROJECT}.{DATASET}._stg_refresh_{table}"

    # 既存 upload_df と同じ整形(列名sanitize→空列除去→既存テーブル列のみに限定→全STRING化)
    df = u.sanitize_columns(df)
    df = df[[c for c in df.columns if c.strip()]]
    main = client.get_table(tid)
    main_cols = [f.name for f in main.schema]
    keep = [c for c in df.columns if c in main_cols]
    df = df[keep]
    if df.empty or not keep:
        print(f"  [{table}] 整形後に対象行/列なし → スキップ")
        return
    df_str = df.astype(str).replace({'nan': '', 'None': ''})

    # 対象日(YYYY-MM-DD)
    target_dates = sorted(df_str[DATE_COL].unique().tolist())
    print(f"  [{table}] 対象日 {len(target_dates)}日: {target_dates[0]}..{target_dates[-1]} / {len(df_str)}行")

    if dry_run:
        print(f"  [{table}] --dry-run のためBQ更新なし")
        return

    # 1) staging へ全量ロード(WRITE_TRUNCATE)。load job はトランザクション外(stagingは使い捨て)。
    load_cfg = bigquery.LoadJobConfig(
        schema=[bigquery.SchemaField(c, 'STRING') for c in df_str.columns],
        write_disposition='WRITE_TRUNCATE',
    )
    client.load_table_from_dataframe(df_str, stg, job_config=load_cfg).result()
    n_stg = client.get_table(stg).num_rows
    if n_stg == 0:
        print(f"  [{table}] staging が空 → 中止(安全のためMAINは触らない)")
        return

    cols_sql = ",".join(f"`{c}`" for c in df_str.columns)
    # 2) 原子トランザクション: 対象日をMAINから削除し、stagingから入れ直す。
    tx = f"""
    BEGIN TRANSACTION;
    DELETE FROM `{tid}`
      WHERE {PARSED_MAIN} IN (SELECT DISTINCT {PARSED_STG} FROM `{stg}`);
    INSERT INTO `{tid}` ({cols_sql})
      SELECT {cols_sql} FROM `{stg}`;
    COMMIT TRANSACTION;
    """
    client.query(tx).result()
    print(f"  [{table}] UPSERT完了: {n_stg}行を{len(target_dates)}日分洗い替え")

    # 3) staging を掃除(残っても無害だが綺麗に)
    try:
        client.delete_table(stg, not_found_ok=True)
    except Exception as e:
        print(f"  [{table}] staging削除に失敗(無害): {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=14, help='直近何日分を洗い替えるか(既定14=再取得が高速安定な範囲)')
    ap.add_argument('--dates', type=str, default=None,
                    help='対象日を明示指定(空白/カンマ区切り, YYYY-MM-DD or YYYYMMDD)。指定時は--daysより優先')
    ap.add_argument('--any-mtime', action='store_true', help='本日再取得済み縛りを外す(手動バックフィル用)')
    ap.add_argument('--dry-run', action='store_true', help='対象日/件数のみ表示しBQを更新しない')
    args = ap.parse_args()

    if args.dates:
        wanted = parse_explicit_dates(args.dates)
        if not wanted:
            print("--dates を解釈できる日付がありませんでした。終了します。")
            sys.exit(0)
        scope = f"明示指定 {len(wanted)}日 ({min(wanted)}..{max(wanted)})"
    else:
        wanted = window_dates(args.days)
        scope = f"直近{args.days}日 ({min(wanted)}..{max(wanted)})"
    print(f"=== refresh_attribution 開始: {scope} / any_mtime={args.any_mtime} / dry_run={args.dry_run} ===")

    client = u.get_client()
    u.ensure_dataset(client)

    any_done = False
    for table, glob_pat in TARGETS.items():
        print(f"\n--- {table} ---")
        df, used = build_window_df(glob_pat, wanted, args.any_mtime)
        if df is None:
            print(f"  対象CSVなし(今日再取得された{table}のwindow内ファイルが見つからない) → スキップ")
            continue
        upsert(client, table, df, args.dry_run)
        any_done = True

    if not any_done:
        print("\n対象ファイルが1つも見つかりませんでした(Download-All-Reports.ps1 -Phase 1 -Dates を先に実行してください)。")
        sys.exit(0)
    print("\n=== refresh_attribution 完了 ===")


if __name__ == '__main__':
    main()

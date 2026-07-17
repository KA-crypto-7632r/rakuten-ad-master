# -*- coding: utf-8 -*-
"""
backfill_rpp_daily.py

RAW_日次（raw_rpp_daily）専用の手動バックフィルスクリプト。
run_all.ps1 からは呼ばれない（手動実行専用・日次パイプラインには一切影響しない）。

upload_to_bigquery.py の既存ヘルパー（normalize_date / sanitize_columns /
upload_df / get_client 等）をそのまま import して再利用するだけで、
upload_to_bigquery.py 本体は一切変更しない。

upload_to_bigquery.py の process_sheet() は「本日更新されたファイルのみ」を
対象にする設計（日次パイプライン用）のため、過去分をまとめて取り込むには
その制約を外した本スクリプトを使う。

使い方（C:\\rakuten-automation\\楽天広告分析マスター で実行）:
  python backfill_rpp_daily.py           # 直近7日分（既定）
  python backfill_rpp_daily.py --days 30 # 直近30日分
  python backfill_rpp_daily.py --all     # 過去全量（1,400件超・時間とAPI負荷に注意）
"""
import argparse
import glob
import os

import pandas as pd

import upload_to_bigquery as u

SHEET_NAME = 'RAW_日次'


def target_date_of(path: str) -> str:
    """
    ファイル名から対象日(YYYYMMDD)を取り出す。
    新形式: RPP日次12h_20260716_20260717_0900.csv -> 20260716（対象日）
    旧形式: RPP日次12h_20250724_2023.csv           -> 20250724（対象日）
    """
    base = os.path.basename(path)
    stem = base.replace('RPP日次12h_', '').replace('.csv', '')
    return stem.split('_')[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7, help='直近何日分の対象日を取り込むか（既定7）')
    ap.add_argument('--all', action='store_true', help='過去全量を対象にする')
    args = ap.parse_args()

    config = u.CSV_CONFIG[SHEET_NAME]
    date_col = config['date_col_name']
    date_fmt = config['date_format']

    client = u.get_client()
    u.ensure_dataset(client)
    tid = u.table_id(SHEET_NAME)

    files = sorted(glob.glob(config['path']), key=os.path.getmtime)

    if not args.all:
        cutoff = (pd.Timestamp.now(tz='Asia/Tokyo').normalize() - pd.Timedelta(days=args.days))
        cutoff_str = cutoff.strftime('%Y%m%d')
        files = [f for f in files if target_date_of(f) >= cutoff_str]

    print(f'対象ファイル数: {len(files)}')
    if not files:
        print('対象ファイルなし。終了します。')
        return

    existing_dates = u.query_set(client, f"SELECT DISTINCT `{date_col}` FROM `{tid}`")
    existing_norm = set()
    for d in existing_dates:
        nd = (u.normalize_date(d, 'rpp_range')
              or u.normalize_date(d, 'datetime_jp')
              or u.normalize_date(d, 'simple'))
        if nd:
            existing_norm.add(nd)

    for csv_path in files:
        print(f'  📄 {os.path.basename(csv_path)}')
        try:
            df = pd.read_csv(
                csv_path,
                encoding=config.get('encoding', 'utf-8-sig'),
                sep=config.get('separator', ','),
                dtype=str,
                header=config.get('header_row', 0),
            ).fillna('')

            df['__norm__'] = df[date_col].apply(
                lambda x: u.normalize_date(x, date_fmt)
                          or u.normalize_date(x, 'datetime_jp')
                          or u.normalize_date(x, 'simple')
            )
            df = df.dropna(subset=['__norm__'])
            new_df = df[~df['__norm__'].isin(existing_norm)].copy()
            new_df[date_col] = new_df['__norm__']
            new_df = new_df.drop(columns=['__norm__'])

            u.upload_df(client, new_df, tid, SHEET_NAME)
            existing_norm.update(new_df[date_col].tolist())

        except Exception as e:
            print(f'  ❌ エラー（{os.path.basename(csv_path)}）: {e}')

    print('完了。')


if __name__ == '__main__':
    main()

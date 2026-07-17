# -*- coding: utf-8 -*-
"""
upload_to_bigquery.py

upload_to_sheets.py の BigQuery版。VPS上でそのまま使えます。
CSV読み込み・日付正規化・重複除外ロジックは元スクリプトと同一。

【必要パッケージ（VPSで1度だけ実行）】
  pip install google-cloud-bigquery google-cloud-bigquery-storage db-dtypes pyarrow pandas
"""

import os
import re
import glob
import time
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from datetime import datetime, timezone, timedelta
from itertools import zip_longest

print("--- BigQueryアップロードスクリプト開始 ---")

# ============================================================
# ▼▼▼ 設定（ここだけ変更すればOK）
# ============================================================
KEY_FILE_NAME = 'boss-rpa-bot-daad02d10efb.json'
PROJECT_ID    = 'boss-rpa-bot'
DATASET_ID    = 'rakuten_ads'

# BigQueryテーブル名（変更不要）
TABLE_MAP = {
    'RAW_商品別':    'raw_shohin_betsu',
    'RAW_キーワード': 'raw_keyword',
    'RAW_アイテム':  'raw_item',
    'RAW_アフィ':    'raw_affi',
    'RAW_RPPEXP':    'raw_rppexp',
    # 2026-07-17追加（AD-VD3）: RPP日次12hレポート（店舗全体の1日1行サマリー）。
    # 割引後実績額列を持つ唯一のraw＝楽天ボリュームディスカウント適用後の答え合わせに使う。
    'RAW_日次':      'raw_rpp_daily',
}

# CSV設定（upload_to_sheets.py と同じ構造を維持）
CSV_CONFIG = {
    'RAW_商品別': {
        'path':         r'C:\csv_out\rpp_reports\RPP商品別*.csv',
        'date_col_name':'日付',
        'date_format':  'rpp_range',
        'encoding':     'utf-8-sig',
        'separator':    ',',
        'header_row':   0,
        'dedup_type':   'date',
    },
    'RAW_キーワード': {
        'path':         r'C:\csv_out\rpp_reports\RPPキーワード別*.csv',
        'date_col_name':'日付',
        'date_format':  'rpp_range',
        'encoding':     'utf-8-sig',
        'separator':    ',',
        'header_row':   0,
        'dedup_type':   'date',
    },
    'RAW_アイテム': {
        'path':         r'C:\csv_out\rms_reports\店舗カルテ_商品ページ分析*.csv',
        'date_col_name':'対象日',
        'date_format':  'simple',
        'encoding':     'utf-8-sig',
        'separator':    ',',
        'header_row':   0,
        'dedup_type':   'date',
    },
    'RAW_アフィ': {
        'path':           r'C:\csv_out\rms_reports\pending*.csv',
        'special_affiliate': True,
        'encoding':       'utf-8-sig',
        'separator':      ',',
        'skiprows':       11,
        'expected_columns': [
            '成果発生日時', '商品管理者商品名', '商品名', 'ジャンル名',
            '受注番号', '受注番号(確定後)', '売上金額', '料率', '成果報酬',
            'ステータス', 'クリック端末種別', '購入端末種別', '商品種別'
        ],
        'dedup_key_col':  '受注番号',
        'dedup_type':     'key',
    },
    'RAW_RPPEXP': {
        'path':           r'C:\csv_out\rppexp_reports\RPPEXP商品別_*.csv',
        'special_rppexp': True,
        'encoding':       'utf-8-sig',
        'separator':      ',',
        'skiprows':       5,
        'expected_columns': [
            '日付', '商品ページURL', '商品管理番号', '実績額',
            'インプレッション数', 'クリック数', 'CTR（クリック率）', 'CPC実績',
            '売上件数-合計-', '売上件数-新規-', '売上件数-既存-',
            'CVR（転換率）-合計-', '注文獲得単価-合計-',
            '売上金額-合計-', '売上金額-新規-', '売上金額-既存-',
            'ROAS-合計-', '平均購入単価-合計-'
        ],
        'date_col_name':  '日付',
        'item_col_name':  '商品管理番号',
        'dedup_type':     'composite',
    },
    # 2026-07-17追加（AD-VD3・新ツール導入ゲート通過済み）
    # RPP日次12h_*.csv: 店舗全体（全RPP広告合算）の日次サマリー。1ファイル=1行（ヘッダー+データ1行）。
    # 「日付」列は既存のRAW_商品別/RAW_キーワードと同じ "YYYY年MM月DD日～YYYY年MM月DD日" 形式
    # （開始日=終了日の単日レンジ）なので date_format は既存の 'rpp_range' をそのまま流用できる。
    'RAW_日次': {
        'path':         r'C:\csv_out\rpp_reports\RPP日次12h_*.csv',
        'date_col_name':'日付',
        'date_format':  'rpp_range',
        'encoding':     'utf-8-sig',
        'separator':    ',',
        'header_row':   0,
        'dedup_type':   'date',
    },
}
# ============================================================
# ▲▲▲ 設定ここまで
# ============================================================

JST       = timezone(timedelta(hours=9))
TODAY_JST = datetime.now(JST).date()


# ===== ユーティリティ（upload_to_sheets.py と同一） =====

def normalize_date(date_string, format_type):
    """日付の表記を YYYY-MM-DD に統一"""
    if date_string is None or str(date_string).strip() == '':
        return None
    s = str(date_string)
    if format_type == 'rpp_range':
        m = re.search(r'(\d{4})年(\d{2})月(\d{2})日', s)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    elif format_type == 'jp_range':
        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', s)
        if m:
            return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    elif format_type == 'datetime_jp':
        m = re.search(r'(\d{4})/(\d{1,2})/(\d{1,2})', s)
        if m:
            return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    elif format_type == 'simple':
        if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
            return s
    return None


def is_today_file(path: str) -> bool:
    """ファイルの最終更新がJSTの本日かどうか"""
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path), JST).date()
        return mtime == TODAY_JST
    except Exception:
        return False


def list_today_files(pattern: str) -> list:
    """本日更新のCSVを更新日時昇順で返す"""
    files = [f for f in glob.glob(pattern) if is_today_file(f)]
    return sorted(files, key=os.path.getmtime)


# ===== BigQuery ヘルパー =====

def get_client() -> bigquery.Client:
    credentials = service_account.Credentials.from_service_account_file(KEY_FILE_NAME)
    return bigquery.Client(project=PROJECT_ID, credentials=credentials)


def ensure_dataset(client: bigquery.Client):
    """データセットがなければ作成"""
    dataset_ref = f"{PROJECT_ID}.{DATASET_ID}"
    try:
        client.get_dataset(dataset_ref)
    except Exception:
        ds = bigquery.Dataset(dataset_ref)
        ds.location = "asia-northeast1"   # 東京リージョン
        client.create_dataset(ds)
        print(f"✅ データセット '{DATASET_ID}' を作成しました（東京リージョン）")


def table_id(sheet_name: str) -> str:
    return f"{PROJECT_ID}.{DATASET_ID}.{TABLE_MAP[sheet_name]}"


def query_set(client: bigquery.Client, sql: str) -> set:
    """SQLを実行してセットで返す（テーブル未存在は空セット）"""
    try:
        return {str(row[0]) for row in client.query(sql).result()}
    except Exception as e:
        if 'Not found' in str(e) or 'notFound' in str(e):
            return set()
        raise


def query_set2(client: bigquery.Client, sql: str) -> set:
    """複合キー用：row[0]+'|'+row[1] のセットで返す"""
    try:
        return {f"{row[0]}|{row[1]}" for row in client.query(sql).result()}
    except Exception as e:
        if 'Not found' in str(e) or 'notFound' in str(e):
            return set()
        raise



def sanitize_columns(df):
    """BigQuery不可文字（括弧・%など）を列名から除去"""
    import re
    KNOWN_RENAME = {
        "CTR(%)": "CTR", "CTR（%）": "CTR",
        "CVR(合記12時間)(%)": "CVR合記12時間",
        "CVR(合記720時間)(%)": "CVR合記720時間",
        "CVR(新規12時間)(%)": "CVR新規12時間",
        "CVR(新規720時間)(%)": "CVR新規720時間",
        "CVR(既嬸12時間)(%)": "CVR既嬸12時間",
        "CVR(既嬸720時間)(%)": "CVR既嬸720時間",
        "ROAS(合記12時間)(%)": "ROAS合記12時間",
        "ROAS(合記720時間)(%)": "ROAS合記720時間",
        "ROAS(新規12時間)(%)": "ROAS新規12時間",
        "ROAS(新規720時間)(%)": "ROAS新規720時間",
        "ROAS(既嬸12時間)(%)": "ROAS既嬸12時間",
        "ROAS(既嬸720時間)(%)": "ROAS既嬸720時間",
    }
    def fix(col):
        if col in KNOWN_RENAME:
            return KNOWN_RENAME[col]
        col = col.replace("（", "").replace("）", "")
        col = col.replace("(", "").replace(")", "").replace(" ", "")
        col = col.replace("%", "pct").replace("-", "_")
        col = re.sub(r"[^\w]", "_", col)
        col = re.sub(r"_+", "_", col)
        return col.strip("_")
    df = df.copy()
    df.columns = [fix(c) for c in df.columns]
    return df

def upload_df(client: bigquery.Client, df: pd.DataFrame, tid: str, label: str):
    """DataFrameをBigQueryに追記（全列STRING・スキーマ競合なし）"""
    if df.empty:
        print(f"  👍 追記なし（{label}）")
        return

    # 全列STRINGで統一（型不一致によるエラーを防止）
    df = sanitize_columns(df)
    # 空列名を除去
    df = df[[c for c in df.columns if c.strip()]]
    # 既存テーブルの列のみに絞る（新列追加エラー防止）
    try:
        tbl = client.get_table(tid)
        existing = {f.name for f in tbl.schema}
        keep = [c for c in df.columns if c in existing]
        if keep:
            df = df[keep]
    except Exception:
        pass
    schema = [bigquery.SchemaField(col, 'STRING') for col in df.columns]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition='WRITE_APPEND',
    )
    # DataFrameの全値を文字列化
    df_str = df.astype(str).replace({'nan': '', 'None': ''})

    job = client.load_table_from_dataframe(df_str, tid, job_config=job_config)
    job.result()
    print(f"  ✅ {len(df_str)}行 → BigQuery [{label}]")


# ===== 欠損日チェック（新機能） =====

def check_missing_dates(client: bigquery.Client, days_back: int = 7):
    """
    RAWデータ系3テーブルで、過去N日分の欠損を検出して表示。
    前日が必ず取れているべきなので days_back=7 がデフォルト。
    """
    check_targets = {
        'RAW_アイテム':   ('raw_item',         '対象日'),
        'RAW_商品別':     ('raw_shohin_betsu',  '日付'),
        'RAW_キーワード': ('raw_keyword',       '日付'),
    }
    print("\n" + "="*50)
    print("📅 欠損日チェック（過去{}日）".format(days_back))
    print("="*50)
    any_missing = False
    for label, (tbl, dcol) in check_targets.items():
        tid = f"{PROJECT_ID}.{DATASET_ID}.{tbl}"
        sql = f"""
        SELECT FORMAT_DATE('%Y-%m-%d', missing_date) AS missing_date
        FROM (
            SELECT DATE_SUB(CURRENT_DATE('Asia/Tokyo'), INTERVAL d DAY) AS missing_date
            FROM UNNEST(GENERATE_ARRAY(1, {days_back})) AS d
        )
        WHERE missing_date NOT IN (
            SELECT DISTINCT SAFE.PARSE_DATE('%Y-%m-%d', `{dcol}`)
            FROM `{tid}`
            WHERE `{dcol}` IS NOT NULL AND `{dcol}` != ''
        )
        ORDER BY missing_date
        """
        try:
            missing = [row[0] for row in client.query(sql).result()]
            if missing:
                print(f"  ⚠️  {label}: 欠損 → {missing}")
                any_missing = True
            else:
                print(f"  ✅  {label}: 欠損なし")
        except Exception as e:
            if 'Not found' in str(e) or 'notFound' in str(e):
                print(f"  ℹ️  {label}: テーブル未作成（初回実行前）")
            else:
                print(f"  ❌  {label}: チェックエラー → {e}")
    if any_missing:
        print("\n  ⚠️  欠損日は Get-Report.ps1 で再取得してください。")
    print("="*50 + "\n")


# ===== シート別処理 =====

def process_sheet(client: bigquery.Client, sheet_name: str, config: dict):
    print(f"\n--- ⚙️ '{sheet_name}' の処理を開始 ---")
    tid = table_id(sheet_name)

    # 対象ファイル決定
    if config.get('special_affiliate'):
        today_list = list_today_files(config['path'])
        if not today_list:
            print("  📂 本日更新のCSVなし → スキップ")
            return
        files = [today_list[-1]]   # アフィは最新1ファイルのみ
    else:
        files = list_today_files(config['path'])
        if not files:
            print("  📂 本日更新のCSVなし → スキップ")
            return

    # ------ RAW_アフィ ------
    if config.get('special_affiliate'):
        _process_affiliate(client, config, files[0], tid)
        return

    # ------ RAW_RPPEXP ------
    if config.get('special_rppexp'):
        for f in files:
            _process_rppexp(client, config, f, tid)
        return

    # ------ 通常シート（RAW_商品別 / RAW_キーワード / RAW_アイテム / RAW_日次）------
    # 既存日付をBigQueryから取得（1回だけ）
    date_col  = config['date_col_name']
    date_fmt  = config['date_format']
    existing_dates = query_set(
        client,
        f"SELECT DISTINCT `{date_col}` FROM `{tid}`"
    )
    # 既存値を正規化セットに変換
    existing_norm = set()
    for d in existing_dates:
        nd = (normalize_date(d, 'rpp_range')
              or normalize_date(d, 'datetime_jp')
              or normalize_date(d, 'simple'))
        if nd:
            existing_norm.add(nd)

    for csv_path in files:
        print(f"  📄 {os.path.basename(csv_path)}")
        try:
            df = pd.read_csv(
                csv_path,
                encoding=config.get('encoding', 'utf-8-sig'),
                sep=config.get('separator', ','),
                dtype=str,
                header=config.get('header_row', 0),
            ).fillna('')

            # 日付を正規化して重複フィルタ
            df['__norm__'] = df[date_col].apply(
                lambda x: normalize_date(x, date_fmt)
                          or normalize_date(x, 'datetime_jp')
                          or normalize_date(x, 'simple')
            )
            df = df.dropna(subset=['__norm__'])
            new_df = df[~df['__norm__'].isin(existing_norm)].copy()

            # 日付列を正規化値で上書き（表記統一）
            new_df[date_col] = new_df['__norm__']
            new_df = new_df.drop(columns=['__norm__'])

            upload_df(client, new_df, tid, sheet_name)

            # 既存セットを更新（同日に複数ファイルある場合の重複防止）
            existing_norm.update(new_df[date_col].tolist())

        except Exception as e:
            print(f"  ❌ エラー: {e}")


def _process_affiliate(client: bigquery.Client, config: dict, csv_path: str, tid: str):
    """RAW_アフィ専用処理"""
    print(f"  📄 {os.path.basename(csv_path)}（アフィリエイト専用処理）")
    try:
        df = pd.read_csv(
            csv_path,
            encoding=config.get('encoding', 'utf-8-sig'),
            sep=config.get('separator', ','),
            dtype=str,
            header=None,
            skiprows=config.get('skiprows', 11),
        ).fillna('')

        exp_cols = config['expected_columns']
        if df.shape[1] < len(exp_cols):
            print(f"  ❌ 列数不足（CSV:{df.shape[1]} < 期待:{len(exp_cols)}）")
            return
        df = df.iloc[:, :len(exp_cols)]
        df.columns = exp_cols

        key_col = config['dedup_key_col']
        existing_ids = query_set(
            client,
            f"SELECT DISTINCT `{key_col}` FROM `{tid}`"
        )

        df[key_col] = df[key_col].astype(str).str.strip()
        new_df = df[~df[key_col].isin(existing_ids)]
        upload_df(client, new_df, tid, 'RAW_アフィ')

    except Exception as e:
        print(f"  ❌ アフィリエイト処理エラー: {e}")


def _process_rppexp(client: bigquery.Client, config: dict, csv_path: str, tid: str):
    """RAW_RPPEXP専用処理（複合キー：日付＋商品管理番号）"""
    print(f"  📄 {os.path.basename(csv_path)}（RPPEXP専用処理）")
    try:
        df = pd.read_csv(
            csv_path,
            encoding=config.get('encoding', 'utf-8-sig'),
            sep=config.get('separator', ','),
            dtype=str,
            header=0,
            skiprows=config.get('skiprows', 5),
        ).fillna('')

        exp_cols = config['expected_columns']
        if df.shape[1] < len(exp_cols):
            print(f"  ❌ 列数不足（CSV:{df.shape[1]} < 期待:{len(exp_cols)}）")
            return
        df = df.iloc[:, :len(exp_cols)]
        df.columns = exp_cols

        date_col = config['date_col_name']
        item_col = config['item_col_name']

        # 日付を YYYY-MM-DD に正規化
        df['__date_norm__'] = df[date_col].apply(
            lambda x: normalize_date(x, 'jp_range')
                      or normalize_date(x, 'rpp_range')
                      or normalize_date(x, 'datetime_jp')
                      or normalize_date(x, 'simple')
        )
        df = df.dropna(subset=['__date_norm__'])
        df[date_col] = df['__date_norm__']
        df = df.drop(columns=['__date_norm__'])
        df[item_col] = df[item_col].astype(str).str.strip()

        # 複合キーで既存チェック
        existing_keys = query_set2(
            client,
            f"SELECT `{date_col}`, `{item_col}` FROM `{tid}`"
        )

        df['__key__'] = df[date_col].str.strip() + '|' + df[item_col].str.strip()
        new_df = df[~df['__key__'].isin(existing_keys)].drop(columns=['__key__'])
        upload_df(client, new_df, tid, 'RAW_RPPEXP')

    except Exception as e:
        print(f"  ❌ RPPEXP処理エラー: {e}")


# ===== メイン =====

def main():
    try:
        client = get_client()
        ensure_dataset(client)
        print(f"✅ BigQuery '{PROJECT_ID}.{DATASET_ID}' に接続しました。\n")

        for sheet_name, config in CSV_CONFIG.items():
            # 2026-07-17追加（AD-VD3）: 1テーブルの想定外エラーが他テーブルの取込を
            # 道連れにしないための例外分離。process_sheet内部は既存どおり
            # ファイル単位でtry/exceptしているため、通常運用時の挙動（既存5テーブル）
            # はこの追加によって一切変わらない。
            try:
                process_sheet(client, sheet_name, config)
            except Exception as e:
                print(f"  ❌ '{sheet_name}' の処理で想定外エラー（他テーブルの処理は継続）: {e}")
            time.sleep(0.3)

        # 実行後に欠損日チェック
        check_missing_dates(client, days_back=7)

    except FileNotFoundError:
        print(f"❌ 認証キー '{KEY_FILE_NAME}' が見つかりません。")
    except Exception as e:
        print(f"❌ 予期せぬエラー: {e}")
        raise


if __name__ == '__main__':
    main()

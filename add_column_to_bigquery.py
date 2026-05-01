"""
add_column_to_bigquery.py
BigQuery の orders テーブルに「注文時間」列を追加するスクリプト
1回だけ実行すればOK
"""

import os
from google.oauth2 import service_account
from google.cloud import bigquery

# ============================
# 設定（変更不要）
# ============================
PROJECT_ID  = 'boss-rpa-bot'
DATASET_ID  = 'rakuten_ads'
TABLE_ID    = 'orders'

# サービスアカウントキーのパス
KEY_PATHS = [
    r'C:\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json',
    r'C:\Users\AmazonTEISHIN\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json',
]

def find_key():
    for p in KEY_PATHS:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        'サービスアカウントキーが見つかりません。\n'
        '以下のいずれかに置いてください:\n' + '\n'.join(KEY_PATHS)
    )

def main():
    key_path    = find_key()
    credentials = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=['https://www.googleapis.com/auth/bigquery']
    )
    bq  = bigquery.Client(project=PROJECT_ID, credentials=credentials)
    tid = f'{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}'

    # ---- 現在のスキーマを確認 ----
    table  = bq.get_table(tid)
    fields = [f.name for f in table.schema]
    print(f'現在の列数: {len(fields)}')
    print(f'列一覧: {fields}')

    if '注文時間' in fields:
        print('\n✅ 「注文時間」列はすでに存在します。作業不要です。')
        return

    # ---- ALTER TABLE で列追加 ----
    sql = f"""
    ALTER TABLE `{tid}`
    ADD COLUMN IF NOT EXISTS `注文時間` STRING
    """
    print(f'\n実行SQL:\n{sql.strip()}')

    job = bq.query(sql)
    job.result()

    print('\n✅ 「注文時間」列を追加しました！')

    # ---- 確認 ----
    table2  = bq.get_table(tid)
    fields2 = [f.name for f in table2.schema]
    print(f'変更後の列数: {len(fields2)}')
    print(f'変更後の列一覧: {fields2}')

if __name__ == '__main__':
    main()

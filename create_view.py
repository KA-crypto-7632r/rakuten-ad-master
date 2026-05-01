"""
create_view.py
BigQuery に Looker Studio 用の v_orders ビューを作成する
SQL に日本語を一切使わない SELECT * アプローチ
1回だけ実行すればOK（再実行しても CREATE OR REPLACE で上書き）
"""

import os
from google.oauth2 import service_account
from google.cloud import bigquery

PROJECT_ID = 'boss-rpa-bot'
DATASET_ID = 'rakuten_ads'

KEY_PATHS = [
    r'C:\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json',
    r'C:\Users\AmazonTEISHIN\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json',
]

def find_key():
    for p in KEY_PATHS:
        if os.path.exists(p):
            return p
    raise FileNotFoundError('Key not found: ' + str(KEY_PATHS))

def main():
    key_path = find_key()
    credentials = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=['https://www.googleapis.com/auth/bigquery']
    )
    client = bigquery.Client(project=PROJECT_ID, credentials=credentials)

    # ---- Step 1: テーブルスキーマを確認 ----
    table = client.get_table(f'{PROJECT_ID}.{DATASET_ID}.orders')
    print(f'orders column count: {len(table.schema)}')
    for i, f in enumerate(table.schema):
        print(f'  col[{i}]: {repr(f.name)}')

    # ---- Step 2: ビュー作成（SQL は純粋なASCIIのみ） ----
    view_ref = f'{PROJECT_ID}.{DATASET_ID}.v_orders'
    src_ref  = f'{PROJECT_ID}.{DATASET_ID}.orders'

    # SQL に日本語を1文字も使わない
    sql = (
        'SELECT * '
        'FROM `' + src_ref + '`'
    )

    print('\nView SQL:')
    print(sql)

    # Tables API でビューを作成 / 更新
    view = bigquery.Table(view_ref)
    view.view_query = sql

    try:
        result = client.create_table(view)
        print('\nv_orders created.')
    except Exception as e:
        if 'Already Exists' in str(e):
            result = client.update_table(view, ['view_query'])
            print('\nv_orders updated (already existed).')
        else:
            raise

    print(f'Done: {view_ref}')

if __name__ == '__main__':
    main()

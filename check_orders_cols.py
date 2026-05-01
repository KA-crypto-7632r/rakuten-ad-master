# -*- coding: utf-8 -*-
from google.oauth2 import service_account
from google.cloud import bigquery

KEY = r'C:\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json'
PROJECT = 'boss-rpa-bot'

creds = service_account.Credentials.from_service_account_file(
    KEY, scopes=['https://www.googleapis.com/auth/bigquery'])
client = bigquery.Client(project=PROJECT, credentials=creds)

# ordersテーブルの全列名を確認
print("=== orders テーブルの列一覧 ===")
tbl = client.get_table('boss-rpa-bot.rakuten_ads.orders')
for f in tbl.schema:
    print(f"  '{f.name}'")

# SKU関連の列を抽出（システム連携用などを探す）
print("\n=== SKU/システム連携関連の列 ===")
sku_cols = [f.name for f in tbl.schema if 'SKU' in f.name or 'システム' in f.name or 'sku' in f.name.lower()]
for c in sku_cols:
    print(f"  '{c}'")

# サンプル1行でSKU関連列の中身を確認
if sku_cols:
    cols_str = ', '.join([f'`{c}`' for c in sku_cols[:5]])
    sql = f"SELECT `商品管理番号`, {cols_str} FROM `boss-rpa-bot.rakuten_ads.orders` WHERE `商品管理番号` IS NOT NULL LIMIT 5"
    print("\n=== サンプルデータ ===")
    for row in client.query(sql):
        print(dict(row))

# -*- coding: utf-8 -*-
"""
check_raw_item.py
raw_item テーブルのカラム一覧と 対象日 フォーマットを確認する
"""
from google.oauth2 import service_account
from google.cloud import bigquery

KEY  = r'C:\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json'
PROJ = 'boss-rpa-bot'
DS   = 'rakuten_ads'

creds  = service_account.Credentials.from_service_account_file(
    KEY, scopes=['https://www.googleapis.com/auth/bigquery'])
client = bigquery.Client(project=PROJ, credentials=creds)

# ① カラム一覧
print("=== raw_item カラム一覧 ===")
tbl = client.get_table(f'{PROJ}.{DS}.raw_item')
for f in tbl.schema:
    print(f"  [{f.field_type}] {repr(f.name)}")

# ② 対象日の実際の値サンプル（先頭10件）
print("\n=== raw_item 対象日 サンプル ===")
sql = """
SELECT DISTINCT `対象日`
FROM `boss-rpa-bot.rakuten_ads.raw_item`
ORDER BY `対象日` DESC
LIMIT 20
"""
for row in client.query(sql):
    print(f"  {repr(row[0])}")

# ③ raw_item と v_report の日付差分（JOINが合うか確認）
print("\n=== v_report report_date サンプル（最新10件） ===")
sql2 = """
SELECT DISTINCT report_date
FROM `boss-rpa-bot.rakuten_ads.v_report`
ORDER BY report_date DESC
LIMIT 10
"""
for row in client.query(sql2):
    print(f"  {repr(row[0])}")

# ④ 商品管理番号の確認
print("\n=== raw_item 商品管理番号 サンプル ===")
sql3 = """
SELECT DISTINCT `商品管理番号`
FROM `boss-rpa-bot.rakuten_ads.raw_item`
LIMIT 10
"""
for row in client.query(sql3):
    print(f"  {repr(row[0])}")

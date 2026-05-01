# -*- coding: utf-8 -*-
from google.oauth2 import service_account
from google.cloud import bigquery

KEY = r'C:\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json'
PROJECT = 'boss-rpa-bot'

creds = service_account.Credentials.from_service_account_file(
    KEY, scopes=['https://www.googleapis.com/auth/bigquery'])
client = bigquery.Client(project=PROJECT, credentials=creds)

# 商品マスタに登録されている原価を確認
sql = """
SELECT
  LOWER(sku) AS sku,
  valid_from,
  cost,
  supplies,
  shipping
FROM `boss-rpa-bot.rakuten_ads.product_master_clean`
ORDER BY LOWER(sku), valid_from
"""

print("=== 商品マスタ登録内容（BigQuery） ===")
for row in client.query(sql):
    print(f"  SKU: {(row.sku or ''):<25}  有効開始: {row.valid_from}  原価: {row.cost}  備品: {row.supplies}  送料: {row.shipping}")

# firebagだけ詳細確認
sql2 = """
SELECT
  report_date,
  sku,
  ROUND(sales, 0) AS sales,
  qty,
  ROUND(cogs, 0) AS cogs,
  ROUND(SAFE_DIVIDE(cogs, qty), 0) AS unit_cost_used
FROM `boss-rpa-bot.rakuten_ads.v_report`
WHERE sku = 'firebag'
  AND report_date >= '2026-03-01'
ORDER BY report_date
"""
print("\n=== firebag 3月分（v_report） ===")
for row in client.query(sql2):
    print(f"  {row.report_date}  売上:{row.sales:>8,.0f}  数量:{row.qty:>4.0f}  原価:{row.cogs:>8,.0f}  単価:{(row.unit_cost_used or 0):>6.0f}")

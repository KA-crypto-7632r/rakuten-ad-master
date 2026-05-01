# -*- coding: utf-8 -*-
from google.oauth2 import service_account
from google.cloud import bigquery

KEY = r'C:\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json'
PROJECT = 'boss-rpa-bot'

creds = service_account.Credentials.from_service_account_file(
    KEY, scopes=['https://www.googleapis.com/auth/bigquery'])
client = bigquery.Client(project=PROJECT, credentials=creds)

# 原価が0かつ売上があるSKUを特定（3/10で確認）
sql = """
WITH
pm_systems AS (
  SELECT DISTINCT LOWER(sku_system) AS sku_system
  FROM `boss-rpa-bot.rakuten_ads.product_master_clean`
  WHERE sku_system IS NOT NULL AND sku_system != ''
),
orders_sku AS (
  SELECT
    LOWER(`商品管理番号`) AS sku,
    LOWER(`システム連携用SKU`) AS sku_system,
    SUM(SAFE_CAST(`商品合計` AS FLOAT64)) AS sales,
    SUM(SAFE_CAST(`数量` AS FLOAT64)) AS qty
  FROM `boss-rpa-bot.rakuten_ads.orders`
  WHERE `注文日` = '2026-03-10'
    AND `商品管理番号` IS NOT NULL
  GROUP BY 1, 2
)
SELECT
  o.sku,
  o.sku_system,
  ROUND(o.sales, 0) AS sales,
  o.qty,
  CASE WHEN pm.sku_system IS NOT NULL THEN 'マッチ済' ELSE '★未マッチ★' END AS status
FROM orders_sku o
LEFT JOIN pm_systems pm ON o.sku_system = pm.sku_system
ORDER BY status DESC, sales DESC
"""

print("=== 2026-03-10 SKU別 商品マスタマッチ状況 ===")
for row in client.query(sql):
    marker = "  ★" if row.status == '★未マッチ★' else ""
    print(f"  {row.status}  sku={row.sku:<20}  sku_system={row.sku_system:<30}  売上={row.sales:>8,.0f}  数量={row.qty:.0f}{marker}")

# 商品マスタに登録されているsku_system一覧
sql2 = """
SELECT LOWER(sku_system) AS sku_system, sku, cost
FROM `boss-rpa-bot.rakuten_ads.product_master_clean`
ORDER BY sku, sku_system
"""
print("\n=== 商品マスタ登録済みSKU_SYSTEM一覧 ===")
for row in client.query(sql2):
    print(f"  sku={row.sku:<20}  sku_system={row.sku_system:<30}  cost={row.cost}")

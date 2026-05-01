# -*- coding: utf-8 -*-
# check_unmatched_all.py - 全期間で商品マスタに未登録のsku_systemを洗い出す
from google.oauth2 import service_account
from google.cloud import bigquery

KEY = r'C:\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json'
PROJECT = 'boss-rpa-bot'

creds = service_account.Credentials.from_service_account_file(
    KEY, scopes=['https://www.googleapis.com/auth/bigquery'])
client = bigquery.Client(project=PROJECT, credentials=creds)

sql = """
WITH
pm_systems AS (
  SELECT DISTINCT LOWER(sku_system) AS sku_system
  FROM `boss-rpa-bot.rakuten_ads.product_master_clean`
  WHERE sku_system IS NOT NULL AND sku_system != ''
),
orders_sku AS (
  SELECT
    LOWER(`商品管理番号`)      AS sku,
    LOWER(`システム連携用SKU`) AS sku_system,
    MIN(`注文日`)              AS first_order_date,
    MAX(`注文日`)              AS last_order_date,
    SUM(SAFE_CAST(`商品合計` AS FLOAT64)) AS total_sales,
    SUM(SAFE_CAST(`数量`     AS FLOAT64)) AS total_qty
  FROM `boss-rpa-bot.rakuten_ads.orders`
  WHERE `注文日` >= '2025-08-07'
    AND `商品管理番号` IS NOT NULL AND `商品管理番号` != ''
    AND `システム連携用SKU` IS NOT NULL AND `システム連携用SKU` != ''
  GROUP BY 1, 2
)
SELECT
  o.sku,
  o.sku_system,
  o.first_order_date,
  o.last_order_date,
  ROUND(o.total_sales, 0) AS total_sales,
  o.total_qty
FROM orders_sku o
LEFT JOIN pm_systems pm ON o.sku_system = pm.sku_system
WHERE pm.sku_system IS NULL
ORDER BY total_sales DESC
"""

print("=== 全期間（2025-08-07以降）商品マスタ未登録sku_system 一覧 ===")
print(f"  {'商品管理番号(sku)':<25} {'システム連携用SKU':<30} {'初回注文':>12} {'最終注文':>12} {'合計売上':>10} {'合計数量':>6}")
print(f"  {'-'*100}")

rows = list(client.query(sql))
if not rows:
    print("  ★未登録のsku_systemはありません（全SKUマッチ済み）")
else:
    for row in rows:
        print(f"  {(row.sku or ''):<25} {(row.sku_system or ''):<30} "
              f"{(row.first_order_date or '')!s:>12} {(row.last_order_date or '')!s:>12} "
              f"{(row.total_sales or 0):>10,.0f} {(row.total_qty or 0):>6.0f}")
    print(f"\n  合計 {len(rows)} 件の未登録sku_systemがあります。")

# 商品マスタ登録済みsku_system一覧（参考）
sql2 = """
SELECT LOWER(sku_system) AS sku_system, sku, cost, supplies, shipping, valid_from
FROM `boss-rpa-bot.rakuten_ads.product_master_clean`
ORDER BY sku, valid_from, sku_system
"""
print("\n=== 商品マスタ登録済みsku_system一覧（参考） ===")
print(f"  {'sku':<20} {'sku_system':<30} {'原価':>6} {'備品':>5} {'送料':>5} {'有効開始':>12}")
print(f"  {'-'*85}")
for row in client.query(sql2):
    print(f"  {(row.sku or ''):<20} {(row.sku_system or ''):<30} "
          f"{(row.cost or '')!s:>6} {(row.supplies or '')!s:>5} {(row.shipping or '')!s:>5} "
          f"{(row.valid_from or '')!s:>12}")

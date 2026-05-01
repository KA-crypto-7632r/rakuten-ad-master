# -*- coding: utf-8 -*-
# check_date.py - 指定日の粗利をSKU別に詳細分解
from google.oauth2 import service_account
from google.cloud import bigquery

KEY = r'C:\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json'
PROJECT = 'boss-rpa-bot'
TARGET_DATE = '2026-03-05'  # ← 確認したい日付

creds = service_account.Credentials.from_service_account_file(
    KEY, scopes=['https://www.googleapis.com/auth/bigquery'])
client = bigquery.Client(project=PROJECT, credentials=creds)

# ① 日次合計
sql1 = """
SELECT
  report_date,
  SUM(sales)          AS sales,
  SUM(cogs)           AS cogs,
  SUM(supplies_cost)  AS supplies,
  SUM(shipping_cost)  AS shipping,
  SUM(fee_cost)       AS fee,
  SUM(rpp_cost)       AS rpp,
  SUM(rppex_cost)     AS rppex,
  SUM(review_cost)    AS review,
  SUM(point_add_cost) AS point_add,
  SUM(affi_cost)      AS affi,
  SUM(gross_profit)   AS gross_profit,
  COUNTIF(cogs = 0 AND sales > 0) AS no_cost_skus
FROM `boss-rpa-bot.rakuten_ads.v_report`
WHERE report_date = '{date}'
GROUP BY report_date
""".format(date=TARGET_DATE)

print(f"=== {TARGET_DATE} 日次合計 ===")
for row in client.query(sql1):
    total_cost = (row.cogs + row.supplies + row.shipping + row.fee +
                  row.rpp + row.rppex + row.review + row.point_add + row.affi)
    print(f"  売上:           {row.sales:>10,.0f}")
    print(f"  原価:           {row.cogs:>10,.0f}")
    print(f"  備品費:         {row.supplies:>10,.0f}")
    print(f"  発送費:         {row.shipping:>10,.0f}")
    print(f"  手数料(10%):    {row.fee:>10,.0f}")
    print(f"  RPP費:          {row.rpp:>10,.0f}")
    print(f"  RPPEX費:        {row.rppex:>10,.0f}")
    print(f"  レビュー費:     {row.review:>10,.0f}")
    print(f"  ポイント加算:   {row.point_add:>10,.0f}")
    print(f"  アフィリ費:     {row.affi:>10,.0f}")
    print(f"  ─────────────────────────")
    print(f"  コスト合計:     {total_cost:>10,.0f}")
    print(f"  粗利:           {row.gross_profit:>10,.0f}")
    print(f"  原価0のSKU:     {row.no_cost_skus:>10}")

# ② SKU別内訳
sql2 = """
SELECT
  sku,
  product_name,
  ROUND(sales,0) AS sales,
  qty,
  ROUND(cogs,0) AS cogs,
  ROUND(supplies_cost,0) AS supplies,
  ROUND(shipping_cost,0) AS shipping,
  ROUND(fee_cost,0) AS fee,
  ROUND(rpp_cost,0) AS rpp,
  ROUND(rppex_cost,0) AS rppex,
  ROUND(gross_profit,0) AS gross_profit
FROM `boss-rpa-bot.rakuten_ads.v_report`
WHERE report_date = '{date}'
ORDER BY sales DESC
""".format(date=TARGET_DATE)

print(f"\n=== {TARGET_DATE} SKU別内訳 ===")
print(f"  {'SKU':<20} {'売上':>8} {'数量':>4} {'原価':>7} {'発送':>6} {'手数':>6} {'RPP':>7} {'粗利':>8}")
print(f"  {'-'*80}")
for row in client.query(sql2):
    print(f"  {row.sku:<20} {row.sales:>8,.0f} {row.qty:>4.0f} "
          f"{row.cogs:>7,.0f} {row.shipping:>6,.0f} {row.fee:>6,.0f} "
          f"{row.rpp:>7,.0f} {row.gross_profit:>8,.0f}")

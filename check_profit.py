# -*- coding: utf-8 -*-
from google.oauth2 import service_account
from google.cloud import bigquery

KEY = r'C:\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json'
PROJECT = 'boss-rpa-bot'

creds = service_account.Credentials.from_service_account_file(
    KEY, scopes=['https://www.googleapis.com/auth/bigquery'])
client = bigquery.Client(project=PROJECT, credentials=creds)

sql = """
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
  -- 原価NULLのSKU数（商品マスタ未マッチ）
  COUNTIF(cogs = 0 AND sales > 0) AS sku_no_cost_count
FROM `boss-rpa-bot.rakuten_ads.v_report`
WHERE report_date >= '2026-03-10' AND report_date <= '2026-03-18'
GROUP BY report_date
ORDER BY report_date
"""

print("=== 粗利内訳チェック ===")
for row in client.query(sql):
    total_cost = (row.cogs + row.supplies + row.shipping + row.fee +
                  row.rpp + row.rppex + row.review + row.point_add + row.affi)
    print(f"\n--- {row.report_date} ---")
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
    print(f"  原価0のSKU数:   {row.sku_no_cost_count:>10}  ← 商品マスタ未マッチ")

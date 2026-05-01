from google.cloud import bigquery
import os, sys

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = r'C:\rakuten-automation\credentials\boss-rpa-bot-daad02d10efb.json'
client = bigquery.Client(project='boss-rpa-bot')

print("=== raw_shohin_betsu: 3/14-3/22 日別データ ===")
q1 = """
SELECT
  LEFT(CAST(`日付` AS STRING), 10) AS dt,
  COUNT(*) AS rows,
  SUM(SAFE_CAST(`クリック数合計` AS FLOAT64)) AS clicks,
  COUNTIF(
    SAFE_CAST(REPLACE(REPLACE(CAST(`CTR` AS STRING),'%',''),'\ufeff','') AS FLOAT64) > 0
  ) AS ctr_non_zero
FROM `boss-rpa-bot.rakuten_ads.raw_shohin_betsu`
WHERE `日付` IS NOT NULL
  AND LEFT(CAST(`日付` AS STRING),10) >= '2026-03-14'
GROUP BY 1 ORDER BY 1
"""
print(f"{'dt':<12} {'rows':>6} {'clicks':>10} {'ctr_non_zero':>14}")
for r in client.query(q1):
    print(f"{str(r.dt):<12} {r.rows:>6} {str(r.clicks):>10} {r.ctr_non_zero:>14}")

print()
print("=== v_report_daily: 3/14-3/22 インプレッション確認 ===")
q2 = """
SELECT report_date, SUM(impressions) AS impressions, SUM(kw_clicks) AS kw_clicks
FROM `boss-rpa-bot.rakuten_ads.v_report_daily`
WHERE report_date >= '2026-03-14'
GROUP BY 1 ORDER BY 1
"""
print(f"{'report_date':<12} {'impressions':>14} {'kw_clicks':>12}")
for r in client.query(q2):
    print(f"{str(r.report_date):<12} {str(r.impressions):>14} {str(r.kw_clicks):>12}")

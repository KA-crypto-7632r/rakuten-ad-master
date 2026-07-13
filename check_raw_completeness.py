# -*- coding: utf-8 -*-
"""
check_raw_completeness.py

Verify that all required raw_* tables in BigQuery have data for the target date,
and that the monthly-cumulative raw_affi table has been refreshed for the current month.

Exit codes:
  0  - all required tables have data for the target date AND raw_affi is fresh
  2  - one or more required tables are missing data
  1  - other error (BQ connection failure, key missing, etc.)
"""
import os
import re
import sys
import hashlib
import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from google.oauth2 import service_account
from google.cloud import bigquery

# ── notify.py 取り込み(欠損検知時のChatwork通知用。2026-07-13 再発防止対応) ──
# 過去: 2026-06-27〜06-30 raw_keyword/raw_shohin_betsu が2週間近く欠損したが、
#       本スクリプトはMISSING(exit 2)を正しく返し続けていたにもかかわらず、
#       誰にも通知されず気づかれなかった(所見a-1)。今後はMISSING時に必ず1件通知する。
NOTIFY_PATHS = [
    r"C:\Users\uraka\Desktop\budget_manager",
    r"C:\rakuten-automation\楽天広告予算LINE通知",
]
_notify_mod = None
for _np in NOTIFY_PATHS:
    if Path(_np).exists():
        sys.path.insert(0, _np)
        try:
            import notify as _notify_mod
            break
        except ImportError:
            _notify_mod = None
            continue


# ── 通知抑制(2026-07-14) ──
# 店舗カルテ(raw_item)は対象日=前日分が楽天側で翌朝9時頃まで未確定のことが常態
# (2026-05-18発見。過去の成功フラグは毎日9:04頃)。5/6/7時のrunで毎朝通知が
# 鳴り続けるのを防ぐため、①raw_itemのみの欠損は9時前は通知しない
# ②同一対象日×同一欠損セットの通知は1日1回まで、とする。
# exit 2(フラグ不書込→自動リトライ)自体は抑制の有無にかかわらず維持する。
CSV_OUT_DIR = Path(r'C:\csv_out')
KARTE_READY_HOUR = 9


def _normalize_missing(missing_list: list) -> list:
    """'raw_affi(stale 40.1h)' 等の動的サフィックスを落としテーブル名だけに揃える。"""
    return sorted({re.split(r'[(\s]', m)[0] for m in missing_list})


def _marker_path(target: str, missing_list: list) -> Path:
    digest = hashlib.md5('|'.join(_normalize_missing(missing_list)).encode('utf-8')).hexdigest()[:8]
    return CSV_OUT_DIR / f'.notified_bq_{target}_{digest}'


def _cleanup_old_markers() -> None:
    try:
        cutoff = datetime.now() - timedelta(days=7)
        for f in CSV_OUT_DIR.glob('.notified_*'):
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
    except OSError:
        pass


def _notify_missing(target: str, missing_list: list) -> None:
    """MISSING検知時にChatwork優先で通知する。失敗しても本処理は止めない。"""
    _cleanup_old_markers()
    if _normalize_missing(missing_list) == ['raw_item'] and datetime.now().hour < KARTE_READY_HOUR:
        print(f'INFO: raw_itemのみ欠損かつ{KARTE_READY_HOUR}時前のため通知を抑制します'
              f'(店舗カルテの早朝未確定は常態。{KARTE_READY_HOUR}時台のリトライで回復しなければ通知されます)。')
        return
    marker = _marker_path(target, missing_list)
    if marker.exists():
        print(f'INFO: 同一内容を本日通知済みのため抑制します ({marker.name})。')
        return
    if _notify_mod is None:
        print('WARN: notify.py 未ロードのため通知をスキップします。')
        return
    try:
        body = (
            f"[楽天広告分析マスター] raw取込 欠損検知\n"
            f"対象日: {target}\n"
            f"欠損: {', '.join(missing_list)}\n"
            f"(run_all.ps1 は本日の成功フラグを立てません。次回スケジュール実行で自動リトライされます)"
        )
        ok = _notify_mod.push(body)
        print(f'INFO: 欠損通知 {"送信成功" if ok else "送信失敗(Chatwork/LINE共に未設定または失敗)"}')
        if ok:
            try:
                marker.touch()
            except OSError:
                pass
    except Exception as e:
        print(f'WARN: 欠損通知の送信中に例外: {type(e).__name__}: {e}')


KEY_PATHS = [
    r'C:\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json',
    r'C:\Users\AmazonTEISHIN\rakuten-automation\楽天広告分析マスター\boss-rpa-bot-daad02d10efb.json',
    'boss-rpa-bot-daad02d10efb.json',
]
PROJECT = 'boss-rpa-bot'
DATASET = 'rakuten_ads'
REQUIRED = ['raw_shohin_betsu', 'raw_keyword', 'raw_rppexp', 'raw_item']
# raw_affi は月次累計データなので、テーブル自体の最終更新時刻 (modified) が
# 「当日中に1回以上 refresh されているか」だけ確認する。
AFFI_TABLE = 'raw_affi'
AFFI_MAX_STALE_HOURS = 36  # 36時間以上更新が無ければ MISSING 扱い


def find_key():
    for p in KEY_PATHS:
        if os.path.exists(p):
            return p
    raise FileNotFoundError('BQ key not found')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYY-MM-DD (default: yesterday JST)')
    args = ap.parse_args()
    target = args.date or (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')

    creds = service_account.Credentials.from_service_account_file(
        find_key(), scopes=['https://www.googleapis.com/auth/bigquery']
    )
    bq = bigquery.Client(project=PROJECT, credentials=creds)

    # NOTE: SQL中の regex `\d{4}` を `.format()` placeholder と誤解させないため、
    #       str.format ではなく .replace でテーブル参照と日付を埋め込む。
    q = """
    WITH parsed AS (
      SELECT 'raw_shohin_betsu' AS tbl,
        COALESCE(SAFE.PARSE_DATE('%Y年%m月%d日',
                 REGEXP_EXTRACT(`日付`, r'^(\\d{4}年\\d{2}月\\d{2}日)')),
                 SAFE.PARSE_DATE('%Y-%m-%d', SUBSTR(`日付`,1,10))) AS d
      FROM `__PROJECT__.__DATASET__.raw_shohin_betsu`
      UNION ALL SELECT 'raw_keyword',
        COALESCE(SAFE.PARSE_DATE('%Y年%m月%d日',
                 REGEXP_EXTRACT(`日付`, r'^(\\d{4}年\\d{2}月\\d{2}日)')),
                 SAFE.PARSE_DATE('%Y-%m-%d', SUBSTR(`日付`,1,10)))
      FROM `__PROJECT__.__DATASET__.raw_keyword`
      UNION ALL SELECT 'raw_rppexp',
        SAFE.PARSE_DATE('%Y-%m-%d', SUBSTR(`日付`,1,10))
      FROM `__PROJECT__.__DATASET__.raw_rppexp`
      UNION ALL SELECT 'raw_item',
        COALESCE(SAFE.PARSE_DATE('%Y-%m-%d', CAST(`対象日` AS STRING)),
                 SAFE.PARSE_DATE('%Y/%m/%d', CAST(`対象日` AS STRING)))
      FROM `__PROJECT__.__DATASET__.raw_item`
    )
    SELECT tbl FROM parsed WHERE d = DATE '__TARGET__' GROUP BY tbl
    """
    q = (q.replace('__PROJECT__', PROJECT)
           .replace('__DATASET__', DATASET)
           .replace('__TARGET__', target))

    present = {r.tbl for r in bq.query(q).result()}
    missing = [t for t in REQUIRED if t not in present]

    # 月次累計の raw_affi は別途「テーブル最終更新時刻が新鮮か」をチェック。
    # 5/15以降取り込み停止インシデント (memory: rakuten_ad_download_stall_20260518.md)
    # の再発防止: PHASE 3 失敗でフラグが書かれて以降スキップされる問題を検知する。
    try:
        affi_tbl = bq.get_table(f'{PROJECT}.{DATASET}.{AFFI_TABLE}')
        now_utc = datetime.now(timezone.utc)
        stale = now_utc - affi_tbl.modified
        if stale > timedelta(hours=AFFI_MAX_STALE_HOURS):
            missing.append(f'{AFFI_TABLE}(stale {stale.total_seconds()/3600:.1f}h)')
    except Exception as e:
        missing.append(f'{AFFI_TABLE}(check_failed:{type(e).__name__})')

    if missing:
        print(f'MISSING({target}): {",".join(missing)}')
        _notify_missing(target, missing)
        sys.exit(2)
    print(f'ALL_PRESENT({target}): {",".join(sorted(present & set(REQUIRED)))}, {AFFI_TABLE}=fresh')
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f'ERROR: {type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(1)

# -*- coding: utf-8 -*-
"""
rpp_zero_delivery.py

RPP(店舗全体)の配信ゼロ日を判定する共有ロジック。

2026-07-31: check_raw_completeness.py 用に作った判定を、check_today_required_csv.py
にも同じ形で適用する必要が生じたため、判定式を1箇所(このモジュール)に集約する。
2箇所に同じ式をコピーすると必ずズレる(feedback_one_definition_one_place)。

判定材料は店舗全体の日次(12h)RPPレポート(欠損日ゼロで必ず取得できる)。
安全性は 2025-07-30〜2026-07-30 の全日突合で検証済み:
  - クリック>0 なのに raw_shohin_betsu/raw_keyword が両方空だった日 = 0件
  - クリック0 なのに BQ に行がある日                                = 0件
"""
import csv
import re
from pathlib import Path

CSV_OUT_DIR = Path(r'C:\csv_out')
RPP_REPORT_DIR = CSV_OUT_DIR / 'rpp_reports'
RPP_DAILY_COLS = 42  # 日次レポートの列数(商品別/KW別レポートと区別するための指紋)


def rpp_daily_totals(target: str):
    """対象日(YYYY-MM-DD)の日次(12h)店舗全体レポートから (クリック数合計, 実績額合計) を返す。

    同一対象日に複数のDL世代があるため最新世代を採用する。
    レポートが見つからない/読めない場合は None(=判定不能)を返す。
    """
    ymd = target.replace('-', '')
    best = None  # (世代スタンプ, clicks, cost)
    try:
        paths = list(RPP_REPORT_DIR.glob(f'*_{ymd}_*.csv'))
    except OSError:
        return None
    for path in paths:
        m = re.search(r'_%s_(\d{8})_(\d{4})\.csv$' % ymd, path.name)
        if not m:
            continue
        stamp = m.group(1) + m.group(2)
        if best is not None and stamp <= best[0]:
            continue
        try:
            with open(path, encoding='cp932', errors='replace', newline='') as f:
                rows = list(csv.reader(f))
        except OSError:
            continue
        # 商品別/KW別レポートも同じ命名なので、列数で日次レポートだけを選ぶ。
        if len(rows) < 2 or len(rows[0]) != RPP_DAILY_COLS:
            continue
        try:
            best = (stamp, int(rows[1][3] or 0), int(rows[1][4] or 0))
        except (ValueError, IndexError):
            continue
    return None if best is None else (best[1], best[2])


def is_zero_delivery_day(target: str) -> bool:
    """対象日がRPP配信ゼロ(クリック0・実績額0)と断定できるか。

    日次レポート自体が無い(判定不能)場合は False を返す(=通知を止めない安全側)。
    """
    totals = rpp_daily_totals(target)
    if totals is None:
        return False
    clicks, cost = totals
    return clicks == 0 and cost == 0

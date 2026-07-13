# -*- coding: utf-8 -*-
"""
check_today_required_csv.py

再発防止(2026-07-13, 所見a-1対応): run_all.ps1 は Download-All-Reports.ps1 の
成否を $LASTEXITCODE だけで判定しており、各フェーズ内で例外を握りつぶして
継続するため「部分失敗」を検知できない構造だった。
本スクリプトは Download フェーズ直後に呼ばれ、必須4レポート(KW実績/商品別RPP/
RPP費用(RPPEXP)/カルテ)の当日更新CSVが実際に存在するかを見て、欠けていれば
即座にChatwork通知する(早期警戒レイヤー)。

最終判定(BigQuery成功フラグの可否)は従来どおり check_raw_completeness.py が行う。
本スクリプトはパイプラインを中断しない(exit codeは診断用のみ、run_all.ps1は
中断させない)。

Exit codes:
  0 - 必須CSVがすべて本日更新分で存在
  2 - 1つ以上欠落(Chatwork通知を送信済み)
  1 - 予期しないエラー
"""
import os
import sys
import glob
from datetime import date, datetime
from pathlib import Path

CSV_OUT = r"C:\csv_out"

REQUIRED = [
    ("KW実績(RPPキーワード別12h→raw_keyword)",     os.path.join(CSV_OUT, "rpp_reports", "RPPキーワード別12h_*.csv")),
    ("商品別RPP(RPP商品別12h→raw_shohin_betsu)",   os.path.join(CSV_OUT, "rpp_reports", "RPP商品別12h_*.csv")),
    ("RPP費用(RPPEXP商品別→raw_rppexp)",           os.path.join(CSV_OUT, "rppexp_reports", "RPPEXP商品別_*.csv")),
    ("カルテ(店舗カルテ_商品ページ分析→raw_item)",  os.path.join(CSV_OUT, "rms_reports", "店舗カルテ_商品ページ分析_*.csv")),
]

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


def _is_today(path: str, today: date) -> bool:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).date() == today
    except Exception:
        return False


def main() -> int:
    today = date.today()
    missing = []
    for label, pattern in REQUIRED:
        files = [f for f in glob.glob(pattern) if _is_today(f, today)]
        if not files:
            missing.append(label)

    if not missing:
        print(f"OK: 必須CSV{len(REQUIRED)}種、本日分すべて存在。")
        return 0

    print(f"WARN: 本日分の必須CSVが{len(missing)}件欠落: {', '.join(missing)}")

    if _notify_mod is not None:
        try:
            body = (
                f"[楽天広告分析マスター] raw取込 ダウンロード欠落(早期警戒)\n"
                f"対象日: {today.isoformat()}\n"
                f"本日分CSV未生成: {', '.join(missing)}\n"
                f"(最終判定はBigQuery完全性チェックで別途行われます。"
                f"同日中の後続スケジュール実行で自動リトライされます)"
            )
            ok = _notify_mod.push(body)
            print(f"INFO: 通知 {'送信成功' if ok else '送信失敗'}")
        except Exception as e:
            print(f"WARN: 通知送信中に例外: {type(e).__name__}: {e}")
    else:
        print("WARN: notify.py 未ロードのため通知をスキップします。")

    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

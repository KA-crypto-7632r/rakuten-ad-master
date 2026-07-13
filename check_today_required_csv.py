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
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

CSV_OUT = r"C:\csv_out"

# ── 通知抑制(2026-07-14) ──
# run_all.ps1 は多段スケジュール(5/6/7/9/10/12/15時)で「取れるまでリトライ」する
# 設計であり、リトライ余地が残っている間の欠落は正常動作(店舗カルテは前日分が
# 楽天側で9時頃まで未確定なのが常態)。通知は「最終スケジュール実行
# (FINAL_RETRY_HOUR時)でもまだ欠落」の場合のみ送る。この通知はBQ欠損通知
# (check_raw_completeness.py)に対し「ダウンロード段階で欠けた」ことを示す切り分け情報。
# ②同一日×同一欠落セットの通知は1日1回まで(check_raw_completeness.py と同方式)。
FINAL_RETRY_HOUR = 15  # タスク RakutenDownloadAuto の最終トリガー時刻に合わせる

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


def _marker_path(today: date, missing: list) -> Path:
    digest = hashlib.md5('|'.join(sorted(missing)).encode('utf-8')).hexdigest()[:8]
    return Path(CSV_OUT) / f'.notified_csv_{today.isoformat()}_{digest}'


def _cleanup_old_markers() -> None:
    try:
        cutoff = datetime.now() - timedelta(days=7)
        for f in Path(CSV_OUT).glob('.notified_*'):
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
    except OSError:
        pass


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

    _cleanup_old_markers()
    if datetime.now().hour < FINAL_RETRY_HOUR:
        print(f"INFO: {FINAL_RETRY_HOUR}時の最終リトライ前のため通知を抑制します"
              f"(リトライで回復しなければ{FINAL_RETRY_HOUR}時台に通知されます)。")
        return 2
    marker = _marker_path(today, missing)
    if marker.exists():
        print(f"INFO: 同一内容を本日通知済みのため抑制します ({marker.name})。")
        return 2

    if _notify_mod is not None:
        try:
            body = (
                f"[楽天広告分析マスター] raw取込 ダウンロード欠落(本日の全リトライ終了)\n"
                f"対象日: {today.isoformat()}\n"
                f"本日分CSV未生成: {', '.join(missing)}\n"
                f"(本日{FINAL_RETRY_HOUR}時の最終リトライ後もダウンロードできていません。"
                f"欠損確定はBQ欠損通知側を参照)"
            )
            ok = _notify_mod.push(body)
            print(f"INFO: 通知 {'送信成功' if ok else '送信失敗'}")
            if ok:
                try:
                    marker.touch()
                except OSError:
                    pass
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

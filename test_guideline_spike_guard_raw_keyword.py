#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
upload_to_bigquery.py（RAW_キーワード取込元）の目安CPCスパイクガードの単体テスト。
BQ接続・CSV読み込みは一切行わない（fetch_guideline_baseline / apply_guideline_spike_guard に
渡す `client` はスタブに差し替える）。

upload_to_bigquery.py は read_kw_settings.py と違い、モジュール直下では
起動バナーのprintしか行わず（main()は `if __name__=="__main__"` ガード内）、実BQ接続・
実CSV読み込みは関数呼び出し時にしか発生しないため、本体を直接importしてテストできる
（read_kw_settings.py 用テストのように複製する必要はない）。

背景: 2026-08-02、read_kw_settings.py（Seleniumスクレイパー）の目安CPCスパイク検知ガードと
同型の汚染が raw_keyword（このファイルが取込元）でも起きていたことをBQ実測で確認
（例: bird-ref「鳩よけ」が2026-07-13の66円→07-14の1001円へ約15倍）。同じ閾値・同じ思想の
ガードを upload_to_bigquery.py 側にも追加した際の回帰テスト。
"""
import sys
import io

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import upload_to_bigquery as u


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class _FakeClient:
    """bigquery.Client の代わりに使うスタブ。query()の戻り値をあらかじめ指定できる。"""
    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    def query(self, sql, *_a, **_kw):
        self.queries.append(sql)
        return _FakeResult(self._rows)


class _RaisingClient:
    def query(self, *_a, **_kw):
        raise RuntimeError("BQ接続失敗（テスト用スタブ）")


def _self_test() -> None:
    ok = 0
    fail = 0

    def check(cond, label):
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print(f"  [FAIL] {label}")

    # ── is_guideline_spike（read_kw_settings.pyと同一ロジックのはず） ──
    check(u.is_guideline_spike(495, 50), "T1: 50→495(9.9倍・+445円)はスパイク判定")
    check(not u.is_guideline_spike(150, 50), "T2: 50→150(ちょうど3.0倍・+100円)は通す")
    check(u.is_guideline_spike(200, 40), "T3: 40→200(5倍・+160円)はスパイク判定")
    check(not u.is_guideline_spike(160, 40), "T4: 40→160(4倍だが+120円<150)は通す(絶対値ガード救済)")
    check(not u.is_guideline_spike(999, None), "T5: 前回値なし(新規KW)は通す(fail-open)")
    check(not u.is_guideline_spike(500, 39), "T6: 前回39円(<40=異常値)は判定スキップで通す")
    check(not u.is_guideline_spike(None, 50), "T7: 今回値None→スパイク判定されない")
    # 実インシデント値の回帰確認: bird-ref「鳩よけ」66円→1001円(2026-07-13→07-14実測)
    check(u.is_guideline_spike(1001, 66), "T8: 実インシデント値(66→1001)はスパイク判定")

    # ── _fetch_guideline_baseline: BQ例外時はfail-open(空dict) ──
    baseline_fail = u._fetch_guideline_baseline(_RaisingClient(), "dummy.table")
    check(baseline_fail == {}, "T9: BQ例外時は空dictを返す(fail-open)")

    # ── _fetch_guideline_baseline: 正常時は{(sku,kw): guide}の辞書化 ──
    fake_rows = [
        {"sku": "bird-ref", "kw": "鳩よけ", "guide": 66.0},
        {"sku": "gips-arm", "kw": "ギプスカバー 腕", "guide": 46.0},
    ]
    baseline_ok = u._fetch_guideline_baseline(_FakeClient(fake_rows), "dummy.table")
    check(baseline_ok.get(("bird-ref", "鳩よけ")) == 66.0, "T10: 正常時は(sku,kw)キーで基準値を引ける")
    check(len(baseline_ok) == 2, "T10b: 2件とも辞書化される")

    # ── apply_guideline_spike_guard: 必須列が無い/空dfならno-op ──
    df_missing_cols = pd.DataFrame({"日付": ["2026-08-01"], "目安CPC": ["100"]})
    out_missing = u.apply_guideline_spike_guard(_FakeClient(fake_rows), "dummy.table", df_missing_cols)
    check(out_missing.equals(df_missing_cols), "T11: 必須列(商品管理番号/キーワード)が無ければno-op")

    df_empty = pd.DataFrame(columns=["商品管理番号", "キーワード", "目安CPC"])
    out_empty = u.apply_guideline_spike_guard(_FakeClient(fake_rows), "dummy.table", df_empty)
    check(len(out_empty) == 0, "T12: 空dfはno-op(0行のまま)")

    # ── apply_guideline_spike_guard: 基準値が空ならfail-open(値はそのまま) ──
    df_normal = pd.DataFrame({
        "商品管理番号": ["bird-ref"],
        "キーワード": ["鳩よけ"],
        "目安CPC": ["1001"],
    })
    out_no_baseline = u.apply_guideline_spike_guard(_FakeClient([]), "dummy.table", df_normal)
    check(out_no_baseline.iloc[0]["目安CPC"] == "1001", "T13: 基準値が空ならfail-openで値は変更されない")

    # ── apply_guideline_spike_guard: 実インシデント相当のスパイクを空値化 ──
    df_spike = pd.DataFrame({
        "商品管理番号": ["bird-ref", "gips-arm", "cattoy2"],
        "キーワード":   ["鳩よけ",     "ギプスカバー 腕", "猫じゃらし"],
        "目安CPC":     ["1001",       "495",             "57"],  # cattoy2は正常値(スパイクでない)
    })
    baseline_for_spike = [
        {"sku": "bird-ref", "kw": "鳩よけ", "guide": 66.0},
        {"sku": "gips-arm", "kw": "ギプスカバー 腕", "guide": 46.0},
        {"sku": "cattoy2", "kw": "猫じゃらし", "guide": 50.0},
    ]
    out_spike = u.apply_guideline_spike_guard(_FakeClient(baseline_for_spike), "dummy.table", df_spike)
    check(out_spike.iloc[0]["目安CPC"] == "", "T14: bird-refの1001円(66円比15倍)は空値化される")
    check(out_spike.iloc[1]["目安CPC"] == "", "T15: gips-armの495円(46円比10.8倍)は空値化される")
    check(out_spike.iloc[2]["目安CPC"] == "57", "T16: cattoy2の57円(50円比1.14倍)はスパイクでないため変更なし")

    # ── apply_guideline_spike_guard: 10件超のスパイクでも例外を投げず全件処理される(print省略のみ) ──
    n = 15
    df_many = pd.DataFrame({
        "商品管理番号": [f"sku{i}" for i in range(n)],
        "キーワード":   [f"kw{i}" for i in range(n)],
        "目安CPC":     ["500"] * n,
    })
    baseline_many = [{"sku": f"sku{i}", "kw": f"kw{i}", "guide": 40.0} for i in range(n)]
    out_many = u.apply_guideline_spike_guard(_FakeClient(baseline_many), "dummy.table", df_many)
    check(all(v == "" for v in out_many["目安CPC"]), "T17: 10件超でも全件が空値化される(printの省略と実処理は独立)")

    print(f"\nself_test: {ok} passed, {fail} failed")
    if fail:
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()

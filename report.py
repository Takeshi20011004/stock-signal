#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
株 状態サマリーレポート（シグナル成立の有無に関わらず watchlist 全銘柄の現況を出力）

- データ取得 : signal_check.fetch_series を再利用（Yahoo Finance / urllib・外部ライブラリ不要）
- 指標計算   : signal_check.sma / rsi を再利用
- 出力       : (1) コンソールの整形テーブル ＋ (2) Markdown ファイル（report_YYYY-MM-DD.md）

使い方:
    python3 report.py            # コンソール表示 ＋ Markdown 出力
    python3 report.py --no-file  # コンソール表示のみ（ファイルを書かない）

※ これは投資助言ではありません。発注は一切行いません。投資判断はご自身で。
"""

import os
import sys
import datetime
import urllib.error

# 既存ツールから関数を再利用（編集しない signal_check.py を import）
from signal_check import (fetch_series, sma, rsi, load_json, CONFIG_PATH, JST,
                          fmt_price)


# ----------------------------------------------------------------------
# 1 銘柄分の状態を計算
# ----------------------------------------------------------------------
def build_row(name, code, series, params, currency="JPY"):
    """1 銘柄の現況を dict で返す。算出できない値は None。"""
    closes = [c for _, c in series]
    last_date = series[-1][0]
    last_close = closes[-1]

    # 前日比（%）
    prev_close = closes[-2] if len(closes) >= 2 else None
    change_pct = None
    if prev_close not in (None, 0):
        change_pct = (last_close - prev_close) / prev_close * 100.0

    # RSI(14) の現在値
    r = rsi(closes, params["rsi_period"])
    rsi_now = r[-1]

    # 25日/75日 SMA と乖離率（終値が SMA から何 % 離れているか）
    short = sma(closes, params["sma_short"])
    long_ = sma(closes, params["sma_long"])
    sma_short_now = short[-1]
    sma_long_now = long_[-1]
    dev_short = _deviation(last_close, sma_short_now)
    dev_long = _deviation(last_close, sma_long_now)

    # トレンド（短期線が長期線の上か下か）
    if sma_short_now is None or sma_long_now is None:
        trend = "—"
    elif sma_short_now > sma_long_now:
        trend = "上昇（短期>長期）"
    elif sma_short_now < sma_long_now:
        trend = "下落（短期<長期）"
    else:
        trend = "横ばい（短期=長期）"

    return {
        "name": name, "code": code, "date": last_date, "close": last_close,
        "currency": currency,
        "change_pct": change_pct, "rsi": rsi_now,
        "sma_short": sma_short_now, "sma_long": sma_long_now,
        "dev_short": dev_short, "dev_long": dev_long, "trend": trend,
    }


def _deviation(price, ma):
    """終値の移動平均からの乖離率（%）。MA が無ければ None。"""
    if ma in (None, 0):
        return None
    return (price - ma) / ma * 100.0


# ----------------------------------------------------------------------
# 整形ヘルパ（None は "—" で表示）
# ----------------------------------------------------------------------
def _f(value, fmt):
    return "—" if value is None else fmt % value


# ----------------------------------------------------------------------
# コンソール出力（整形テーブル）
# ----------------------------------------------------------------------
def print_table(rows, params, now):
    print("=" * 78)
    print("株 状態サマリー  %s" % now.strftime("%Y-%m-%d %H:%M"))
    print("※ これは投資助言ではありません。発注は行いません。投資判断はご自身で。")
    print("=" * 78)

    header = ("%-10s %-6s %8s %7s %6s %7s %7s  %s" %
              ("銘柄", "コード", "終値", "前日比", "RSI",
               "25日乖離", "75日乖離", "トレンド"))
    print(header)
    print("-" * 78)
    for row in rows:
        print("%-10s %-6s %8s %7s %6s %7s %7s  %s" % (
            _clip(row["name"], 10), row["code"],
            fmt_price(row["close"], row["currency"]),
            _f(row["change_pct"], "%+.2f%%"),
            _f(row["rsi"], "%.1f"),
            _f(row["dev_short"], "%+.1f%%"),
            _f(row["dev_long"], "%+.1f%%"),
            row["trend"],
        ))
    print("-" * 78)
    print("対象 %d 銘柄（参考: SMA%d/%d, RSI%d）" % (
        len(rows), params["sma_short"], params["sma_long"], params["rsi_period"]))


def _clip(s, width):
    """全角を含む銘柄名がはみ出さないよう簡易に丸める。"""
    if len(s) > width:
        return s[:width - 1] + "…"
    return s


# ----------------------------------------------------------------------
# Markdown 出力
# ----------------------------------------------------------------------
def build_markdown(rows, params, now):
    lines = []
    lines.append("# 株 状態サマリー  %s" % now.strftime("%Y-%m-%d %H:%M"))
    lines.append("")
    lines.append("> ⚠️ これは投資助言ではありません。発注は一切行いません。投資判断はご自身で。")
    lines.append("")
    lines.append("対象 %d 銘柄（参考: SMA%d/%d, RSI%d）" % (
        len(rows), params["sma_short"], params["sma_long"], params["rsi_period"]))
    lines.append("")
    lines.append("| 銘柄 | コード | 日付 | 終値 | 前日比 | RSI(14) | 25日乖離 | 75日乖離 | トレンド |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for row in rows:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["name"], row["code"], row["date"],
            fmt_price(row["close"], row["currency"]),
            _f(row["change_pct"], "%+.2f%%"),
            _f(row["rsi"], "%.1f"),
            _f(row["dev_short"], "%+.1f%%"),
            _f(row["dev_long"], "%+.1f%%"),
            row["trend"],
        ))
    lines.append("")
    lines.append("_生成: report.py / %s_" % now.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------
def main():
    write_file = "--no-file" not in sys.argv  # 既定はファイルも出力
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        print("config.json が読めません", file=sys.stderr)
        sys.exit(1)

    params = cfg["params"]
    now = datetime.datetime.now(JST)

    rows = []
    for item in cfg["watchlist"]:
        code, name = item["code"], item.get("name", item["code"])
        try:
            series, meta = fetch_series(code)
        except (urllib.error.URLError, KeyError, IndexError, ValueError) as e:
            print("取得失敗 %s: %s" % (code, e), file=sys.stderr)
            continue
        if len(series) < 2:
            print("データ不足 %s（%d本）" % (code, len(series)), file=sys.stderr)
            continue
        rows.append(build_row(name, code, series, params,
                              meta.get("currency", "JPY")))

    if not rows:
        print("出力できる銘柄がありません（データ取得に失敗した可能性）", file=sys.stderr)
        sys.exit(1)

    print_table(rows, params, now)

    if write_file:
        fname = "report_%s.md" % now.strftime("%Y-%m-%d")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_markdown(rows, params, now))
        print("Markdown を書き出しました: %s" % path)


if __name__ == "__main__":
    main()

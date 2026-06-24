#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
株シグナル 簡易バックテスト（過去検証ツール）

signal_check.py のシグナルルールを、過去1年の日足に対して擬似的に
売買した場合の成績を検証します。

- ルール   : ゴールデンクロスで買い・デッドクロスで売り。
             （SMAクロスが出ない地合いでも検証できるよう RSI 30/70 も補助エントリ）
             ・買いポジションが無いとき: ゴールデンクロス or RSI<rsi_low で買い
             ・買いポジションが有るとき: デッドクロス or RSI>rsi_high で売り（手仕舞い）
- データ   : signal_check.fetch_series を再利用（Yahoo Finance / 外部ライブラリ不要）
- 指標     : signal_check.sma / rsi をそのまま再利用
- 出力     : 銘柄ごとの損益率・取引回数・勝率＋合計をコンソール表示

※ これは投資助言ではありません。あくまで過去データに対する検証であり、
   将来の成績を保証するものではありません。発注は一切行いません。

使い方:
    python3 backtest.py                # config.json の watchlist 全銘柄
    python3 backtest.py --code 7203.T  # 特定銘柄のみ（.T は省略可）
"""

import json
import os
import sys
import datetime
import urllib.error

# signal_check の関数を再利用（指標ロジックを二重実装しないため）
from signal_check import fetch_series, sma, rsi

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
JST = datetime.timezone(datetime.timedelta(hours=9))


# ----------------------------------------------------------------------
# バックテスト本体
# ----------------------------------------------------------------------
def backtest(series, params):
    """1銘柄の時系列を走査し、トレード履歴と集計を返す。

    戻り値: dict
        trades      : [(entry_date, entry_px, exit_date, exit_px, ret), ...]
        n_trades    : 確定した取引回数
        n_wins      : 勝ち取引数
        total_ret   : 各取引の損益率を複利で合成した総合損益率（%）
        open_pos    : 期末に建てたまま手仕舞いできなかった建玉（dict or None）
    """
    dates = [d for d, _ in series]
    closes = [c for _, c in series]

    short = sma(closes, params["sma_short"])
    long_ = sma(closes, params["sma_long"])
    r = rsi(closes, params["rsi_period"])

    in_position = False
    entry_date = None
    entry_px = None
    trades = []

    # クロス判定には前日値が要るので 1 本目はスキップ
    for i in range(1, len(closes)):
        px = closes[i]

        s0, s1 = short[i - 1], short[i]
        l0, l1 = long_[i - 1], long_[i]
        r0, r1 = r[i - 1], r[i]

        golden = (s0 is not None and s1 is not None
                  and l0 is not None and l1 is not None
                  and s0 <= l0 and s1 > l1)
        dead = (s0 is not None and s1 is not None
                and l0 is not None and l1 is not None
                and s0 >= l0 and s1 < l1)
        rsi_buy = (r0 is not None and r1 is not None
                   and r0 >= params["rsi_low"] and r1 < params["rsi_low"])
        rsi_sell = (r0 is not None and r1 is not None
                    and r0 <= params["rsi_high"] and r1 > params["rsi_high"])

        if not in_position:
            if golden or rsi_buy:
                in_position = True
                entry_date = dates[i]
                entry_px = px
        else:
            if dead or rsi_sell:
                ret = (px - entry_px) / entry_px * 100.0
                trades.append((entry_date, entry_px, dates[i], px, ret))
                in_position = False
                entry_date = entry_px = None

    n_trades = len(trades)
    n_wins = sum(1 for t in trades if t[4] > 0)

    # 各取引を複利合成（(1+r1)(1+r2)... - 1）
    equity = 1.0
    for t in trades:
        equity *= (1.0 + t[4] / 100.0)
    total_ret = (equity - 1.0) * 100.0

    open_pos = None
    if in_position:
        last_px = closes[-1]
        open_pos = {
            "entry_date": entry_date,
            "entry_px": entry_px,
            "last_date": dates[-1],
            "last_px": last_px,
            "unreal_ret": (last_px - entry_px) / entry_px * 100.0,
        }

    return {
        "trades": trades,
        "n_trades": n_trades,
        "n_wins": n_wins,
        "total_ret": total_ret,
        "open_pos": open_pos,
    }


# ----------------------------------------------------------------------
# 表示
# ----------------------------------------------------------------------
def print_result(name, code, res, span):
    win_rate = (res["n_wins"] / res["n_trades"] * 100.0) if res["n_trades"] else 0.0
    print("-" * 60)
    print("【%s (%s)】 期間: %s" % (name, code, span))
    print("  取引回数 : %d 回 ／ 勝ち %d 回 ／ 勝率 %.1f%%"
          % (res["n_trades"], res["n_wins"], win_rate))
    print("  総合損益率（複利合成）: %+.2f%%" % res["total_ret"])
    if res["open_pos"]:
        op = res["open_pos"]
        print("  ※ 期末に建玉あり（未決済）: %s に %.0f円で買い → 直近 %.0f円 (%+.2f%%)"
              % (op["entry_date"], op["entry_px"], op["last_px"], op["unreal_ret"]))
    for t in res["trades"]:
        print("    %s 買 %.0f → %s 売 %.0f  (%+.2f%%)"
              % (t[0], t[1], t[2], t[3], t[4]))


def print_total(per_code):
    total_trades = sum(r["n_trades"] for r in per_code)
    total_wins = sum(r["n_wins"] for r in per_code)
    win_rate = (total_wins / total_trades * 100.0) if total_trades else 0.0
    # 全銘柄を等金額で配分したと仮定し、損益率を単純平均
    rets = [r["total_ret"] for r in per_code]
    avg_ret = sum(rets) / len(rets) if rets else 0.0
    print("=" * 60)
    print("【合計】 銘柄数 %d" % len(per_code))
    print("  取引回数 : %d 回 ／ 勝ち %d 回 ／ 勝率 %.1f%%"
          % (total_trades, total_wins, win_rate))
    print("  平均損益率（銘柄等金額・単純平均）: %+.2f%%" % avg_ret)
    print("=" * 60)
    print("※ 本結果は過去データの検証であり投資助言ではありません。発注は行いません。")


# ----------------------------------------------------------------------
# 設定読み込み（signal_check.load_json は使わず軽量に）
# ----------------------------------------------------------------------
def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print("config.json が読めません: %s" % e, file=sys.stderr)
        sys.exit(1)


def parse_code_arg(argv):
    """--code 7203.T 形式を拾う。無ければ None。"""
    if "--code" in argv:
        i = argv.index("--code")
        if i + 1 < len(argv):
            return argv[i + 1]
        print("--code の後に銘柄コードを指定してください", file=sys.stderr)
        sys.exit(1)
    return None


# ----------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------
def main():
    cfg = load_config()
    params = cfg["params"]

    code_arg = parse_code_arg(sys.argv)
    if code_arg:
        targets = [{"code": code_arg, "name": code_arg}]
    else:
        targets = cfg["watchlist"]

    print("=" * 60)
    print("簡易バックテスト（過去検証 / 投資助言ではありません）")
    print("ルール: ゴールデンクロス買い・デッドクロス売り（RSI %d/%d 補助）"
          % (params["rsi_low"], params["rsi_high"]))
    print("SMA: %d日 / %d日  RSI: %d日"
          % (params["sma_short"], params["sma_long"], params["rsi_period"]))

    per_code = []
    for item in targets:
        code, name = item["code"], item.get("name", item["code"])
        try:
            series, _meta = fetch_series(code)
        except (urllib.error.URLError, KeyError, IndexError, ValueError) as e:
            print("取得失敗 %s: %s" % (code, e), file=sys.stderr)
            continue
        if len(series) < params["sma_long"] + 1:
            print("データ不足 %s（%d本）" % (code, len(series)), file=sys.stderr)
            continue

        span = "%s 〜 %s" % (series[0][0], series[-1][0])
        res = backtest(series, params)
        print_result(name, code, res, span)
        per_code.append(res)

    if per_code:
        print_total(per_code)
    else:
        print("検証できる銘柄がありませんでした。", file=sys.stderr)


if __name__ == "__main__":
    main()

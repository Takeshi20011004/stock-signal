#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
株シグナル通知（楽天証券などで手動発注する前提のシグナル検出）

- データ取得 : Yahoo Finance chart API を urllib で直接取得（外部ライブラリ不要）
- シグナル   : 移動平均クロス / RSI / 指定価格到達
- 通知       : macOS 通知センター（osascript）＋ ログ
- 重複防止   : state.json に「銘柄:シグナル種別:対象日」を記録し、同じものは再通知しない

LINE 連携は notify_line() を実装して notify() から呼ぶだけで差し替え可能（下部参照）。
発注は一切行いません。投資判断はご自身で。
"""

import json
import os
import sys
import subprocess
import datetime
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
LOG_PATH = os.path.join(BASE_DIR, "signals.log")

YAHOO_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/"
             "{symbol}?range=1y&interval=1d")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
JST = datetime.timezone(datetime.timedelta(hours=9))


# ----------------------------------------------------------------------
# データ取得
# ----------------------------------------------------------------------
def fetch_series(code):
    """日足の (date文字列, 終値) リストを古い順で返す。欠損は除外。"""
    # 銘柄コードの自動判別:
    #   "." を含む    → そのまま（例: 7203.T, BRK-B など指定済み）
    #   数字のみ       → 日本株とみなして東証サフィックス .T を付与
    #   それ以外       → 米国株などのティッカーとしてそのまま（大文字化）
    if "." in code:
        symbol = code
    elif code.isdigit():
        symbol = code + ".T"
    else:
        symbol = code.upper()
    url = YAHOO_URL.format(symbol=symbol)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    meta = result["meta"]

    series = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        d = datetime.datetime.fromtimestamp(ts, JST).strftime("%Y-%m-%d")
        series.append((d, float(close)))
    return series, meta


# ----------------------------------------------------------------------
# 指標計算（純Python）
# ----------------------------------------------------------------------
def sma(values, period):
    """末尾を含む単純移動平均の系列。算出できない位置は None。"""
    out = [None] * len(values)
    if len(values) < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def rsi(values, period):
    """Wilder 平滑による RSI 系列。算出できない位置は None。"""
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_from(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain, avg_loss):
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def ema(values, period):
    """指数平滑移動平均の系列。最初の period 本の SMA を起点にする。算出できない位置は None。"""
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def macd(values, fast, slow, signal):
    """MACD線とシグナル線の (macd_line, signal_line) を返す。算出できない位置は None。"""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = [None] * len(values)
    for i in range(len(values)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]
    # シグナル線は MACD 線（None でない区間）に対する EMA
    start = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line = [None] * len(values)
    if start is not None:
        sig = ema(macd_line[start:], signal)
        for offset, v in enumerate(sig):
            signal_line[start + offset] = v
    return macd_line, signal_line


def bollinger(values, period, k):
    """ボリンジャーバンドの (upper, middle, lower) 系列を返す。算出できない位置は None。"""
    middle = sma(values, period)
    upper = [None] * len(values)
    lower = [None] * len(values)
    if len(values) < period:
        return upper, middle, lower
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean = middle[i]
        var = sum((x - mean) ** 2 for x in window) / period
        sd = var ** 0.5
        upper[i] = mean + k * sd
        lower[i] = mean - k * sd
    return upper, middle, lower


# ----------------------------------------------------------------------
# シグナル判定
# ----------------------------------------------------------------------
def detect_signals(name, code, series, params, alerts):
    """このセッションで成立したシグナルの dict リストを返す。"""
    dates = [d for d, _ in series]
    closes = [c for _, c in series]
    last_date = dates[-1]
    last_close = closes[-1]
    signals = []

    short = sma(closes, params["sma_short"])
    long_ = sma(closes, params["sma_long"])
    if short[-1] is not None and short[-2] is not None \
            and long_[-1] is not None and long_[-2] is not None:
        if short[-2] <= long_[-2] and short[-1] > long_[-1]:
            signals.append(_mk(code, name, last_date, last_close,
                               "golden_cross", "📈 ゴールデンクロス（買いシグナル）",
                               "%d日線が%d日線を上抜け" % (params["sma_short"], params["sma_long"])))
        elif short[-2] >= long_[-2] and short[-1] < long_[-1]:
            signals.append(_mk(code, name, last_date, last_close,
                               "dead_cross", "📉 デッドクロス（売りシグナル）",
                               "%d日線が%d日線を下抜け" % (params["sma_short"], params["sma_long"])))

    r = rsi(closes, params["rsi_period"])
    if r[-1] is not None and r[-2] is not None:
        if r[-2] >= params["rsi_low"] and r[-1] < params["rsi_low"]:
            signals.append(_mk(code, name, last_date, last_close,
                               "rsi_oversold", "🟢 RSI 売られすぎ（買い候補）",
                               "RSI=%.1f が %d を下抜け" % (r[-1], params["rsi_low"])))
        elif r[-2] <= params["rsi_high"] and r[-1] > params["rsi_high"]:
            signals.append(_mk(code, name, last_date, last_close,
                               "rsi_overbought", "🔴 RSI 買われすぎ（売り候補）",
                               "RSI=%.1f が %d を上抜け" % (r[-1], params["rsi_high"])))

    macd_line, signal_line = macd(closes, params["macd_fast"],
                                  params["macd_slow"], params["macd_signal"])
    if macd_line[-1] is not None and macd_line[-2] is not None \
            and signal_line[-1] is not None and signal_line[-2] is not None:
        if macd_line[-2] <= signal_line[-2] and macd_line[-1] > signal_line[-1]:
            signals.append(_mk(code, name, last_date, last_close,
                               "macd_golden", "📊 MACDゴールデンクロス（買いシグナル）",
                               "MACD線がシグナル線を上抜け"))
        elif macd_line[-2] >= signal_line[-2] and macd_line[-1] < signal_line[-1]:
            signals.append(_mk(code, name, last_date, last_close,
                               "macd_dead", "📊 MACDデッドクロス（売りシグナル）",
                               "MACD線がシグナル線を下抜け"))

    upper, _mid, lower = bollinger(closes, params["bb_period"], params["bb_k"])
    if upper[-1] is not None and last_close > upper[-1]:
        signals.append(_mk(code, name, last_date, last_close,
                           "bb_upper", "🟠 ボリンジャーバンド +%dσ突破（買われすぎ）" % params["bb_k"],
                           "終値 %.0f円 が上限 %.0f円 を上抜け" % (last_close, upper[-1])))
    elif lower[-1] is not None and last_close < lower[-1]:
        signals.append(_mk(code, name, last_date, last_close,
                           "bb_lower", "🔵 ボリンジャーバンド -%dσ突破（売られすぎ）" % params["bb_k"],
                           "終値 %.0f円 が下限 %.0f円 を下抜け" % (last_close, lower[-1])))

    for a in alerts:
        if "below" in a and last_close <= a["below"]:
            signals.append(_mk(code, name, last_date, last_close,
                               "price_below_%s" % a["below"], "🔻 指定価格に到達（以下）",
                               "終値 %.0f円 ≤ %s円" % (last_close, a["below"])))
        if "above" in a and last_close >= a["above"]:
            signals.append(_mk(code, name, last_date, last_close,
                               "price_above_%s" % a["above"], "🔺 指定価格に到達（以上）",
                               "終値 %.0f円 ≥ %s円" % (last_close, a["above"])))
    return signals


def _mk(code, name, date, close, kind, title, detail):
    return {
        "key": "%s:%s:%s" % (code, kind, date),
        "code": code, "name": name, "date": date, "close": close,
        "kind": kind, "title": title, "detail": detail,
    }


# ----------------------------------------------------------------------
# 通知
# ----------------------------------------------------------------------
def notify(sig, cfg):
    title = "株シグナル: %s (%s)" % (sig["name"], sig["code"])
    body = "%s\n%s ／ 終値 %.0f円 [%s]" % (
        sig["title"], sig["detail"], sig["close"], sig["date"])
    if cfg.get("macos"):
        notify_macos(title, sig["title"] + " / " + sig["detail"], cfg.get("sound"))
    if cfg.get("log"):
        append_log(body.replace("\n", " | "))
    # 後でLINEを使うときは下のコメントを外して notify_line を実装するだけ
    # notify_line(title + "\n" + body)
    print("[NOTIFY]", body.replace("\n", " | "))


def notify_macos(title, message, sound=None):
    script = 'display notification "%s" with title "%s"' % (
        _esc(message), _esc(title))
    if sound:
        script += ' sound name "%s"' % _esc(sound)
    try:
        subprocess.run(["osascript", "-e", script], check=False,
                       capture_output=True, timeout=10)
    except Exception as e:
        print("macOS通知に失敗:", e, file=sys.stderr)


def _esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def append_log(line):
    stamp = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("%s  %s\n" % (stamp, line))


# 【LINE連携用 雛形】後でセットアップが済んだら中身を書く
# def notify_line(text):
#     token = os.environ["LINE_CHANNEL_TOKEN"]
#     user_id = os.environ["LINE_USER_ID"]
#     payload = json.dumps({"to": user_id,
#         "messages": [{"type": "text", "text": text}]}).encode()
#     req = urllib.request.Request(
#         "https://api.line.me/v2/bot/message/push", data=payload,
#         headers={"Content-Type": "application/json",
#                  "Authorization": "Bearer " + token})
#     urllib.request.urlopen(req, timeout=10)


# ----------------------------------------------------------------------
# 状態管理（重複通知の防止）
# ----------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_state(state):
    keys = sorted(state.get("notified", []))[-500:]  # 直近500件だけ保持
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"notified": keys}, f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------
def main():
    test_mode = "--test" in sys.argv  # state を無視して必ず表示（動作確認用）
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        print("config.json が読めません", file=sys.stderr)
        sys.exit(1)

    state = load_json(STATE_PATH, {"notified": []})
    notified = set(state.get("notified", []))
    params = cfg["params"]
    notify_cfg = cfg["notify"]

    alerts_by_code = {}
    for a in cfg.get("price_alerts", []):
        alerts_by_code.setdefault(a["code"], []).append(a)

    fired = 0
    for item in cfg["watchlist"]:
        code, name = item["code"], item.get("name", item["code"])
        try:
            series, _meta = fetch_series(code)
        except (urllib.error.URLError, KeyError, IndexError, ValueError) as e:
            print("取得失敗 %s: %s" % (code, e), file=sys.stderr)
            continue
        if len(series) < params["sma_long"] + 1:
            print("データ不足 %s（%d本）" % (code, len(series)), file=sys.stderr)
            continue

        for sig in detect_signals(name, code, series, params,
                                  alerts_by_code.get(code, [])):
            if not test_mode and sig["key"] in notified:
                continue
            notify(sig, notify_cfg)
            notified.add(sig["key"])
            fired += 1

    if not test_mode:
        state["notified"] = list(notified)
        save_state(state)

    if fired == 0:
        print("シグナルなし（%s）" %
              datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M"))


if __name__ == "__main__":
    main()

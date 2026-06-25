#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
監視銘柄をブラウザから追加・削除する小さなローカルWebアプリ。

  起動:  python3 web_add.py      （自動でブラウザが開きます）
  停止:  ターミナルで Ctrl+C

- 会社名で検索 → 候補のコードをワンクリックで入力
- コード（または検索で選んだ銘柄）を「追加」ボタンで watchlist へ
- 価格アラート（以下/以上）も同時に登録可
- 現在の監視リストを一覧表示、各行に削除ボタン
- 追加/削除すると config.json を更新し、自動で GitHub に push（翌朝メールへ反映）

外部ライブラリ不要（標準ライブラリのみ）。発注は一切行いません。
localhost(127.0.0.1) のみで待ち受けるので外部からはアクセスできません。
"""

import os
import sys
import json
import html
import webbrowser
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# add_stock.py のロジックを再利用
from add_stock import (load_config, save_config, normalize_code,
                       resolve_name, git_push)

HOST, PORT = "127.0.0.1", 8765
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
SEARCH_URL = ("https://query2.finance.yahoo.com/v1/finance/search"
              "?q={q}&quotesCount=8&newsCount=0")


# ----------------------------------------------------------------------
# 操作
# ----------------------------------------------------------------------
def search_symbols(query):
    """Yahoo の検索APIで会社名→銘柄候補を返す。失敗時は空リスト。"""
    url = SEARCH_URL.format(q=urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except Exception:
        return []
    out = []
    for q in data.get("quotes", []):
        sym = q.get("symbol")
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "name": q.get("shortname") or q.get("longname") or sym,
            "exchange": q.get("exchDisp") or q.get("exchange") or "",
        })
    return out


def do_add(code, name, below, above):
    code = normalize_code(code)
    cfg = load_config()
    wl = cfg.setdefault("watchlist", [])
    if any(i["code"] == code for i in wl):
        msg = "%s はすでに監視中です。" % code
    else:
        if not name:
            name = resolve_name(code)
        wl.append({"code": code, "name": name})
        msg = "追加しました: %s %s" % (code, name)
    if below or above:
        a = {"code": code}
        if below:
            a["below"] = float(below)
        if above:
            a["above"] = float(above)
        cfg.setdefault("price_alerts", []).append(a)
        msg += "（価格アラートも登録）"
    save_config(cfg)
    pushed = git_push_safe("chore: add %s via web" % code)
    return msg + pushed


def do_remove(code):
    code = normalize_code(code)
    cfg = load_config()
    before = len(cfg.get("watchlist", []))
    cfg["watchlist"] = [i for i in cfg.get("watchlist", []) if i["code"] != code]
    cfg["price_alerts"] = [a for a in cfg.get("price_alerts", []) if a.get("code") != code]
    if len(cfg["watchlist"]) == before:
        return "%s は見つかりませんでした。" % code
    save_config(cfg)
    pushed = git_push_safe("chore: remove %s via web" % code)
    return "削除しました: %s%s" % (code, pushed)


def git_push_safe(message):
    """push を試み、結果を短い文字列で返す（失敗してもアプリは落とさない）。"""
    try:
        git_push(message)
        return " → GitHubへ反映済み"
    except Exception:
        return " → ローカル更新のみ（push失敗。手動pushが必要かも）"


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------
def _e(s):
    return html.escape(str(s))


def render(message="", query="", results=None):
    cfg = load_config()
    wl = cfg.get("watchlist", [])
    alerts = cfg.get("price_alerts", [])

    rows = []
    for item in wl:
        code = item["code"]
        my = [a for a in alerts if a.get("code") == code]
        bits = []
        for a in my:
            if "below" in a:
                bits.append("≤%s" % a["below"])
            if "above" in a:
                bits.append("≥%s" % a["above"])
        alert_txt = " / ".join(bits) if bits else "—"
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td>"
            "<td><form method='post' action='/remove' style='margin:0'>"
            "<input type='hidden' name='code' value='%s'>"
            "<button class='del'>削除</button></form></td></tr>" % (
                _e(item.get("name", code)), _e(code), _e(alert_txt), _e(code)))

    result_html = ""
    if query:
        if results:
            cells = []
            for r in results:
                cells.append(
                    "<tr><td>%s</td><td>%s</td><td>%s</td>"
                    "<td><form method='post' action='/add' style='margin:0'>"
                    "<input type='hidden' name='code' value='%s'>"
                    "<input type='hidden' name='name' value='%s'>"
                    "<button>この銘柄を追加</button></form></td></tr>" % (
                        _e(r["name"]), _e(r["symbol"]), _e(r["exchange"]),
                        _e(r["symbol"]), _e(r["name"])))
            result_html = ("<h2>「%s」の検索結果</h2>"
                           "<table><tr><th>銘柄</th><th>コード</th><th>市場</th><th></th></tr>"
                           "%s</table>" % (_e(query), "".join(cells)))
        else:
            hint = ""
            if not query.isascii():
                hint = ("（日本語名は検索できないことがあります。"
                        "コード（例: <b>7203</b>）かローマ字（例: <b>Toyota</b>）でお試しください）")
            result_html = ("<p class='msg'>「%s」に一致する銘柄が見つかりませんでした。%s</p>"
                           % (_e(query), hint))

    msg_html = "<p class='msg'>%s</p>" % _e(message) if message else ""

    return """<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>監視銘柄の管理</title>
<style>
 body{{font-family:-apple-system,"Hiragino Sans",sans-serif;max-width:760px;
   margin:24px auto;padding:0 16px;color:#222;background:#fafafe}}
 h1{{font-size:22px}} h2{{font-size:17px;margin-top:28px}}
 .card{{background:#fff;border:1px solid #e0e0ea;border-radius:10px;padding:16px;margin:14px 0}}
 input[type=text],input[type=number]{{padding:8px;border:1px solid #ccc;border-radius:6px;font-size:15px}}
 button{{padding:8px 14px;border:none;border-radius:6px;background:#1a1a2e;color:#fff;
   font-size:14px;cursor:pointer}}
 button:hover{{background:#3a3a5e}} button.del{{background:#e94560;padding:5px 10px}}
 table{{border-collapse:collapse;width:100%;margin-top:8px}}
 th,td{{border:1px solid #e0e0ea;padding:8px;text-align:left;font-size:14px}}
 th{{background:#1a1a2e;color:#fff}}
 .msg{{background:#e8f5e9;border:1px solid #b6e0bd;padding:10px;border-radius:6px}}
 label{{font-size:13px;color:#555;display:block;margin:8px 0 2px}}
 .row{{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}}
</style></head><body>
<h1>📈 監視銘柄の管理</h1>
{msg}

<div class="card">
  <h2>① 会社名で検索（コードがわからない時）</h2>
  <form method="get" action="/" class="row">
    <div><label>会社名・キーワード</label>
      <input type="text" name="q" value="{q}" placeholder="例: トヨタ / Micron / 半導体" size="28" autofocus></div>
    <button>検索</button>
  </form>
  {results}
</div>

<div class="card">
  <h2>② コードを直接入力して追加</h2>
  <form method="post" action="/add" class="row">
    <div><label>コード（日本株は数字、米国株は英字。.T や ^ もOK）</label>
      <input type="text" name="code" placeholder="例: 7203 / AAPL / 285A.T" size="18" required></div>
    <div><label>名前（空欄なら自動取得）</label>
      <input type="text" name="name" placeholder="任意" size="16"></div>
    <div><label>以下で通知</label><input type="number" name="below" step="any" size="8"></div>
    <div><label>以上で通知</label><input type="number" name="above" step="any" size="8"></div>
    <button>追加</button>
  </form>
</div>

<div class="card">
  <h2>③ 現在の監視リスト（{n}銘柄）</h2>
  <table><tr><th>銘柄</th><th>コード</th><th>価格アラート</th><th></th></tr>
  {rows}</table>
</div>
<p style="color:#888;font-size:12px">※ 投資助言ではありません。発注は行いません。停止はターミナルで Ctrl+C。</p>
</body></html>""".format(
        msg=msg_html, q=_e(query), results=result_html,
        n=len(wl), rows="".join(rows))


# ----------------------------------------------------------------------
# HTTP ハンドラ
# ----------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, body, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _redirect(self, msg):
        self.send_response(303)
        self.send_header("Location", "/?msg=" + urllib.parse.quote(msg))
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        query = (params.get("q", [""])[0]).strip()
        message = (params.get("msg", [""])[0]).strip()
        results = search_symbols(query) if query else None
        self._send(render(message, query, results))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        data = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/add":
                msg = do_add(data.get("code", [""])[0].strip(),
                             data.get("name", [""])[0].strip(),
                             data.get("below", [""])[0].strip(),
                             data.get("above", [""])[0].strip())
            elif path == "/remove":
                msg = do_remove(data.get("code", [""])[0].strip())
            else:
                msg = "不明な操作です。"
        except Exception as e:
            msg = "エラー: %s" % e
        self._redirect(msg)

    def log_message(self, *args):
        pass  # アクセスログは出さない


def main():
    url = "http://%s:%d/" % (HOST, PORT)
    print("監視銘柄の管理ツールを起動しました → %s" % url)
    print("ブラウザを開きます。停止するには Ctrl+C。")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        HTTPServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")


if __name__ == "__main__":
    main()

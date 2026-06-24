#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
監視銘柄をかんたんに追加・削除するヘルパー。

config.json を直接いじらなくても、コマンド一発で watchlist を編集できます。
銘柄名は省略すると Yahoo Finance から自動取得します。
変更後はデフォルトで git に commit & push し、毎朝のクラウドメールにも反映します。

使い方:
  python3 add_stock.py 7203                 # コードだけ。名前は自動取得して追加
  python3 add_stock.py 7203 --name トヨタ    # 名前を指定して追加
  python3 add_stock.py 7203 --below 2500    # 同時に「2500円以下で通知」も登録
  python3 add_stock.py 9984 --above 12000   # 「12000円以上で通知」も登録
  python3 add_stock.py --remove 7203        # 監視から外す（価格アラートも削除）
  python3 add_stock.py --list               # 今の監視銘柄を一覧表示
  python3 add_stock.py 7203 --no-push       # ローカルだけ更新（pushしない）

発注は一切行いません。投資判断はご自身で。
"""

import os
import sys
import json
import argparse
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# 既存の signal_check の取得処理を再利用（外部ライブラリなし）
try:
    from signal_check import fetch_series
except Exception:
    fetch_series = None


# ----------------------------------------------------------------------
# config 読み書き
# ----------------------------------------------------------------------
def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ----------------------------------------------------------------------
# 銘柄名の自動取得
# ----------------------------------------------------------------------
def resolve_name(code):
    """Yahoo から銘柄名を取れたら返す。無理ならコードをそのまま返す。"""
    if fetch_series is None:
        return code
    try:
        _series, meta = fetch_series(code)
        for key in ("shortName", "longName", "symbol"):
            if meta.get(key):
                return str(meta[key])
    except Exception as e:
        print("名前の自動取得に失敗（コードで登録します）:", e, file=sys.stderr)
    return code


# ----------------------------------------------------------------------
# git 反映
# ----------------------------------------------------------------------
def git_push(message):
    """config.json を commit して push。失敗しても致命的にしない。"""
    try:
        subprocess.run(["git", "-C", BASE_DIR, "add", "config.json"], check=True)
        subprocess.run(["git", "-C", BASE_DIR, "commit", "-m", message], check=True)
        subprocess.run(["git", "-C", BASE_DIR, "push"], check=True)
        print("→ GitHub に反映しました（毎朝のメールにも反映されます）")
    except subprocess.CalledProcessError as e:
        print("git への反映に失敗しました:", e, file=sys.stderr)
        print("  ローカルの config.json は更新済みです。手動で push してください。",
              file=sys.stderr)


# ----------------------------------------------------------------------
# コマンド
# ----------------------------------------------------------------------
def cmd_list(cfg):
    wl = cfg.get("watchlist", [])
    alerts = cfg.get("price_alerts", [])
    if not wl:
        print("監視銘柄はまだありません。")
        return
    print("=== 監視銘柄 (%d) ===" % len(wl))
    for item in wl:
        code = item["code"]
        line = "  %-6s %s" % (code, item.get("name", code))
        my = [a for a in alerts if a.get("code") == code]
        if my:
            parts = []
            for a in my:
                if "below" in a:
                    parts.append("≤%s円で通知" % a["below"])
                if "above" in a:
                    parts.append("≥%s円で通知" % a["above"])
            line += "   [" + " / ".join(parts) + "]"
        print(line)


def normalize_code(code):
    """米国株ティッカー（英字）は大文字に統一。日本株（数字）はそのまま。"""
    code = code.strip()
    if "." not in code and not code.isdigit():
        return code.upper()
    return code


def cmd_add(cfg, code, name, below, above):
    code = normalize_code(code)
    wl = cfg.setdefault("watchlist", [])
    if any(item["code"] == code for item in wl):
        print("%s はすでに監視中です。" % code)
    else:
        if not name:
            name = resolve_name(code)
        wl.append({"code": code, "name": name})
        print("追加しました: %s %s" % (code, name))

    # 価格アラート
    if below is not None or above is not None:
        alert = {"code": code}
        if below is not None:
            alert["below"] = below
        if above is not None:
            alert["above"] = above
        cfg.setdefault("price_alerts", []).append(alert)
        bits = []
        if below is not None:
            bits.append("≤%s円" % below)
        if above is not None:
            bits.append("≥%s円" % above)
        print("  価格アラートを登録: " + " / ".join(bits))


def cmd_remove(cfg, code):
    code = normalize_code(code)
    wl = cfg.get("watchlist", [])
    before = len(wl)
    cfg["watchlist"] = [item for item in wl if item["code"] != code]
    cfg["price_alerts"] = [a for a in cfg.get("price_alerts", [])
                           if a.get("code") != code]
    if len(cfg["watchlist"]) == before:
        print("%s は監視リストにありませんでした。" % code)
        return False
    print("監視から外しました: %s" % code)
    return True


# ----------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="監視銘柄をかんたんに追加・削除する",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("code", nargs="?", help="銘柄コード（例: 7203。.T は不要）")
    p.add_argument("--name", help="銘柄名（省略時は自動取得）")
    p.add_argument("--below", type=float, help="この価格以下で通知")
    p.add_argument("--above", type=float, help="この価格以上で通知")
    p.add_argument("--remove", metavar="CODE", help="監視から外す")
    p.add_argument("--list", action="store_true", help="監視銘柄を一覧表示")
    p.add_argument("--no-push", action="store_true",
                   help="GitHub に push せずローカルだけ更新")
    args = p.parse_args()

    cfg = load_config()

    if args.list:
        cmd_list(cfg)
        return

    if args.remove:
        changed = cmd_remove(cfg, args.remove)
        if changed:
            save_config(cfg)
            if not args.no_push:
                git_push("chore: remove %s from watchlist" % args.remove)
        return

    if not args.code:
        p.print_help()
        return

    cmd_add(cfg, args.code, args.name, args.below, args.above)
    save_config(cfg)
    cmd_list(cfg)
    if not args.no_push:
        git_push("chore: add %s to watchlist" % args.code)


if __name__ == "__main__":
    main()

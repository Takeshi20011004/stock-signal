#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
毎朝のレポートメール送信（ローカル実行・SMTP）。

report.py / signal_check.py --test / backtest.py の3分析を実行し、
1通のメールにまとめて Gmail の SMTP で送信します。
launchd から毎朝8時に呼ばれる想定。

アプリパスワードはこのリポジトリが公開のため、コードには書きません。
環境変数 STOCK_MAIL_APP_PASSWORD から読み込みます（launchd の plist で渡す）。

  環境変数:
    STOCK_MAIL_APP_PASSWORD  … Gmail アプリパスワード（必須）
    STOCK_MAIL_USER          … 送信元/ログイン（既定: udonn123@gmail.com）
    STOCK_MAIL_TO            … 宛先（既定: 送信元と同じ）

発注は一切行いません。投資判断はご自身で。
"""

import os
import sys
import ssl
import smtplib
import datetime
import subprocess
from email.message import EmailMessage

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JST = datetime.timezone(datetime.timedelta(hours=9))

USER = os.environ.get("STOCK_MAIL_USER", "udonn123@gmail.com")
TO = os.environ.get("STOCK_MAIL_TO", USER)
APP_PASS = os.environ.get("STOCK_MAIL_APP_PASSWORD")

ANALYSES = [
    ("■ 現況サマリー", ["python3", "report.py", "--no-file"]),
    ("■ 本日のシグナル", ["python3", "signal_check.py", "--test"]),
    ("■ バックテスト", ["python3", "backtest.py"]),
]


def run_analysis(cmd):
    """分析スクリプトを実行し、標準出力+標準エラーをまとめて返す。失敗しても落とさない。"""
    try:
        proc = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True,
                              text=True, timeout=180)
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        if err:
            out = (out + "\n" + err).strip() if out else err
        return out or "(出力なし)"
    except Exception as e:
        return "実行に失敗しました: %s: %s" % (type(e).__name__, e)


def build_body(now):
    parts = ["株シグナル 朝レポート  %s" % now.strftime("%Y-%m-%d %H:%M"), ""]
    for title, cmd in ANALYSES:
        parts.append(title)
        parts.append("-" * 60)
        parts.append(run_analysis(cmd))
        parts.append("")
    parts.append("※ これは投資助言ではありません。発注は行いません。投資判断はご自身で。")
    return "\n".join(parts)


def main():
    if not APP_PASS:
        print("環境変数 STOCK_MAIL_APP_PASSWORD が未設定です。送信できません。",
              file=sys.stderr)
        sys.exit(1)

    now = datetime.datetime.now(JST)
    body = build_body(now)

    msg = EmailMessage()
    msg["Subject"] = "📈 株シグナル朝レポート %s" % now.strftime("%Y-%m-%d")
    msg["From"] = USER
    msg["To"] = TO
    msg.set_content(body)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(USER, APP_PASS)
            s.send_message(msg)
        print("送信完了: %s → %s (%s)" % (USER, TO, now.strftime("%Y-%m-%d %H:%M")))
    except Exception as e:
        print("送信に失敗: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/bin/bash
# 株シグナルチェックを実行するラッパー（launchd / 手動 両用）
cd "$(dirname "$0")" || exit 1
/usr/bin/python3 signal_check.py "$@" >> run.log 2>&1

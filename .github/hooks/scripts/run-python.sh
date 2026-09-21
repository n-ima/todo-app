#!/usr/bin/env bash
# Stop/SessionEnd フックの Python 起動ラッパ(D046)。
# Windows は `python`、素の Linux/macOS は `python3` しか無いことがあるため、
# 存在する方で実行する(どちらも無ければ何もしない=fail-open)。
# `python3 x || python x` 形式にしないのは、(1) remind-record が意図的に block を返した
# とき後段が stdin を失った状態で再実行される、(2) Windows の Microsoft Store スタブ
# python3.exe が誤検出される、の2つを避けるため(python を先に探す)。
if command -v python >/dev/null 2>&1; then exec python "$@"; fi
if command -v python3 >/dev/null 2>&1; then exec python3 "$@"; fi
exit 0

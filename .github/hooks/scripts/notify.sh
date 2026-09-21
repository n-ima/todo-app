#!/usr/bin/env bash
# Notification hook: 承認待ち等の通知が発生したらローカルで音を鳴らし、
# logs/notify.log に1行だけ記録する(時刻+通知種別のみ。本文は記録しない)。
# いかなる失敗でも exit 0 の fail-open(通知は利便機能であり作業を妨げない)。
input=$(cat 2>/dev/null) || input=""

kind=""
if command -v jq >/dev/null 2>&1; then
  kind=$(printf '%s' "$input" | jq -r '.notification_type // .hook_event_name // empty' 2>/dev/null)
fi
if [[ -z "$kind" ]]; then
  # grepフォールバックは出現順で拾うため、より具体的な notification_type を先に探す
  kind=$(printf '%s' "$input" | grep -oE '"notification_type"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
fi
if [[ -z "$kind" ]]; then
  kind=$(printf '%s' "$input" | grep -oE '"hook_event_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
fi
[[ -z "$kind" ]] && kind="Notification"

# 音を鳴らす(OS別。バックグラウンド実行でフックの応答を待たせない)
case "$(uname -s 2>/dev/null)" in
  Darwin)
    afplay /System/Library/Sounds/Glass.aiff >/dev/null 2>&1 & ;;
  MINGW*|MSYS*|CYGWIN*)
    powershell.exe -NoProfile -Command "[System.Media.SystemSounds]::Exclamation.Play()" >/dev/null 2>&1 & ;;
  *)
    printf '\a' >/dev/tty 2>/dev/null || printf '\a' ;;
esac

# ログ(ローカルのみ・gitignore対象)。失敗しても無視。
{ d="$(dirname "$0")/../logs" && mkdir -p "$d" &&
  printf '%s\t%s\n' "$(date +%Y-%m-%dT%H:%M:%S)" "$kind" >>"$d/notify.log"; } 2>/dev/null || true

exit 0

# Notification hook: 承認待ち等の通知が発生したらローカルで音を鳴らし、
# logs/notify.log に1行だけ記録する(時刻+通知種別のみ。本文は記録しない)。
# いかなる失敗でも exit 0 の fail-open(通知は利便機能であり作業を妨げない)。
$ErrorActionPreference = 'SilentlyContinue'
# stdin/stdout を UTF-8(BOMなし) に固定する(既定の CP932 では日本語直後のパターン照合と
# 出力 JSON の符号化が壊れる)。設定に失敗しても既定エンコーディングのまま続行する(fail-open)。
try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
$raw = [Console]::In.ReadToEnd()
# PS5.1 は InputEncoding 設定下のリダイレクト stdin 先頭に U+FEFF を現すことがあり、
# ConvertFrom-Json が常に失敗する(他ガードと同じ対処。W3/D052)
$raw = $raw.TrimStart([char]0xFEFF)
$kind = 'Notification'
try {
  $obj = $raw | ConvertFrom-Json
  if ($obj.notification_type) { $kind = [string]$obj.notification_type }
  elseif ($obj.hook_event_name) { $kind = [string]$obj.hook_event_name }
} catch {}

try { [System.Media.SystemSounds]::Exclamation.Play() } catch {}

# ログ(ローカルのみ・gitignore対象)。失敗しても無視。
try {
  $dir = Join-Path $PSScriptRoot '..\logs'
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $line = "{0}`t{1}" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $kind
  Add-Content -Path (Join-Path $dir 'notify.log') -Value $line -Encoding UTF8
} catch {}

exit 0

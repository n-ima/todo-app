# 判定ログ(Write-HookLog)の共通実装(_log.sh の PowerShell 鏡。python は _log.py)。
# 各ガード .ps1 は `. (Join-Path $PSScriptRoot '_log.ps1')` でドットソースし、従来どおり
# `Write-HookLog $decision $target` を呼ぶ(呼び出し側の書き換えなし)。無い環境では
# `if (-not (Get-Command Write-HookLog -ErrorAction SilentlyContinue)) { function Write-HookLog { } }`
# で無記録・無害(fail-open)。単体で実行されても何も出力せず正常終了する(CI の
# 「全 .ps1 フックの実機実行」ステップ対策。_paths.ps1 と同じ)。
#
# 書式(1 行 JSONL。欄の順序も固定。_log.py / _log.sh と同一):
#   {"ts","session_id","hook_event","tool_name","script","decision","target","tool_use_id","duration_ms","host"}
#   target は redaction(privacy-patterns.json(.github/harness))→ 先頭 120 字 → JSON エスケープ(改行は \n)。
#   duration_ms は本ファイルをドットソースした時点(=スクリプト開始の近似。powershell.exe 起動分は含まない)からの
#   Stopwatch 経過ミリ秒、host はペイロードの欄名(snake_case=claude-code / camelCase=copilot)→ 環境変数
#   CLAUDE_PROJECT_DIR → unknown の近似判定(R-09 / A2-7。欄は末尾に足し、旧行は欠落=null で読める)。
# 置き場: logs/hook-decisions.jsonl(gitignore 済)。HARNESS_HOOK_LOG_DIR で差し替え可(selftest 用)。
# 512KB 超は書き込み前に後半 256KB(行境界)だけ残す。
# session_id / hook_event / tool_name / tool_use_id は呼び出し側スクリプトの $obj(ConvertFrom-Json 済み)から、
# 無ければ $raw(生ペイロード)から正規表現で拾う(PowerShell の動的スコープで親スクリプトの変数を参照)。

$script:__hookLogName = 'hook-decisions.jsonl'
$script:__hookLogTargetMax = 120
$script:__hookLogPatterns = $null
$script:__hookLogStopwatch = [System.Diagnostics.Stopwatch]::StartNew()  # duration_ms の起点(ドットソース時点)

function Get-HookLogDir {
  if ($env:HARNESS_HOOK_LOG_DIR) { return $env:HARNESS_HOOK_LOG_DIR }
  return (Join-Path $PSScriptRoot '..\logs')
}

function Get-PrivacyPatterns {
  if ($null -ne $script:__hookLogPatterns) { return $script:__hookLogPatterns }
  $list = New-Object System.Collections.ArrayList
  $path = Join-Path $PSScriptRoot '..\..\harness\privacy-patterns.json'  # .github/harness/(2026-09-21 移動)
  if (Test-Path -LiteralPath $path) {
    $text = ''
    try { $text = [IO.File]::ReadAllText($path, (New-Object System.Text.UTF8Encoding $false)) } catch {}
    $parsed = $false
    try {
      $data = $text | ConvertFrom-Json
      foreach ($e in @($data.redact)) {
        if ($null -eq $e -or -not ($e.pattern -is [string])) { continue }
        $rep = if ($null -ne $e.replace) { [string]$e.replace } else { '[REDACTED]' }
        $ic = [bool]$e.ignore_case
        [void]$list.Add(@{ p = [string]$e.pattern; r = $rep; ic = $ic })
      }
      $parsed = $true
    } catch {}
    if (-not $parsed) {
      # JSON として読めなければ行単位で pattern / replace / ignore_case を拾う(_log.sh と同じ規則)
      foreach ($line in ($text -split "`n")) {
        $m = [regex]::Match($line, '"pattern"\s*:\s*"((?:[^"\\]|\\.)*)"')
        if (-not $m.Success) { continue }
        $p = $m.Groups[1].Value -replace '\\"', '"' -replace '\\\\', '\'
        $rep = '[REDACTED]'
        $mr = [regex]::Match($line, '"replace"\s*:\s*"((?:[^"\\]|\\.)*)"')
        if ($mr.Success) { $rep = $mr.Groups[1].Value -replace '\\"', '"' -replace '\\\\', '\' }
        $ic = [bool]($line -match '"ignore_case"\s*:\s*true')
        [void]$list.Add(@{ p = $p; r = $rep; ic = $ic })
      }
    }
  }
  $script:__hookLogPatterns = $list
  return $list
}

# 秘密値らしき文字列を [REDACTED] に置換する(順序は privacy-patterns.json(.github/harness) のとおり)
function Protect-HookLogText([string]$s) {
  if ($null -eq $s) { return '' }
  foreach ($e in @(Get-PrivacyPatterns)) {
    try {
      $opt = if ($e.ic) { [System.Text.RegularExpressions.RegexOptions]::IgnoreCase } else { [System.Text.RegularExpressions.RegexOptions]::None }
      $rep = [string]$e.r -replace '\$', '$$'
      $s = [regex]::Replace($s, [string]$e.p, $rep, $opt)
    } catch {}
  }
  return $s
}

# redaction → 先頭 120 字(切り詰めは redaction の後: 境界で切れた秘密値の前半を残さない)
function ConvertTo-HookLogTarget([string]$t) {
  $s = Protect-HookLogText ([string]$t)
  if ($s.Length -gt $script:__hookLogTargetMax) { $s = $s.Substring(0, $script:__hookLogTargetMax) }
  return $s
}

function ConvertTo-HookLogJsonString($v) {
  if ($null -eq $v -or ([string]$v) -eq '') { return 'null' }
  $s = [string]$v
  $s = $s.Replace('\', '\\').Replace('"', '\"').Replace("`n", '\n').Replace("`r", '\r').Replace("`t", '\t')
  $s = [regex]::Replace($s, '[\x00-\x1f]', ' ')
  return '"' + $s + '"'
}

function Get-HookLogField([string]$name) {
  # 呼び出し側スクリプトの $obj(解析済み)→ $raw(生ペイロード)の順に探す(動的スコープ)
  $o = $null
  try { $o = Get-Variable -Name obj -ValueOnly -ErrorAction SilentlyContinue } catch {}
  if ($null -ne $o) {
    try {
      $v = $o.$name
      if ($null -eq $v -and $name -eq 'tool_name') { $v = $o.toolName }
      if ($null -ne $v -and ([string]$v) -ne '') { return [string]$v }
    } catch {}
  }
  $r = $null
  try { $r = Get-Variable -Name raw -ValueOnly -ErrorAction SilentlyContinue } catch {}
  if ($r -is [string] -and $r) {
    $m = [regex]::Match($r, '"' + [regex]::Escape($name) + '"\s*:\s*"([^"\\]*)"')
    if ($m.Success) { return $m.Groups[1].Value }
  }
  return $null
}

function Get-HookLogHost {
  # ホストの近似判定(_log.py detect_host / _log.sh _log_detect_host と同じ規則): $obj(解析済み)の欄名 → $raw の正規表現 →
  # 環境変数 CLAUDE_PROJECT_DIR → unknown。COPILOT_CLI(Copilot CLI 1.0.86 が立てる。同 CLI は Claude 互換の snake_case
  # ペイロードと CLAUDE_PROJECT_DIR を渡す)を最優先(2026-09-21 実測)
  if ($env:COPILOT_CLI) { return 'copilot' }
  $o = $null
  try { $o = Get-Variable -Name obj -ValueOnly -ErrorAction SilentlyContinue } catch {}
  if ($null -ne $o) {
    $names = @()
    try { $names = @($o.PSObject.Properties.Name) } catch {}
    foreach ($k in @('hook_event_name', 'session_id', 'tool_use_id', 'transcript_path')) { if ($names -contains $k) { return 'claude-code' } }
    foreach ($k in @('hookEventName', 'sessionId', 'toolName', 'transcriptPath')) { if ($names -contains $k) { return 'copilot' } }
  }
  $r = $null
  try { $r = Get-Variable -Name raw -ValueOnly -ErrorAction SilentlyContinue } catch {}
  if ($r -is [string] -and $r) {
    if ($r -match '"(hook_event_name|session_id|tool_use_id|transcript_path)"\s*:') { return 'claude-code' }
    if ($r -match '"(hookEventName|sessionId|toolName|transcriptPath)"\s*:') { return 'copilot' }
  }
  if ($env:CLAUDE_PROJECT_DIR) { return 'claude-code' }
  return 'unknown'
}

function Write-HookLog($decision, $target) {
  try {
    $dir = Get-HookLogDir
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $log = Join-Path $dir $script:__hookLogName
    if ((Test-Path -LiteralPath $log) -and ((Get-Item -LiteralPath $log).Length -gt 524288)) {
      $bytes = [System.IO.File]::ReadAllBytes($log)
      $start = $bytes.Length - 262144
      $nl = [Array]::IndexOf($bytes, [byte]10, $start)
      if ($nl -ge 0 -and $nl -lt ($bytes.Length - 1)) { $start = $nl + 1 }
      $keep = New-Object byte[] ($bytes.Length - $start)
      [Array]::Copy($bytes, $start, $keep, 0, $keep.Length)
      [System.IO.File]::WriteAllBytes($log, $keep)
    }
    $ts = Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'
    $caller = $null
    try { $caller = $MyInvocation.ScriptName } catch {}
    if (-not $caller) { $caller = $PSCommandPath }
    $scriptName = if ($env:HOOK_LOG_SCRIPT) { $env:HOOK_LOG_SCRIPT } elseif ($caller) { Split-Path $caller -Leaf } else { 'powershell' }
    $line = '{"ts":"' + $ts + '"' +
      ',"session_id":' + (ConvertTo-HookLogJsonString (Get-HookLogField 'session_id')) +
      ',"hook_event":' + (ConvertTo-HookLogJsonString (Get-HookLogField 'hook_event_name')) +
      ',"tool_name":' + (ConvertTo-HookLogJsonString (Get-HookLogField 'tool_name')) +
      ',"script":' + (ConvertTo-HookLogJsonString $scriptName) +
      ',"decision":' + (ConvertTo-HookLogJsonString ([string]$decision)) +
      ',"target":' + (ConvertTo-HookLogJsonString (ConvertTo-HookLogTarget ([string]$target))) +
      ',"tool_use_id":' + (ConvertTo-HookLogJsonString (Get-HookLogField 'tool_use_id')) +
      ',"duration_ms":' + ([string][int64]$script:__hookLogStopwatch.ElapsedMilliseconds) +
      ',"host":"' + (Get-HookLogHost) + '"}'
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::AppendAllText($log, $line + "`n", $utf8)
  } catch {}
}

# ---- ゲート遷移ログ(gate-transitions.jsonl)と session.lock(状態機械の堅牢化。再監査 2026-08-31 §6 A7-M-3 /
#      codex R-04 / IA-20260831-15。仕様の正は .github/harness/STATE-MACHINE.md。_log.sh / _log.py と同一の書式・規則) ----
# 書式(1 行 JSONL。欄の順序固定):
#   {"ts","rev","session_id","hook_event","tool_name","script","source","before","after","changed","loops"}
# 呼び出し側は compact 形('requirements=done,design=in_progress,…')で渡す。
$script:__gateLogName = 'gate-transitions.jsonl'
$script:__sessionLockName = 'session.lock'
$script:__gatePhases = @('requirements', 'design', 'implementation', 'test', 'release')

# progress.md から現在の状態を読む → @{ state = compact(正準順・初出優先。ブロックが無ければ ''); loops = GATE_COUNTERS の値(無ければ $null) }
# 値は最初のトークン(後ろの注記は落とす)。英数字と _ - 以外は _ に置換(_log.sh の gate_file_state_into と同一)
function Get-GateFileState([string]$path) {
  $res = @{ state = ''; loops = $null }
  try {
    if (-not (Test-Path -LiteralPath $path)) { return $res }
    $text = [IO.File]::ReadAllText($path, (New-Object System.Text.UTF8Encoding $false))
    $vals = @{}
    $bm = [regex]::Match($text, '(?s)<!--[ \t]*GATE_STATUS[ \t]*(?:-->|\r?\n)(.*?)-->')
    if ($bm.Success) {
      foreach ($ln in ($bm.Groups[1].Value -split "`n")) {
        $m = [regex]::Match($ln.TrimEnd("`r"), '^[ \t]*(requirements|design|implementation|test|release)[ \t]*:[ \t]*([^ \t\r\n]*)')
        if ($m.Success -and -not $vals.ContainsKey($m.Groups[1].Value)) {
          $vals[$m.Groups[1].Value] = ($m.Groups[2].Value -replace '[^A-Za-z0-9_-]', '_')
        }
      }
    }
    $parts = @()
    foreach ($k in $script:__gatePhases) { if ($vals.ContainsKey($k) -and $vals[$k]) { $parts += ($k + '=' + $vals[$k]) } }
    $res.state = ($parts -join ',')
    $cm = [regex]::Match($text, '(?s)<!--[ \t]*GATE_COUNTERS[ \t]*(?:-->|\r?\n)(.*?)-->')
    if ($cm.Success) {
      $lm = [regex]::Match($cm.Groups[1].Value, '(?m)^[ \t]*implement_test_loops[ \t]*:[ \t]*(\d+)')
      if ($lm.Success) { $res.loops = [int]$lm.Groups[1].Value }
    }
  } catch {}
  return $res
}

function ConvertFrom-GateCompact([string]$s) {
  $o = [ordered]@{}
  if (-not $s) { return $o }
  foreach ($pair in ($s -split ',')) {
    $i = $pair.IndexOf('=')
    if ($i -gt 0) { $o[$pair.Substring(0, $i).Trim()] = $pair.Substring($i + 1).Trim() }
  }
  return $o
}

function ConvertTo-GateJsonObject([string]$s) {
  if (-not $s) { return 'null' }
  $parts = @()
  foreach ($pair in ($s -split ',')) {
    $i = $pair.IndexOf('=')
    if ($i -le 0) { continue }
    $k = $pair.Substring(0, $i).Trim() -replace '[^A-Za-z0-9_-]', '_'
    $v = $pair.Substring($i + 1).Trim() -replace '[^A-Za-z0-9_-]', '_'
    if ($k) { $parts += ('"' + $k + '":"' + $v + '"') }
  }
  return ('{' + ($parts -join ',') + '}')
}

# 差分 "phase:前->後,…"(after の順。before に無い phase は none)
function Get-GateDiff([string]$before, [string]$after) {
  $b = ConvertFrom-GateCompact $before
  $a = ConvertFrom-GateCompact $after
  $out = @()
  foreach ($k in $a.Keys) {
    $bv = if ($b.Contains($k)) { [string]$b[$k] } else { '' }
    if ($bv -ne [string]$a[$k]) { $out += ($k + ':' + $(if ($bv) { $bv } else { 'none' }) + '->' + [string]$a[$k]) }
  }
  return ($out -join ',')
}

# 最終の遷移記録 → @{ after = compact; rev = 通番 }(記録が無ければ after '' / rev 0)
function Get-GateLast {
  $res = @{ after = ''; rev = 0 }
  try {
    $log = Join-Path (Get-HookLogDir) $script:__gateLogName
    if (-not (Test-Path -LiteralPath $log)) { return $res }
    $lines = @([IO.File]::ReadAllLines($log, (New-Object System.Text.UTF8Encoding $false)) | Where-Object { $_.Trim() })
    if ($lines.Count -eq 0) { return $res }
    $last = [string]$lines[-1]
    $rm = [regex]::Match($last, '"rev":(\d+)')
    if ($rm.Success) { $res.rev = [int]$rm.Groups[1].Value }
    $am = [regex]::Match($last, '"after":\{([^}]*)\}')
    if ($am.Success) {
      $parts = @()
      foreach ($pm in [regex]::Matches($am.Groups[1].Value, '"([A-Za-z_]+)":"([^"]*)"')) { $parts += ($pm.Groups[1].Value + '=' + $pm.Groups[2].Value) }
      $res.after = ($parts -join ',')
    }
  } catch {}
  return $res
}

function Write-GateLog([string]$before, [string]$after, $loops, [string]$source) {
  try {
    $dir = Get-HookLogDir
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $log = Join-Path $dir $script:__gateLogName
    if ((Test-Path -LiteralPath $log) -and ((Get-Item -LiteralPath $log).Length -gt 524288)) {
      $bytes = [System.IO.File]::ReadAllBytes($log)
      $start = $bytes.Length - 262144
      $nl = [Array]::IndexOf($bytes, [byte]10, $start)
      if ($nl -ge 0 -and $nl -lt ($bytes.Length - 1)) { $start = $nl + 1 }
      $keep = New-Object byte[] ($bytes.Length - $start)
      [Array]::Copy($bytes, $start, $keep, 0, $keep.Length)
      [System.IO.File]::WriteAllBytes($log, $keep)
    }
    $last = Get-GateLast
    $rev = [int]$last.rev + 1
    $ts = Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'
    $caller = $null
    try { $caller = $MyInvocation.ScriptName } catch {}
    if (-not $caller) { $caller = $PSCommandPath }
    $scriptName = if ($env:HOOK_LOG_SCRIPT) { $env:HOOK_LOG_SCRIPT } elseif ($caller) { Split-Path $caller -Leaf } else { 'powershell' }
    $loopsJson = 'null'
    if ($null -ne $loops -and ([string]$loops) -match '^\d+$') { $loopsJson = [string]([int]$loops) }
    if (-not $source) { $source = 'hook' }
    $chg = Get-GateDiff $before $after
    $chgJson = if ($chg) { ConvertTo-HookLogJsonString $chg } else { '""' }
    $line = '{"ts":"' + $ts + '"' +
      ',"rev":' + $rev +
      ',"session_id":' + (ConvertTo-HookLogJsonString (Get-HookLogField 'session_id')) +
      ',"hook_event":' + (ConvertTo-HookLogJsonString (Get-HookLogField 'hook_event_name')) +
      ',"tool_name":' + (ConvertTo-HookLogJsonString (Get-HookLogField 'tool_name')) +
      ',"script":' + (ConvertTo-HookLogJsonString $scriptName) +
      ',"source":' + (ConvertTo-HookLogJsonString $source) +
      ',"before":' + (ConvertTo-GateJsonObject $before) +
      ',"after":' + (ConvertTo-GateJsonObject $after) +
      ',"changed":' + $chgJson +
      ',"loops":' + $loopsJson + '}'
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::AppendAllText($log, $line + "`n", $utf8)
  } catch {}
}

# session.lock({"session_id","pid","ts","epoch","cwd"})を読む → @{ sid; epoch; ageMin } か $null
function Read-SessionLock {
  try {
    $f = Join-Path (Get-HookLogDir) $script:__sessionLockName
    if (-not (Test-Path -LiteralPath $f)) { return $null }
    $text = [IO.File]::ReadAllText($f, (New-Object System.Text.UTF8Encoding $false))
    if ($text.Length -gt 4096) { $text = $text.Substring(0, 4096) }
    $sm = [regex]::Match($text, '"session_id":"([^"]*)"')
    $em = [regex]::Match($text, '"epoch":(\d+)')
    if (-not ($sm.Success -and $em.Success)) { return $null }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $age = [int][math]::Floor(($now - [int64]$em.Groups[1].Value) / 60)
    if ($age -lt 0) { $age = 0 }
    return @{ sid = $sm.Groups[1].Value; epoch = [int64]$em.Groups[1].Value; ageMin = $age }
  } catch { return $null }
}

# stale 閾値(分): HARNESS_SESSION_LOCK_STALE_MIN > tools/usage-config.json の session_lock_stale_minutes > 120
function Get-SessionLockStaleMin {
  $v = [string]$env:HARNESS_SESSION_LOCK_STALE_MIN
  if ($v -notmatch '^\d+$') {
    $v = ''
    try {
      if (Test-Path -LiteralPath 'tools/usage-config.json') {
        $m = [regex]::Match([IO.File]::ReadAllText('tools/usage-config.json'), '"session_lock_stale_minutes"\s*:\s*(\d+)')
        if ($m.Success) { $v = $m.Groups[1].Value }
      }
    } catch {}
  }
  if ($v -match '^\d+$') { return [int]$v }
  return 120
}

# 別セッションの新しい(stale でない)lock があれば @{ sid; ageMin }、無ければ $null(自分の session_id が空なら判定しない)
function Test-SessionLockOther([string]$sessionId) {
  if (-not $sessionId) { return $null }
  $lk = Read-SessionLock
  if ($null -eq $lk) { return $null }
  if ($lk.sid -eq $sessionId) { return $null }
  if ($lk.ageMin -ge (Get-SessionLockStaleMin)) { return $null }
  return $lk
}

# 自セッションの lock を書く(一時ファイル→rename。書けなければ無視)
function Write-SessionLock([string]$sessionId) {
  try {
    if (-not $sessionId) { return }
    $dir = Get-HookLogDir
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $f = Join-Path $dir $script:__sessionLockName
    $sid = $sessionId -replace '[^A-Za-z0-9_-]', '_'
    $ts = Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'
    $epoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $line = '{"session_id":"' + $sid + '","pid":' + $PID + ',"ts":"' + $ts + '","epoch":' + $epoch + ',"cwd":' + (ConvertTo-HookLogJsonString ((Get-Location).Path)) + '}'
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [IO.File]::WriteAllText(($f + '.tmp'), $line + "`n", $utf8)
    # 既存ファイルの置換は File.Replace(バックアップ無し=[NullString]。$null は空文字に変換され失敗する実測)。失敗時は Move-Item -Force
    if (Test-Path -LiteralPath $f) {
      try { [IO.File]::Replace(($f + '.tmp'), $f, [NullString]::Value) } catch { Move-Item -LiteralPath ($f + '.tmp') -Destination $f -Force }
    } else { [IO.File]::Move(($f + '.tmp'), $f) }
  } catch {}
}

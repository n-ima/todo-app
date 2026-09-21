# PostToolUse hook: 承認済み(done)のフェーズ文書が編集されたら、後続フェーズとの
# 整合確認を促す非ブロッキングの警告を出す(手動編集自体は妨げない)。
$ErrorActionPreference = 'SilentlyContinue'
# stdin/stdout を UTF-8(BOMなし) に固定する(既定の CP932 では日本語直後のパターン照合と
# 出力 JSON の符号化が壊れる)。設定に失敗しても既定エンコーディングのまま続行する(fail-open)。
try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
$raw = [Console]::In.ReadToEnd()
# PowerShell 5.1 はリダイレクトされた stdin の先頭に U+FEFF を現すことがあり(InputEncoding
# 設定時の実測)、そのままだと ConvertFrom-Json が失敗して常に grep 相当のフォールバック
# 判定に落ちる(第2回監査で検出。watchdog-continue.py の lstrip と同じ対処で先頭 BOM を除去)。
$raw = $raw.TrimStart([char]0xFEFF)
# 共通ライブラリ(D072。無ければ従来の単一フィールド判定だけで動く=fail-open)
try { $__paths = Join-Path $PSScriptRoot '_paths.ps1'; if (Test-Path $__paths) { . $__paths } } catch {}
# 判定ログの共通実装(_log.ps1 → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
try { $__log = Join-Path $PSScriptRoot '_log.ps1'; if (Test-Path $__log) { . $__log } } catch {}
if (-not (Get-Command Write-HookLog -ErrorAction SilentlyContinue)) { function Write-HookLog($decision, $target) { } }
$file = $null
$obj = $null
try {
  $obj = $raw | ConvertFrom-Json
  $file = $obj.tool_input.file_path
  if (-not $file) { $file = $obj.tool_input.filePath }
  if (-not $file) { $file = $obj.tool_input.path }
} catch {
  if ($raw -match '"(file_path|filePath|path)"\s*:\s*"([^"]*)"') {
    $file = $Matches[2]
  }
}
# 読み取り系ツールは対象外(CP-1 / D059。warn-stale-gate.sh と同一規則)
$toolName = $null
if (Get-Command Get-ToolName -ErrorAction SilentlyContinue) { $toolName = Get-ToolName $obj }
if ((Get-Command Test-ReadOnlyTool -ErrorAction SilentlyContinue) -and (Test-ReadOnlyTool ([string]$toolName))) {
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}
# 一括編集/パッチ系・uri 経由(第5回・D072): 候補の中にフェーズ配下の実体文書があればそれを対象にする
if ($obj -and (Get-Command Get-PathCandidates -ErrorAction SilentlyContinue)) {
  foreach ($c in @(Get-PathCandidates $obj 'auto')) {
    if (($c -match '(^|/)docs/0[1-5]-[a-z]+/') -and ($c -notlike '*_template.md')) { $file = $c; break }
  }
}

$progress = "docs/00-overview/progress.md"
if (-not $file -or -not (Test-Path $progress)) {
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}

# GATE_STATUS の完全性・順序矛盾(状態機械の堅牢化。再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15。
# 規則の正は tools/gate_status.py と .github/harness/STATE-MACHINE.md、ここは PowerShell の鏡(.sh の gate_integrity_msg と
# 同一判定): progress.md 自身が書かれた直後に実ファイルを読み、5 キーの欠落・重複・未知のキー・読めない行・語彙外の値
# (値は最初のトークン。注記は許容)・GATE_COUNTERS の不正(完全性)と、後続が着手済みなのに先行が not_started(規則 A)・
# 後続が done なのに先行が done でない(規則 B。「状態: 運用中」の注記があれば免除)を非ブロッキングの 1 行で警告する。
function Get-GateIntegrityMessage([string]$path) {
  try {
    $phases = @('requirements', 'design', 'implementation', 'test', 'release')
    $values = @('not_started', 'in_progress', 'pending_approval', 'done')
    $text = [IO.File]::ReadAllText($path, (New-Object System.Text.UTF8Encoding $false))
    $opnote = ($text -match '状態: 運用中')
    $bm = [regex]::Match($text, '(?s)<!--[ \t]*GATE_STATUS[ \t]*(?:-->|\r?\n)(.*?)-->')
    if (-not $bm.Success) { return "GATE_STATUS ブロックがありません(docs/00-overview/progress_template.md の形式に戻す。復旧は python tools/gate_status.py recover)。" }
    $vals = @{}; $dup = @(); $unknown = @(); $junk = @(); $bad = @(); $cbad = @(); $msg = ''
    $lineRe = '^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*:[ \t]*([^ \t\r\n]*)'
    foreach ($ln in ($bm.Groups[1].Value -split "`n")) {
      $l = $ln.TrimEnd("`r")
      if (-not $l.Trim()) { continue }
      $m = [regex]::Match($l, $lineRe)
      if (-not $m.Success) { $junk += ("'" + $l.Trim().Substring(0, [math]::Min(20, $l.Trim().Length)) + "'"); continue }
      $k = $m.Groups[1].Value; $v = $m.Groups[2].Value
      if ($phases -notcontains $k) { $unknown += $k; continue }
      if ($vals.ContainsKey($k)) { $dup += $k; continue }
      if ($values -notcontains $v) { $bad += ($k + '=' + $(if ($v) { $v } else { '(空)' })) }
      $vals[$k] = $v
    }
    $cm = [regex]::Match($text, '(?s)<!--[ \t]*GATE_COUNTERS[ \t]*(?:-->|\r?\n)(.*?)-->')
    if ($cm.Success) {
      foreach ($ln in ($cm.Groups[1].Value -split "`n")) {
        $l = $ln.TrimEnd("`r")
        if (-not $l.Trim()) { continue }
        $m = [regex]::Match($l, $lineRe)
        if (-not $m.Success) { $cbad += ("読めない行 '" + $l.Trim().Substring(0, [math]::Min(20, $l.Trim().Length)) + "'"); continue }
        if ($m.Groups[1].Value -ne 'implement_test_loops') { $cbad += ('未知のキー ' + $m.Groups[1].Value) }
        elseif ($m.Groups[2].Value -notmatch '^\d+$') { $cbad += ($m.Groups[1].Value + '=' + $(if ($m.Groups[2].Value) { $m.Groups[2].Value } else { '(空)' }) + ' が非負整数でない') }
      }
    }
    $missing = @($phases | Where-Object { -not $vals.ContainsKey($_) })
    if ($missing.Count -gt 0) { $msg += ('キー欠落: ' + ($missing -join ' ') + ' ') }
    if ($dup.Count -gt 0) { $msg += ('キー重複: ' + ($dup -join ' ') + ' ') }
    if ($unknown.Count -gt 0) { $msg += ('未知のキー: ' + ($unknown -join ' ') + ' ') }
    if ($junk.Count -gt 0) { $msg += ('読めない行: ' + ($junk -join ' ') + ' ') }
    if ($bad.Count -gt 0) { $msg += ('語彙外の値: ' + ($bad -join ' ') + ' (値は not_started/in_progress/pending_approval/done。注記は値の後ろに空白区切りで)') }
    if ($cbad.Count -gt 0) { $msg += ('GATE_COUNTERS: ' + ($cbad -join ' ') + ' ') }
    if (-not $msg) {
      $ord = ''
      for ($j = 0; $j -lt 5; $j++) {
        $later = $phases[$j]; $vj = [string]$vals[$later]
        for ($i = 0; $i -lt $j; $i++) {
          $earlier = $phases[$i]; $vi = [string]$vals[$earlier]
          if ($vj -ne 'not_started' -and $vi -eq 'not_started') { $ord += ($later + ': ' + $vj + ' なのに先行の ' + $earlier + ' が not_started(規則 A)。') }
          elseif ($vj -eq 'done' -and $vi -ne 'done' -and -not $opnote) { $ord += ($later + ': done なのに先行の ' + $earlier + ' が ' + $vi + '(規則 B。改修サイクルなら progress.md に「状態: 運用中」の注記を置く)。') }
        }
      }
      if ($ord) { $msg = '順序矛盾: ' + $ord }
    }
    if ($msg) { return ('GATE_STATUS の完全性検査: ' + $msg + '（正は .github/harness/STATE-MACHINE.md。python tools/gate_status.py check で再確認、壊れていれば recover）') }
  } catch {}
  return $null
}
$normLint = $file -replace '\\', '/' -replace '/{2,}', '/'
if ($normLint -like '*docs/00-overview/progress.md') {
  $gmsg = Get-GateIntegrityMessage $progress
  if ($gmsg) {
    Write-HookLog 'warn' "$file gate-integrity"
    $out = @{ continue = $true; systemMessage = $gmsg; hookSpecificOutput = @{ hookEventName = "PostToolUse"; additionalContext = $gmsg } }
    $out | ConvertTo-Json -Depth 5 -Compress
  } else {
    @{ continue = $true } | ConvertTo-Json -Compress
  }
  exit 0
}

# 対象はフェーズ配下の実体文書すべて(warn-stale-gate.sh と同一判定。D046)
$normalized = $file -replace '\\', '/'
$phase = $null
if ($normalized -notlike '*_template.md') {
  if ($normalized -like '*docs/01-requirements/*') { $phase = 'requirements' }
  elseif ($normalized -like '*docs/02-design/*') { $phase = 'design' }
  elseif ($normalized -like '*docs/03-implementation/*') { $phase = 'implementation' }
  elseif ($normalized -like '*docs/04-test/*') { $phase = 'test' }
  elseif ($normalized -like '*docs/05-release/*') { $phase = 'release' }
}

if (-not $phase) {
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}

$progressText = Get-Content -Raw $progress
$status = $null
if ($progressText -match "(?m)^$([regex]::Escape($phase)):\s*(\S+)") {
  $status = $Matches[1]
}

if ($status -eq "done") {
  Write-HookLog 'warn' $file
  # systemMessage(ユーザー向け)に加えて hookSpecificOutput.additionalContext にも併記し、
  # モデルにも同じ警告が届くようにする(第2回監査の出力契約統一)。
  $msg = "この文書($phase)は承認済み(done)ですが編集されました。後続フェーズとの整合を確認してください(必要ならdocs/00-overview/progress.mdのGATE_STATUSも見直してください)。"
  $out = @{
    continue = $true
    systemMessage = $msg
    hookSpecificOutput = @{
      hookEventName = "PostToolUse"
      additionalContext = $msg
    }
  }
} else {
  $out = @{ continue = $true }
}
$out | ConvertTo-Json -Depth 5 -Compress

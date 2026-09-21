# PreToolUse hook (Edit|Write|MultiEdit|NotebookEdit): evidence-gated deny(A2-4b。再監査 2026-09-09 RG-13。
# guard-done-evidence.sh と同一判定。selftest 両系で固定)。
# progress.md の done 遷移、または tasks.md への完了マーク [x]/[X] の追加を含む書込は、その書込の
# 新内容(追加・変更される行)に証拠 3 点セット(再実行可能なコマンド / 出力の要約 / YYYY-MM-DD HH:MM)が
# 揃っていなければ deny する。判定は書込の差分(Edit: new_string − old_string の行、Write: content −
# ディスクの現ファイルの行)に対して行い、done / [x] の「追加」は新旧の件数比較で判定する。
# 役割分担: warn-gate-tamper(PostToolUse・警告)は承認・独立レビュー記録・形式リント、本フックは
# 証拠の書式だけ。対象はエージェントのツール呼出だけ(人間の直接編集は対象外)。fail-open。
$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
$raw = [Console]::In.ReadToEnd()
$raw = $raw.TrimStart([char]0xFEFF)
try { $__paths = Join-Path $PSScriptRoot '_paths.ps1'; if (Test-Path $__paths) { . $__paths } } catch {}

# 判定ログの共通実装(_log.ps1 → logs/hook-decisions.jsonl。A6-20 / D085)。無ければ無記録で動く(fail-open)
try { $__log = Join-Path $PSScriptRoot '_log.ps1'; if (Test-Path $__log) { . $__log } } catch {}
if (-not (Get-Command Write-HookLog -ErrorAction SilentlyContinue)) { function Write-HookLog($decision, $target) { } }
function Allow-Exit { @{ continue = $true } | ConvertTo-Json -Compress; exit 0 }

$obj = $null
try { $obj = $raw | ConvertFrom-Json } catch { Allow-Exit }
if ($null -eq $obj -or $null -eq $obj.tool_input) { Allow-Exit }

# 読み取り系ツールの除外 (CP-1 / D059)
$toolName = $null
if (Get-Command Get-ToolName -ErrorAction SilentlyContinue) { $toolName = Get-ToolName $obj }
if ((Get-Command Test-ReadOnlyTool -ErrorAction SilentlyContinue) -and (Test-ReadOnlyTool ([string]$toolName))) { Allow-Exit }

# 対象パス(progress.md / tasks.md)
$cands = @()
if (Get-Command Get-PathCandidates -ErrorAction SilentlyContinue) { $cands = @(Get-PathCandidates $obj 'auto') }
else {
  $f = $obj.tool_input.file_path; if (-not $f) { $f = $obj.tool_input.filePath }; if (-not $f) { $f = $obj.tool_input.path }
  if ($f) { $cands = @(([string]$f) -replace '\\', '/') }
}
$target = $null; $kind = $null
foreach ($c in $cands) {
  if (-not $c) { continue }
  if ($c -like '*docs/00-overview/progress.md') { $target = $c; $kind = 'progress'; break }
  if ($c -like '*docs/03-implementation/tasks.md') { $target = $c; $kind = 'tasks'; break }
}
if (-not $target) { Allow-Exit }

# 新内容と旧内容
$newText = ''
if (Get-Command Get-NewText -ErrorAction SilentlyContinue) { $newText = [string](Get-NewText $obj) }
if (-not ($newText -replace '\s', '')) { Allow-Exit }
$oldParts = @(); $hasOld = $false
try {
  if ($null -ne $obj.tool_input.old_string) { $oldParts += [string]$obj.tool_input.old_string; $hasOld = $true }
  if ($null -ne $obj.tool_input.old_str) { $oldParts += [string]$obj.tool_input.old_str; $hasOld = $true }  # Copilot CLI の Edit(2026-09-21 実測)
  foreach ($e in @($obj.tool_input.edits)) { if ($e -and ($null -ne $e.old_string)) { $oldParts += [string]$e.old_string; $hasOld = $true } }
} catch {}
$oldText = ($oldParts -join "`n")
if (-not $hasOld) {
  $disk = $null
  if (Test-Path -LiteralPath $target -PathType Leaf) { $disk = $target }
  elseif ($kind -eq 'progress' -and (Test-Path 'docs/00-overview/progress.md')) { $disk = 'docs/00-overview/progress.md' }
  elseif ($kind -eq 'tasks' -and (Test-Path 'docs/03-implementation/tasks.md')) { $disk = 'docs/03-implementation/tasks.md' }
  if ($disk) { try { $oldText = [IO.File]::ReadAllText($disk); $hasOld = $true } catch {} }
}

# done / [x] の「追加」判定(新旧の件数比較)
function Count-Marks([string]$t) { if (-not $t) { return 0 }; return ([regex]::Matches($t, '\[[xX]\]')).Count }
function Phases-Done([string]$t) {
  $set = @{}
  if ($t) { foreach ($m in [regex]::Matches($t, '(?m)^[ \t]*(requirements|design|implementation|test|release):[ \t]*done')) { $set[$m.Groups[1].Value] = $true } }
  return $set
}
$added = ''
if ($kind -eq 'tasks') {
  $nm = Count-Marks $newText; $om = 0
  if ($hasOld) { $om = Count-Marks $oldText }
  if ($nm -gt $om) { $added = "[x] +$($nm - $om)" }
} else {
  $nd = Phases-Done $newText; $od = @{}
  if ($hasOld) { $od = Phases-Done $oldText }
  foreach ($ph in @('requirements', 'design', 'implementation', 'test', 'release')) {
    if ($nd.ContainsKey($ph) -and -not $od.ContainsKey($ph)) { if ($added) { $added += ' ' }; $added += "${ph}: done" }
  }
}
if (-not $added) { Allow-Exit }

# 証拠 3 点セットの検査(書込で新しく入る行だけを見る)
$scope = $newText
if ($hasOld -and ($oldText -replace '\s', '')) {
  $oldSet = @{}
  foreach ($ln in ($oldText -split "`r?`n")) { $oldSet[$ln] = $true }
  $scope = (($newText -split "`r?`n") | Where-Object { -not $oldSet.ContainsKey($_) }) -join "`n"
}
$tsRe = '\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}'
$cmdRe = '`[^`]+`|(コマンド|command|cmd|検証)\s*[:：]'
$outRe = '(→|->|=>|出力|結果|output|result|passed|failed|pass|fail|OK|NG|成功|失敗|exit\s*(code)?\s*[0-9]|[0-9]+\s*(件|tests?|passed|errors?|failures?))'
$missing = @()
if (-not ($scope -cmatch $tsRe)) { $missing += '実行日時(YYYY-MM-DD HH:MM)' }
if (-not ($scope -imatch $cmdRe)) { $missing += '再実行可能なコマンド(`...` またはコマンド: ラベル)' }
if (-not ($scope -imatch $outRe)) { $missing += '出力の要約(→ / 結果: / passed / OK 等)' }
if ($missing.Count -eq 0) { Write-HookLog 'allow' "$kind evidence-ok ($added)"; Allow-Exit }

$missingText = ($missing -join '・')
Write-HookLog 'deny' "$target $kind evidence-missing: $missingText"
$label = '完了マーク [x] の追加'
if ($kind -eq 'progress') { $label = "GATE_STATUS の done 遷移($added)" }
$reason = "$label を検知しましたが、この書込の新内容に証拠 3 点セットが揃っていません(不足: $missingText)。同じ書込に「証拠: ``<再実行可能なコマンド>`` → <出力の要約> (YYYY-MM-DD HH:MM)」の形で証拠を含めてください(gate-check スキル: 完了マークは必ず完了条件の証拠とセットで付け、done への遷移はユーザーの明示承認と当日の証拠を要する。証拠なし・古い証拠での完了化は不可)。人間が直接編集する場合はこのガードの対象外です(A2-4b)。"
$out = @{
  continue = $true
  hookSpecificOutput = @{ hookEventName = 'PreToolUse'; permissionDecision = 'deny'; permissionDecisionReason = $reason }
}
$out | ConvertTo-Json -Depth 5 -Compress

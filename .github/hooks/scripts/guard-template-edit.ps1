# PreToolUse hook: *_template.md への直接編集をブロックする。
# 想定外のペイロード形状でも安全側(継続許可)に倒す。
# 第5回(2026-09-09 再監査 RG-4/CP-1): パス候補は _paths.ps1(D072)で file_path/filePath/path/
# uri/notebook_path/apply_patch 本文から網羅収集し(H-2 の封鎖)、読み取り系ツール名は冒頭で
# 除外する(guard-template-edit.sh と同一規則)。
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

# 共通ライブラリ(無ければ従来の単一フィールド判定だけで動く=fail-open)
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

# ---- 読み取り系ツールの除外 (CP-1 / D059) ----
$toolName = $null
if (Get-Command Get-ToolName -ErrorAction SilentlyContinue) { $toolName = Get-ToolName $obj }
if ((Get-Command Test-ReadOnlyTool -ErrorAction SilentlyContinue) -and (Test-ReadOnlyTool ([string]$toolName))) {
  Write-HookLog 'allow' "read-tool:$toolName"
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}

# パス候補の網羅収集(H-2 / D072)。従来の単一フィールドも候補に含める
$cands = New-Object System.Collections.Generic.List[string]
if ($file) { $cands.Add([string]$file) }
if (Get-Command Get-PathCandidates -ErrorAction SilentlyContinue) {
  foreach ($c in @(Get-PathCandidates $obj 'auto')) { if ($c) { $cands.Add([string]$c) } }
}
# 8.3 短縮名(docs\01-REQ~1\REQUIR~1.MD 等)は _paths.ps1 の展開で長形式に直してから照合する
# (H-10/RG-7 の横展開。展開できなければ字面のまま=fail-open。~数字を含まない候補は無変更)
$has83 = [bool](Get-Command ConvertTo-LongPathCandidate -ErrorAction SilentlyContinue)
$hit = $null
foreach ($c in $cands) {
  if (-not $c) { continue }
  if ($has83) { $c = ConvertTo-LongPathCandidate ([string]$c) }
  if ($c -like "*_template.md") { $hit = $c; break }
}

if ($hit) {
  Write-HookLog 'deny' $hit
  $out = @{
    continue = $true
    hookSpecificOutput = @{
      hookEventName = "PreToolUse"
      permissionDecision = "deny"
      permissionDecisionReason = "テンプレートファイルは直接編集せず、コピーして実体ファイル(例: requirements_template.md -> requirements.md)を作成してください。"
    }
  }
} else {
  $out = @{ continue = $true }
}
$out | ConvertTo-Json -Depth 5 -Compress

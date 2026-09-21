# PostToolUse hook: フェーズゲートの状態を書き換える編集を検知したら、承認・証拠の
# 確認を促す非ブロッキングの警告を出す(編集自体は妨げない。P2-4。
# warn-gate-tamper.sh と同一判定)。
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
# 共通ライブラリ(D072。無ければ従来の内蔵収集で動く=fail-open)
try { $__paths = Join-Path $PSScriptRoot '_paths.ps1'; if (Test-Path $__paths) { . $__paths } } catch {}
# 判定ログの共通実装(_log.ps1 → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
try { $__log = Join-Path $PSScriptRoot '_log.ps1'; if (Test-Path $__log) { . $__log } } catch {}
if (-not (Get-Command Write-HookLog -ErrorAction SilentlyContinue)) { function Write-HookLog($decision, $target) { } }
$file = $null
$newText = ''
$obj = $null
try {
  $obj = $raw | ConvertFrom-Json
  $file = $obj.tool_input.file_path
  if (-not $file) { $file = $obj.tool_input.filePath }
  if (-not $file) { $file = $obj.tool_input.path }
  $parts = @()
  if ($obj.tool_input.new_string) { $parts += [string]$obj.tool_input.new_string }
  if ($obj.tool_input.content) { $parts += [string]$obj.tool_input.content }
  foreach ($e in @($obj.tool_input.edits)) {
    if ($e -and $e.new_string) { $parts += [string]$e.new_string }
  }
  $newText = $parts -join "`n"
} catch {
  # JSON解析できない場合はパスのみ拾い、新内容の判定はしない(安全側=警告なし)
  if ($raw -match '"(file_path|filePath|path)"\s*:\s*"([^"]*)"') {
    $file = $Matches[2]
  }
}

# 読み取り系ツールは対象外(CP-1 / D059。warn-gate-tamper.sh と同一規則)
$toolName = $null
if (Get-Command Get-ToolName -ErrorAction SilentlyContinue) { $toolName = Get-ToolName $obj }
if ((Get-Command Test-ReadOnlyTool -ErrorAction SilentlyContinue) -and (Test-ReadOnlyTool ([string]$toolName))) {
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}

# 一括編集/パッチ系: 共通ライブラリ(_paths.ps1)があればそちらの収集を正とする(第5回・D072)
if ((-not $file) -and $obj -and $obj.tool_input -and (Get-Command Get-PathCandidates -ErrorAction SilentlyContinue)) {
  foreach ($c in @(Get-PathCandidates $obj 'auto')) {
    if ($c -like '*docs/00-overview/progress.md' -or $c -like '*docs/03-implementation/tasks.md') { $file = $c; break }
  }
  if ($file -and -not $newText) {
    foreach ($k in @('input', 'patch', 'content', 'diff')) {
      $v = $obj.tool_input.$k
      if ($v -is [string] -and $v) { $newText = $v; break }
    }
  }
}

# 一括編集/パッチ系(D066): 詳細は .sh 版のコメント参照。
if ((-not $file) -and $obj -and $obj.tool_input) {
  $gtCands = New-Object System.Collections.Generic.List[string]
  function Collect-GtPaths($o) {
    if ($null -eq $o) { return }
    if ($o -is [System.Management.Automation.PSCustomObject]) {
      foreach ($pp in $o.PSObject.Properties) {
        if ((@('file_path','filePath','path','uri') -contains $pp.Name) -and ($pp.Value -is [string])) { $gtCands.Add($pp.Value) }
        else { Collect-GtPaths $pp.Value }
      }
    } elseif ($o -is [System.Array]) { foreach ($it in $o) { Collect-GtPaths $it } }
  }
  Collect-GtPaths $obj.tool_input
  foreach ($k in @('input', 'patch', 'content', 'diff')) {
    $v = $obj.tool_input.$k
    if ($v -is [string] -and $v) {
      foreach ($pm in [regex]::Matches($v, '(?im)^[ \t]*\*{3} (?:Add|Update|Delete) File: (.+)$')) { $gtCands.Add($pm.Groups[1].Value.Trim()) }
    }
  }
  foreach ($c in $gtCands) {
    $cn = ($c -replace '\\', '/')
    if ($cn -like '*docs/00-overview/progress.md' -or $cn -like '*docs/03-implementation/tasks.md') { $file = $cn; break }
  }
  if ($file -and -not $newText) {
    foreach ($k in @('input', 'patch', 'content', 'diff')) {
      $v = $obj.tool_input.$k
      if ($v -is [string] -and $v) { $newText = $v; break }
    }
  }
}

if (-not $file) {
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}

# 形式リント(D059): progress.md 編集後、実ファイルに正準ブロックが無ければ警告
# (エージェントの独自形式 progress.md 自作をフックが読めなくなる実失敗の再発防止)。
# パス正規化は後段の $normalized と同一規則をここで局所適用する。
$normLint = $file -replace '\\', '/' -replace '/{2,}', '/'
if ($normLint -like '*docs/00-overview/progress.md' -and (Test-Path 'docs/00-overview/progress.md')) {
  # 照合は機械読取側(guard-phase-scope / inject-progress の '<!-- GATE_STATUS' リテラル)と
  # 同一規則(空白1個・大小区別)。読み取り失敗のみ fail-open(警告なし)、空ファイルは
  # ブロック喪失として警告する(.sh 版と同挙動)。
  $readOk = $false
  $bodyRaw = ''
  try { $bodyRaw = [IO.File]::ReadAllText('docs/00-overview/progress.md'); $readOk = $true } catch {}
  if ($readOk -and ($bodyRaw -cnotmatch '<!-- GATE_STATUS')) {
    Write-HookLog 'warn' "$file gate-format"
    $fmtMsg = "progress.md に正準の <!-- GATE_STATUS --> コメントブロックがありません。docs/00-overview/progress_template.md の形式(キー: requirements/design/implementation/test/release)に戻してください。フックとゲート判定はこのブロックだけを機械読取します。復旧手順は .github/harness/STATE-MACHINE.md(python tools/gate_status.py recover)。"
    $out = @{ continue = $true; systemMessage = $fmtMsg; hookSpecificOutput = @{ hookEventName = "PostToolUse"; additionalContext = $fmtMsg } }
    $out | ConvertTo-Json -Depth 5 -Compress
    exit 0
  }
}

# 状態機械の堅牢化(再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15。仕様の正は .github/harness/STATE-MACHINE.md。
# .sh と同一判定): progress.md の書込後に実ファイルの GATE_STATUS を読み、遷移ログ logs/gate-transitions.jsonl の最終記録と
# 違えば _log.ps1 の Write-GateLog で「前→後」の差分・通番・session_id・ts を 1 行追記する。あわせて (a) 別セッションの
# stale でない session.lock があれば同時更新の警告、(b) implement↔test の往復カウンタ(GATE_COUNTERS)が上限
# (HARNESS_LOOP_MAX > tools/usage-config.json の implement_test_loop_max > 3)超なら警告を $extra に積み、他の警告文の
# 末尾に添える(単独なら単独で出す)。非ブロッキング・fail-open。
$extra = ''
if ($normLint -like '*docs/00-overview/progress.md' -and (Test-Path 'docs/00-overview/progress.md') -and (Get-Command Get-GateFileState -ErrorAction SilentlyContinue)) {
  try {
    $gs = Get-GateFileState 'docs/00-overview/progress.md'
    if ($gs.state) {
      $gl = Get-GateLast
      if ($gs.state -ne $gl.after) { Write-GateLog $gl.after $gs.state $gs.loops 'hook' }
    }
    $sidSelf = $null
    if ($obj) { $sidSelf = [string]$obj.session_id }
    $lk = Test-SessionLockOther $sidSelf
    if ($lk) {
      $short = [string]$lk.sid
      if ($short.Length -gt 8) { $short = $short.Substring(0, 8) }
      Write-HookLog 'warn' "$file gate-lock-other:$short"
      $extra += "別のセッション(${short}…)が $($lk.ageMin) 分前から同じ作業ツリーで作業中です(.github/hooks/logs/session.lock)。GATE_STATUS の同時更新は後勝ちで前の遷移が失われます。片方のセッションだけが書き、必要なら python tools/gate_status.py reconcile で遷移ログを突き合わせてください(STATE-MACHINE.md「並行更新」)。"
    }
    $loopMax = [string]$env:HARNESS_LOOP_MAX
    if ($loopMax -notmatch '^\d+$') {
      $loopMax = ''
      if (Test-Path 'tools/usage-config.json') {
        $lmm = [regex]::Match([IO.File]::ReadAllText('tools/usage-config.json'), '"implement_test_loop_max"\s*:\s*(\d+)')
        if ($lmm.Success) { $loopMax = $lmm.Groups[1].Value }
      }
    }
    if ($loopMax -notmatch '^\d+$') { $loopMax = '3' }
    if ($null -ne $gs.loops -and ([int]$gs.loops -gt [int]$loopMax)) {
      Write-HookLog 'warn' "$file gate-loop-cap:$($gs.loops)"
      $extra += "implement↔test の往復が $($gs.loops) 回で上限 $loopMax を超えています(GATE_COUNTERS の implement_test_loops)。自動の差し戻しを止め、/13-converge(乖離の棚卸し)か人の判断に上げてください(STATE-MACHINE.md「往復上限」)。"
    }
  } catch {}
}

if (-not $newText) {
  if ($extra) {
    $out = @{ continue = $true; systemMessage = $extra; hookSpecificOutput = @{ hookEventName = "PostToolUse"; additionalContext = $extra } }
    $out | ConvertTo-Json -Depth 5 -Compress
  } else {
    @{ continue = $true } | ConvertTo-Json -Compress
  }
  exit 0
}

# \ 区切り(Windows)を / に正規化してから照合(warn-gate-tamper.sh と同一)
$normalized = $file -replace '\\', '/' -replace '/{2,}', '/'

# 独立レビュー記録の検査(A6-14 / RD-2。.sh 版の review_record_gap と同一判定):
# implementation/test の done 遷移で、docs/04-test/review-log.md の日時付きエントリ
# (YYYY-MM-DD HH:MM)も security-review-report.md も無ければ警告文、問題なしなら $null。
# 「当該フェーズ開始以降」は機械可読な開始日が無いため「そのフェーズの最新の完了証拠
# (tasks.md / test-report.md の日時の最大値)と同日以降」で近似する。読取失敗は fail-open。
function Get-ReviewRecordGap($phase) {
  try {
    $ts = '\d{4}-\d{2}-\d{2} \d{2}:\d{2}'
    $log = 'docs/04-test/review-log.md'
    $rep = 'docs/04-test/security-review-report.md'
    $reviewText = ''
    foreach ($f in @($log, $rep)) { if (Test-Path $f) { $reviewText += [IO.File]::ReadAllText($f) + "`n" } }
    $reviewTs = @([regex]::Matches($reviewText, $ts) | ForEach-Object { $_.Value } | Sort-Object)
    $latestReview = $null
    if ($reviewTs.Count -gt 0) { $latestReview = [string]$reviewTs[$reviewTs.Count - 1] }
    $hasReport = (Test-Path $rep) -and ((Get-Item $rep).Length -gt 0)
    if ((-not $latestReview) -and (-not $hasReport)) {
      return "${phase} の done 遷移を検知しましたが、独立レビューの記録がありません（docs/04-test/review-log.md に日時付きエントリが無く、security-review-report.md もありません）。reviewer を起動し、結果を review-log.md に追記してから done にしてください（gate-check スキルの done 条件。A6-14）。"
    }
    $art = 'docs/04-test/test-report.md'
    if ($phase -eq 'implementation') { $art = 'docs/03-implementation/tasks.md' }
    $latestEvidence = $null
    if (Test-Path $art) {
      $evTs = @([regex]::Matches([IO.File]::ReadAllText($art), $ts) | ForEach-Object { $_.Value } | Sort-Object)
      if ($evTs.Count -gt 0) { $latestEvidence = [string]$evTs[$evTs.Count - 1] }
    }
    if ($latestReview -and $latestEvidence) {
      $rd = $latestReview.Substring(0, 10)
      $ed = $latestEvidence.Substring(0, 10)
      if ([string]::CompareOrdinal($rd, $ed) -lt 0) {
        return "${phase} の done 遷移を検知しましたが、最新の完了証拠（${ed}）以降の独立レビューの記録がありません（review-log.md の最新エントリは ${rd}）。最終タスク完了後に reviewer を再度起動し、記録してから done にしてください（gate-check スキルの done 条件。A6-14）。"
      }
    }
  } catch {}
  return $null
}
# done 契約の model_mismatch(D077 決定3 の残: 三層強制の done 契約側。A6-10。.sh 版の model_mismatch_gap と同一判定):
# implementation / test の done 遷移で、このセッションの logs/usage/<session_id>.subagents.jsonl にある直近の
# reviewer / spec-critic の resolved_model(null なら requested_model)が、役割別モデル方針の allowed
# (python tools/model_policy.py --print-role-table。読めなければ隣の guard-subagent-model.ps1 に埋め込まれた同じ
# 生成表)に無ければ警告文、それ以外は $null(記録が無い・session_id が無い・表が読めない=警告しない)。
# 記録先は HARNESS_HOOK_LOG_DIR で差し替え可(selftest 用)。
function Get-ModelMismatchGap($phase) {
  try {
    $sid = $null
    if ($obj) { $sid = [string]$obj.session_id }
    if (-not $sid) { return $null }
    $sid = $sid -replace '[^A-Za-z0-9_-]', '_'
    $logDir = if ($env:HARNESS_HOOK_LOG_DIR) { $env:HARNESS_HOOK_LOG_DIR } else { Join-Path $PSScriptRoot '..\logs' }
    $f = Join-Path (Join-Path $logDir 'usage') ($sid + '.subagents.jsonl')
    if (-not (Test-Path -LiteralPath $f)) { return $null }
    $lastRole = $null; $lastModel = $null
    foreach ($line in [IO.File]::ReadAllLines($f, [Text.Encoding]::UTF8)) {
      if (-not $line.Trim()) { continue }
      $r = $null
      try { $r = $line | ConvertFrom-Json } catch { continue }
      if ($null -eq $r -or ($r.agent_type -ne 'reviewer' -and $r.agent_type -ne 'spec-critic')) { continue }
      $m = $r.resolved_model
      if (-not $m) { $m = $r.requested_model }
      if ($m) { $lastRole = [string]$r.agent_type; $lastModel = [string]$m }
    }
    if (-not $lastModel) { return $null }
    $tableJson = $null
    if (Test-Path -LiteralPath 'tools/model_policy.py') {
      foreach ($cand in @('python', 'python3')) {
        if (Get-Command $cand -ErrorAction SilentlyContinue) {
          try { $tableJson = (& $cand 'tools/model_policy.py' '--print-role-table' 2>$null | Out-String).Trim() } catch {}
          if ($tableJson) { break }
        }
      }
    }
    if (-not $tableJson) {
      $sib = Join-Path $PSScriptRoot 'guard-subagent-model.ps1'
      if (Test-Path -LiteralPath $sib) {
        $mm = [regex]::Match([IO.File]::ReadAllText($sib), '(?m)^\$roleTableJson = ''([^'']*)''')
        if ($mm.Success) { $tableJson = $mm.Groups[1].Value }
      }
    }
    if (-not $tableJson) { return $null }
    $table = $tableJson | ConvertFrom-Json
    $roleProp = $table.roles.PSObject.Properties[$lastRole]
    if ($null -eq $roleProp) { return $null }
    $rl = $roleProp.Value
    $hit = $false
    foreach ($e in @($rl.allowed_exact)) { if ($null -ne $e -and ($lastModel -ceq [string]$e)) { $hit = $true; break } }
    if (-not $hit) {
      foreach ($p in @($rl.allowed_prefix)) { if ($null -ne $p -and ([string]$p) -ne '' -and $lastModel.StartsWith([string]$p)) { $hit = $true; break } }
    }
    if ($hit) { return $null }
    return "${phase} の done 遷移を検知しましたが、このセッションの独立レビュー（${lastRole}）の実行モデル ${lastModel} は役割別モデル方針（.github/harness/model-policy.yml）の allowed 外です（model_mismatch）。方針どおりのモデルで ${lastRole} を再実行して記録するか、方針を変更して python tools/generate-adapters.py を再実行してください（D077 三層強制の done 契約側。警告のみ）。"
  } catch {}
  return $null
}

$reviewPhase = $null
$reviewGap = $null
$mismatchGap = $null
if ($normalized -like '*docs/00-overview/progress.md') {
  foreach ($pm in [regex]::Matches($newText, '(?m)^(implementation|test):[ \t]*done')) {
    $reviewPhase = $pm.Groups[1].Value
    $reviewGap = Get-ReviewRecordGap $reviewPhase
    if ($reviewGap) { break }
    # done 契約の model_mismatch(D077): 記録が方針外モデルなら専用警告(記録が無ければ鳴らさない)
    $mismatchGap = Get-ModelMismatchGap $reviewPhase
    if ($mismatchGap) { break }
  }
}

# 「: done」は大文字小文字を区別して照合し(.sh の grep -qE と同一)、
# 完了マークは [x]/[X] の両方を検知する(冒頭コメントの規則どおり)。
$msg = $null
# 照合はキー行アンカー・文言は .sh とバイト同一(全角括弧。D066)
if ($normalized -like '*docs/00-overview/progress.md' -and ($newText -cmatch '(?m)^release:[ \t]*done') -and (-not (Test-Path 'docs/04-test/security-review-report.md'))) {
  Write-HookLog 'warn' "$file release-done-without-security-report"
  $msg = "release の done 遷移を検知しましたが docs/04-test/security-review-report.md がありません。リリース前に release-security-review スキル(独立セキュリティレビュー)を実施し、レポートを作成してください(D070)。"
} elseif ($reviewGap) {
  # 独立レビュー記録(A6-14): implementation/test の done 遷移で記録が無ければ専用警告(一般 done 警告より優先)
  Write-HookLog 'warn' "$file ${reviewPhase}-done-without-review-log"
  $msg = $reviewGap
} elseif ($mismatchGap) {
  Write-HookLog 'warn' "$file ${reviewPhase}-done-model-mismatch"
  $msg = $mismatchGap
} elseif ($normalized -like '*docs/00-overview/progress.md' -and $newText -cmatch '(?m)^(requirements|design|implementation|test|release):[ \t]*done') {
  Write-HookLog 'warn' "$file gate-done"
  $msg = "GATE_STATUS の変更を検知。done への遷移はユーザーの明示承認と証拠が必要（gate-check スキル参照）。"
} elseif ($normalized -like '*docs/00-overview/progress.md' -and $newText -cmatch '(?m)^(requirements|design|implementation|test|release):[ \t]*pending_approval') {
  # 遷移の責務(STATE-MACHINE.md): pending_approval は各フェーズの最終ステップ(成果物確定後・承認前)、in_progress は
  # 入口(該当フェーズのコマンド / /12 / /13)の最初のステップとしての正規遷移(D067)。文言を分け、後者だけ改竄の注意を添える(.sh と同一)
  Write-HookLog 'warn' "$file gate-pending-approval"
  $msg = "pending_approval(ゲート承認待ち)への遷移を検知。そのフェーズの成果物が確定し、独立レビュー記録(implementation/test は review-log.md)が揃っているか確認してください。done への遷移は人の承認発言を得てから行います(.github/harness/STATE-MACHINE.md)。"
} elseif ($normalized -like '*docs/00-overview/progress.md' -and $newText -cmatch '(?m)^(requirements|design|implementation|test|release):[ \t]*in_progress') {
  Write-HookLog 'warn' "$file gate-in-progress"
  $msg = "in_progress への遷移を検知。入口(該当フェーズのコマンド / /12-change-request / /13-converge)の最初のステップとしての正規の遷移か、変更履歴/CR記録があるかを確認。入口を経ずにガードを通すためだけに書き換えるのはゲート改竄（D063/D066。.github/harness/STATE-MACHINE.md）。"
} elseif ($normalized -like '*docs/03-implementation/tasks.md' -and ($newText.Contains('[x]') -or $newText.Contains('[X]'))) {
  Write-HookLog 'warn' "$file task-done-mark"
  $msg = "完了マークの追加を検知。完了条件（done契約）の証拠が併記されているか確認してください。"
}

# 状態機械の付帯警告(別セッションの lock / 往復上限超)は他の警告文の末尾に添え、単独ならそれだけを出す(.sh の finish_ok と同一)
if ($extra) { if ($msg) { $msg = $msg + ' ' + $extra } else { $msg = $extra } }

if ($msg) {
  # systemMessage(ユーザー向け)に加えて hookSpecificOutput.additionalContext にも併記し、
  # モデルにも同じ警告が届くようにする(第2回監査の出力契約統一)。
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

# SessionStart/PreCompact hook: フェーズゲート状況(GATE_STATUS)と教訓ログ(learnings)を
# 会話開始時およびコンテキスト圧縮前に自動注入する(圧縮で注入済み情報が失われる穴を塞ぐ)。
param([string]$EventName = "SessionStart")
$ErrorActionPreference = 'SilentlyContinue'
# stdin/stdout を UTF-8(BOMなし) に固定する(既定の CP932 では日本語直後のパターン照合と
# 出力 JSON の符号化が壊れる)。設定に失敗しても既定エンコーディングのまま続行する(fail-open)。
try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
$progress = "docs/00-overview/progress.md"
$learnings = "docs/00-overview/learnings.md"
# ペイロード(session_id 等)は stdin。リダイレクトされていないとき(端末から直接実行)は読まない=ブロックしない。
# 先頭の U+FEFF は除去する(PowerShell 5.1 のリダイレクト stdin の実測。他フックと同じ対処)
$raw = ''
try {
  if ([Console]::IsInputRedirected) {
    # パイプが閉じない環境(CI の陽性確認ステップはパイプ無しで起動する)でも 3 秒で打ち切る(SessionStart の timeout 5s 内)
    $readTask = [Console]::In.ReadToEndAsync()
    if ($readTask.Wait(3000)) { $raw = [string]$readTask.Result }
  }
} catch {}
if ($null -eq $raw) { $raw = '' }
$raw = $raw.TrimStart([char]0xFEFF)
$obj = $null
try { if ($raw.Trim()) { $obj = $raw | ConvertFrom-Json } } catch {}
# 判定ログ・遷移ログ・session.lock の共通実装(_log.ps1。無ければ lock と遷移ログを扱わない=fail-open)
try { $__log = Join-Path $PSScriptRoot '_log.ps1'; if (Test-Path $__log) { . $__log } } catch {}
if (-not (Get-Command Write-HookLog -ErrorAction SilentlyContinue)) { function Write-HookLog($decision, $target) { } }

# 本体判定の補助(D058): requirements/memo.md がテンプレートのまま
# (「（ここから記入）」マーカーの後に実内容が無い、またはファイル自体が無い)なら $true。
# 実メモが書かれていれば $false。USAGE.md は全配布物に含まれるため単独では本体の
# 識別子にならない(D053 の過剰修正の回帰を Copilot 手動 E2E が検出)。
function Test-MemoPristine {
  $memo = "requirements/memo.md"
  if (-not (Test-Path $memo)) { return $true }
  try { $memoLines = @(Get-Content $memo -Encoding UTF8 -ErrorAction Stop) } catch { return $true }
  $hit = $memoLines | Select-String -SimpleMatch "（ここから記入）" | Select-Object -First 1
  if (-not $hit) { return $false }
  $tailText = ($memoLines | Select-Object -Skip $hit.LineNumber) -join ""
  return [string]::IsNullOrWhiteSpace($tailText)
}

$ctx = ""
if (Test-Path $progress) {
  $lines = Get-Content $progress -Raw -Encoding UTF8
  $match = [regex]::Match($lines, '(?s)<!-- GATE_STATUS.*?-->')
  $block = if ($match.Success) { $match.Value } else { "" }
  $ctx = "現在のフェーズゲート状況(docs/00-overview/progress.md):`n$block"
  # 運用中判定は route-request.sh と同じ「全フェーズ done、または progress.md 本文に
  # 『状態: 運用中』の注記がある」の論理和(値への注記等で done を数え落としても取りこぼさない)。
  # 次の依頼の入口を明示する(入口が示されないと場当たり作業に落ちる)
  $doneCount = ([regex]::Matches($block, '(?m)^(requirements|design|implementation|test|release):\s*done')).Count
  if ($doneCount -eq 5 -or $lines -match '状態: 運用中') {
    $ctx += "`n全フェーズ done または運用中注記 = 運用中です。変更依頼の入口は /12-change-request(受付の振り分けは request-routing スキル参照)。"
  }
} elseif (Test-Path "docs/00-overview/intake-report.md") {
  # 取り込み済み・/11未完了のプロジェクト。本体検知より先に判定(D044。DECISIONS.md は D049 で
  # export-ignore になったが、導入前の配布物から作られた既存プロジェクトは複製を持ち得る)
  $ctx = "既存アプリの取り込みが完了していません(intake-report.md あり・progress.md なし)。他の作業より先に /11-brownfield-intake を実行してください(Claude Code では自分で起動してよい。as-is逆起こし → 整合検証 → ゲート初期化。intake-report.md が入力になります)。"
} elseif (((Test-Path "DECISIONS.md") -and (Test-MemoPristine)) -or ((Test-Path ".github/harness/USAGE.md") -and (Test-MemoPristine))) {
  # ハーネス本体リポジトリ判定(D058/H-11): 「DECISIONS.md がある(実クローン)」または
  # 「USAGE.md がある(ZIP/archive 展開コピー)」、かつ memo がテンプレのまま。
  # 実メモが書かれたコピーは新規プロジェクトとして扱う(request-routing タイブレークの機械化。
  # DECISIONS.md はテンプレート経路でも残留するため memo 判定を AND する=RG-6。.sh と同一)。
  $ctx = "ここはハーネス本体リポジトリです(progress.md なし・本体シグネチャあり)。アプリ開発の入口(/00, /11, /12)は使いません。振り返りの還流適用は /90-apply-retrospective、ハーネス設定の変更は人間が tools/harness-maintenance.py で保守モードにしてから行います。"
} else {
  # brownfield(既存アプリ持ち込み)に /00 を案内すると、グリーンフィールドの一本道が
  # 始まってしまう(実装済みコードを無視した要件ヒアリング)。必ず両論併記する。
  $ctx = "docs/00-overview/progress.md が未作成です。新規開発なら /00-start-project、既存アプリの取り込みなら /11-brownfield-intake を実行してください(既存コードがあるのに /00 を実行しない)。"
}

# 配布鮮度(再監査 2026-09-09 RD-4 / A6-15。.sh と同一の判定・注入文): harness-origin.md の latest_decision(旧形式は
# version)と本体パス(path:)の DECISIONS.md(無ければ CHANGELOG.md)の最新 D 番号を比べ、閾値(HARNESS_STALE_GENERATIONS。
# 既定 1)以上古ければ「/91 を先に実行」を 1 行注入する。SessionStart のみ。本体自身・到達不能・D 番号不明は注入しない
# (fail-open)。同じ判定を python tools/doctor.py の distribution 行が表で出す。
$origin = "docs/00-overview/harness-origin.md"
if ($EventName -eq 'SessionStart' -and (Test-Path $origin)) {
  $originText = ""
  try { $originText = Get-Content $origin -Raw -Encoding UTF8 -ErrorAction Stop } catch {}
  $originPath = ""; $originDec = ""; $bodyDec = ""
  $pm = [regex]::Match($originText, '(?m)^path:\s*(.+?)\s*$')
  if ($pm.Success) { $originPath = $pm.Groups[1].Value }
  $dm = [regex]::Match($originText, '(?m)^latest_decision:\s*D?(\d+)')
  if (-not $dm.Success) { $dm = [regex]::Match($originText, '(?m)^version:\s*D(\d+)') }
  if ($dm.Success) { $originDec = $dm.Groups[1].Value }
  if ($originPath -and $originDec -and (Test-Path $originPath -PathType Container)) {
    $selfPath = (Get-Location).Path.TrimEnd('\', '/')
    $resolved = ""
    try { $resolved = (Resolve-Path $originPath -ErrorAction Stop).Path.TrimEnd('\', '/') } catch {}
    if ($resolved -and ($resolved -ne $selfPath)) {
      $decFile = Join-Path $originPath 'DECISIONS.md'
      $chgFile = Join-Path $originPath 'CHANGELOG.md'
      $nums = @()
      if (Test-Path $decFile) {
        $nums = @([regex]::Matches((Get-Content $decFile -Raw -Encoding UTF8), '(?m)^## D(\d+)') | ForEach-Object { [int]$_.Groups[1].Value })
      } elseif (Test-Path $chgFile) {
        $nums = @([regex]::Matches((Get-Content $chgFile -Raw -Encoding UTF8), '\bD(\d{3})\b') | ForEach-Object { [int]$_.Groups[1].Value })
      }
      if ($nums.Count -gt 0) { $bodyDec = ($nums | Measure-Object -Maximum).Maximum }
    }
  }
  if ($bodyDec -ne "") {
    $have = [int]$originDec; $latest = [int]$bodyDec
    $threshold = 1
    if ($env:HARNESS_STALE_GENERATIONS -match '^\d+$') { $threshold = [int]$env:HARNESS_STALE_GENERATIONS }
    if ((($latest - $have) -ge $threshold) -and (($latest - $have) -gt 0)) {
      $ctx += "`nハーネスコピーが本体より $($latest - $have) 世代古い可能性があります(コピー D$('{0:d3}' -f $have) / 本体 D$('{0:d3}' -f $latest))。作業を始める前に /91-sync-from-harness を先に実行してください(判定は docs/00-overview/harness-origin.md の latest_decision と本体 DECISIONS.md の D 番号差。python tools/doctor.py の distribution 行と同じ)。"
    }
  }
}

if (Test-Path $learnings) {
  # 「## 教訓」以降の箇条書きを注入する。肥大化対策の上限は「新しい50件」。
  # 以前は最古50件で打ち切っており、新しい教訓ほど注入されない欠陥があった
  # (総数207件のうち157件が一度も注入されないまま全工程が終わった実例あり)。
  # 上限を超えたら、打ち切ったことを注入文に必ず明示する(silentに取りこぼさない)。
  # -Encoding UTF8 を明示する(PowerShell 5.1 の既定は ANSI 読みのため、BOMなしUTF-8の
  # 日本語見出し「## 教訓」が文字化けして一致せず、教訓が1件も注入されない)
  $content = Get-Content $learnings -Encoding UTF8
  $flag = $false
  $all = @()
  foreach ($line in $content) {
    if ($line -match '^## 教訓') { $flag = $true; continue }
    if ($flag -and $line -match '^- ') { $all += $line }
  }
  $total = $all.Count
  $lessons = if ($total -gt 50) { $all[($total - 50)..($total - 1)] } else { $all }
  if ($lessons.Count -gt 0) {
    $ctx += "`n`nこのプロジェクトの教訓(docs/00-overview/learnings.md、必ず前提として扱うこと):`n" + ($lessons -join "`n")
    if ($total -gt 50) {
      $ctx += "`n（教訓 ${total}件中 新しい50件のみ表示。全文は docs/00-overview/learnings.md。上限到達につき振り返りでの棚卸しを推奨）"
    }
  }
}

# 作業ノートパッド(A3-10/D065): .sh 版と同一(UTF-8 バイト基準の先頭2KB。D066 で
# 文字数基準→バイト基準に統一し、切断で壊れた末尾のマルチバイトは落とす)。
$notepad = "docs/00-overview/notepad.md"
if (Test-Path $notepad) {
  $np = ""
  try {
    $npBytes = [IO.File]::ReadAllBytes($notepad)
    if ($npBytes.Length -gt 2048) { $npBytes = $npBytes[0..2047] }
    $np = [Text.Encoding]::UTF8.GetString($npBytes)
    $np = $np.TrimEnd([char]0xFFFD)
  } catch {}
  $np = $np -replace "`r", ""
  if (-not [string]::IsNullOrWhiteSpace($np)) {
    $ctx += "`n`n作業ノートパッド(docs/00-overview/notepad.md。未確定の途中メモ。圧縮・セッション切替でも本注入で保持される。確定したら docs 本体へ移して行を消すこと):`n" + $np
  }
}

# 異常終了の検知(再監査 CC-9): 24 時間以内の logs/abnormal-stop-*.json(StopFailure フック mark-abnormal-stop.py の
# 記録)があれば前回の途中終了を注入する(SessionStart のみ。.sh と同一の判定条件・注入文)。ファイルは消さない。
# 記録先は mark-abnormal-stop.py と同じく HARNESS_HOOK_LOG_DIR で差し替え可(selftest が実 logs/ を汚さない)
if ($EventName -eq 'SessionStart') {
  $abnDir = if ($env:HARNESS_HOOK_LOG_DIR) { $env:HARNESS_HOOK_LOG_DIR } else { Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\logs' }
  $abnFile = Get-ChildItem -Path $abnDir -Filter 'abnormal-stop-*.json' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -gt (Get-Date).AddHours(-24) } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($abnFile) {
    $abnType = 'unknown'
    try { $j = Get-Content $abnFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json; if ($j.error_type) { $abnType = $j.error_type } } catch {}
    $ctx += "`n`n前回のセッションは異常終了しました(API エラー: $abnType。記録: .github/hooks/logs/$($abnFile.Name))。Stop フックの記録処理が走っていないため、作業を始める前に docs/00-overview/progress.md の GATE_STATUS と docs/03-implementation/tasks.md の整合(実際の作業状態・未コミットの変更・記録漏れ)を先に確認してください。"
  }
}
# 並行更新の防止と遷移ログの突合(状態機械の堅牢化。再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15。
# 仕様の正は .github/harness/STATE-MACHINE.md。.sh と同一の判定・注入文): SessionStart で (1) logs/session.lock を置く
# (別セッションの stale でない lock があれば上書きせず「片方だけが GATE_STATUS を書く」を注入、stale なら取得)、
# (2) progress.md の GATE_STATUS が遷移ログの最終記録と違えば source=session-start で 1 行追記する。session_id が
# 取れないとき(VS Code の SessionStart ペイロードに載るかは未検証)は lock を扱わない。PreCompact では何もしない。fail-open。
if ($EventName -eq 'SessionStart' -and (Get-Command Write-SessionLock -ErrorAction SilentlyContinue)) {
  try {
    $sid = $null
    if ($obj) { $sid = [string]$obj.session_id }
    if ($sid) {
      $lk = Test-SessionLockOther $sid
      if ($lk) {
        $short = [string]$lk.sid
        if ($short.Length -gt 8) { $short = $short.Substring(0, 8) }
        Write-HookLog 'inject' "session-lock-other:$short"
        $ctx += "`n`n別のセッション(${short}…)が $($lk.ageMin) 分前からこの作業ツリーで作業中です(.github/hooks/logs/session.lock)。GATE_STATUS(progress.md)の書換は片方のセッションだけで行い、こちらは読み取りに留めるか、相手が終わってから続けてください(同時更新は後勝ちで前の遷移が失われます。復旧は .github/harness/STATE-MACHINE.md「復旧手順」)。"
      } else {
        Write-SessionLock $sid
      }
    }
    if (Test-Path $progress) {
      $gs = Get-GateFileState $progress
      if ($gs.state) {
        $gl = Get-GateLast
        if ($gs.state -ne $gl.after) { Write-GateLog $gl.after $gs.state $gs.loops 'session-start' }
      }
    }
  } catch {}
}

# 注入量の上限(第3波 A7-M-8 / 再監査 2026-08-31 §6。.sh と同一の判定・注入文): tools/usage-config.json の
# inject_progress_max_chars(文字数)、環境変数 HARNESS_INJECT_MAX_CHARS が優先、読めなければ 8700。超えたら先頭 max 文字で
# 打ち切り、末尾 1 行で打ち切りと全文の場所を明示する(GATE_STATUS の閉じタグ欠落で progress.md 全文が注入される事故も有界になる)。
$injMax = $null
if ($env:HARNESS_INJECT_MAX_CHARS -match '^\d+$') { $injMax = [int]$env:HARNESS_INJECT_MAX_CHARS }
if ($null -eq $injMax -and (Test-Path 'tools/usage-config.json')) {
  try {
    $ucRaw = Get-Content 'tools/usage-config.json' -Raw -Encoding UTF8 -ErrorAction Stop
    $um = [regex]::Match($ucRaw, '"inject_progress_max_chars"\s*:\s*(\d+)')
    if ($um.Success) { $injMax = [int]$um.Groups[1].Value }
  } catch {}
}
if ($null -eq $injMax) { $injMax = 8700 }
if ($injMax -gt 0 -and $ctx.Length -gt $injMax) {
  $ctxTotal = $ctx.Length
  $ctx = $ctx.Substring(0, $injMax)
  $ctx += "`n（注入上限 $injMax 文字に達したため打ち切り: 全体 $ctxTotal 文字。全文は docs/00-overview/ の progress.md / learnings.md / notepad.md を直接読むこと。上限は tools/usage-config.json の inject_progress_max_chars）"
}
$out = @{
  hookSpecificOutput = @{
    hookEventName = $EventName
    additionalContext = $ctx
  }
}
$out | ConvertTo-Json -Depth 5 -Compress

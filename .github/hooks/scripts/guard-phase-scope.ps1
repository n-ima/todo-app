# PreToolUse hook: 進行中フェーズが無い状態でのアプリコード編集を状態依存で止める。
# guard-phase-scope.sh と同一判定(Copilot Windows用)。判定: in_progress/pending_approval
# あり=allow / progress.md ありで進行中なし=deny(D062) / 未初期化=ask。一括編集・パッチ系は
# tool_input 全体からパス候補を収集し、いずれかがアプリスコープなら同じ状態判定を適用
# (D063/D066。状態判定はパス非依存のため子プロセス再帰は廃止)。想定外のペイロードで
# パスが1件も取れない場合、編集系ツール名なら ask・それ以外は継続許可(安全側)。
# 第6波(A6-20 / D080 残課題): ツール名・パス候補・8.3 短縮名展開(ConvertTo-LongPath)・読み取り系除外の複製を
# _paths.ps1 の同名契約関数に差し替え、判定ログは _log.ps1(JSONL)に一本化した(.sh 版と同時)。
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
# 共通ライブラリ(D072/A6-20)。無ければ単一フィールドの最小判定・無記録で動く(fail-open)
try { $__paths = Join-Path $PSScriptRoot '_paths.ps1'; if (Test-Path $__paths) { . $__paths } } catch {}
try { $__log = Join-Path $PSScriptRoot '_log.ps1'; if (Test-Path $__log) { . $__log } } catch {}
if (-not (Get-Command Write-HookLog -ErrorAction SilentlyContinue)) { function Write-HookLog($decision, $target) { } }
$file = $null
$toolName = $null
$obj = $null
try {
  $obj = $raw | ConvertFrom-Json
  $file = $obj.tool_input.file_path
  if (-not $file) { $file = $obj.tool_input.filePath }
  if (-not $file) { $file = $obj.tool_input.path }
  if (-not $file) { $file = $obj.tool_input.notebook_path }
  if (Get-Command Get-ToolName -ErrorAction SilentlyContinue) { $toolName = Get-ToolName $obj }
  else { $toolName = $obj.tool_name; if (-not $toolName) { $toolName = $obj.toolName } }
} catch {
  if ($raw -match '"(file_path|filePath|path|notebook_path)"\s*:\s*"([^"]*)"') {
    $file = $Matches[2]
  }
}

function Out-Allow {
  (@{ continue = $true } | ConvertTo-Json -Depth 5 -Compress)
  exit 0
}

function Out-Ask($tag, $reason) {
  Write-HookLog 'ask' $tag
  $o = @{
    continue = $true
    hookSpecificOutput = @{
      hookEventName = "PreToolUse"
      permissionDecision = "ask"
      permissionDecisionReason = $reason
    }
  }
  $o | ConvertTo-Json -Depth 5 -Compress
  exit 0
}

function Out-Deny($tag, $reason) {
  Write-HookLog 'deny' $tag
  $o = @{
    continue = $true
    hookSpecificOutput = @{
      hookEventName = "PreToolUse"
      permissionDecision = "deny"
      permissionDecisionReason = $reason
    }
  }
  $o | ConvertTo-Json -Depth 5 -Compress
  exit 0
}

# 読み取り系ツールは対象外(D059 / CP-1): 読み取りまで ask すると警報疲れ→「すべて許可」で
# 本命の書き込み ask まで無効化される連鎖が実機 E2E で観測された。二段判定の正は _paths.ps1 の
# Test-ReadOnlyTool(読み取り系パターン一致 かつ 書き込み系の語を含まない)。取れなければパス判定(安全側)。
if ((Get-Command Test-ReadOnlyTool -ErrorAction SilentlyContinue) -and (Test-ReadOnlyTool ([string]$toolName))) {
  Write-HookLog 'allow' "read-tool:$toolName"
  Out-Allow
}

# ---- パス候補の収集 (D063/D066 → _paths.ps1 の Get-PathCandidates に一本化) ----
# 単一フィールドに加え tool_input 全体の path 系フィールドを再帰収集し、パッチ本文(*** Add/Update/Delete
# File: 行)は「ツール名に patch を含む」か「フィールド候補0件」の場合のみ走査する(auto)。候補は正規化・
# 重複排除済み(最大 64)。共通ライブラリが無い配布物では単一フィールドだけで判定する(fail-open)。
$uniq = @()
if ($obj -and (Get-Command Get-PathCandidates -ErrorAction SilentlyContinue)) {
  $uniq = @(Get-PathCandidates $obj 'auto')
} elseif ($file) {
  $uniq = @([string]$file)
}

# 候補ゼロ: パスを持たないツール(ターミナル等)は対象外。編集系の名前なのにパスが
# 取れない場合は検査不能として ask に倒す(fail-closed。旧D063の無記録 fail-open の是正)。
if ($uniq.Count -eq 0) {
  if ($toolName -and ($toolName -imatch 'edit|write|creat|replace|insert|apply|patch|notebook')) {
    Out-Ask "no-path write-tool:$toolName" "編集系ツール($toolName)のペイロードから対象ファイルパスを取得できませんでした。フェーズ外編集ガードの検査対象外になるため確認します。対象ファイルがハーネス管理領域(docs/等)なら承認して構いません。アプリコードなら該当フェーズのコマンドを経由してください。"
  }
  Out-Allow
}

# 候補過多はガードが全対象を検査しきれないため fail-closed(細工した一括編集への防御)
if ($uniq.Count -gt 64) {
  Out-Deny ("too-many-candidates({0}, tool={1})" -f $uniq.Count, $toolName) ("一括編集の対象が多すぎます(候補{0}件>64)。ガードが全対象を検査できないため拒否しました。編集を分割して実行してください。" -f $uniq.Count)
}

# ---- 候補の正規化とアプリスコープ判定 (D066) ----
# 正規化: %エンコード復号(: / \ . 空白)→ file:// 除去→ /C: 形のドライブ先頭スラッシュ
# 除去→ \→/ →連続/圧縮→ 8.3 短縮名の長形式展開。'..' セグメントが残るパスは
# トラバーサルとして常にアプリスコープ扱い(fail-closed。docs/../app 等の字句的すり抜けの封鎖)。

# 8.3 短縮名(C:\Users\RUNNER~1\… 等)の長形式展開(H-10/RG-7)は _paths.ps1 の ConvertTo-LongPath(同一契約:
# ~数字セグメントを持つ絶対パスだけ Get-Item の FullName で展開。失敗時は入力のまま=fail-open)。無い環境では恒等。
if (-not (Get-Command ConvertTo-LongPath -ErrorAction SilentlyContinue)) { function ConvertTo-LongPath([string]$p) { return $p } }

$pwdn = (ConvertTo-LongPath ((Get-Location).Path)) -replace '\\', '/'
$allowPatterns = @(
  'docs/*', 'requirements/*', '.github/*', '.claude/*', '.agents/*',
  'tools/*', '.vscode/*', 'README.md', '.gitignore', '.gitattributes',
  'DECISIONS.md', 'MEMORY.md'
)

function Get-NormPath($c) {
  $x = [string]$c
  $x = $x -replace "`r", ''
  $x = $x -replace '%3[Aa]', ':' -replace '%2[Ff]', '/' -replace '%5[Cc]', '\' -replace '%2[Ee]', '.' -replace '%20', ' '
  $x = $x -replace '^file://', ''
  $x = $x -replace '^/([A-Za-z]):', '$1:'
  $x = $x -replace '\\', '/'
  $x = $x -replace '/{2,}', '/'
  $x = (ConvertTo-LongPath $x) -replace '\\', '/'
  return $x
}

$script:AppRel = $null
function Test-AppScope($p) {
  if (-not $p) { return $false }
  if ($p -match '(^|/)\.\.(/|$)') {
    $script:AppRel = "$p (path traversal)"
    return $true
  }
  $rel = $null
  if ($p.StartsWith("$pwdn/", [System.StringComparison]::OrdinalIgnoreCase)) {
    $rel = $p.Substring($pwdn.Length + 1)
  } elseif ($p -notmatch '^([A-Za-z]:)?/') {
    $rel = $p  # 相対パスはルート相対とみなす
  } else {
    return $false  # リポジトリ外
  }
  foreach ($pat in $allowPatterns) {
    if ($rel -like $pat) { return $false }
  }
  $script:AppRel = $rel
  return $true
}

$appHit = $false
foreach ($c in $uniq) {
  $nc = Get-NormPath $c
  if (Test-AppScope $nc) { $appHit = $true; break }
}
if (-not $appHit) { Out-Allow }

# ---- 状態判定(パス非依存。全アプリスコープ候補に同一の判定が適用される) ----

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

$progress = "docs/00-overview/progress.md"

if (-not (Test-Path $progress)) {
  # 取り込み済み・/11未完了のプロジェクトは「未初期化」として扱う。本体検知より先に判定
  # (D044。DECISIONS.md は D049 で export-ignore になったが、導入前の配布物から作られた
  #  既存プロジェクトは複製を持ち得る)
  if (-not (Test-Path "docs/00-overview/intake-report.md")) {
    # ハーネス本体リポジトリでは発火させない。本体判定(D058/H-11): 「DECISIONS.md がある」
    # または「USAGE.md がある」、かつ memo がテンプレのまま。DECISIONS.md はテンプレート経路
    # (GitHub テンプレート/クローン)で残留するため、実メモが書かれたコピーは本体ではなく
    # 新規プロジェクトとして扱う(RG-6。.sh 版・route-request・inject-progress と同一判定)。
    if (((Test-Path "DECISIONS.md") -and (Test-MemoPristine)) -or ((Test-Path ".github/harness/USAGE.md") -and (Test-MemoPristine))) { Out-Allow }
  }
  Out-Ask "$($script:AppRel) (uninitialized)" "プロジェクトが未初期化です(progress.md なし)。コードに触る前に、新規開発なら /00-start-project、既存アプリの取り込みなら /11-brownfield-intake を実行してください(取り込み前のアプリ改変は brownfield-intake のアンチパターン)。緊急の場合はこの確認を承認して続行できます。"
}

# GATE_STATUS ブロックの抽出は正規表現でなく線形の IndexOf(細工した progress.md での
# バックトラック暴走を防ぐ。route-request.ps1 と同方式)。
$content = ""
try { $content = [IO.File]::ReadAllText($progress) } catch {}
if ($content.Length -gt 262144) { $content = $content.Substring(0, 262144) }
$block = ""
$bi = $content.IndexOf('<!-- GATE_STATUS')
if ($bi -ge 0) {
  $bj = $content.IndexOf('-->', $bi)
  if ($bj -ge 0) { $block = $content.Substring($bi, $bj - $bi) } else { $block = $content.Substring($bi) }
}
# 運用中注記(H-4/RG-6): brownfield 取り込みは test を in_progress のまま「状態: 運用中」を
# 書くことがある(gate-check スキル)。この残余を進行中に数えると運用中の deny が無効化される
# ため、注記があるときは test 行を除いて判定する(.sh 版と同一。/12 が要件/設計/実装を
# in_progress にした改修サイクル中は従来どおり allow)。
$operatingNote = ($content -match '状態: 運用中')
$activeRe = '(?m)^(requirements|design|implementation|test|release):[ \t]*(in_progress|pending_approval)'
if ($operatingNote) { $activeRe = '(?m)^(requirements|design|implementation|release):[ \t]*(in_progress|pending_approval)' }
if ($block -cmatch $activeRe) {
  Out-Allow  # 進行中フェーズあり=通常のフェーズ作業
}

# ask から deny への格上げ(D062): 詳細は .sh 版のコメント参照。
$denyReason = "進行中のフェーズがありません(GATE_STATUSに in_progress がない=運用中または着手前)。アプリコードの編集はブロックされました(D062)。/12-change-request(運用中の変更請求)または該当フェーズのコマンドを実行してフェーズを in_progress にしてから編集してください(変更管理・回帰確認つきで同じ変更ができます)。緊急時は人間が直接エディタで編集するか、人間の明示指示のもとでフェーズコマンドを経由してください。in_progress への遷移は入口(該当フェーズのコマンド・/12・/13)の最初のステップとして行う正規の操作であり(D067。.github/harness/STATE-MACHINE.md)、入口を経ずに編集を通すためだけに progress.md を書き換えるのはゲート改竄(D063)として行わないこと。"
if ($operatingNote) {
  Out-Deny "$($script:AppRel) (operating note, no active phase)" ("運用中注記あり(progress.md の「状態: 運用中」。test の in_progress は取り込み残余であり進行中フェーズに数えない)。" + $denyReason)
}
Out-Deny "$($script:AppRel) (no active phase)" $denyReason

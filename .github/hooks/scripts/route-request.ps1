# UserPromptSubmit hook: ユーザーの依頼のたびに、ゲート状況の要約と受付ルーチンの契約を注入する。
# route-request.sh と同一判定・同一注入文の PowerShell 版(Copilot Windows 経路用。D060)。
# gate-hooks.json の UserPromptSubmit に配線し、既定の Copilot にも受付ルーチンを機械で届ける
# (D058 実測: VS Code は .claude/settings.json のフックを opt-in でしか読まないため、
#  .sh 版だけでは Claude Code にしか届いていなかった)。Claude フックを opt-in 有効化した
# 場合のみ両系が動き、注入テキストが重複する(無害)。
$ErrorActionPreference = 'SilentlyContinue'
# 出力 JSON の符号化を UTF-8(BOMなし) に固定(既定の CP932 では日本語注入文が壊れる)。
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}

$progress = "docs/00-overview/progress.md"

# 本体判定の補助(D058): requirements/memo.md がテンプレートのまま
# (「（ここから記入）」マーカーの後に実内容が無い、またはファイル自体が無い)なら $true。
# 実メモが書かれていれば $false。USAGE.md は全配布物に含まれるため単独では本体の
# 識別子にならない(inject-progress.ps1 と同一実装)。
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
  $body = ""
  try { $body = [IO.File]::ReadAllText($progress) } catch {}
  # 読み取りは先頭256KBに制限し、ブロック抽出は正規表現でなく線形の IndexOf で行う
  # (細工した progress.md でのバックトラック暴走=毎依頼5秒タイムアウトを防ぐ。第4回検証)。
  # 終端 --> が無い書きかけブロックは EOF までを対象にする(.sh の awk と同挙動)。
  if ($body.Length -gt 262144) { $body = $body.Substring(0, 262144) }
  $block = ""
  $bi = $body.IndexOf('<!-- GATE_STATUS')
  if ($bi -ge 0) {
    $bj = $body.IndexOf('-->', $bi)
    if ($bj -ge 0) { $block = $body.Substring($bi, $bj - $bi) } else { $block = $body.Substring($bi) }
  }
  # キーごとに初出のみ・値は行末まで(64文字で打ち切り。注記つき値も保持)。.sh と同一仕様。
  $vals = ""
  $doneCount = 0
  $seenKeys = @{}
  foreach ($ln in ($block -split "`n")) {
    $ln = $ln.TrimEnd("`r")
    if ($ln -cmatch '^(requirements|design|implementation|test|release):[ \t]*(.*)$') {
      $k = $Matches[1]; $v = $Matches[2]
      if (-not $seenKeys.ContainsKey($k)) {
        $seenKeys[$k] = $true
        if ($v.Length -gt 64) { $v = $v.Substring(0, 64) }
        $vals += "$k=$v "
        if ($v -like 'done*') { $doneCount++ }
      }
    }
  }
  $ctx = "[受付ルーチン] ゲート状況: $vals"
  # 全done判定はフェーズごとの値を数える。運用中判定は「全done、または本文(先頭256KB)に
  # 『状態: 運用中』の注記がある」の論理和とし、in_progress より優先する(.sh と同一)。
  if (($doneCount -eq 5) -or ($body -match '状態: 運用中')) {
    $ctx += "（運用中）`n変更依頼の入口は /12-change-request。依頼を受けたら request-routing スキルに従い、応答の冒頭で「分類/入口/影響」を宣言してから入口コマンドを自分で起動すること（Claude Code では Skill ツール。自分で起動できない環境ではコマンド名を提示し、提示したターンではアプリコードを編集せず停止する=場当たり編集はフェーズ外ガードが止める）。セッション分割表が新規セッションを求める移行でも、現セッションが短い（最初の1〜2ターン）ならそのまま起動する。"
  } elseif ($vals -match 'in_progress|pending_approval') {
    $ctx += "`n進行中フェーズの作業はそのフェーズのコマンドで続行する。新しい種類の依頼を受けたら request-routing スキルに従い、応答の冒頭で「分類/入口/影響」を宣言してから入口コマンドを自分で起動すること（Claude Code では Skill ツール。自分で起動できない環境ではコマンド名を提示）。セッション分割表が新規セッションを求める移行でも、現セッションが短い（最初の1〜2ターン）ならそのまま起動する。"
  } else {
    $ctx += "`n依頼を受けたら request-routing スキルに従い、応答の冒頭で「分類/入口/影響」を宣言してから入口コマンドを自分で起動すること（Claude Code では Skill ツール。自分で起動できない環境ではコマンド名を提示）。セッション分割表が新規セッションを求める移行でも、現セッションが短い（最初の1〜2ターン）ならそのまま起動する。"
  }
} elseif (Test-Path "docs/00-overview/intake-report.md") {
  # 取り込み済み・/11未完了。本体検知より先に判定(D044・.sh と同一)
  $ctx = "[受付ルーチン] 取り込み済み・未初期化(intake-report.md あり)。依頼の前に /11-brownfield-intake を自分で起動して（Claude Code では Skill ツール）as-is 逆起こしとゲート初期化を完了すること。"
} elseif (((Test-Path "DECISIONS.md") -and (Test-MemoPristine)) -or ((Test-Path ".github/harness/USAGE.md") -and (Test-MemoPristine))) {
  # ハーネス本体リポジトリ判定(D058/H-11): DECISIONS.md または USAGE.md があり、かつ memo が
  # テンプレのまま(.sh と同一。実メモが書かれたコピーは新規プロジェクト=RG-6)。
  # アプリ開発の受付契約は注入しない。出力契約の
  # 統一のため無出力ではなく最小 JSON を返す(無出力はフック失敗と区別できない。D060)。
  [Console]::Out.Write('{"continue": true}' + "`n")
  exit 0
} else {
  $ctx = "[受付ルーチン] progress.md 未作成。新規開発なら /00-start-project、既存アプリの取り込みなら /11-brownfield-intake を自分で起動する（Claude Code では Skill ツール。既存コードがあるのに /00 を実行しない）。"
}

# ConvertTo-Json が引用符・改行・非ASCIIを厳密にエスケープする(手書きエスケープをしない)
$obj = @{ hookSpecificOutput = @{ hookEventName = "UserPromptSubmit"; additionalContext = $ctx } }
$json = $obj | ConvertTo-Json -Compress -Depth 4
[Console]::Out.Write($json + "`n")

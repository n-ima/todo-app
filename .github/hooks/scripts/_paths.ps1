# 共通ライブラリ(D072。_paths.sh の PowerShell 鏡): tool_input からパス候補を網羅収集して
# 正規化し、読み取り系ツール名の除外(D059/CP-1)と新内容の収集を提供する。
# 各ガード .ps1 は `. (Join-Path $PSScriptRoot '_paths.ps1')` でドットソースし、関数が
# 無い環境(配布漏れ等)でも落ちないよう `Get-Command <fn> -ErrorAction SilentlyContinue` で
# 存在確認してから呼ぶ(fail-open)。単体で実行されても何も出力せず正常終了する
# (CI の「全 .ps1 フックの実機実行」ステップ対策)。判定規則は _paths.sh と同一に保ち、
# selftest.sh / selftest.ps1 の同名ケースで両系の挙動を固定する。
#
#   Get-ToolName $obj                       : tool_name / toolName(無ければ $null)
#   Test-ReadOnlyTool [string]$name         : 読み取り系ツール名なら $true(二段判定)
#   Get-PathCandidates $obj [auto|always|never] : 正規化済み候補の配列(重複排除・最大64)
#   Get-NewText $obj                        : new_string / content / edits[].new_string の連結
#   Get-OldText $obj                        : old_string / edits[].old_string の連結(Edit / MultiEdit の置換元。Write は空。
#                                             第7波 A7-G-3: 置換元から消えた制限キーの検出に使う)
#   Test-PathTraversal [string]$p           : '..' セグメントを含めば $true
#   ConvertTo-LongPath [string]$p           : 8.3 短縮名(C:\Users\RUNNER~1\… 等)を長形式に展開(H-10/RG-7。
#                                             guard-phase-scope.ps1 の同名関数と同一契約)
#   ConvertTo-LongPathCandidate [string]$c  : 候補パス向け(~数字を含む相対パスは cwd 結合。/ 区切りで返す)

function Get-ToolName($obj) {
  if ($null -eq $obj) { return $null }
  $t = $null
  try {
    $t = $obj.tool_name
    if (-not $t) { $t = $obj.toolName }
  } catch {}
  if ($t) { return ([string]$t).Trim() }
  return $null
}

# 読み取り系ツール名の判定規則(D059。guard-phase-scope.ps1 と同一の正規表現)。
function Test-ReadOnlyTool([string]$name) {
  if (-not $name) { return $false }
  if ($name -inotmatch '^(read|get|list|find|glob|grep|fetch|view|codebase|usages|problems)|(^|[_-])search') { return $false }
  if ($name -imatch 'edit|write|creat|replace|insert|apply|delet|patch|run|exec|update|remove|rename|move|upload|save|modify|set|index|add') { return $false }
  return $true
}

function Get-PathCandidates($obj, [string]$mode = 'auto') {
  $cands = New-Object System.Collections.Generic.List[string]
  if ($null -eq $obj -or $null -eq $obj.tool_input) { return @() }
  $ti = $obj.tool_input
  function _Collect-PathFields($o) {
    if ($null -eq $o) { return }
    if ($o -is [System.Management.Automation.PSCustomObject]) {
      foreach ($pp in $o.PSObject.Properties) {
        if ((@('file_path','filePath','path','notebook_path','uri') -contains $pp.Name) -and ($pp.Value -is [string])) { $cands.Add($pp.Value) }
        else { _Collect-PathFields $pp.Value }
      }
    } elseif ($o -is [System.Array]) { foreach ($it in $o) { _Collect-PathFields $it } }
  }
  try { _Collect-PathFields $ti } catch {}
  $hasFieldCands = ($cands.Count -gt 0)
  $tname = [string](Get-ToolName $obj)
  $scan = $false
  if ($mode -eq 'always') { $scan = $true }
  elseif ($mode -eq 'never') { $scan = $false }
  else { $scan = (($tname -imatch 'patch') -or (-not $hasFieldCands)) }
  if ($scan) {
    foreach ($k in @('input', 'patch', 'content', 'diff', 'file_text')) {
      $v = $null
      try { $v = $ti.$k } catch {}
      if ($v -is [string] -and $v) {
        foreach ($pm in [regex]::Matches($v, '(?im)^[ \t]*\*{3} (?:Add|Update|Delete) File: (.+)$')) { $cands.Add($pm.Groups[1].Value.Trim()) }
      }
    }
  }
  $out = New-Object System.Collections.Generic.List[string]
  $seen = @{}
  foreach ($c in $cands) {
    if (-not $c) { continue }
    $n = $c -replace "`r", ''
    $n = $n -replace '%3[Aa]', ':' -replace '%2[Ff]', '/' -replace '%5[Cc]', '\' -replace '%2[Ee]', '.' -replace '%20', ' '
    $n = $n -replace '^file://', '' -replace '^/([A-Za-z]):', '$1:'
    $n = $n -replace '\\', '/' -replace '/{2,}', '/'
    if (-not $n.Trim()) { continue }
    if ($seen.ContainsKey($n)) { continue }
    $seen[$n] = $true
    $out.Add($n)
    if ($out.Count -ge 64) { break }
  }
  return @($out)
}

function Get-NewText($obj) {
  if ($null -eq $obj -or $null -eq $obj.tool_input) { return '' }
  $parts = @()
  try {
    if ($obj.tool_input.new_string) { $parts += [string]$obj.tool_input.new_string }
    if ($obj.tool_input.content) { $parts += [string]$obj.tool_input.content }
    if ($obj.tool_input.file_text) { $parts += [string]$obj.tool_input.file_text }  # Copilot CLI の Write(2026-09-21 実測)
    if ($obj.tool_input.new_str) { $parts += [string]$obj.tool_input.new_str }  # Copilot CLI の Edit
    foreach ($e in @($obj.tool_input.edits)) {
      if ($e -and $e.new_string) { $parts += [string]$e.new_string }
    }
  } catch {}
  return ($parts -join "`n")
}

function Get-OldText($obj) {
  if ($null -eq $obj -or $null -eq $obj.tool_input) { return '' }
  $parts = @()
  try {
    if ($obj.tool_input.old_string) { $parts += [string]$obj.tool_input.old_string }
    if ($obj.tool_input.old_str) { $parts += [string]$obj.tool_input.old_str }  # Copilot CLI の Edit
    foreach ($e in @($obj.tool_input.edits)) {
      if ($e -and $e.old_string) { $parts += [string]$e.old_string }
    }
  } catch {}
  return ($parts -join "`n")
}

function Test-PathTraversal([string]$p) {
  if (-not $p) { return $false }
  return ($p -match '(^|/)\.\.(/|$)')
}

# ---- 8.3 短縮名の長形式展開(H-10/RG-7。guard-phase-scope.ps1 の ConvertTo-LongPath / _paths.sh の
# win_longpath と同一契約) ----
# 8.3 短縮名(C:\Users\RUNNER~1\… や C:\proj\GITHUB~1\hooks\x 等)を長形式に展開して返す。~数字セグメント
# (拡張子つき NAME~N.EXT 含む)を持つ絶対パスだけが対象。存在する最深の祖先を Get-Item(FullName=長形式)で
# 展開して残りを再結合する。[IO.Path]::GetFullPath は .NET Framework では存在パスしか展開せず Core では
# 展開しない、Add-Type の P/Invoke は毎回 200ms のコンパイルが要るため使わない。失敗時は入力のまま
# (fail-open)。保護ディレクトリ名そのもの(.github→GITHUB~1、*_template.md→REQUIR~1.MD)が短縮されると
# 字面照合が外れるため、guard-harness-config-edit / guard-template-edit は比較前にこれを通す。
function ConvertTo-LongPath([string]$p) {
  if (-not $p -or ($p -notmatch '^[A-Za-z]:[\\/].*~\d+(\.[^\\/]*)?([\\/]|$)')) { return $p }
  try {
    $head = $p -replace '/', '\'
    $tail = ''
    while ($head -and -not (Test-Path -LiteralPath $head)) {
      $idx = $head.LastIndexOf('\')
      if ($idx -le 2) { return $p }  # ドライブ直下まで存在しない
      $seg = $head.Substring($idx + 1)
      if ($tail) { $tail = $seg + '\' + $tail } else { $tail = $seg }
      $head = $head.Substring(0, $idx)
    }
    $long = (Get-Item -LiteralPath $head -Force -ErrorAction Stop).FullName
    if (-not $long) { return $p }
    if ($tail) { return ($long.TrimEnd('\') + '\' + $tail) }
    return $long
  } catch { return $p }
}

# 候補パス向けの展開(_paths.sh の expand_candidate_83_into と同一規則): ~数字セグメントを含む相対パス
# (GITHUB~1/hooks/x)は cwd に結合し、/d/x 形は d:/x に直してから ConvertTo-LongPath を通す。
# 結果は / 区切りで返す。~数字を含まないパスは無変更。
function ConvertTo-LongPathCandidate([string]$c) {
  if (-not $c -or ($c -notmatch '~\d+(\.[^\\/]*)?([\\/]|$)')) { return $c }
  try {
    if ($c -match '^/[A-Za-z]/') { $c = $c.Substring(1, 1) + ':' + $c.Substring(2) }
    elseif ($c -notmatch '^([A-Za-z]:)?[\\/]') { $c = ((Get-Location).Path.TrimEnd('\', '/')) + '/' + $c }
  } catch { return $c }
  return ((ConvertTo-LongPath $c) -replace '\\', '/')
}

# PreToolUse hook: ハーネス自体の運用ルール(エージェント定義/フック/AGENTS.md等)への
# 無断編集をdenyする。プロンプトインジェクションによる自己権限昇格・ガードレール解除を防ぐ。
# 加えて、Bash/PowerShell ツール(ペイロードに command がある場合)のコマンド文字列を検査し、
# 保護対象パスが「読み取り専用の文脈」以外に現れたら ask を返す(第5回: C-1 封鎖。
# 判定規則は guard-harness-config-edit.sh と同一。両系の挙動は selftest.sh / selftest.ps1 で固定)。
#   (1) 保護パスを含むコマンドは全セグメント(; & | 改行区切り)の先頭語が読み取り専用語
#       (cat/grep/head/tail/rg/find/ls/diff/wc/stat/file/git diff|log|show|blame/type/Get-Content/
#       Select-String 等)で、保護パスへのリダイレクト・変数代入・書き込み語(python/node/perl/awk/
#       sed/dd/install/cp/mv/tee/rsync/mklink/ln/git apply|checkout|restore|…/Set-Content/[IO.File]::
#       等)を含まない場合だけ allow、それ以外は ask。
#   (2) デコードパイプ・難読化(base64 -d | sh、-EncodedCommand、Invoke-Expression、\x エスケープ
#       等)と、パッチ適用系 git(apply/am/read-tree/update-index/stash pop)は保護パス不在でも ask。
# deny でなく ask なのは、読み取り(cat/grep)や言及との誤検知を人間が即時に解消できるようにするため。
# 注意: .github/skills/ は動的なSkill追加を許容するため原則対象外(request-routing/gate-checkのみ例外)。
# ただし skills 配下でも権限系 frontmatter(hooks:/allowed-tools:/shell:/disable-model-invocation:)の
# 追加と hooks/hooks.json の書込は ask(SC-2)。
# パス候補は _paths.ps1(D072)で file_path/filePath/path/uri/notebook_path/apply_patch 本文から
# 網羅収集する(H-2)。読み取り系ツール名(readFile 等)は冒頭で除外する(CP-1)。
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
$cmd = $null
$obj = $null
try {
  $obj = $raw | ConvertFrom-Json
  $file = $obj.tool_input.file_path
  if (-not $file) { $file = $obj.tool_input.filePath }
  if (-not $file) { $file = $obj.tool_input.path }
  if (-not $file) { $file = $obj.tool_input.notebook_path }
  # Bash/PowerShell ツールのペイロード(command)は file が無い場合のみ使う
  if (-not $file) { $cmd = $obj.tool_input.command }
} catch {
  if ($raw -match '"(file_path|filePath|path|notebook_path)"\s*:\s*"([^"]*)"') {
    $file = $Matches[2]
  } elseif ($raw -match '"command"\s*:\s*"([^"]*)"') {
    $cmd = $Matches[1]
  }
}

function Out-Deny($tag) {
  Write-HookLog 'deny' $tag
  $o = @{
    continue = $true
    hookSpecificOutput = @{
      hookEventName = "PreToolUse"
      permissionDecision = "deny"
      permissionDecisionReason = "ハーネスの運用ルール自体(agents/hooks/workflows/prompts/commands/rules/AGENTS.md/CLAUDE.md/plugin.json/settings.json/copilot 設定)はエージェントが自動で書き換えません。変更が必要な場合は人間が直接編集するか、明示的な指示のもとで行ってください。"
    }
  }
  $o | ConvertTo-Json -Depth 5 -Compress
  exit 0
}
function Out-Ask($tag, $sysMsg, $reason) {
  Write-HookLog 'ask' $tag
  $o = @{
    continue = $true
    systemMessage = $sysMsg
    hookSpecificOutput = @{
      hookEventName = "PreToolUse"
      permissionDecision = "ask"
      permissionDecisionReason = $reason
    }
  }
  $o | ConvertTo-Json -Depth 5 -Compress
  exit 0
}

# ---- 読み取り系ツールの除外 (CP-1 / D059) ----
$toolName = $null
if (Get-Command Get-ToolName -ErrorAction SilentlyContinue) { $toolName = Get-ToolName $obj }
if ((Get-Command Test-ReadOnlyTool -ErrorAction SilentlyContinue) -and (Test-ReadOnlyTool ([string]$toolName))) {
  Write-HookLog 'allow' "read-tool:$toolName"
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}

# .github/prompts/ も正レイヤ(起動指示)のため保護対象(D046)
# 例外: request-routing/gate-check はゲート契約の実体のため、動的追加を許すスキルの中でこの2つだけ保護する。
# .github/harness/PLATFORM.md と .github/instructions/ は D050 で AGENTS.md から移設した規範の実体
# のため保護対象(第2回監査で保護非対称を検出)。
# 第5回(2026-09-09 再監査 SC-1/SC-6/MP-3): .claude/rules/、.github/copilot/、.github/copilot-instructions.md、
# CLAUDE.local.md、GEMINI.md、.github/harness/model-policy.yml を追加(.sh・deny・CROSS_ITEMS・CODEOWNERS と同時)。
# 第7波(2026-09-17 IA-20260831-11): 外部 Skill / MCP のロック .github/harness/external-lock.json を保護対象に追加(.sh と同時)
$protectedPattern = '(^|[\\/])\.github[\\/]agents[\\/]|(^|[\\/])\.github[\\/]hooks[\\/]|(^|[\\/])\.github[\\/]workflows[\\/]|(^|[\\/])\.github[\\/]prompts[\\/]|(^|[\\/])\.github[\\/]harness[\\/]PLATFORM\.md$|(^|[\\/])\.github[\\/]harness[\\/]model-policy\.yml$|(^|[\\/])\.github[\\/]harness[\\/]external-lock\.json$|(^|[\\/])\.github[\\/]instructions[\\/]|(^|[\\/])\.github[\\/]copilot[\\/]|(^|[\\/])\.github[\\/]copilot-instructions\.md$|(^|[\\/])AGENTS\.md$|(^|[\\/])CLAUDE\.md$|(^|[\\/])CLAUDE\.local\.md$|(^|[\\/])GEMINI\.md$|(^|[\\/])plugin\.json$|(^|[\\/])\.vscode[\\/]settings\.json$|(^|[\\/])\.claude[\\/]settings(\.local)?\.json$|(^|[\\/])\.claude[\\/]agents[\\/]|(^|[\\/])\.claude[\\/]commands[\\/]|(^|[\\/])\.claude[\\/]rules[\\/]|(^|[\\/])\.agents[\\/]workflows[\\/]|(^|[\\/])\.github[\\/]skills[\\/](request-routing|gate-check)[\\/]|(^|[\\/])\.claude[\\/]skills[\\/](request-routing|gate-check)[\\/]'

# skills 配下(中核2スキル以外も)で ask にする書込(SC-2。.sh と同一規則)。第7波(A7-G-3): 権限系 frontmatter は
# 「拡張だけ ask、縮小・同値・既定値の明示は allow」。キー集合と規則は .sh の fm_expansion / guard-config-change.py の
# privileged_expansion と同一(selftest 両系で意味照合)。
$skillsPattern = '(^|[\\/])(\.claude|\.github)[\\/]skills[\\/]'
$skillsHooksPattern = '(^|[\\/])(\.claude|\.github)[\\/]skills[\\/]([^\\/]+[\\/])+hooks[\\/]hooks\.json$'
$privilegedFm = '(?im)^[ \t]*(allowed-tools|tools|permissions|permission-?mode|hooks|context|agent|shell|mcp|mcp-?servers|disable-model-invocation|user-invocable|disallowed-tools)[ \t]*:'
$fmGrantKeys = @('allowed-tools', 'tools', 'permissions', 'permission-mode', 'permissionmode', 'hooks', 'context', 'agent', 'shell', 'mcp', 'mcp-servers', 'mcpservers')

# 権限系キーの値ブロブ(キー行の値 + 続く字下げ行 / リスト行)。戻り値: @{ has = $true/$false; val = '...' }
function Get-FmValue([string]$text, [string]$key) {
  $acc = New-Object System.Text.StringBuilder
  $has = $false
  $in = $false
  $reKey = '^[ \t]*' + [regex]::Escape($key) + '[ \t]*:(.*)$'
  foreach ($raw in (([string]$text) -split "`n")) {
    $line = $raw.TrimEnd("`r")
    if ($in) {
      if ($line -eq '---' -or $line -notmatch '^([ \t]|-)') { $in = $false } else { [void]$acc.Append("`n" + $line); continue }
    }
    $m = [regex]::Match($line, $reKey, 'IgnoreCase')
    if ($m.Success) { $has = $true; [void]$acc.Append("`n" + $m.Groups[1].Value); $in = $true }
  }
  return @{ has = $has; val = $acc.ToString() }
}
# トークン集合(空白・カンマ・括弧・引用符で分割、小文字化。YAML リストの `-` 単独は除く)
function Get-FmTokens([string]$blob) {
  $set = New-Object 'System.Collections.Generic.HashSet[string]'
  foreach ($t in [regex]::Split(([string]$blob), '[\s,\[\]"''`]+')) {
    if ($t -and $t -ne '-') { [void]$set.Add($t.ToLowerInvariant()) }
  }
  return ,$set
}
function Test-FmSubset($a, $b) { foreach ($t in $a) { if (-not $b.Contains($t)) { return $false } }; return $true }
function Test-FmTruthy([string]$blob) {
  foreach ($t in [regex]::Split(([string]$blob), '[\s,\[\]"''`#]+')) {
    if ($t) { return (@('true', 'yes', 'on', '1') -contains $t.ToLowerInvariant()) }
  }
  return $false
}
# 拡張キーの一覧(空配列=拡張なし)。$full=$true は新内容がファイル全体(Write)、$false は置換断片(Edit)
function Get-FmExpansion([string]$old, [string]$new, [bool]$full, [string]$oldFrag) {
  $exp = @()
  foreach ($k in $fmGrantKeys) {
    $nv = Get-FmValue $new $k
    if (-not $nv.has) { continue }
    $ntoks = Get-FmTokens $nv.val
    $ov = Get-FmValue $old $k
    if (-not $ov.has) { if ($ntoks.Count -gt 0) { $exp += $k } }
    elseif (-not (Test-FmSubset $ntoks (Get-FmTokens $ov.val))) { $exp += $k }
  }
  $nv = Get-FmValue $new 'disable-model-invocation'
  if ($nv.has -and -not (Test-FmTruthy $nv.val)) {
    $ov = Get-FmValue $old 'disable-model-invocation'
    if ($ov.has -and (Test-FmTruthy $ov.val)) { $exp += 'disable-model-invocation' }
  }
  $nv = Get-FmValue $new 'user-invocable'
  if ($nv.has -and (Test-FmTruthy $nv.val)) {
    $ov = Get-FmValue $old 'user-invocable'
    if ($ov.has -and -not (Test-FmTruthy $ov.val)) { $exp += 'user-invocable' }
  }
  $ov = Get-FmValue $old 'disallowed-tools'
  if ($ov.has) {
    $otoks = Get-FmTokens $ov.val
    $nv = Get-FmValue $new 'disallowed-tools'
    if (-not $nv.has) {
      if ($full) { $exp += 'disallowed-tools' }
      elseif ((Get-FmValue $oldFrag 'disallowed-tools').has) { $exp += 'disallowed-tools' }
    } elseif (-not (Test-FmSubset $otoks (Get-FmTokens $nv.val))) { $exp += 'disallowed-tools' }
  }
  # 配列をそのまま返す(`,$exp` にすると空配列が 1 要素の入れ子になり Count が 1 になる)。呼び出し側は @() で受ける
  return $exp
}

# ---- ファイル系ツール: パス候補の網羅判定 (H-2 / D072) ----
$cands = New-Object System.Collections.Generic.List[string]
if ($file) { $cands.Add([string]$file) }
if ((-not $cmd) -and (Get-Command Get-PathCandidates -ErrorAction SilentlyContinue)) {
  foreach ($c in @(Get-PathCandidates $obj 'auto')) { if ($c) { $cands.Add([string]$c) } }
}
# 8.3 短縮名(GITHUB~1\hooks\x、CLAUDE~1\rules\x、C:\…\TMP~1\… 等)は _paths.ps1 の展開で長形式に
# 直してから照合する(H-10/RG-7 の横展開。展開できなければ字面のまま=fail-open。~数字を含まない候補は無変更)
$has83 = [bool](Get-Command ConvertTo-LongPathCandidate -ErrorAction SilentlyContinue)
$skillHit = $null
foreach ($c in $cands) {
  if (-not $c) { continue }
  if ($has83) { $c = ConvertTo-LongPathCandidate ([string]$c) }
  if ($c -match $protectedPattern) { Out-Deny $c }
  if ($c -match $skillsHooksPattern) {
    Out-Ask "skill-hooks-json:$c" "スキル配下の hooks/hooks.json への書き込みを検知しました(スキル経由の任意コマンド実行=自己権限昇格の経路)。" "skills 配下の hooks/hooks.json はスキル読込時に任意コマンドを実行できる設定です。人間の明示指示による追加であれば許可してください(SC-2)。"
  }
  if ((-not $skillHit) -and ($c -match $skillsPattern)) { $skillHit = $c }
}
if ($skillHit -and (Get-Command Get-NewText -ErrorAction SilentlyContinue)) {
  $newText = [string](Get-NewText $obj)
  $oldFrag = ''
  if (Get-Command Get-OldText -ErrorAction SilentlyContinue) { $oldFrag = [string](Get-OldText $obj) }
  # 置換元(old_string)に権限系キーがある場合も見る(制限キーだけを消す Edit=new_string が空)
  if (($newText -or $oldFrag) -and (($newText + "`n" + $oldFrag) -match $privilegedFm)) {
    # 拡張だけ ask(A7-G-3): 旧内容=ディスク上の現ファイル(無ければ新規=空)、置換元=old_string。
    # Write(old_string 無し)はファイル全体(full)、Edit / MultiEdit は断片
    $oldText = ''
    try { if (Test-Path -LiteralPath $skillHit -PathType Leaf) { $oldText = [IO.File]::ReadAllText($skillHit) } } catch {}
    $full = [bool](-not $oldFrag)
    $expanded = @(Get-FmExpansion $oldText $newText $full $oldFrag)
    if ($expanded.Count -gt 0) {
      $keys = ($expanded -join ',')
      Out-Ask "skill-privileged-frontmatter($keys):$skillHit" "スキル定義への権限系 frontmatter の拡張($keys)を検知しました。" "skills 配下への権限系 frontmatter の追加・拡張(allowed-tools / hooks / context: fork / agent / shell / mcp / permissions、disable-model-invocation を false へ、user-invocable を true へ、disallowed-tools の削除)は、スキル読込時のツール権限拡張・任意コマンド実行につながります。人間の明示指示による追加であれば許可してください(SC-2 / A7-G-3。縮小・同値の書き直しは確認なし)。"
    }
    Write-HookLog 'allow' "skill-frontmatter-no-expansion:$skillHit"
  }
}

# ---- コマンド文字列内の「保護対象パスへの書き込み」検知(第4回・第5回) ----
# 照合用コピー: 行継続(\ / ` / ^ +改行)を畳み、重複リダイレクト(2>&1 等)を落とし、\ 区切りを
# / に正規化する(元の $cmd はログ用に保持)。デコード/難読化の検査は \x エスケープを見る必要が
# あるため \→/ 変換前のコピー($cmdf)に対して行う。
$cmdf = $null
$cmdn = $null
if ($cmd) {
  $cmdf = $cmd -replace '(\\|`|\^)\r?\n', ' '
  $cmdn = $cmdf -replace '[0-9]?>&[0-9]', '' -replace '&>\s*/dev/null', ''
  $cmdn = $cmdn -replace '\\', '/'
  $cmdn = $cmdn -replace '/{2,}', '/'
  # コマンド文字列内の 8.3 短縮名トークン(C:/x/GITHUB~1/hooks/a.sh、GITHUB~1/hooks/a.sh 等)も長形式に
  # 展開してから照合する(H-10 の横展開。.sh と同一規則: / を含む ~数字トークンだけ、最大 8 個。失敗時は無変更)
  if (($cmdn -match '~\d') -and (Get-Command ConvertTo-LongPathCandidate -ErrorAction SilentlyContinue)) {
    try {
      $rx83 = New-Object System.Text.RegularExpressions.Regex '[^\s"''<>|;&=()]*~\d+[^\s"''<>|;&=()]*'
      $ev83 = [System.Text.RegularExpressions.MatchEvaluator]{ param($m) if ($m.Value -match '/') { ConvertTo-LongPathCandidate $m.Value } else { $m.Value } }
      $cmdn = $rx83.Replace($cmdn, $ev83, 8)
    } catch {}
  }
}
# 保護対象パス($protectedPattern と同じ集合のコマンド内出現形。.sh の prot_dirs/prot_files と同一)。
# ディレクトリは末尾 / の無い出現(mklink /J .github\hooks 等)も検知するため、直後が / か境界を要求。
$protDirs = '\.github/(agents|hooks|workflows|prompts|instructions|copilot)(/|$|[^0-9A-Za-z_.-])|(\.github|\.claude)/skills/(request-routing|gate-check)(/|$|[^0-9A-Za-z_.-])|\.claude/(agents|commands|rules)(/|$|[^0-9A-Za-z_.-])|\.agents/workflows(/|$|[^0-9A-Za-z_.-])'
$protFiles = '(AGENTS\.md|CLAUDE\.md|CLAUDE\.local\.md|GEMINI\.md|plugin\.json|\.github/harness/PLATFORM\.md|\.github/harness/model-policy\.yml|\.github/harness/external-lock\.json|\.github/copilot-instructions\.md|\.vscode/settings\.json|\.claude/settings(\.local)?\.json)($|[^0-9A-Za-z_])'
$protCmd = "($protDirs|$protFiles)"
# mid: 書き込み文脈から保護パスまでの引数読み飛ばし。コマンド区切り(;&|)とリダイレクトは
# 跨がず、保護パス直前が英数字等なら別ファイル名の一部とみなして不一致にする。
$mid = '([^;&|<>]*[^0-9A-Za-z_.-])?'
$sep = '(^|[\s;&|(])'
# 書き込み文脈(第4回の固定リスト。第5回の反転判定と併用=多層)
$wRedirect = ">>?[|]?\s*$mid$protCmd"
$wTee = "${sep}tee\s$mid$protCmd"
$wCopy = "${sep}(cp|mv|copy)\s+(-[^\s;&|<>]+\s+)*[^\s;&|<>]+\s$mid$protCmd"
$wSed = "${sep}sed\s+([^;&|<>]*\s)?(-i|--in-place)[^\s;&|<>]*\s$mid$protCmd"
$wPs = "${sep}(Set-Content|Add-Content|Out-File|New-Item|ni)\s$mid$protCmd"
$writePattern = "$wRedirect|$wTee|$wCopy|$wSed|$wPs"

# --- 第5回: allowlist の反転(.sh の ro_heads / git_ro / write_words / wrappers / decode_pattern と同一集合) ---
$roHeads = '^(cat|tac|head|tail|less|more|grep|egrep|fgrep|rg|ag|ack|find|fd|ls|dir|tree|diff|cmp|comm|wc|stat|file|md5sum|sha1sum|sha256sum|shasum|cksum|od|hexdump|xxd|strings|realpath|readlink|basename|dirname|test|\[|\[\[|cygpath|which|where|cd|pushd|popd|echo|printf|true|:|type|findstr|get-content|gc|select-string|sls|get-childitem|gci|get-item|gi|test-path|compare-object|get-filehash|resolve-path|format-hex|measure-object|measure|select-object|select|sort-object|sort|where-object|where|\?|foreach-object|foreach|%|group-object|group|format-list|fl|format-table|ft|out-string|out-host|out-default|write-host|write-output|write-verbose|convertfrom-json|convertto-json|test-json|split-path|join-path|get-location|pwd|gl|get-date|get-command|gcm|\[(system\.)?io\.file\]::(readalltext|readalllines|readallbytes|exists|getlastwritetime)|\[(system\.)?io\.directory\]::(exists|getfiles))$'
$gitRo = '^(diff|log|show|blame|status|grep|ls-files|ls-tree|cat-file|rev-parse|describe|shortlog|check-ignore|check-attr|branch|remote|reflog|for-each-ref|name-rev|rev-list|count-objects|var|version|help)$'
$gitSkip = '^git(\.exe)?\s+((-c|-C)\s+\S+\s+|--(git-dir|work-tree|namespace)=\S+\s+|--no-pager\s+|-p\s+)*'
$writeWords = '(^|[^0-9A-Za-z_.-])(python[0-9.]*|py|node|nodejs|deno|bun|perl|ruby|php|awk|gawk|mawk|nawk|sed|dd|install|cp|mv|copy|move|ren|rename|xcopy|robocopy|tee|rsync|scp|curl|wget|mklink|ln|patch|truncate|shred|rm|del|erase|rmdir|rd|chmod|chown|attrib|icacls|xargs|eval|exec|source|set-content|out-file|add-content|copy-item|move-item|rename-item|new-item|ni|remove-item|ri|clear-content|clc|set-item|set-itemproperty|invoke-expression|iex|invoke-webrequest|iwr|invoke-restmethod|irm|start-process|saps|invoke-command|icm|invoke-item|ii|expand-archive|writealltext|writeallbytes|writealllines|appendalltext|appendalllines|createsymboliclink|createhardlink|copyfile|writefilesync|writefile|appendfilesync|appendfile|renamesync|unlinksync|shutil|subprocess|os\.system|os\.remove|os\.rename|os\.replace|os\.symlink|os\.link|os\.unlink)([^0-9A-Za-z_-]|$)|open\(|\.write\(|\$\(|`|\s-(delete|exec|execdir|ok|okdir)(\s|$)'
$wrappers = '^((sudo|doas|command|builtin|time|nice|nohup|env|&)\s+|cmd(\.exe)?\s+/[ck]\s+|(powershell|pwsh)(\.exe)?\s+(-noprofile\s+|-nologo\s+|-noninteractive\s+|-executionpolicy\s+[a-z]+\s+)*-c(ommand)?\s+|(bash|sh|zsh|dash|ksh)\s+-c\s+)+["'']?'
$decodePattern = '((base64|base32|base32hex)[^;&|]*(-d|--decode)(\s|$)|uudecode|xxd\s+-r|certutil[^;&|]*-decode|openssl\s+(enc|base64)[^;&|]*-d|frombase64string|\[text\.encoding\]::[a-z0-9]+\.getstring|-encodedcommand|-enc(odedcommand)?\s+[a-z0-9+/=]{16,}|-e\s+[a-z0-9+/=]{16,}|invoke-expression|(^|[^0-9A-Za-z_.-])iex([^0-9A-Za-z_-]|$)|(^|[^0-9A-Za-z_.-])eval(\s|$)|\$''[^'']*\\x|\\x[0-9a-f]{2}|\\u[0-9a-f]{4}|\[char\]\s*[0-9]|\.decode\(|b64decode|codecs\.|rot13|(^|[^0-9A-Za-z_.-])rev(\s*\||$)|\|\s*(ba|z|da|k)?sh(\.exe)?(\s+-[a-z]+)*\s*($|[;&|)])|\|\s*(python[0-9.]*|py|node|deno|bun|perl|ruby|php|pwsh|powershell(\.exe)?|cmd(\.exe)?)(\s+-[a-z]+)*\s*(-\s*)?($|[;&|)]))'
$gitPatchPattern = "${sep}git(\.exe)?\s+((-c|-C)\s+\S+\s+|--(git-dir|work-tree)=\S+\s+)*(apply|am|read-tree|update-index|stash\s+(pop|apply|branch))(\s|$)"
# 第7波(R-01): reparse point(symlink / junction / hardlink)の作成は保護パス不在でも ask(.sh の reparse_pattern と同一集合)。
$reparsePattern = "${sep}(ln\s+(-[a-z]*s[a-z]*|--symbolic)|mklink(\s|$)|new-item[^;&|]*-itemtype\s+(symboliclink|junction|hardlink)|\[(system\.)?io\.(file|directory)\]::createsymboliclink|os\.(symlink|link)\(|fs\.symlink(sync)?\(|symlinkat\()"
# git plumbing / worktree で保護パスを名指しする形(.sh の git_plumbing_pattern と同一。判定ログのタグと理由文を専用にする)
$gitPlumbingPattern = "${sep}git(\.exe)?\s+((-c|-C)\s+\S+\s+|--(git-dir|work-tree)=\S+\s+)*(checkout|restore|switch|update-index|worktree\s+add|hash-object)[^;&|]*$protCmd"

# セグメント(; & | 改行で分割)が読み取り専用の文脈かを判定する
function Test-SegmentReadOnly([string]$seg) {
  # 代入の右辺に保護パス(f=AGENTS.md; $f='AGENTS.md'; set f=...; export F=...)は書き込み文脈
  if ($seg -imatch ('^[\s({!]*([A-Za-z_][A-Za-z0-9_]*=\S*\s+)*[A-Za-z_][A-Za-z0-9_]*=\S*' + $protCmd)) { return $false }
  if ($seg -imatch '^[\s({!]*(\$[A-Za-z_:][A-Za-z0-9_:]*\s*=|set\s+[A-Za-z_][A-Za-z0-9_]*=|export\s+|setx\s+)') { return $false }
  # リダイレクト: 宛先が保護パス・変数・不明なら書き込み文脈(/dev/null・nul・リテラルの非保護パスは可)
  foreach ($m in [regex]::Matches($seg, '[0-9]?>>?[|]?\s*[^\s;&|]*')) {
    $t = $m.Value -replace '^[0-9]?>>?[|]?\s*', ''
    if (-not $t) { return $false }
    if ($t -eq '/dev/null' -or $t -ieq 'nul') { continue }
    if ($t -match '^["'']?[$%]') { return $false }
    if ($t -imatch $protCmd) { return $false }
  }
  # 書き込み語
  if ($seg -imatch $writeWords) { return $false }
  # 先頭語(空白・括弧・環境変数代入・ラッパ語を読み飛ばし、パス/.exe/( 以降を落とす)
  $s = $seg -replace '^[\s({!]+', '' -replace '^([A-Za-z_][A-Za-z0-9_]*=\S*\s+)*', ''
  $s = $s -replace $wrappers, ''
  $head = [string](($s -split '\s+')[0])
  $head = $head -replace '["'']', ''
  $head = [string](($head -split '\(')[0])
  if ($head -notmatch '^\[') { $head = [string](($head -split '/')[-1]) }
  $head = ($head -replace '\.exe$', '').ToLowerInvariant()
  if (-not $head) { return $false }
  if ($head -eq 'git') {
    $sub = [string]((($s -replace $gitSkip, '') -split '\s+')[0])
    return ($sub -imatch $gitRo)
  }
  return ($head -imatch $roHeads)
}

# 保護パスを含むコマンドの全セグメントが読み取り専用か。セグメント数が上限(32)を超える
# コマンドは検査しきれない(timeout 超過は fail-open)ため、評価に入る前に数えて ask に倒す
# (fail-closed。.sh と同一。上限は Windows 実測で 32 セグメントでも約 0.8s に収まる値)
function Test-CommandReadOnly([string]$c) {
  $segs = @()
  foreach ($seg in [regex]::Split($c, '[;|&\r\n]')) {
    if ($seg.Trim()) { $segs += $seg }
  }
  if ($segs.Count -gt 32) { return $false }
  foreach ($seg in $segs) {
    if (-not (Test-SegmentReadOnly $seg)) { return $false }
  }
  return $true
}

if ($cmdn) {
  # ログには認証情報を残さない(guard-dangerous-git.ps1 と同じマスキングで先頭200文字のみ)
  # 判定ログの redaction(URL 埋め込み認証・鍵形式)と先頭 120 字への切り詰めは _log.ps1(privacy-patterns.json)が行う。
  # タグは 1 行に畳む(改行・タブは空白)
  $safeCmd = ([string]$cmd) -replace '[\r\n\t]', ' '
  $safeCmd = ($safeCmd.Substring(0, [Math]::Min(200, $safeCmd.Length))) -replace '[\r\n\t]', ' '
  if ($cmdn -imatch $writePattern) {
    Out-Ask $safeCmd "コマンドがハーネス保護対象(AGENTS.md/.github/hooks 等)へ書き込む可能性があるため確認します。" "保護対象パスへの書き込みパターン(リダイレクト/tee/cp・mv/sed -i/Set-Content 等)を検知しました。読み取りや言及のみであれば許可してください。"
  }
  if ($cmdf -imatch $decodePattern) {
    Out-Ask "decode-pipe:$safeCmd" "デコードパイプ・難読化・インタプリタへの標準入力実行(base64 -d | sh、-EncodedCommand、Invoke-Expression、\\x エスケープ等)を検知しました。" "内容を機械検査できない実行形式(デコード後実行・難読化・標準入力からのスクリプト実行)です。ハーネス保護対象へ書き込まないことを確認して許可してください(SC-4)。"
  }
  if ($cmdn -imatch $gitPatchPattern) {
    Out-Ask "git-patch:$safeCmd" "パッチ適用系の git(apply/am/read-tree/update-index/stash pop)を検知しました。" "パッチ本文の宛先はコマンド文字列から判定できません。AGENTS.md や .github/hooks 等のハーネス保護対象を書き換えないことを確認して許可してください(RG-1)。"
  }
  if ($cmdn -imatch $reparsePattern) {
    Out-Ask "reparse-point:$safeCmd" "reparse point(symlink / junction / hardlink)の作成を検知しました(ln -s / mklink / New-Item -ItemType SymbolicLink|Junction 等)。" "リンクの作成後はハーネス保護対象(.github/hooks / .claude 等)を別名経由で書き換えられ、文字列検査では追えません。保護ディレクトリを指さない・保護域内に作らないことを確認して許可してください(R-01)。"
  }
  if ($cmdn -imatch $gitPlumbingPattern) {
    Out-Ask "git-plumbing:$safeCmd" "git plumbing / worktree による保護対象パスの書き換え(checkout -- / restore / update-index / worktree add / hash-object)を検知しました。" "git の checkout -- <保護パス> / restore / update-index / worktree add <保護域内> は AGENTS.md や .github/hooks 等を HEAD やインデックスの内容で上書きします。人間の明示指示であれば許可してください(R-01)。"
  }
  if (($cmdn -imatch $protCmd) -and (-not (Test-CommandReadOnly $cmdn))) {
    Out-Ask "write-context:$safeCmd" "コマンドがハーネス保護対象(AGENTS.md/.github/hooks/.claude/settings.json 等)を読み取り専用でない文脈で扱うため確認します。" "保護対象パスが読み取り専用語(cat/grep/head/tail/diff/git diff|log|show/Get-Content 等)以外の文脈(インタプリタ・複製・リンク・変数代入・リダイレクト等)に現れました。読み取りや言及のみであれば許可してください(C-1 封鎖: 判定は allowlist 反転)。"
  }
}

@{ continue = $true } | ConvertTo-Json -Compress

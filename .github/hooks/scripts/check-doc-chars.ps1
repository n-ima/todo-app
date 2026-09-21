# PostToolUse hook: docs/ 配下の .md への書き込み後に、編集ツールが混入させがちな
# 不可視文字・文字化け(NUL / 本文中BOM / ハングル / 置換文字 / 生タブ / 全角空白 /
# BMP外漢字)を数え、0件でなければ非ブロッキングの警告を出す。
# 注意: 検査対象の文字をこのスクリプト自身に混入させないため、パターンは
# [char]0xXXXX から組み立てる(リテラルでもバックスラッシュu表記でも書かない。
# windows-shell-conventions §5 のバックスラッシュ表記破壊対策)。
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
# 共通ライブラリ(D072。無ければ従来どおり=fail-open)
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
# 読み取り系ツールは対象外(CP-1 / D059。check-doc-chars.sh と同一規則)
$toolName = $null
if (Get-Command Get-ToolName -ErrorAction SilentlyContinue) { $toolName = Get-ToolName $obj }
if ((Get-Command Test-ReadOnlyTool -ErrorAction SilentlyContinue) -and (Test-ReadOnlyTool ([string]$toolName))) {
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}
if ($file) {
  $file = $file -replace '\\', '/'
  $file = $file -replace '/{2,}', '/'
}

if (-not $file -or $file -notmatch 'docs/.*\.md$' -or -not (Test-Path $file)) {
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}

# ReadAllText は先頭BOMを除去して読むため、テキスト中に U+FEFF が残っていれば本文中のもの
$text = [System.IO.File]::ReadAllText($file)
$hits = @()
$checks = @(
  @{ n = 'NUL';               p = [string][char]0x0000 },
  @{ n = 'hombunBOM';         p = [string][char]0xFEFF },
  @{ n = 'hanguru';           p = ('[' + [char]0x1100 + '-' + [char]0x11FF + [char]0xAC00 + '-' + [char]0xD7AF + ']') },
  @{ n = 'chikanmoji-U+FFFD'; p = [string][char]0xFFFD },
  @{ n = 'nama-tab';          p = "`t" },
  @{ n = 'zenkaku-kuhaku';    p = [string][char]0x3000 },
  @{ n = 'BMP-gai-kanji';     p = ('[' + [char]0xD840 + '-' + [char]0xD87F + '][' + [char]0xDC00 + '-' + [char]0xDFFF + ']') },
  # 第3波(A7-M-8): ZWSP / NBSP / 双方向制御文字(U+202A-202E, U+2066-2069)。.sh と同じ 3 クラス・同じ名前
  @{ n = 'ZWSP';              p = [string][char]0x200B },
  @{ n = 'NBSP';              p = [string][char]0x00A0 },
  @{ n = 'bidi-seigyo';       p = ('[' + [char]0x202A + '-' + [char]0x202E + [char]0x2066 + '-' + [char]0x2069 + ']') }
)
foreach ($c in $checks) {
  $m = [regex]::Matches($text, $c.p)
  if ($m.Count -gt 0) { $hits += ("{0}:{1}" -f $c.n, $m.Count) }
}

if ($hits.Count -gt 0) {
  Write-HookLog 'warn' ("{0} {1}" -f $file, ($hits -join ' '))
  # systemMessage(ユーザー向け)に加えて hookSpecificOutput.additionalContext にも併記し、
  # モデルにも同じ警告が届くようにする(第2回監査の出力契約統一)。
  $msg = "docs への書き込みに不可視文字/文字化けの疑いがあります($file): $($hits -join ' ')。意図した文字か確認し、混入なら除去してください(NUL=ヌル文字, hombunBOM=本文中BOM, hanguru=ハングル, nama-tab=タブ, zenkaku-kuhaku=全角空白, ZWSP=ゼロ幅スペース U+200B, NBSP=ノーブレークスペース U+00A0, bidi-seigyo=双方向制御文字 U+202A〜202E/U+2066〜2069)。"
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

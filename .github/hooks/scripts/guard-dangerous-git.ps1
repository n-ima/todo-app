# PreToolUse hook: push/tag/force系のgit操作は毎回ユーザーに確認(ask)させる。
# denyではなくaskにしているのは、リリースフェーズなど正当なタイミングもあるため。
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
# 判定ログの共通実装(_log.ps1 → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
try { $__log = Join-Path $PSScriptRoot '_log.ps1'; if (Test-Path $__log) { . $__log } } catch {}
if (-not (Get-Command Write-HookLog -ErrorAction SilentlyContinue)) { function Write-HookLog($decision, $target) { } }
$cmd = $null
try {
  $obj = $raw | ConvertFrom-Json
  $cmd = $obj.tool_input.command
} catch {
  if ($raw -match '"command"\s*:\s*"([^"]*)"') {
    $cmd = $Matches[1]
  }
}

# 照合前に行継続を空白に畳む(`git \` + 改行 + `push` のように分割された危険コマンドが
# 照合をすり抜けるのを防ぐ。第2回監査)。バックスラッシュ(sh)に加えて、バッククォート
# (PowerShell)とキャレット(cmd)の行継続も畳む(第3回監査)。
if ($cmd) { $cmd = $cmd -replace '(\\|`|\^)\r?\n', ' ' }

# git のグローバルオプション(-C/-c/--git-dir/--work-tree)を挟んだ push/tag、
# フラグ順不同の rm -rf も検知する(guard-dangerous-git.sh と同一判定。D046)
# PowerShell/cmd の再帰削除(Remove-Item の -Recurse/-Force 併用は順不同、rd /s・del /s)も検知する。
# git clean の -f 系(未追跡ファイルの一括削除)も対象(第2回監査)。
$gitOpt = '((-C|-c)\s+\S+|--(git-dir|work-tree)(=\S+|\s+\S+))'
$rmRf = 'rm\s+(-\w*(r\w*f|f\w*r)\w*|-\w*r\w*\s+-\w*f\w*|-\w*f\w*\s+-\w*r\w*|--recursive\s+--force|--force\s+--recursive)'
$removeItem = 'Remove-Item[^|;&]*-Recurse[^|;&]*-Force|Remove-Item[^|;&]*-Force[^|;&]*-Recurse'
$rdDel = '(^|[;&|\s])(rd|del)\s+(/\w+\s+)*/s(\s|[;&|]|$)'
# clean は分割フラグ(-d -f)と --force も検知する(-[a-zA-Z]*f 単独では clean -d -f /
# clean --force が素通しだった。第3回監査)。
# 第7波(R-01): 作業ツリー全体を HEAD の内容で書き戻す discard-all 形(git checkout -- . / git checkout . /
# git restore . / git restore :/ / git restore --worktree .)は保護対象を名指ししないまま上書きするため ask
# (.sh と同一規則。単一パスの restore は対象外=保護パスなら guard-harness-config-edit が ask)。
$discardAll = "git\s+($gitOpt\s+)*(checkout|restore)(\s+(-[^\s;&|]+|[^-\s;&|][^\s;&|]*))*\s+(--\s+)?(\.|\./|:/|\*)(\s|[;&|]|$)"
$dangerPattern = "git\s+($gitOpt\s+)*push|reset\s+--hard|push\s+(-f|--force)|clean\s+(-[a-zA-Z]+\s+)*(-[a-zA-Z]*f[a-zA-Z]*|--force)|$rmRf|$removeItem|$rdDel|$discardAll"

# git tag は一覧表示(引数なしの `git tag` 単体、および -l/--list/-n 系)を対象外にし、
# 作成/削除系(引数あり)のみ ask する。全出現が一覧形と確定できた場合だけ除外し、
# 迷う形は従来どおり ask に倒す。
$tagAny = "git\s+($gitOpt\s+)*tag"
$tagList = "$tagAny(\s*([;&|]|$)|\s+(-l|--list|-n[0-9]*)(\s|[;&|]|$))"

$isDanger = $false
if ($cmd -and ($cmd -match $dangerPattern)) {
  $isDanger = $true
} elseif ($cmd -and ($cmd -match $tagAny)) {
  # 一覧形の判定は行単位(Multiline: $ が各行末に一致)で行う(.sh の grep が行単位で
  # 照合するのと同一。複数行コマンドの行末 `git tag` を一覧形と数えられず ask に倒れる乖離の修正)。
  $tagTotal = [regex]::Matches($cmd, $tagAny, 'IgnoreCase').Count
  $tagListed = [regex]::Matches($cmd, $tagList, 'IgnoreCase, Multiline').Count
  if ($tagTotal -gt $tagListed) { $isDanger = $true }
}

if ($isDanger) {
  # 判定ログの redaction(URL 埋め込み認証・鍵形式)と先頭 120 字への切り詰めは _log.ps1(privacy-patterns.json)が行う。
  # 理由文に「何を」(コマンドの先頭 160 文字)を含め、exact-action の承認単位を利用者が確認できるようにする
  # (codex 監査 2026-08-31 C-01 / IA-20260831-01。.sh と同一)。抜粋も _log.ps1 の redaction(Protect-HookLogText。
  # 無ければそのまま)を通す。
  $ex = $cmd.Substring(0, [Math]::Min(160, $cmd.Length))
  if (Get-Command Protect-HookLogText -ErrorAction SilentlyContinue) { $ex = Protect-HookLogText $ex }
  $ex = $ex -replace '[\r\n\t]', ' '
  # 承認バイパス下(payload の permission_mode が bypassPermissions / dontAsk / autopilot 等、または環境変数
  # HARNESS_EXTERNAL_EFFECT_MODE=deny)では ask が消音されて素通りになるため deny に倒す(guard-external-effect と同一規則)。
  $pmode = ''
  try { if ($obj -and $obj.permission_mode) { $pmode = [string]$obj.permission_mode } } catch {}
  if ((-not $pmode) -and ($raw -match '"permission_mode"\s*:\s*"([^"]*)"')) { $pmode = $Matches[1] }
  $pm = $pmode -replace '[^A-Za-z0-9_-]', ''
  $bypass = ($pm -imatch 'bypass|dontask|dont_ask|autopilot|yolo') -or ($env:HARNESS_EXTERNAL_EFFECT_MODE -eq 'deny')
  if ($bypass) {
    Write-HookLog 'deny' ('bypass:' + $cmd.Substring(0, [Math]::Min(200, $cmd.Length)))
    if (-not $pm) { $pm = 'env HARNESS_EXTERNAL_EFFECT_MODE' }
    $out = @{
      continue = $true
      systemMessage = "承認バイパス下で push/tag/force系またはrm -rfを検知したため拒否しました: $ex"
      hookSpecificOutput = @{
        hookEventName = "PreToolUse"
        permissionDecision = "deny"
        permissionDecisionReason = "承認バイパス(permission_mode=$pm)下では外部/履歴に影響する操作の ask が消音されるため deny します。通常モードで人間の exact-action 承認を経て実行してください(AGENTS.md「必ず止まる条件」2)。コマンド: $ex"
      }
    }
    $out | ConvertTo-Json -Depth 5 -Compress
    exit 0
  }
  Write-HookLog 'ask' ($cmd.Substring(0, [Math]::Min(200, $cmd.Length)))
  $out = @{
    continue = $true
    systemMessage = "push/tag/force系またはrm -rfはAGENTS.mdの方針により都度確認が必要です。"
    hookSpecificOutput = @{
      hookEventName = "PreToolUse"
      permissionDecision = "ask"
      permissionDecisionReason = "外部/履歴に影響する可能性がある操作のため確認します。何を: $ex (承認は『この操作・この対象・この引数』に対する一回限り。environment.md の「自動」分類でも push/タグ付けは承認を経ます=AGENTS.md)"
    }
  }
} else {
  $out = @{ continue = $true }
}
$out | ConvertTo-Json -Depth 5 -Compress

# PreToolUse hook(matcher `Agent|Task`。Claude Code 側 .claude/settings.json のみに配線。gate-hooks.json には
# 配線しない=VS Code は matcher を無視し Copilot の runSubagent は入力形が異なるため):
# サブエージェント呼出の model パラメータを役割別モデル方針(.github/harness/model-policy.yml)と照合する。
# 三層強制(permissions.deny / 本フック / PostToolUse・SubagentStop の記録)の第2層(監査 2026-09-09 §4.5)。
# 判定規則は guard-subagent-model.sh と同一(sh/ps1 は同じ挙動を両方の selftest で固定する):
#   - tool_input.model が方針外(候補外モデル or 役割の許可集合に無い名前)なら deny
#   - model 省略: 役割の既定が inherit なら素通し({"continue": true})。既定が具体モデルの役割のみ
#     updatedInput で tool_input 全体を複製し model だけ差し替えて注入
#   - 非 Agent/Task ツール・subagent_type 欠落・表に無い役割・表が読めない・壊れた JSON は無出力 exit 0
#     (fail-open。CI windows の固定ペイロードでも空出力)
# 役割表は tools/generate-adapters.py が下のマーカー間に埋め込む(手で書かない)。
# テスト用: GUARD_SUBAGENT_MODEL_TABLE=<json ファイル> で表を差し替えられる(fail-open の範囲内)。
$ErrorActionPreference = 'SilentlyContinue'
# stdin/stdout を UTF-8(BOMなし) に固定する(既定の CP932 では日本語の出力 JSON が壊れる)。
try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
$raw = [Console]::In.ReadToEnd()
# PowerShell 5.1 はリダイレクトされた stdin の先頭に U+FEFF を現すことがある(他フックと同じ対処)
$raw = $raw.TrimStart([char]0xFEFF)
# 判定ログの共通実装(_log.ps1 → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
try { $__log = Join-Path $PSScriptRoot '_log.ps1'; if (Test-Path $__log) { . $__log } } catch {}
if (-not (Get-Command Write-HookLog -ErrorAction SilentlyContinue)) { function Write-HookLog($decision, $target) { } }

# BEGIN GENERATED: model-policy-role-table
$roleTableJson = '{"deny_exact":["haiku"],"deny_prefix":["claude-haiku-4-5"],"generated_from":".github/harness/model-policy.yml","retrieved":"2026-09-10","roles":{"reviewer":{"allowed_exact":["inherit","fable","opus","sonnet"],"allowed_prefix":["claude-fable-5-1","claude-opus-5","claude-sonnet-5","claude-fable-5","claude-mythos","claude-opus-4","claude-sonnet-4"],"default":"inherit"},"spec-critic":{"allowed_exact":["inherit","fable","opus","sonnet"],"allowed_prefix":["claude-fable-5-1","claude-opus-5","claude-sonnet-5","claude-fable-5","claude-mythos","claude-opus-4","claude-sonnet-4"],"default":"inherit"},"task-worker":{"allowed_exact":["inherit","fable","opus","sonnet"],"allowed_prefix":["claude-fable-5-1","claude-opus-5","claude-sonnet-5","claude-fable-5","claude-mythos","claude-opus-4","claude-sonnet-4"],"default":"inherit"}}}'
# END GENERATED: model-policy-role-table

if ($env:GUARD_SUBAGENT_MODEL_TABLE -and (Test-Path -LiteralPath $env:GUARD_SUBAGENT_MODEL_TABLE)) {
  try { $roleTableJson = [IO.File]::ReadAllText($env:GUARD_SUBAGENT_MODEL_TABLE) } catch { exit 0 }
}

function Test-InList($m, $exact, $prefix) {
  # sh 版(node/python)と同じ照合: exact は完全一致(大文字小文字を区別)、prefix は前方一致
  foreach ($e in @($exact)) { if ($null -ne $e -and ($m -ceq [string]$e)) { return $true } }
  foreach ($p in @($prefix)) { if ($null -ne $p -and ([string]$p) -ne '' -and (($m -ceq [string]$p) -or $m.StartsWith([string]$p))) { return $true } }
  return $false
}

$obj = $null
$table = $null
try { $obj = $raw | ConvertFrom-Json } catch { exit 0 }
try { $table = $roleTableJson | ConvertFrom-Json } catch { exit 0 }
if ($null -eq $obj -or $null -eq $table) { exit 0 }
$tn = $obj.tool_name
if ($null -ne $tn -and ($tn -cne 'Agent') -and ($tn -cne 'Task')) { exit 0 }
$ti = $obj.tool_input
if ($null -eq $ti) { exit 0 }
$role = $ti.subagent_type
if (-not ($role -is [string]) -or $role -eq '') { exit 0 }
if ($null -eq $table.roles) { exit 0 }
$roleProp = $table.roles.PSObject.Properties[$role]
if ($null -eq $roleProp) { exit 0 }
$r = $roleProp.Value
$model = $ti.model

if ($null -eq $model -or ([string]$model) -eq '') {
  $d = $r.default
  if (-not $d -or ($d -ceq 'inherit')) { Write-Output '{"continue": true}'; exit 0 }
  $ti | Add-Member -NotePropertyName 'model' -NotePropertyValue ([string]$d) -Force
  Write-HookLog 'inject' ("{0} model={1}" -f $role, $d)
  $out = @{
    continue = $true
    hookSpecificOutput = @{ hookEventName = 'PreToolUse'; permissionDecision = 'allow'; updatedInput = $ti }
  }
  $out | ConvertTo-Json -Depth 10 -Compress
  exit 0
}

$m = [string]$model
$reason = $null
if (Test-InList $m $table.deny_exact $table.deny_prefix) { $reason = '候補外のモデル' }
elseif (-not (Test-InList $m $r.allowed_exact $r.allowed_prefix)) { $reason = '方針に無いモデル名' }
if ($reason) {
  Write-HookLog 'deny' ("{0} model={1}" -f $role, $m)
  $why = "サブエージェント $role への model=$m は役割別モデル方針(.github/harness/model-policy.yml)で許可されていません($reason)。model を省略して方針の既定(inherit)を使うか、方針を変更して python tools/generate-adapters.py を再実行してください。"
  $out = @{
    continue = $true
    hookSpecificOutput = @{ hookEventName = 'PreToolUse'; permissionDecision = 'deny'; permissionDecisionReason = $why }
  }
  $out | ConvertTo-Json -Depth 5 -Compress
  exit 0
}
Write-Output '{"continue": true}'

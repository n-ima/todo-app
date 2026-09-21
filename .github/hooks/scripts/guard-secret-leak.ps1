# PreToolUse hook: ハードコードされた認証情報っぽい文字列の書き込みを検知する。
# 高確度パターン(クラウドの鍵形式・秘密鍵ヘッダ等)はdeny、
# 汎用パターン(api_key=... 等、誤検知しうる)はaskに留める。
# 第5回(2026-09-09 再監査 RG-5/CP-1): 読み取り系ツール名は冒頭で除外する(guard-secret-leak.sh と同一規則)。
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

# 共通ライブラリ(無ければ従来どおり全ペイロードを走査する=fail-open)
try { $__paths = Join-Path $PSScriptRoot '_paths.ps1'; if (Test-Path $__paths) { . $__paths } } catch {}
# 判定ログの共通実装(_log.ps1 → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
try { $__log = Join-Path $PSScriptRoot '_log.ps1'; if (Test-Path $__log) { . $__log } } catch {}
if (-not (Get-Command Write-HookLog -ErrorAction SilentlyContinue)) { function Write-HookLog($decision, $target) { } }

# ---- 読み取り系ツールの除外 (CP-1 / D059) ----
$obj = $null
try { $obj = $raw | ConvertFrom-Json } catch {}
$toolName = $null
if (Get-Command Get-ToolName -ErrorAction SilentlyContinue) { $toolName = Get-ToolName $obj }
if ((Get-Command Test-ReadOnlyTool -ErrorAction SilentlyContinue) -and (Test-ReadOnlyTool ([string]$toolName))) {
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}

# AIza(Google APIキー)・(sk|rk)_live_(Stripe 本番キー)・hooks.slack.com/services/(Slack
# Incoming Webhook)も高確度として検知する(第2回監査)。
# 第5回(RG-5): npm・JWT・Azure AccountKey=・SAS sig=・OpenAI・GitLab・Hugging Face・PyPI・SendGrid・
# DigitalOcean・Google OAuth・AWS 一時鍵・Slack App・PGP 秘密鍵を追加(.sh と同一パターン)。
$highConfidence = 'AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|-----BEGIN( RSA| EC| OPENSSH| DSA| PGP)? PRIVATE KEY( BLOCK)?-----|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|xapp-[0-9]-[A-Za-z0-9-]{10,}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|(sk|rk)_live_[A-Za-z0-9]{20,}|hooks\.slack\.com/services/|npm_[A-Za-z0-9]{36}|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|AccountKey=[A-Za-z0-9+/=]{40,}|(\?|&|&amp;)sig=[A-Za-z0-9%+/=]{30,}|sk-proj-[A-Za-z0-9_-]{40,}|sk-[A-Za-z0-9]{48}|glpat-[A-Za-z0-9_-]{20}|hf_[A-Za-z0-9]{34}|pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}|SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}|dop_v1_[a-f0-9]{64}|ya29\.[A-Za-z0-9_-]{30,}'
# 汎用パターン(第5回で緩和。.sh と同一規則): クォート付きは 16 文字以上で ask、クォート無しは
# 16 文字以上かつ数字を含む場合のみ ask。
$genericKey = '(api[_-]?key|secret|token|password|passwd|client_secret|access_key)'
$genericQuoted = $genericKey + '\\?[''"]?\s*[:=]\s*\\?[''"][A-Za-z0-9/+=_-]{16,}\\?[''"]'
$genericBare = $genericKey + '\\?[''"]?\s*[:=]\s*([A-Za-z0-9/+=_-]{16,})'

# 高確度は大文字小文字を区別して照合する(.sh の grep -Eq と同一。-match だと
# wakiannotation1234567 のような通常文字列を AKIA 形式と誤検知して deny する)。
if ($raw -cmatch $highConfidence) {
  Write-HookLog 'deny' 'secret:high-confidence'
  $out = @{
    continue = $true
    hookSpecificOutput = @{
      hookEventName = "PreToolUse"
      permissionDecision = "deny"
      permissionDecisionReason = "クラウド認証情報/秘密鍵とみられる文字列を検出しました。認証情報はコードやドキュメントに直接書かず、environment.mdに記載したシークレット管理先(GitHub Secrets等)を参照してください。検知された鍵を削除する目的の編集であれば、Writeツールで該当箇所を除去した全文に置き換えてください(denyはペイロード全体を走査するため、Editのold_stringにもマッチします)。"
    }
  }
  $out | ConvertTo-Json -Depth 5 -Compress
  exit 0
}

$genericHit = $false
if ($raw -match $genericQuoted) {
  $genericHit = $true
} else {
  foreach ($m in [regex]::Matches($raw, $genericBare, 'IgnoreCase')) {
    if ($m.Groups[2].Value -match '[0-9]') { $genericHit = $true; break }
  }
}
if ($genericHit) {
  Write-HookLog 'ask' 'secret:generic'
  $out = @{
    continue = $true
    systemMessage = "ハードコードされた認証情報らしき文字列を検出しました(誤検知の可能性もあります)。意図した内容か確認してください。"
    hookSpecificOutput = @{
      hookEventName = "PreToolUse"
      permissionDecision = "ask"
      permissionDecisionReason = "認証情報らしきパターンを検出したため確認します。"
    }
  }
} else {
  $out = @{ continue = $true }
}
$out | ConvertTo-Json -Depth 5 -Compress

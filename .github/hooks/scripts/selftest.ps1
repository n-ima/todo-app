# フック自己テスト(PowerShell 5.1 / Copilot Windows 経路用): 代表ペイロードを各 .ps1 ガードに
# 子プロセス(powershell.exe)経由で流し、permissionDecision / systemMessage が期待どおり
# 返るかを突き合わせる。selftest.sh の代表 deny/ask/warn ケースの .ps1 版
# (dangerous-git / harness-config-edit(コマンド書き込み迂回の ask 含む) / template-edit /
#  secret-leak / phase-scope / gate-tamper / stale-gate / doc-chars + 日本語隣接シークレット)。
# CI への配線は harness-ci.yml 側で行う。
# 使い方: powershell -NoProfile -ExecutionPolicy Bypass -File .github/hooks/scripts/selftest.ps1
# (全PASSなら exit 0)
# 注意: 実行すると判定ログ(logs/hook-decisions.jsonl。A6-20 で JSONL 化)にテスト分の行が入る(ローカルのみ・無害)。
#       第6波以降のケースは HARNESS_HOOK_LOG_DIR で一時ディレクトリに向け、実 logs/ を汚さない。
$ErrorActionPreference = 'Continue'
$scriptsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:pass = 0
$script:fail = 0

function Invoke-Hook($scriptName, $payload, $workDir, $extraArgs) {
  # 子プロセスの stdin は Process API で BOM なし UTF-8 のバイト列を直接書く。
  # PowerShell のパイプ($payload | powershell.exe)は先頭に BOM を付けてしまい、
  # ガード側の ConvertFrom-Json が失敗して grep 相当のフォールバック判定に落ちる
  # (=本番の配線(BOMなしJSON)と違う経路をテストしてしまう)ため使わない。
  # $extraArgs(省略可)はスクリプトの位置引数(例: inject-progress.ps1 の PreCompact)。
  $path = Join-Path $scriptsDir $scriptName
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = "powershell.exe"
  $psi.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $path + '"'
  if ($extraArgs) { $psi.Arguments += ' ' + $extraArgs }
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding $false
  if ($workDir) { $psi.WorkingDirectory = $workDir } else { $psi.WorkingDirectory = (Get-Location).Path }
  $proc = [System.Diagnostics.Process]::Start($psi)
  $bytes = (New-Object System.Text.UTF8Encoding $false).GetBytes($payload)
  $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
  $proc.StandardInput.Close()
  $out = $proc.StandardOutput.ReadToEnd()
  $proc.StandardError.ReadToEnd() | Out-Null
  $proc.WaitForExit()
  $out
}

function Check($desc, $scriptName, $payload, $expected, $workDir) {
  $out = Invoke-Hook $scriptName $payload $workDir
  $ok = $false
  switch ($expected) {
    'deny'  { if ($out -match '"permissionDecision":\s*"deny"') { $ok = $true } }
    'ask'   { if ($out -match '"permissionDecision":\s*"ask"') { $ok = $true } }
    'warn'  { if ($out -match '"systemMessage"') { $ok = $true } }
    'allow' {
      # 無出力を allow と誤認しない(selftest.sh と同じ方針): 非空の出力があり、
      # かつ ask/deny/warn 判定を含まないこと
      if ($out -and ($out -notmatch '"permissionDecision"') -and ($out -notmatch '"systemMessage"')) { $ok = $true }
    }
  }
  if ($ok) {
    $script:pass++
    Write-Output "PASS: $desc"
  } else {
    $script:fail++
    Write-Output "FAIL: $desc"
    Write-Output "  expected: $expected"
    Write-Output "  got: $out"
  }
}

# --- guard-dangerous-git.ps1: 危険 git 操作と再帰削除の ask、無害コマンドの allow ---
Check "dangerous-git: quoted cd + git push -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"cd \"D:/proj\" && git push origin main"}}' 'ask'
Check "dangerous-git: plain git push -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git push origin main"}}' 'ask'
Check "dangerous-git: git -C path push -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git -C /some/repo push origin main"}}' 'ask'
Check "dangerous-git: rm -fr -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"rm -fr build"}}' 'ask'
Check "dangerous-git: Remove-Item -Recurse -Force -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"Remove-Item -Recurse -Force src"}}' 'ask'
# 第2回監査: git clean -f 系と行継続(バックスラッシュ+改行)分割の検知
Check "dangerous-git: git clean -fd -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git clean -fd"}}' 'ask'
Check "dangerous-git: line-continuation git push -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git \\\npush origin main"}}' 'ask'
Check "dangerous-git: harmless git status -> allow" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git status"}}' 'allow'

# --- guard-harness-config-edit.ps1: 保護対象の deny(第2回監査で追加した PLATFORM.md / instructions 含む)---
Check "harness-config-edit: agents path -> deny" 'guard-harness-config-edit.ps1' '{"tool_input":{"file_path":"d:\\proj\\.github\\agents\\reviewer.agent.md"}}' 'deny'
Check "harness-config-edit: AGENTS.md -> deny" 'guard-harness-config-edit.ps1' '{"tool_input":{"file_path":"d:\\proj\\AGENTS.md"}}' 'deny'
Check "harness-config-edit: harness PLATFORM.md -> deny" 'guard-harness-config-edit.ps1' '{"tool_input":{"file_path":"d:\\proj\\.github\\harness\\PLATFORM.md"}}' 'deny'
Check "harness-config-edit: instructions path -> deny" 'guard-harness-config-edit.ps1' '{"tool_input":{"file_path":"d:\\proj\\.github\\instructions\\docs-authoring.instructions.md"}}' 'deny'
Check "harness-config-edit: normal file -> allow" 'guard-harness-config-edit.ps1' '{"tool_input":{"file_path":"d:\\proj\\src\\app.ts"}}' 'allow'
# 第4回: Bash/PowerShell 書き込み迂回(リダイレクト/tee/sed -i)は ask、読み取りは allow(selftest.sh と同一ケース)
Check "harness-config-edit: echo >> AGENTS.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_input":{"command":"echo x >> AGENTS.md"}}' 'ask'
Check "harness-config-edit: tee harness PLATFORM.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_input":{"command":"printf y | tee .github/harness/PLATFORM.md"}}' 'ask'
Check "harness-config-edit: cat AGENTS.md (read) -> allow" 'guard-harness-config-edit.ps1' '{"tool_input":{"command":"cat AGENTS.md"}}' 'allow'
Check "harness-config-edit: sed -i AGENTS.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_input":{"command":"sed -i -e s/a/b/ AGENTS.md"}}' 'ask'

# --- guard-template-edit.ps1: テンプレート編集の deny(大文字パス含む)---
Check "template-edit: template path -> deny" 'guard-template-edit.ps1' '{"tool_input":{"file_path":"d:\\proj\\docs\\01-requirements\\requirements_template.md"}}' 'deny'
Check "template-edit: uppercase template path -> deny" 'guard-template-edit.ps1' '{"tool_input":{"file_path":"d:\\proj\\docs\\01-requirements\\REQUIREMENTS_TEMPLATE.MD"}}' 'deny'
Check "template-edit: non-template -> allow" 'guard-template-edit.ps1' '{"tool_input":{"file_path":"d:\\proj\\docs\\01-requirements\\requirements.md"}}' 'allow'

# --- guard-secret-leak.ps1: 高確度 deny(第2回監査で追加した AIza / sk_live / Slack Webhook 含む)と汎用 ask ---
Check "secret-leak: sk-ant api key -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"api_key = \"sk-ant-abcdefghijklmnopqrstuvwx\""}}' 'deny'
Check "secret-leak: AKIA aws key -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"aws = \"AKIAABCDEFGHIJKLMNOP\""}}' 'deny'
Check "secret-leak: AIza google api key -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"key = \"AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz0123456\""}}' 'deny'
Check "secret-leak: sk_live stripe key -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"stripe = \"sk_live_abcdefghijklmnopqrstuv\""}}' 'deny'
Check "secret-leak: slack webhook url -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"url = \"https://hooks.slack.com/services/T000/B000/XXXX\""}}' 'deny'
Check "secret-leak: generic password assignment -> ask" 'guard-secret-leak.ps1' '{"tool_input":{"content":"password = \"abcdefghijklmnop1234\""}}' 'ask'
# 日本語の地の文に隣接したシークレットも検知できること(CP932 化けで照合が壊れない)
Check "secret-leak: japanese-adjacent sk-ant -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"APIキーはsk-ant-abcdefghijklmnopqrstuvwxを使う"}}' 'deny'
Check "secret-leak: harmless content -> allow" 'guard-secret-leak.ps1' '{"tool_input":{"content":"const version = 1"}}' 'allow'

# --- guard-phase-scope.ps1: 進行中フェーズの有無で ask/allow が切り替わること(fixture 必須)---
$fx = Join-Path $env:TEMP ("hook-selftest-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path (Join-Path $fx "docs\00-overview") -Force | Out-Null
$gateAllDone = "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: done`ntest: done`nrelease: done`n-->"
Set-Content -Path (Join-Path $fx "docs\00-overview\progress.md") -Value $gateAllDone -Encoding UTF8
$fxs = $fx -replace '\\', '/'
Check "phase-scope: app file, all done -> deny" 'guard-phase-scope.ps1' ('{"tool_input":{"file_path":"' + $fxs + '/src/app.ts"}}') 'deny' $fx
Check "phase-scope: docs file, all done -> allow" 'guard-phase-scope.ps1' ('{"tool_input":{"file_path":"' + $fxs + '/docs/01-requirements/requirements.md"}}') 'allow' $fx
# D063: 複数ファイル/パッチ系ツールもパス候補の再帰収集で判定される
Check "phase-scope: multi-file edit tool (files array) -> deny" 'guard-phase-scope.ps1' '{"tool_name":"edit_files","tool_input":{"files":[{"path":"app/main.py"},{"path":"docs/x.md"}]}}' 'deny' $fx
Check "phase-scope: apply_patch (Update File marker) -> deny" 'guard-phase-scope.ps1' '{"tool_name":"apply_patch","tool_input":{"input":"*** Begin Patch\n*** Update File: app/main.py\n+x\n*** End Patch"}}' 'deny' $fx
# D066: 第5回敵対的検証の攻撃リプレイ
Check "phase-scope: path traversal (docs/../app) -> deny" 'guard-phase-scope.ps1' '{"tool_input":{"file_path":"docs/../app/main.py"}}' 'deny' $fx
Check "phase-scope: encoded traversal (%2e%2e) -> deny" 'guard-phase-scope.ps1' '{"tool_input":{"file_path":"docs/%2e%2e/app/main.py"}}' 'deny' $fx
Check "phase-scope: canonical file URI (%3A) -> deny" 'guard-phase-scope.ps1' ('{"tool_input":{"uri":"file:///' + ($fxs -replace ':','%3A') + '/src/app.ts"}}') 'deny' $fx
Check "phase-scope: 9-file batch (app at #9) -> deny" 'guard-phase-scope.ps1' '{"tool_name":"edit_files","tool_input":{"files":[{"path":"docs/a.md"},{"path":"docs/b.md"},{"path":"docs/c.md"},{"path":"docs/d.md"},{"path":"docs/e.md"},{"path":"docs/f.md"},{"path":"docs/g.md"},{"path":"docs/h.md"},{"path":"app/memo.py"}]}}' 'deny' $fx
Check "phase-scope: patch in content field -> deny" 'guard-phase-scope.ps1' '{"tool_name":"apply_patch","tool_input":{"content":"*** Update File: app/memo.py\n+x"}}' 'deny' $fx
Check "phase-scope: lax patch marker (indent+lowercase) -> deny" 'guard-phase-scope.ps1' '{"tool_name":"apply_patch","tool_input":{"input":"  *** update file: app/main.py\n+x"}}' 'deny' $fx
Check "phase-scope: write-tool without any path -> ask" 'guard-phase-scope.ps1' '{"tool_name":"editFiles","tool_input":{"foo":"bar"}}' 'ask' $fx
Check "gate-tamper: pending_approval transition -> warn" 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"docs/00-overview/progress.md","content":"implementation: pending_approval"}}' 'warn' $fx
Check "gate-tamper: legend mention (no key line) -> allow" 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"docs/00-overview/progress.md","content":"註: 状態は in_progress などを取る"}}' 'allow' $fx
Check "gate-tamper: patch-based gate edit -> warn" 'warn-gate-tamper.ps1' '{"tool_name":"apply_patch","tool_input":{"input":"*** Update File: docs/00-overview/progress.md\nimplementation: done"}}' 'warn' $fx
# D070: レポート無き release done は専用警告(レポートがあれば一般 done 警告に落ちる)
$out = Invoke-Hook 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"docs/00-overview/progress.md","content":"release: done"}}' $fx
if ($out -match [regex]::Escape('security-review-report.md がありません')) {
  $script:pass++; Write-Output "PASS: gate-tamper: release done without security report -> dedicated warn"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: release done without security report -> dedicated warn"; Write-Output "  got: $out"
}
New-Item -ItemType Directory -Force (Join-Path $fx 'docs\04-test') | Out-Null
[IO.File]::WriteAllText((Join-Path $fx 'docs\04-test\security-review-report.md'), "report", (New-Object System.Text.UTF8Encoding $false))
$out = Invoke-Hook 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"docs/00-overview/progress.md","content":"release: done"}}' $fx
if (($out -match [regex]::Escape('明示承認')) -and ($out -notmatch [regex]::Escape('がありません'))) {
  $script:pass++; Write-Output "PASS: gate-tamper: release done with report -> generic done warn"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: release done with report -> generic done warn"; Write-Output "  got: $out"
}
Remove-Item -Recurse -Force (Join-Path $fx 'docs\04-test')
Check "gate-tamper: in_progress transition -> warn" 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"docs/00-overview/progress.md","content":"implementation: in_progress"}}' 'warn' $fx
$gateInProgress = "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: in_progress`ntest: done`nrelease: done`n-->"
Set-Content -Path (Join-Path $fx "docs\00-overview\progress.md") -Value $gateInProgress -Encoding UTF8
Check "phase-scope: app file, in_progress -> allow" 'guard-phase-scope.ps1' ('{"tool_input":{"file_path":"' + $fxs + '/src/app.ts"}}') 'allow' $fx
Remove-Item -Recurse -Force $fx

# --- warn-gate-tamper.ps1: ゲート状態を書き換える編集の warn(progress「: done」/ tasks [X])---
Check "gate-tamper: progress.md ': done' transition -> warn" 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"d:\\proj\\docs\\00-overview\\progress.md","new_string":"implementation: done"}}' 'warn'
Check "gate-tamper: tasks.md [X] mark -> warn" 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"d:\\proj\\docs\\03-implementation\\tasks.md","new_string":"- [X] T1 done"}}' 'warn'
Check "gate-tamper: unrelated file with done -> allow" 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"d:\\proj\\src\\app.ts","new_string":"status: done"}}' 'allow'

# --- warn-stale-gate.ps1: done 文書の編集は warn、in_progress/対象外は allow(fixture 必須。
#     期待値の規則は selftest.sh の同名ケースと同一。第4回で未カバーだった warn 系を対称化)---
$fx2 = Join-Path $env:TEMP ("hook-selftest-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path (Join-Path $fx2 "docs\00-overview") -Force | Out-Null
Set-Content -Path (Join-Path $fx2 "docs\00-overview\progress.md") -Value "requirements: done`ndesign: in_progress`ntest: done" -Encoding UTF8
Check "warn-stale-gate: done doc (backslash path) -> warn" 'warn-stale-gate.ps1' '{"tool_input":{"file_path":"d:\\proj\\docs\\01-requirements\\requirements.md"}}' 'warn' $fx2
Check "warn-stale-gate: in_progress doc -> allow" 'warn-stale-gate.ps1' '{"tool_input":{"file_path":"d:\\proj\\docs\\02-design\\architecture.md"}}' 'allow' $fx2
Check "warn-stale-gate: unrelated file -> allow" 'warn-stale-gate.ps1' '{"tool_input":{"file_path":"d:\\proj\\src\\app.ts"}}' 'allow' $fx2
Check "warn-stale-gate: template file -> allow" 'warn-stale-gate.ps1' '{"tool_input":{"file_path":"d:\\proj\\docs\\01-requirements\\requirements_template.md"}}' 'allow' $fx2
Remove-Item -Recurse -Force $fx2

# --- check-doc-chars.ps1: docs 配下の md の不可視文字(NUL)は warn、クリーン/対象外は allow
#     (実ファイル必須。期待値の規則は selftest.sh の同名ケースと同一)---
$fx3 = Join-Path $env:TEMP ("hook-selftest-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path (Join-Path $fx3 "docs") -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $fx3 "docs\bad.md"), ("normal" + [char]0 + "text"))
[System.IO.File]::WriteAllText((Join-Path $fx3 "docs\good.md"), "# normal text")
$fx3s = $fx3 -replace '\\', '/'
Check "doc-chars: NUL in docs md -> warn" 'check-doc-chars.ps1' ('{"tool_input":{"file_path":"' + $fx3s + '/docs/bad.md"}}') 'warn'
Check "doc-chars: clean docs md -> allow" 'check-doc-chars.ps1' ('{"tool_input":{"file_path":"' + $fx3s + '/docs/good.md"}}') 'allow'
Check "doc-chars: non-docs file -> allow" 'check-doc-chars.ps1' ('{"tool_input":{"file_path":"' + $fx3s + '/src.md"}}') 'allow'
Remove-Item -Recurse -Force $fx3

# --- 本体判定(D058): USAGE.md 単独では本体にしない。memo プリスティンで判別する回帰テスト ---
# (D053 の過剰修正でテンプレ由来の新規プロジェクトが本体誤検知→/00 拒否になった回帰の再発防止)
$fx9 = Join-Path $env:TEMP ("hooktest9-" + [IO.Path]::GetRandomFileName())
foreach ($d in 'fresh\.github\harness','fresh\requirements','copy\.github\harness','copy\requirements') {
  New-Item -ItemType Directory -Force (Join-Path $fx9 $d) | Out-Null
}
$utf8nb = New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText((Join-Path $fx9 'fresh\.github\harness\USAGE.md'), "usage", $utf8nb)
[IO.File]::WriteAllText((Join-Path $fx9 'fresh\requirements\memo.md'), "# 要件メモ`nCLIメモ帳ツールを作りたい。add/list/delete。`n", $utf8nb)
[IO.File]::WriteAllText((Join-Path $fx9 'copy\.github\harness\USAGE.md'), "usage", $utf8nb)
[IO.File]::WriteAllText((Join-Path $fx9 'copy\requirements\memo.md'), "# 要件メモ（自由記述）`n`nここに自由に書いてください。`n`n---`n`n（ここから記入）`n", $utf8nb)
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx9 'fresh')
if ($out -match '00-start-project') {
  $script:pass++; Write-Output "PASS: body-detect: fresh project SessionStart -> /00 guidance"
} else {
  $script:fail++; Write-Output "FAIL: body-detect: fresh project SessionStart -> /00 guidance"; Write-Output "  got: $out"
}
New-Item -ItemType Directory -Force (Join-Path $fx9 'fresh\docs\00-overview') | Out-Null
[IO.File]::WriteAllText((Join-Path $fx9 'fresh\docs\00-overview\notepad.md'), "- 実装方針は案Bで検討中(未確定)`n", $utf8nb)
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx9 'fresh')
if ($out -match [regex]::Escape('案Bで検討中')) {
  $script:pass++; Write-Output "PASS: inject-progress: notepad content injected"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress: notepad content injected"; Write-Output "  got: $out"
}
$bigNp = New-Object System.Text.StringBuilder
for ($i = 0; $i -lt 200; $i++) { [void]$bigNp.AppendFormat("未確定メモ{0:d3}件目`n", $i) }
[IO.File]::WriteAllText((Join-Path $fx9 'fresh\docs\00-overview\notepad.md'), $bigNp.ToString(), $utf8nb)
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx9 'fresh')
if (($out -match [regex]::Escape('未確定メモ000')) -and ($out -notmatch [regex]::Escape('未確定メモ199'))) {
  $script:pass++; Write-Output "PASS: inject-progress: notepad 2KB byte-cap (head kept, tail cut)"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress: notepad 2KB byte-cap (head kept, tail cut)"
}
Remove-Item (Join-Path $fx9 'fresh\docs\00-overview\notepad.md') -Force
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx9 'copy')
if ($out -match [regex]::Escape('本体リポジトリ')) {
  $script:pass++; Write-Output "PASS: body-detect: body ZIP copy SessionStart -> body notice"
} else {
  $script:fail++; Write-Output "FAIL: body-detect: body ZIP copy SessionStart -> body notice"; Write-Output "  got: $out"
}
Check "body-detect: fresh project app edit -> ask" 'guard-phase-scope.ps1' '{"tool_input":{"file_path":"app/main.py"}}' 'ask' (Join-Path $fx9 'fresh')
Check "body-detect: body ZIP copy app edit -> allow" 'guard-phase-scope.ps1' '{"tool_input":{"file_path":"app/main.py"}}' 'allow' (Join-Path $fx9 'copy')
Remove-Item -Recurse -Force $fx9

# --- D059: 読み取り系ツール除外と GATE_STATUS 形式リントの回帰テスト ---
$fx10 = Join-Path $env:TEMP ("hooktest10-" + [IO.Path]::GetRandomFileName())
foreach ($d in '.github\harness','requirements','docs\00-overview') {
  New-Item -ItemType Directory -Force (Join-Path $fx10 $d) | Out-Null
}
[IO.File]::WriteAllText((Join-Path $fx10 '.github\harness\USAGE.md'), "usage", $utf8nb)
[IO.File]::WriteAllText((Join-Path $fx10 'requirements\memo.md'), "# 要件メモ`n実メモ`n", $utf8nb)
Check "phase-scope: read tool (readFile) -> allow" 'guard-phase-scope.ps1' '{"tool_name":"readFile","tool_input":{"filePath":"app/main.py"}}' 'allow' $fx10
Check "phase-scope: write tool (replace_string_in_file) -> ask" 'guard-phase-scope.ps1' '{"tool_name":"replace_string_in_file","tool_input":{"filePath":"app/main.py"}}' 'ask' $fx10
Check "phase-scope: read-prefixed write tool (findAndReplace) -> ask" 'guard-phase-scope.ps1' '{"tool_name":"findAndReplace","tool_input":{"filePath":"app/main.py"}}' 'ask' $fx10
Check "phase-scope: mcp search-server write tool -> ask" 'guard-phase-scope.ps1' '{"tool_name":"mcp__elasticsearch__index_document","tool_input":{"file_path":"app/main.py"}}' 'ask' $fx10
[IO.File]::WriteAllText((Join-Path $fx10 'docs\00-overview\progress.md'), "## GATE_STATUS`nphase_x: done`n", $utf8nb)
Check "gate-tamper: non-canonical progress.md -> warn (format lint)" 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"docs/00-overview/progress.md","content":"x"}}' 'warn' $fx10
Remove-Item -Recurse -Force $fx10

# --- route-request.ps1: 受付注入の4分岐(新規/本体コピー/運用中/進行中)(D060) ---
$fx11 = Join-Path $env:TEMP ("hooktest11-" + [IO.Path]::GetRandomFileName())
foreach ($d in 'fresh\.github\harness','fresh\requirements','copy\.github\harness','copy\requirements','gate\docs\00-overview') {
  New-Item -ItemType Directory -Force (Join-Path $fx11 $d) | Out-Null
}
$utf8rr = New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText((Join-Path $fx11 'fresh\.github\harness\USAGE.md'), "usage", $utf8rr)
[IO.File]::WriteAllText((Join-Path $fx11 'fresh\requirements\memo.md'), "# 要件メモ`nCLIメモ帳ツールを作りたい。`n", $utf8rr)
[IO.File]::WriteAllText((Join-Path $fx11 'copy\.github\harness\USAGE.md'), "usage", $utf8rr)
[IO.File]::WriteAllText((Join-Path $fx11 'copy\requirements\memo.md'), "# 要件メモ（自由記述）`n`nここに自由に書いてください。`n`n---`n`n（ここから記入）`n", $utf8rr)
$out = Invoke-Hook 'route-request.ps1' '{}' (Join-Path $fx11 'fresh')
if ($out -match '00-start-project' -and $out -match 'additionalContext') {
  $script:pass++; Write-Output "PASS: route-request.ps1: fresh project -> /00 injected"
} else {
  $script:fail++; Write-Output "FAIL: route-request.ps1: fresh project -> /00 injected"; Write-Output "  got: $out"
}
$out = Invoke-Hook 'route-request.ps1' '{}' (Join-Path $fx11 'copy')
if ($out.Trim() -eq '{"continue": true}') {
  $script:pass++; Write-Output "PASS: route-request.ps1: body ZIP copy -> minimal allow JSON"
} else {
  $script:fail++; Write-Output "FAIL: route-request.ps1: body ZIP copy -> minimal allow JSON"; Write-Output "  got: $out"
}
[IO.File]::WriteAllText((Join-Path $fx11 'gate\docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: done`ntest: done`nrelease: done`n-->`n", $utf8rr)
$out = Invoke-Hook 'route-request.ps1' '{}' (Join-Path $fx11 'gate')
if ($out -match '12-change-request') {
  $script:pass++; Write-Output "PASS: route-request.ps1: all done -> operating notice (/12)"
} else {
  $script:fail++; Write-Output "FAIL: route-request.ps1: all done -> operating notice (/12)"; Write-Output "  got: $out"
}
[IO.File]::WriteAllText((Join-Path $fx11 'gate\docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: in_progress`ntest: not_started`nrelease: not_started`n-->`n", $utf8rr)
$out = Invoke-Hook 'route-request.ps1' '{}' (Join-Path $fx11 'gate')
if ($out -match [regex]::Escape('進行中フェーズ')) {
  $script:pass++; Write-Output "PASS: route-request.ps1: in_progress -> continue-in-phase notice"
} else {
  $script:fail++; Write-Output "FAIL: route-request.ps1: in_progress -> continue-in-phase notice"; Write-Output "  got: $out"
}
[IO.File]::WriteAllText((Join-Path $fx11 'gate\docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: in_progress`ntest: not_started`nrelease: not_started`n", $utf8rr)
$out = Invoke-Hook 'route-request.ps1' '{}' (Join-Path $fx11 'gate')
if ($out -match [regex]::Escape('進行中フェーズ')) {
  $script:pass++; Write-Output "PASS: route-request.ps1: missing --> terminator -> EOF fallback (in_progress)"
} else {
  $script:fail++; Write-Output "FAIL: route-request.ps1: missing --> terminator -> EOF fallback (in_progress)"; Write-Output "  got: $out"
}
[IO.File]::WriteAllText((Join-Path $fx11 'gate\docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: done`ntest: done`nrelease: done (PM approved)`n-->`n", $utf8rr)
$out = Invoke-Hook 'route-request.ps1' '{}' (Join-Path $fx11 'gate')
if ($out -match '12-change-request' -and $out -match 'PM approved') {
  $script:pass++; Write-Output "PASS: route-request.ps1: annotated done value -> kept in summary + operating"
} else {
  $script:fail++; Write-Output "FAIL: route-request.ps1: annotated done value -> kept in summary + operating"; Write-Output "  got: $out"
}
Remove-Item -Recurse -Force $fx11

# --- guard-harness-config-edit.ps1: model-policy の正と Copilot CLI 設定も保護対象(MP-7。監査 2026-09-09 §4.5) ---
Check "harness-config-edit: model-policy.yml -> deny" 'guard-harness-config-edit.ps1' '{"tool_input":{"file_path":"d:/proj/.github/harness/model-policy.yml"}}' 'deny'
Check "harness-config-edit: .github/copilot/settings.json (backslash) -> deny" 'guard-harness-config-edit.ps1' '{"tool_input":{"file_path":"d:\\proj\\.github\\copilot\\settings.json"}}' 'deny'
Check "harness-config-edit: echo >> .github/copilot/settings.json -> ask" 'guard-harness-config-edit.ps1' '{"tool_input":{"command":"echo x >> .github/copilot/settings.json"}}' 'ask'

# --- guard-subagent-model.ps1: 役割別モデル方針の PreToolUse 照合(selftest.sh と同一ケース・同一判定) ---
Check "subagent-model: allowed alias (reviewer, opus) -> allow" 'guard-subagent-model.ps1' '{"tool_name":"Agent","tool_input":{"subagent_type":"reviewer","model":"opus","prompt":"review","description":"x"}}' 'allow'
Check "subagent-model: allowed full id (task-worker, claude-sonnet-5) -> allow" 'guard-subagent-model.ps1' '{"tool_name":"Agent","tool_input":{"subagent_type":"task-worker","model":"claude-sonnet-5","prompt":"x"}}' 'allow'
Check "subagent-model: denied alias (task-worker, haiku) -> deny" 'guard-subagent-model.ps1' '{"tool_name":"Agent","tool_input":{"subagent_type":"task-worker","model":"haiku","prompt":"x"}}' 'deny'
Check "subagent-model: denied full id with date suffix (Task tool name) -> deny" 'guard-subagent-model.ps1' '{"tool_name":"Task","tool_input":{"subagent_type":"task-worker","model":"claude-haiku-4-5-20251001","prompt":"x"}}' 'deny'
Check "subagent-model: model not in policy (gpt-5) -> deny" 'guard-subagent-model.ps1' '{"tool_name":"Agent","tool_input":{"subagent_type":"spec-critic","model":"gpt-5","prompt":"x"}}' 'deny'
Check "subagent-model: model omitted, default inherit -> allow (no injection)" 'guard-subagent-model.ps1' '{"tool_name":"Agent","tool_input":{"subagent_type":"task-worker","prompt":"x"}}' 'allow'
# 無出力(fail-open)の契約: 非 Agent ツール / 壊れた JSON / CI windows の固定ペイロード / 表に無い役割
$out = Invoke-Hook 'guard-subagent-model.ps1' '{"tool_name":"Bash","tool_input":{"command":"git status","model":"haiku"}}'
if ($out.Trim() -eq '') {
  $script:pass++; Write-Output "PASS: subagent-model: non-Agent tool (Bash) -> silent"
} else {
  $script:fail++; Write-Output "FAIL: subagent-model: non-Agent tool (Bash) -> silent"; Write-Output "  got: $out"
}
$out = Invoke-Hook 'guard-subagent-model.ps1' '{"tool_name":"Agent","tool_input":{"subagent_type":'
if ($out.Trim() -eq '') {
  $script:pass++; Write-Output "PASS: subagent-model: broken JSON -> silent"
} else {
  $script:fail++; Write-Output "FAIL: subagent-model: broken JSON -> silent"; Write-Output "  got: $out"
}
$out = Invoke-Hook 'guard-subagent-model.ps1' '{"tool_input":{"command":"git status","file_path":"src/app.ts","content":"hello"}}'
if ($out.Trim() -eq '') {
  $script:pass++; Write-Output "PASS: subagent-model: CI fixed payload (no subagent_type) -> silent"
} else {
  $script:fail++; Write-Output "FAIL: subagent-model: CI fixed payload (no subagent_type) -> silent"; Write-Output "  got: $out"
}
$out = Invoke-Hook 'guard-subagent-model.ps1' '{"tool_name":"Agent","tool_input":{"subagent_type":"custom-worker","model":"haiku","prompt":"x"}}'
if ($out.Trim() -eq '') {
  $script:pass++; Write-Output "PASS: subagent-model: unknown subagent_type -> silent (fail-open)"
} else {
  $script:fail++; Write-Output "FAIL: subagent-model: unknown subagent_type -> silent (fail-open)"; Write-Output "  got: $out"
}
# 既定が具体モデルの役割(A/B 後の形)は updatedInput で tool_input 全体を複製し model だけ差し替える
$fx12 = Join-Path $env:TEMP ("hooktest12-" + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Force $fx12 | Out-Null
[IO.File]::WriteAllText((Join-Path $fx12 'table.json'), '{"roles":{"worker-low":{"default":"claude-sonnet-5","allowed_exact":["inherit","sonnet"],"allowed_prefix":["claude-sonnet-5"]}},"deny_exact":["haiku"],"deny_prefix":["claude-haiku-4-5"]}', (New-Object System.Text.UTF8Encoding $false))
$env:GUARD_SUBAGENT_MODEL_TABLE = (Join-Path $fx12 'table.json')
$out = Invoke-Hook 'guard-subagent-model.ps1' '{"tool_name":"Agent","tool_input":{"subagent_type":"worker-low","prompt":"keep me","description":"d"}}'
Remove-Item Env:GUARD_SUBAGENT_MODEL_TABLE -ErrorAction SilentlyContinue
$injectOk = $false
try {
  $o = $out | ConvertFrom-Json
  $h = $o.hookSpecificOutput
  $u = $h.updatedInput
  if ($h.permissionDecision -eq 'allow' -and $u.model -eq 'claude-sonnet-5' -and $u.prompt -eq 'keep me' -and $u.subagent_type -eq 'worker-low') { $injectOk = $true }
} catch {}
if ($injectOk) {
  $script:pass++; Write-Output "PASS: subagent-model: model omitted, concrete default -> updatedInput (full copy + model)"
} else {
  $script:fail++; Write-Output "FAIL: subagent-model: model omitted, concrete default -> updatedInput (full copy + model)"; Write-Output "  got: $out"
}
Remove-Item -Recurse -Force $fx12

# =====================================================================================
# 第5回(2026-09-09 再監査): 設定・ファイル書込系ガードの封鎖(RG-1/SC-4/前回 C-1・RG-3(H-1)・
# RG-4(H-2)・RG-5(H-3)・CP-1・SC-1/SC-6/MP-3・SC-2・CC-10)。期待値は selftest.sh の同名ケースと同一。
# =====================================================================================

# --- guard-harness-config-edit.ps1: allowlist 反転(RG-1 の 16 ベクタ) ---
Check "harness-config-edit: python -c open(settings.json,'w') -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"python -c \"open(''.claude/settings.json'',''w'').write(''{}'')\""}}' 'ask'
Check "harness-config-edit: node -e writeFileSync AGENTS.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"node -e \"require(''fs'').writeFileSync(''AGENTS.md'',''x'')\""}}' 'ask'
Check "harness-config-edit: git apply patch.diff (path-less) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git apply patch.diff"}}' 'ask'
Check "harness-config-edit: git checkout HEAD~1 -- AGENTS.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git checkout HEAD~1 -- AGENTS.md"}}' 'ask'
Check "harness-config-edit: git restore --source AGENTS.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git restore --source=HEAD~1 AGENTS.md"}}' 'ask'
Check "harness-config-edit: dd of=settings.json -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"dd if=x of=.claude/settings.json"}}' 'ask'
Check "harness-config-edit: install x AGENTS.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"install x AGENTS.md"}}' 'ask'
Check "harness-config-edit: cmd /c mklink /J .github\hooks -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"cmd /c mklink /J .github\\hooks C:\\junk"}}' 'ask'
Check "harness-config-edit: New-Item Junction .claude\agents -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"New-Item -ItemType Junction -Path .claude\\agents -Target C:\\junk"}}' 'ask'
Check "harness-config-edit: ln -s x .claude/agents -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"ln -s /tmp/x .claude/agents"}}' 'ask'
Check "harness-config-edit: perl -pi -e AGENTS.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"perl -pi -e ''s/a/b/'' AGENTS.md"}}' 'ask'
Check "harness-config-edit: [IO.File]::WriteAllText settings.json -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"[IO.File]::WriteAllText(''.claude/settings.json'',''{}'')"}}' 'ask'
Check "harness-config-edit: variable indirection f=AGENTS.md; echo > `$f -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"f=AGENTS.md; echo x > $f"}}' 'ask'
Check "harness-config-edit: python heredoc writing settings.json -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"python - <<PYEOF\nopen(''.claude/settings.json'',''w'').write(''{}'')\nPYEOF"}}' 'ask'
# SC-4 の 4 ベクタ
Check "harness-config-edit: Copy-Item x AGENTS.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"Copy-Item x.md AGENTS.md"}}' 'ask'
Check "harness-config-edit: base64 -d | sh (no protected path) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"echo ZWNobyB4ID4gQUdFTlRTLm1k | base64 -d | sh"}}' 'ask'
Check "harness-config-edit: PowerShell variable indirection Set-Content -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"$f = ''AGENTS.md''; Set-Content $f ''x''"}}' 'ask'
Check "harness-config-edit: iex FromBase64String -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"iex ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(''ZWNobyB4'')))"}}' 'ask'
# 前回 C-1 と本監査の追加ベクタ
Check "harness-config-edit: powershell -EncodedCommand -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"powershell -EncodedCommand ZQBjAGgAbwAgAHgAIAA+ACAAQQBHAEUATgBUAFMALgBtAGQA"}}' 'ask'
Check "harness-config-edit: git read-tree (path-less) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git read-tree -u HEAD~3"}}' 'ask'
Check "harness-config-edit: git stash pop (path-less) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git stash pop"}}' 'ask'
Check "harness-config-edit: echo AGENTS.md | xargs rm -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"echo AGENTS.md | xargs rm"}}' 'ask'
Check "harness-config-edit: cat evil.py | python (stdin script) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"cat evil.py | python"}}' 'ask'
Check "harness-config-edit: bash -c wrapper redirect -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"bash -c \"echo x > AGENTS.md\""}}' 'ask'
Check "harness-config-edit: find .github/hooks -delete -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"find .github/hooks -name ''*.sh'' -delete"}}' 'ask'
Check "harness-config-edit: awk print > settings.json -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"awk ''{print > \".claude/settings.json\"}'' x"}}' 'ask'
Check "harness-config-edit: cp to .github/copilot/settings.json -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"cp x.json .github/copilot/settings.json"}}' 'ask'
Check "harness-config-edit: New-Item .claude\rules\evil.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"New-Item -Path .claude\\rules\\evil.md -ItemType File"}}' 'ask'
Check "harness-config-edit: git add AGENTS.md (non-read git) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git add AGENTS.md"}}' 'ask'
# H-1(RG-3): 行継続3種
Check "harness-config-edit: backtick line-continuation redirect -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"echo x > `\nAGENTS.md"}}' 'ask'
Check "harness-config-edit: caret line-continuation redirect -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"echo x > ^\nAGENTS.md"}}' 'ask'
Check "harness-config-edit: backslash line-continuation redirect -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"echo x > \\\nAGENTS.md"}}' 'ask'
# 対照: 読み取り専用文脈・保護パス不在は allow
Check "harness-config-edit: grep | head (read pipeline) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"grep -n \"foo\" AGENTS.md | head -5"}}' 'allow'
Check "harness-config-edit: git diff settings.json -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git diff HEAD -- .claude/settings.json"}}' 'allow'
Check "harness-config-edit: git -C log -- AGENTS.md -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git -C /d/proj log --oneline -5 -- AGENTS.md"}}' 'allow'
Check "harness-config-edit: git show HEAD:AGENTS.md | head -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git show HEAD:AGENTS.md | head -20"}}' 'allow'
Check "harness-config-edit: echo > non-protected path -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"echo x > notes.txt"}}' 'allow'
Check "harness-config-edit: ls .github/hooks && wc -l AGENTS.md -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"ls -la .github/hooks/scripts && wc -l AGENTS.md"}}' 'allow'
Check "harness-config-edit: Get-Content settings.json | ConvertFrom-Json -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"Get-Content .claude\\settings.json | ConvertFrom-Json"}}' 'allow'
Check "harness-config-edit: Select-String AGENTS.md -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"Select-String -Path AGENTS.md -Pattern ''foo''"}}' 'allow'
Check "harness-config-edit: type AGENTS.md (cmd read) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"type AGENTS.md"}}' 'allow'
Check "harness-config-edit: test -f && cat 2>/dev/null -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"[ -f AGENTS.md ] && cat AGENTS.md 2>/dev/null"}}' 'allow'
Check "harness-config-edit: cat AGENTS.md > /tmp (non-protected target) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"cat AGENTS.md > /tmp/agents.txt"}}' 'allow'
Check "harness-config-edit: echo mention of AGENTS.md -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"echo \"see AGENTS.md\""}}' 'allow'
Check "harness-config-edit: [IO.File]::ReadAllText AGENTS.md -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"[IO.File]::ReadAllText(''AGENTS.md'')"}}' 'allow'
Check "harness-config-edit: pipe into python -c (visible code, no protected path) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"cat data.json | python -c ''import sys,json; print(len(json.load(sys.stdin)))''"}}' 'allow'
Check "harness-config-edit: base64 encode (no decode/exec) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"echo -n user:pass | base64"}}' 'allow'
Check "harness-config-edit: python tools/validate-harness.py -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"python tools/validate-harness.py"}}' 'allow'

# --- H-2(RG-4): uri / notebook_path / apply_patch 本文経由(D072 の _paths.ps1 配線) ---
Check "harness-config-edit: uri file:// AGENTS.md -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"Edit","tool_input":{"uri":"file:///D:/x/AGENTS.md"}}' 'deny'
Check "harness-config-edit: uri with %3A drive settings.json -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"Edit","tool_input":{"uri":"file:///D%3A/x/.claude/settings.json"}}' 'deny'
Check "harness-config-edit: notebook_path under .github/hooks -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"NotebookEdit","tool_input":{"notebook_path":".github/hooks/a.ipynb"}}' 'deny'
Check "harness-config-edit: apply_patch Update File AGENTS.md -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"apply_patch","tool_input":{"input":"*** Begin Patch\n*** Update File: AGENTS.md\n@@\n-a\n+b\n*** End Patch"}}' 'deny'
Check "harness-config-edit: multi-file edit with .claude/commands at #2 -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"edit_files","tool_input":{"files":[{"path":"src/a.ts"},{"path":".claude/commands/01-x.md"}]}}' 'deny'
Check "template-edit: uri file:// template -> deny" 'guard-template-edit.ps1' '{"tool_name":"Edit","tool_input":{"uri":"file:///D:/x/docs/requirements_template.md"}}' 'deny'
Check "template-edit: apply_patch Update File template -> deny" 'guard-template-edit.ps1' '{"tool_name":"apply_patch","tool_input":{"input":"*** Update File: docs/01/requirements_template.md"}}' 'deny'
Check "template-edit: notebook_path template -> deny" 'guard-template-edit.ps1' '{"tool_name":"NotebookEdit","tool_input":{"notebook_path":"docs/x_template.md"}}' 'deny'
Check "template-edit: multi-file edit with template at #2 -> deny" 'guard-template-edit.ps1' '{"tool_name":"edit_files","tool_input":{"files":[{"path":"docs/a.md"},{"path":"docs/01-requirements/nfr_template.md"}]}}' 'deny'

# --- CP-1: 読み取り系ツール名(VS Code 形)は全ガードで allow、書込系は従来判定 ---
Check "harness-config-edit: readFile PLATFORM.md (read tool) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"readFile","tool_input":{"filePath":".github/harness/PLATFORM.md"}}' 'allow'
Check "harness-config-edit: read_file gate-check SKILL.md -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"read_file","tool_input":{"filePath":".github/skills/gate-check/SKILL.md"}}' 'allow'
Check "harness-config-edit: replace_string_in_file PLATFORM.md -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"replace_string_in_file","tool_input":{"filePath":".github/harness/PLATFORM.md"}}' 'deny'
Check "harness-config-edit: create_file reviewer.agent.md -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"create_file","tool_input":{"filePath":".github/agents/reviewer.agent.md"}}' 'deny'
Check "template-edit: readFile template -> allow" 'guard-template-edit.ps1' '{"tool_name":"readFile","tool_input":{"filePath":"docs/01-requirements/requirements_template.md"}}' 'allow'
Check "template-edit: create_file template -> deny" 'guard-template-edit.ps1' '{"tool_name":"create_file","tool_input":{"filePath":"docs/01-requirements/requirements_template.md"}}' 'deny'
Check "secret-leak: readFile payload with key-like content -> allow" 'guard-secret-leak.ps1' '{"tool_name":"readFile","tool_input":{"filePath":"a.env","content":"password = \"abcdefghijklmnop1234\""}}' 'allow'
$fx12 = Join-Path $env:TEMP ("hooktest12-" + [IO.Path]::GetRandomFileName())
foreach ($d in 'docs\00-overview','docs\01-requirements') { New-Item -ItemType Directory -Force (Join-Path $fx12 $d) | Out-Null }
$utf8x = New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText((Join-Path $fx12 'docs\00-overview\progress.md'), "requirements: done`ndesign: in_progress`n", $utf8x)
Check "warn-stale-gate: readFile done doc -> allow (read filter)" 'warn-stale-gate.ps1' '{"tool_name":"readFile","tool_input":{"filePath":"docs/01-requirements/requirements.md"}}' 'allow' $fx12
Check "warn-stale-gate: uri done doc -> warn" 'warn-stale-gate.ps1' '{"tool_name":"Edit","tool_input":{"uri":"file:///D:/x/docs/01-requirements/requirements.md"}}' 'warn' $fx12
Check "warn-stale-gate: apply_patch done doc -> warn" 'warn-stale-gate.ps1' '{"tool_name":"apply_patch","tool_input":{"input":"*** Update File: docs/01-requirements/nfr.md\n+x"}}' 'warn' $fx12
Check "gate-tamper: readFile progress.md -> allow (read filter)" 'warn-gate-tamper.ps1' '{"tool_name":"readFile","tool_input":{"filePath":"docs/00-overview/progress.md","content":"implementation: done"}}' 'allow' $fx12
Check "gate-tamper: uri tasks.md [x] -> warn" 'warn-gate-tamper.ps1' '{"tool_name":"Edit","tool_input":{"uri":"file:///D:/x/docs/03-implementation/tasks.md","new_string":"- [x] T1"}}' 'warn' $fx12
[System.IO.File]::WriteAllText((Join-Path $fx12 "docs\bad.md"), ("normal" + [char]0 + "text"))
$fx12s = $fx12 -replace '\\', '/'
Check "doc-chars: readFile bad.md -> allow (read filter)" 'check-doc-chars.ps1' ('{"tool_name":"readFile","tool_input":{"filePath":"' + $fx12s + '/docs/bad.md"}}') 'allow'
Remove-Item -Recurse -Force $fx12

# --- SC-1 / SC-6 / MP-3: 保護面の拡張 ---
Check "harness-config-edit: Write .claude/rules/evil.md -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".claude/rules/evil.md","content":"x"}}' 'deny'
Check "harness-config-edit: Write .github/copilot/settings.json -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/copilot/settings.json","content":"{}"}}' 'deny'
Check "harness-config-edit: Write .github/copilot/settings.local.json -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":"d:\\proj\\.github\\copilot\\settings.local.json","content":"{}"}}' 'deny'
Check "harness-config-edit: Write .github/copilot-instructions.md -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/copilot-instructions.md","content":"x"}}' 'deny'
Check "harness-config-edit: Write CLAUDE.local.md -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":"d:\\proj\\CLAUDE.local.md","content":"x"}}' 'deny'
Check "harness-config-edit: Write GEMINI.md -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":"GEMINI.md","content":"x"}}' 'deny'
Check "harness-config-edit: Edit .github/harness/model-policy.yml -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"Edit","tool_input":{"file_path":".github/harness/model-policy.yml","old_string":"a","new_string":"b"}}' 'deny'
Check "harness-config-edit: Write notes/GEMINI.md.bak (different name) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":"notes/GEMINI.md.bak","content":"x"}}' 'allow'

# --- SC-2: skills 配下の権限系 frontmatter / hooks.json は ask、通常の動的スキル追加は allow ---
Check "harness-config-edit: skill SKILL.md with allowed-tools -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".claude/skills/evil/SKILL.md","content":"---\nname: evil\nallowed-tools: Bash(*)\n---\nrun"}}' 'ask'
Check "harness-config-edit: skill Edit adding hooks: -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Edit","tool_input":{"file_path":".github/skills/foo/SKILL.md","old_string":"a","new_string":"hooks:\n  PreToolUse: x"}}' 'ask'
Check "harness-config-edit: skill hooks/hooks.json -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".claude/skills/evil/hooks/hooks.json","content":"{}"}}' 'ask'
Check "harness-config-edit: skill plain SKILL.md (dynamic add) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".claude/skills/evil/SKILL.md","content":"---\nname: evil\ndescription: x\n---\nbody"}}' 'allow'
Check "harness-config-edit: Write docs containing patch marker text -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":"docs/02-design/patch-format.md","content":"example:\n*** Update File: AGENTS.md\n"}}' 'allow'

# --- H-3(RG-5): 高確度パターンの追加と汎用パターンのクォート必須撤廃(偽陽性対照つき) ---
Check "secret-leak: npm token -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"NPM_TOKEN=npm_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"}}' 'deny'
Check "secret-leak: JWT -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"JWT=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz"}}' 'deny'
Check "secret-leak: Azure AccountKey -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"AccountKey=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz=="}}' 'deny'
Check "secret-leak: SAS sig= -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"https://x.blob.core.windows.net/c?sv=2020&sig=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG%3D"}}' 'deny'
Check "secret-leak: unquoted password= -> ask" 'guard-secret-leak.ps1' '{"tool_input":{"content":"password=hunter2longenough16chars"}}' 'ask'
Check "secret-leak: OpenAI sk-proj -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"}}' 'deny'
Check "secret-leak: GitLab glpat -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"GITLAB=glpat-abcdefghijklmnopqrst"}}' 'deny'
Check "secret-leak: PGP private key block -> deny" 'guard-secret-leak.ps1' '{"tool_input":{"content":"-----BEGIN PGP PRIVATE KEY BLOCK-----"}}' 'deny'
Check "secret-leak: password placeholder <your-password> -> allow" 'guard-secret-leak.ps1' '{"tool_input":{"content":"password=<your-password>"}}' 'allow'
Check "secret-leak: short password value -> allow" 'guard-secret-leak.ps1' '{"tool_input":{"content":"password=short1"}}' 'allow'
Check "secret-leak: token=process.env.X (identifier) -> allow" 'guard-secret-leak.ps1' '{"tool_input":{"content":"token=process.env.GITHUB_TOKEN"}}' 'allow'
Check "secret-leak: token = function call -> allow" 'guard-secret-leak.ps1' '{"tool_input":{"content":"token = generate_access_token()"}}' 'allow'

# --- guard-config-change.py(ConfigChange 第2防衛線。CC-10/SC-3): python 系フックの起動確認 ---
# python の解決順は run-python.sh と同じ python→python3(Windows の Store スタブは --version で除外)
$py = $null
foreach ($cand in @('python', 'python3')) {
  if (Get-Command $cand -ErrorAction SilentlyContinue) {
    try { $null = & $cand --version 2>&1; if ($LASTEXITCODE -eq 0) { $py = $cand; break } } catch {}
  }
}
if ($py) {
  $gcc = Join-Path $scriptsDir 'guard-config-change.py'
  $stOut = (& $py $gcc --selftest 2>&1 | Out-String)
  if ($LASTEXITCODE -eq 0) {
    $script:pass++; Write-Output "PASS: guard-config-change: --selftest exit 0"
  } else {
    $script:fail++; Write-Output "FAIL: guard-config-change: --selftest exit 0"; Write-Output ("  got: " + ($stOut -split "`n" | Where-Object { $_ -match '^FAIL|SKIP' } | Select-Object -First 5) -join ' / ')
  }
  $out = ('{not json' | & $py $gcc 2>&1 | Out-String).Trim()
  if ($LASTEXITCODE -eq 0 -and -not $out) {
    $script:pass++; Write-Output "PASS: guard-config-change: broken JSON -> silent exit 0 (fail-open)"
  } else {
    $script:fail++; Write-Output "FAIL: guard-config-change: broken JSON -> silent exit 0 (fail-open)"; Write-Output "  got: $out"
  }
  $out = ('{"hook_event_name":"ConfigChange","source":"policy_settings"}' | & $py $gcc 2>&1 | Out-String).Trim()
  if ($LASTEXITCODE -eq 0 -and -not $out) {
    $script:pass++; Write-Output "PASS: guard-config-change: policy_settings -> allow (no output)"
  } else {
    $script:fail++; Write-Output "FAIL: guard-config-change: policy_settings -> allow (no output)"; Write-Output "  got: $out"
  }
} else {
  Write-Output "SKIP: guard-config-change (python not found)"
}

# --- A6-14 / RD-2: implementation/test の done 遷移で独立レビュー記録(review-log.md)が無ければ専用警告
#     (selftest.sh の同名ケースと同一の期待値。記録あり→一般 done 警告 / 無し→専用警告 / 最新の完了証拠より
#      古い→鮮度警告 / security-review-report.md だけでも記録あり / 対象外フェーズは従来どおり)---
$fx12 = Join-Path $env:TEMP ("hooktest12-" + [IO.Path]::GetRandomFileName())
foreach ($d in 'docs\00-overview','docs\03-implementation','docs\04-test') { New-Item -ItemType Directory -Force (Join-Path $fx12 $d) | Out-Null }
$utf8rl = New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText((Join-Path $fx12 'docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: in_progress`ntest: not_started`nrelease: not_started`n-->`n", $utf8rl)
[IO.File]::WriteAllText((Join-Path $fx12 'docs\03-implementation\tasks.md'), "- [x] TASK-001: x（完了条件: npm test 12件成功 2026-09-10 09:00）`n", $utf8rl)
$rlPayload = '{"tool_input":{"file_path":"docs/00-overview/progress.md","new_string":"implementation: done"}}'
$out = Invoke-Hook 'warn-gate-tamper.ps1' $rlPayload $fx12
if ($out -match [regex]::Escape('独立レビューの記録がありません')) {
  $script:pass++; Write-Output "PASS: gate-tamper: implementation done without review-log -> dedicated warn"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: implementation done without review-log -> dedicated warn"; Write-Output "  got: $out"
}
[IO.File]::WriteAllText((Join-Path $fx12 'docs\04-test\review-log.md'), "# 独立レビュー記録`n`n### 2026-09-10 10:30 / implementation / 対象: TASK-001 / verdict: 承認`n`n- 根拠（file:line）: src/app.py:10 — 問題なし（INFO）`n", $utf8rl)
$out = Invoke-Hook 'warn-gate-tamper.ps1' $rlPayload $fx12
if (($out -match [regex]::Escape('明示承認')) -and ($out -notmatch [regex]::Escape('独立レビューの記録がありません'))) {
  $script:pass++; Write-Output "PASS: gate-tamper: implementation done with dated review-log -> generic done warn"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: implementation done with dated review-log -> generic done warn"; Write-Output "  got: $out"
}
[IO.File]::AppendAllText((Join-Path $fx12 'docs\03-implementation\tasks.md'), "- [x] TASK-002: y（完了条件: npm test 13件成功 2026-09-12 09:00）`n", $utf8rl)
$out = Invoke-Hook 'warn-gate-tamper.ps1' $rlPayload $fx12
if ($out -match [regex]::Escape('以降の独立レビューの記録がありません')) {
  $script:pass++; Write-Output "PASS: gate-tamper: review-log older than latest evidence -> stale warn"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: review-log older than latest evidence -> stale warn"; Write-Output "  got: $out"
}
Remove-Item (Join-Path $fx12 'docs\04-test\review-log.md') -Force
[IO.File]::WriteAllText((Join-Path $fx12 'docs\04-test\security-review-report.md'), "report", $utf8rl)
$out = Invoke-Hook 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"docs/00-overview/progress.md","new_string":"test: done"}}' $fx12
if (($out -match [regex]::Escape('明示承認')) -and ($out -notmatch [regex]::Escape('独立レビューの記録がありません'))) {
  $script:pass++; Write-Output "PASS: gate-tamper: test done with security report only -> generic done warn"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: test done with security report only -> generic done warn"; Write-Output "  got: $out"
}
Remove-Item (Join-Path $fx12 'docs\04-test\security-review-report.md') -Force
$out = Invoke-Hook 'warn-gate-tamper.ps1' '{"tool_input":{"file_path":"docs/00-overview/progress.md","new_string":"design: done"}}' $fx12
if (($out -match [regex]::Escape('明示承認')) -and ($out -notmatch [regex]::Escape('独立レビューの記録がありません'))) {
  $script:pass++; Write-Output "PASS: gate-tamper: design done (out of scope) -> generic done warn"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: design done (out of scope) -> generic done warn"; Write-Output "  got: $out"
}
Remove-Item -Recurse -Force $fx12
# --- 未配線イベントの記録フック(再監査 CC-9/CC-8): StopFailure / InstructionsLoaded / Pre・PostModelSwitch ---
# selftest.sh と同じ挙動を固定する: 空入力→無出力 exit 0(fail-open)、代表ペイロード→ログ生成。
# python の解決順は run-python.sh と同じ python→python3 とし、--version で実行可否を確認する
# (Windows の Store スタブ python3 は存在しても実行不能)。無ければ SKIP。
$pybin = $null
foreach ($cand in 'python', 'python3') {
  if (Get-Command $cand -ErrorAction SilentlyContinue) {
    try { & $cand --version *> $null; if ($LASTEXITCODE -eq 0) { $pybin = $cand; break } } catch {}
  }
}
function Invoke-PyHook($scriptName, $payload, $logDir) {
  # Invoke-Hook と同じく stdin は BOM なし UTF-8 のバイト列を直接書く。記録先は HARNESS_HOOK_LOG_DIR で一時ディレクトリに向ける
  $path = Join-Path $scriptsDir $scriptName
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $pybin
  $psi.Arguments = '"' + $path + '"'
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding $false
  $psi.EnvironmentVariables['HARNESS_HOOK_LOG_DIR'] = $logDir
  $psi.WorkingDirectory = $scriptsDir
  $proc = [System.Diagnostics.Process]::Start($psi)
  $bytes = (New-Object System.Text.UTF8Encoding $false).GetBytes($payload)
  $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
  $proc.StandardInput.Close()
  $out = $proc.StandardOutput.ReadToEnd()
  $proc.StandardError.ReadToEnd() | Out-Null
  $proc.WaitForExit()
  @{ out = $out; rc = $proc.ExitCode }
}
if ($pybin) {
  $fx12 = Join-Path $env:TEMP ("hooktest12-" + [IO.Path]::GetRandomFileName())
  New-Item -ItemType Directory -Force (Join-Path $fx12 'docs\00-overview') | Out-Null
  $logs12 = Join-Path $fx12 'logs'
  $p12 = $fx12 -replace '\\', '/'
  [IO.File]::WriteAllText((Join-Path $fx12 'docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`nimplementation: in_progress`n-->`n", $utf8nb)
  # (ループにしない: gen-docs / validate の静的件数(pass 増分行の数)と実行件数を一致させる。selftest.sh と同じ規約)
  $r = Invoke-PyHook 'mark-abnormal-stop.py' '' $logs12
  if ($r.rc -eq 0 -and $r.out -eq '' -and -not (Test-Path $logs12)) {
    $script:pass++; Write-Output "PASS: mark-abnormal-stop.py: empty stdin -> silent exit 0 (no log)"
  } else {
    $script:fail++; Write-Output "FAIL: mark-abnormal-stop.py: empty stdin -> silent exit 0 (no log)"; Write-Output ("  rc={0} got: {1}" -f $r.rc, $r.out)
  }
  $r = Invoke-PyHook 'log-instructions-loaded.py' '' $logs12
  if ($r.rc -eq 0 -and $r.out -eq '' -and -not (Test-Path $logs12)) {
    $script:pass++; Write-Output "PASS: log-instructions-loaded.py: empty stdin -> silent exit 0 (no log)"
  } else {
    $script:fail++; Write-Output "FAIL: log-instructions-loaded.py: empty stdin -> silent exit 0 (no log)"; Write-Output ("  rc={0} got: {1}" -f $r.rc, $r.out)
  }
  $r = Invoke-PyHook 'log-model-switch.py' '' $logs12
  if ($r.rc -eq 0 -and $r.out -eq '' -and -not (Test-Path $logs12)) {
    $script:pass++; Write-Output "PASS: log-model-switch.py: empty stdin -> silent exit 0 (no log)"
  } else {
    $script:fail++; Write-Output "FAIL: log-model-switch.py: empty stdin -> silent exit 0 (no log)"; Write-Output ("  rc={0} got: {1}" -f $r.rc, $r.out)
  }
  $r = Invoke-PyHook 'mark-abnormal-stop.py' ('{"session_id":"st 1","cwd":"' + $p12 + '","hook_event_name":"StopFailure","error_type":"rate_limit","error_message":"Rate limit exceeded"}') $logs12
  $f = Join-Path $logs12 'abnormal-stop-st_1.json'
  $txt = if (Test-Path $f) { [IO.File]::ReadAllText($f, [Text.Encoding]::UTF8) } else { '' }
  if ($r.rc -eq 0 -and $r.out -eq '' -and $txt -match '"error_type": "rate_limit"' -and $txt -match 'implementation: in_progress') {
    $script:pass++; Write-Output "PASS: mark-abnormal-stop: StopFailure payload -> abnormal-stop-<sid>.json with GATE_STATUS"
  } else {
    $script:fail++; Write-Output "FAIL: mark-abnormal-stop: StopFailure payload -> abnormal-stop-<sid>.json with GATE_STATUS"; Write-Output ("  rc={0} got: {1} file: {2}" -f $r.rc, $r.out, $txt)
  }
  $r = Invoke-PyHook 'log-instructions-loaded.py' ('{"session_id":"st 1","cwd":"' + $p12 + '","hook_event_name":"InstructionsLoaded","load_reason":"session_start","file_path":"' + $p12 + '/CLAUDE.md","file_content":"秘匿本文\n2\n3\n"}') $logs12
  $f = Join-Path $logs12 'instructions-loaded.jsonl'
  $txt = if (Test-Path $f) { [IO.File]::ReadAllText($f, [Text.Encoding]::UTF8) } else { '' }
  if ($r.rc -eq 0 -and $r.out -eq '' -and $txt -match '"lines": 3' -and $txt -match '"file_path": "CLAUDE.md"' -and $txt -notmatch [regex]::Escape('秘匿本文')) {
    $script:pass++; Write-Output "PASS: log-instructions-loaded: payload -> jsonl (lines/bytes, no file_content)"
  } else {
    $script:fail++; Write-Output "FAIL: log-instructions-loaded: payload -> jsonl (lines/bytes, no file_content)"; Write-Output ("  rc={0} got: {1} file: {2}" -f $r.rc, $r.out, $txt)
  }
  $r = Invoke-PyHook 'log-model-switch.py' ('{"session_id":"st 1","cwd":"' + $p12 + '","hook_event_name":"PreModelSwitch","from_model":"claude-opus-5","to_model":"claude-fable-5-1"}') $logs12
  $f = Join-Path $logs12 'model-switch.jsonl'
  $txt = if (Test-Path $f) { [IO.File]::ReadAllText($f, [Text.Encoding]::UTF8) } else { '' }
  if ($r.rc -eq 0 -and $r.out -eq '' -and $txt -match '"to_model": "claude-fable-5-1"' -and $txt -match '"hook_event_name": "PreModelSwitch"') {
    $script:pass++; Write-Output "PASS: log-model-switch: PreModelSwitch payload -> jsonl, no stdout (never blocks)"
  } else {
    $script:fail++; Write-Output "FAIL: log-model-switch: PreModelSwitch payload -> jsonl, no stdout (never blocks)"; Write-Output ("  rc={0} got: {1} file: {2}" -f $r.rc, $r.out, $txt)
  }
  Remove-Item -Recurse -Force $fx12
} else {
  Write-Output "SKIP: mark-abnormal-stop / log-instructions-loaded / log-model-switch (python not found)"
}

# --- 第5回再監査(2026-09-09: RG-3 / RG-6 / RG-7 / CC-14)の回帰テスト。selftest.sh と同一ケース ---
# H-1/RG-3: バッククォート(PowerShell)・キャレット(cmd)の行継続で分割された git push も ask
Check "dangerous-git: backtick line-continuation git push -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git `\npush origin main"}}' 'ask'
Check "dangerous-git: caret line-continuation git push -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git ^\npush origin main"}}' 'ask'

# H-4/RG-6: brownfield 取り込み直後(test: in_progress の残余 + 「状態: 運用中」注記)は deny。
# /12 が implementation を in_progress にした改修サイクル中は従来どおり allow。
$fx16 = Join-Path $env:TEMP ("hooktest12-" + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Force (Join-Path $fx16 'docs\00-overview') | Out-Null
[IO.File]::WriteAllText((Join-Path $fx16 'docs\00-overview\progress.md'), "# 進捗`n`n<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: done`ntest: in_progress`nrelease: not_started`n-->`n`n状態: 運用中（改修サイクル）。次の依頼は /12-change-request で受け付ける`n", $utf8nb)
Check "phase-scope: operating note + residual test in_progress -> deny (H-4)" 'guard-phase-scope.ps1' '{"tool_input":{"file_path":"src/app.ts"}}' 'deny' $fx16
[IO.File]::WriteAllText((Join-Path $fx16 'docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: in_progress`ntest: in_progress`nrelease: not_started`n-->`n`n状態: 運用中`n", $utf8nb)
Check "phase-scope: operating note + implementation in_progress (CR active) -> allow" 'guard-phase-scope.ps1' '{"tool_input":{"file_path":"src/app.ts"}}' 'allow' $fx16
Remove-Item -Recurse -Force $fx16

# H-11/RG-6: テンプレート経路(DECISIONS.md 残留 + USAGE.md + 実メモ)は本体ではなく新規プロジェクト
$fx17 = Join-Path $env:TEMP ("hooktest13-" + [IO.Path]::GetRandomFileName())
foreach ($d in '.github\harness','requirements') { New-Item -ItemType Directory -Force (Join-Path $fx17 $d) | Out-Null }
[IO.File]::WriteAllText((Join-Path $fx17 'DECISIONS.md'), "", $utf8nb)
[IO.File]::WriteAllText((Join-Path $fx17 '.github\harness\USAGE.md'), "usage", $utf8nb)
[IO.File]::WriteAllText((Join-Path $fx17 'requirements\memo.md'), "# 要件メモ（自由記述）`n`n---`n`n（ここから記入）`nCLIメモ帳ツールを作りたい。add/list/delete。`n", $utf8nb)
Check "phase-scope: template route (DECISIONS.md + real memo) -> ask, not body (H-11)" 'guard-phase-scope.ps1' '{"tool_input":{"file_path":"src/app.ts"}}' 'ask' $fx17
$out = Invoke-Hook 'route-request.ps1' '{}' $fx17
if ($out -match '00-start-project') {
  $script:pass++; Write-Output "PASS: route-request.ps1: template route (DECISIONS.md + real memo) -> /00 injected (H-11)"
} else {
  $script:fail++; Write-Output "FAIL: route-request.ps1: template route (DECISIONS.md + real memo) -> /00 injected (H-11)"; Write-Output "  got: $out"
}
$out = Invoke-Hook 'inject-progress.ps1' '{}' $fx17
if (($out -match '00-start-project') -and ($out -notmatch [regex]::Escape('本体リポジトリ'))) {
  $script:pass++; Write-Output "PASS: inject-progress.ps1: template route (DECISIONS.md + real memo) -> /00 guidance, not body (H-11)"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress.ps1: template route (DECISIONS.md + real memo) -> /00 guidance, not body (H-11)"; Write-Output "  got: $out"
}
Remove-Item -Recurse -Force $fx17

# H-10/RG-7: 8.3 短縮名(C:\Users\RUNNER~1\… 型)のペイロード/cwd でもフェーズ外編集を deny。
# 実 8.3 名は Scripting.FileSystemObject の ShortPath(GetShortPathNameW 相当)で取得する。得られない
# 環境(8dot3name 無効ボリューム)では skip にせず、短縮名の形をした実ディレクトリ(HOOKSE~1)で
# 同じ2ケースを通す。長形式側は Get-Item の FullName で揃える(CI runner は TEMP 自体が RUNNER~1)。
$fx18 = Join-Path $env:TEMP ("hook-selftest-longname-directory-" + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Force (Join-Path $fx18 'docs\00-overview') | Out-Null
[IO.File]::WriteAllText((Join-Path $fx18 'docs\00-overview\progress.md'), $gateAllDone, $utf8nb)
$long18 = (Get-Item -LiteralPath $fx18).FullName
$short18 = $null
try { $short18 = (New-Object -ComObject Scripting.FileSystemObject).GetFolder($fx18).ShortPath } catch {}
if ((-not $short18) -or ($short18 -ieq $long18)) {
  $alt18 = Join-Path $fx18 'HOOKSE~1'
  New-Item -ItemType Directory -Force (Join-Path $alt18 'docs\00-overview') | Out-Null
  Copy-Item (Join-Path $fx18 'docs\00-overview\progress.md') (Join-Path $alt18 'docs\00-overview\progress.md')
  $long18 = $alt18; $short18 = $alt18
}
$long18s = $long18 -replace '\\', '/'
$short18s = $short18 -replace '\\', '/'
Check "phase-scope: 8.3 short-name payload, all done -> deny (H-10)" 'guard-phase-scope.ps1' ('{"tool_input":{"file_path":"' + $short18s + '/src/app.ts"}}') 'deny' $long18
Check "phase-scope: 8.3 short-name cwd, all done -> deny (H-10)" 'guard-phase-scope.ps1' ('{"tool_input":{"file_path":"' + $long18s + '/src/app.ts"}}') 'deny' $short18
Remove-Item -Recurse -Force $fx18

# --- python 系 Stop フック(remind-record / draft-learnings)の --selftest(CC-14 の鮮度ゲート含む)。
#     Windows 実機でも python 経路を回す(従来 selftest.ps1 は python 系を1件も検証していなかった)。
#     python が無い環境では SKIP(件数に数えない)。解決順は run-python.sh と同じ python→python3。 ---
$pyBin = $null
foreach ($cand in 'python', 'python3') {
  if (Get-Command $cand -ErrorAction SilentlyContinue) {
    try { & $cand --version *> $null; if ($LASTEXITCODE -eq 0) { $pyBin = $cand; break } } catch {}
  }
}
if ($pyBin) {
  # (ループにしない: 静的件数と実行件数を一致させる。selftest.sh と同じ規約)
  & $pyBin (Join-Path $scriptsDir 'remind-record.py') --selftest *> $null
  if ($LASTEXITCODE -eq 0) {
    $script:pass++; Write-Output "PASS: remind-record.py --selftest"
  } else {
    $script:fail++; Write-Output "FAIL: remind-record.py --selftest"
  }
  & $pyBin (Join-Path $scriptsDir 'draft-learnings.py') --selftest *> $null
  if ($LASTEXITCODE -eq 0) {
    $script:pass++; Write-Output "PASS: draft-learnings.py --selftest"
  } else {
    $script:fail++; Write-Output "FAIL: draft-learnings.py --selftest"
  }
} else {
  Write-Output "SKIP: python hook selftests (python not found)"
}

# --- 計測系 python フック(2026-09-09 再監査 §5): 起動テスト。空入力では無出力 exit 0(fail-open)、
#     statusline.py は公式モックの縮約 JSON で非空の 1 行を返す(ps1 鏡は無く python 1 本。ロジックの検証は各 --selftest) ---
function Invoke-PyHook($scriptName, $payload) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $script:pyExe
  $psi.Arguments = '"' + (Join-Path $scriptsDir $scriptName) + '"'
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding $false
  $psi.WorkingDirectory = (Get-Location).Path
  $proc = [System.Diagnostics.Process]::Start($psi)
  if ($payload) {
    $bytes = (New-Object System.Text.UTF8Encoding $false).GetBytes($payload)
    $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
  }
  $proc.StandardInput.Close()
  $out = $proc.StandardOutput.ReadToEnd()
  $proc.StandardError.ReadToEnd() | Out-Null
  $proc.WaitForExit()
  return @{ out = $out; rc = $proc.ExitCode }
}
# python の解決順は run-python.sh と同じ python→python3。Windows の Store スタブは --version で除外する
$script:pyExe = $null
foreach ($cand in 'python', 'python3') {
  $cmd = Get-Command $cand -ErrorAction SilentlyContinue
  if ($cmd) {
    try { & $cmd.Source --version *> $null; if ($LASTEXITCODE -eq 0) { $script:pyExe = $cmd.Source; break } } catch {}
  }
}
if ($script:pyExe) {
  # (ループにしない: 静的件数と実行件数を一致させる。selftest.sh と同じ規約)
  $r = Invoke-PyHook 'log-effort.py' ''
  if ($r.rc -eq 0 -and -not $r.out.Trim()) {
    $script:pass++; Write-Output "PASS: log-effort.py (python): empty stdin -> no output, exit 0"
  } else {
    $script:fail++; Write-Output "FAIL: log-effort.py (python): empty stdin -> no output, exit 0"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  $r = Invoke-PyHook 'log-subagent.py' ''
  if ($r.rc -eq 0 -and -not $r.out.Trim()) {
    $script:pass++; Write-Output "PASS: log-subagent.py (python): empty stdin -> no output, exit 0"
  } else {
    $script:fail++; Write-Output "FAIL: log-subagent.py (python): empty stdin -> no output, exit 0"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  $r = Invoke-PyHook 'session-baseline.py' ''
  if ($r.rc -eq 0 -and -not $r.out.Trim()) {
    $script:pass++; Write-Output "PASS: session-baseline.py (python): empty stdin -> no output, exit 0"
  } else {
    $script:fail++; Write-Output "FAIL: session-baseline.py (python): empty stdin -> no output, exit 0"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  $r = Invoke-PyHook 'statusline.py' '{"session_id":"selftest-sl","model":{"display_name":"Opus"},"cost":{"total_cost_usd":0.5,"total_lines_added":1,"total_lines_removed":0},"context_window":{"used_percentage":12},"effort":{"level":"high"},"version":"2.1.201"}'
  if ($r.rc -eq 0 -and $r.out -match 'ctx 12%' -and $r.out -match [regex]::Escape('$0.50')) {
    $script:pass++; Write-Output "PASS: statusline.py (python): mock JSON -> one-line display with cost and ctx%"
  } else {
    $script:fail++; Write-Output "FAIL: statusline.py (python): mock JSON -> one-line display with cost and ctx%"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $scriptsDir '..\logs\usage\selftest-sl.status.json'), (Join-Path $scriptsDir '..\logs\usage\selftest-sl.statusline.txt')
} else {
  Write-Output "SKIP: python hooks startup tests (python not found)"
}

# --- 統合(2026-09-10): CC-9 の SessionStart 注入(.sh と同一ケース)。24 時間以内の abnormal-stop-*.json があれば
#     前回の異常終了を注入する。記録先は HARNESS_HOOK_LOG_DIR で一時ディレクトリに向け、実 logs/ を汚さない ---
$fx20 = Join-Path $env:TEMP ("hooktest20-" + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path (Join-Path $fx20 'logs') -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $fx20 'logs\abnormal-stop-selftest.json'), '{"error_type": "rate_limit"}', (New-Object System.Text.UTF8Encoding $false))
$env:HARNESS_HOOK_LOG_DIR = (Join-Path $fx20 'logs')
$out = Invoke-Hook 'inject-progress.ps1' '{}' $fx20
Remove-Item Env:HARNESS_HOOK_LOG_DIR -ErrorAction SilentlyContinue
if (($out -match '異常終了') -and ($out -match 'rate_limit')) {
  $script:pass++; Write-Output "PASS: inject-progress.ps1: recent abnormal-stop -> crash notice injected (CC-9)"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress.ps1: recent abnormal-stop -> crash notice injected (CC-9)"; Write-Output "  got: $out"
}
Remove-Item -Recurse -Force $fx20
# =====================================================================================
# 第5回追補(2026-09-10): guard-harness-config-edit / guard-template-edit の 8.3 短縮名展開(H-10/RG-7 の
# 横展開。_paths.ps1 の ConvertTo-LongPath / ConvertTo-LongPathCandidate)、セグメント数上限の fail-closed、
# 候補 64 件でも最後の候補に判定が届くこと。期待値は selftest.sh の同名ケースと同一。
# 実 8.3 名は Scripting.FileSystemObject の ShortPath で取得し、得られない環境(8dot3name 無効ボリューム)では
# 長形式で同じ期待値を通す(ケース数を環境で変えない)。
# =====================================================================================
$fx21 = Join-Path $env:TEMP ("hook-selftest-shortname-directory-" + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Force (Join-Path $fx21 '.github\hooks') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $fx21 'docs\01-requirements') | Out-Null
[IO.File]::WriteAllText((Join-Path $fx21 'docs\01-requirements\requirements_template.md'), 'x')
$long21 = (Get-Item -LiteralPath $fx21).FullName -replace '\\', '/'
$short21g = $null; $short21t = $null
try {
  $fso21 = New-Object -ComObject Scripting.FileSystemObject
  $short21g = $fso21.GetFolder((Join-Path $fx21 '.github')).ShortPath
  $short21t = $fso21.GetFile((Join-Path $fx21 'docs\01-requirements\requirements_template.md')).ShortPath
} catch {}
if ((-not $short21g) -or ($short21g -imatch '\.github$')) {
  Write-Output "INFO: 8.3 short names unavailable here; H-10 cross-check cases run with long paths"
  $short21g = $long21 + '/.github'; $short21t = $long21 + '/docs/01-requirements/requirements_template.md'
}
$short21g = $short21g -replace '\\', '/'; $short21t = $short21t -replace '\\', '/'
$short21rel = Split-Path -Leaf $short21g
Check "harness-config-edit: 8.3 short-name GITHUB~1/hooks Write -> deny (H-10)" 'guard-harness-config-edit.ps1' ('{"tool_name":"Write","tool_input":{"file_path":"' + $short21g + '/hooks/evil.sh","content":"x"}}') 'deny'
Check "harness-config-edit: 8.3 short-name relative GITHUB~1 Write (cwd join) -> deny (H-10)" 'guard-harness-config-edit.ps1' ('{"tool_name":"Write","tool_input":{"file_path":"' + $short21rel + '/hooks/evil.sh","content":"x"}}') 'deny' $fx21
Check "harness-config-edit: 8.3 short-name in command redirect -> ask (H-10)" 'guard-harness-config-edit.ps1' ('{"tool_name":"Bash","tool_input":{"command":"echo x > ' + $short21g + '/hooks/evil.sh"}}') 'ask'
Check "template-edit: 8.3 short-name REQUIR~1.MD Write -> deny (H-10)" 'guard-template-edit.ps1' ('{"tool_name":"Write","tool_input":{"file_path":"' + $short21t + '","content":"x"}}') 'deny'
Check "harness-config-edit: tilde-digit path outside protected dirs -> allow (H-10 contrast)" 'guard-harness-config-edit.ps1' ('{"tool_name":"Write","tool_input":{"file_path":"' + $long21 + '/NOTES~1/readme.md","content":"x"}}') 'allow'
Remove-Item -Recurse -Force $fx21
# セグメント数上限(32)の fail-closed: 33 セグメント以上は評価前に ask、32 以下の読み取り専用は allow
$segs33 = ('cat AGENTS.md; ' * 33)
Check "harness-config-edit: 33 read-only segments with protected path -> ask (segment cap, fail-closed)" 'guard-harness-config-edit.ps1' ('{"tool_name":"Bash","tool_input":{"command":"' + $segs33 + '"}}') 'ask'
$segs32 = ('cat AGENTS.md; ' * 32)
Check "harness-config-edit: 32 read-only segments with protected path -> allow (within cap)" 'guard-harness-config-edit.ps1' ('{"tool_name":"Bash","tool_input":{"command":"' + $segs32 + '"}}') 'allow'
# 候補 64 件(上限)の一括編集で 64 件目の保護パスにも判定が届くこと
$files64 = (1..63 | ForEach-Object { '{"path":"src/f' + $_ + '.ts"}' }) -join ','
Check "harness-config-edit: 64-file edit with .github/hooks at #64 -> deny" 'guard-harness-config-edit.ps1' ('{"tool_name":"edit_files","tool_input":{"files":[' + $files64 + ',{"path":".github/hooks/x.sh"}]}}') 'deny'

# =====================================================================================
# 第6波(2026-09-14: A6-20 / D077 notes 5 / D073 open / D074 open / PF-10)の回帰テスト。selftest.sh と同一ケース
# (parse_hook_input の契約は sh 固有のため ps1 には無い)。判定ログの JSONL 化(_log.ps1)と canary secret の redaction、
# python フックの stdin UTF-8、done 契約の model_mismatch、SubagentStop の verdict 強制、PreModelSwitch の ask モード。
# 記録先は HARNESS_HOOK_LOG_DIR で一時ディレクトリに向け、実 logs/ を汚さない。
# =====================================================================================
$fx22 = Join-Path $env:TEMP ("hooktest22-" + [IO.Path]::GetRandomFileName())
foreach ($d in 'logs\usage', 'docs\00-overview', 'docs\03-implementation', 'docs\04-test', 'jp\docs\00-overview') { New-Item -ItemType Directory -Force (Join-Path $fx22 $d) | Out-Null }
$p22 = $fx22 -replace '\\', '/'
$utf822 = New-Object System.Text.UTF8Encoding $false
$logs22 = Join-Path $fx22 'logs'
$jl22 = Join-Path $logs22 'hook-decisions.jsonl'
# (1) 判定ログ JSONL(A6-20): canary secret が平文で残らず [REDACTED] になり、欄の順序と session_id 等が入ること
$env:HARNESS_HOOK_LOG_DIR = $logs22
$out = Invoke-Hook 'guard-dangerous-git.ps1' '{"session_id":"selftest-canary","hook_event_name":"PreToolUse","tool_name":"Bash","tool_use_id":"toolu_canary","tool_input":{"command":"git push https://alice:hunter2-canary@example.com/repo.git ; export X=sk-ant-api03-CANARYCANARYCANARY0123456789abcdef"}}'
Remove-Item Env:HARNESS_HOOK_LOG_DIR -ErrorAction SilentlyContinue
$jlText = ''
if (Test-Path -LiteralPath $jl22) { $jlText = [IO.File]::ReadAllText($jl22, $utf822) }
if (($out -match '"permissionDecision":\s*"ask"') -and $jlText -and ($jlText -notmatch 'hunter2-canary') -and ($jlText -notmatch 'CANARYCANARY') -and ($jlText -match [regex]::Escape('://[REDACTED]@'))) {
  $script:pass++; Write-Output "PASS: hook-log: canary secrets are redacted in hook-decisions.jsonl (dangerous-git ask)"
} else {
  $script:fail++; Write-Output "FAIL: hook-log: canary secrets are redacted in hook-decisions.jsonl (dangerous-git ask)"; Write-Output "  out: $out"; Write-Output "  log: $jlText"
}
$rec22 = $null
try { $rec22 = ((($jlText -split "`n") | Where-Object { $_.Trim() }) | Select-Object -Last 1) | ConvertFrom-Json } catch {}
if ($rec22 -and (($rec22.PSObject.Properties.Name -join ',') -eq 'ts,session_id,hook_event,tool_name,script,decision,target,tool_use_id,duration_ms,host') -and ($rec22.session_id -eq 'selftest-canary') -and ($rec22.hook_event -eq 'PreToolUse') -and ($rec22.tool_name -eq 'Bash') -and ($rec22.tool_use_id -eq 'toolu_canary') -and ($rec22.script -eq 'guard-dangerous-git.ps1') -and ($rec22.decision -eq 'ask') -and ($rec22.target.Length -le 120) -and (($rec22.duration_ms -is [int]) -or ($rec22.duration_ms -is [long])) -and ($rec22.duration_ms -ge 0) -and ($rec22.host -eq 'claude-code')) {
  $script:pass++; Write-Output "PASS: hook-log: JSONL record has fixed field order (10 fields incl. duration_ms int / host claude-code), session_id/hook_event/tool_name/tool_use_id/script, target <= 120"
} else {
  $script:fail++; Write-Output "FAIL: hook-log: JSONL record has fixed field order (10 fields incl. duration_ms int / host claude-code), session_id/hook_event/tool_name/tool_use_id/script, target <= 120"; Write-Output "  log: $jlText"
}
# (3) done 契約の model_mismatch(D077 notes 5 / A6-10): sh と同一の 3 ケース(方針外 → 専用警告 / 方針内 → 一般 done 警告 /
#     記録なし → 一般 done 警告)。方針表は fixture に tools/ が無いため guard-subagent-model.ps1 の埋め込み表を使う
[IO.File]::WriteAllText((Join-Path $fx22 'docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: in_progress`ntest: not_started`nrelease: not_started`n-->`n", $utf822)
[IO.File]::WriteAllText((Join-Path $fx22 'docs\03-implementation\tasks.md'), "- [x] TASK-001: x（完了条件: npm test 12件成功 2026-09-10 09:00）`n", $utf822)
[IO.File]::WriteAllText((Join-Path $fx22 'docs\04-test\review-log.md'), "# 独立レビュー記録`n`n### 2026-09-10 10:30 / implementation / 対象: TASK-001 / verdict: 承認`n", $utf822)
$mm22 = '{"session_id":"selftest-mm","hook_event_name":"PostToolUse","tool_name":"Edit","tool_input":{"file_path":"docs/00-overview/progress.md","new_string":"implementation: done"}}'
$sub22 = Join-Path $logs22 'usage\selftest-mm.subagents.jsonl'
[IO.File]::WriteAllText($sub22, '{"session_id":"selftest-mm","hook_event":"PostToolUse","agent_type":"reviewer","agent_id":"a1","requested_model":"haiku","resolved_model":"claude-haiku-4-5-20251001"}' + "`n", $utf822)
$env:HARNESS_HOOK_LOG_DIR = $logs22
$out = Invoke-Hook 'warn-gate-tamper.ps1' $mm22 $fx22
if (($out -match 'model_mismatch') -and ($out -match 'claude-haiku-4-5-20251001')) {
  $script:pass++; Write-Output "PASS: gate-tamper: implementation done, last reviewer ran on out-of-policy model -> model_mismatch warn (D077)"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: implementation done, last reviewer ran on out-of-policy model -> model_mismatch warn (D077)"; Write-Output "  got: $out"
}
[IO.File]::AppendAllText($sub22, '{"session_id":"selftest-mm","hook_event":"SubagentStop","agent_type":"reviewer","agent_id":"a2","requested_model":null,"resolved_model":"claude-fable-5-1-20260101"}' + "`n", $utf822)
$out = Invoke-Hook 'warn-gate-tamper.ps1' $mm22 $fx22
if (($out -match [regex]::Escape('明示承認')) -and ($out -notmatch 'model_mismatch')) {
  $script:pass++; Write-Output "PASS: gate-tamper: latest reviewer on allowed model -> generic done warn (no model_mismatch)"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: latest reviewer on allowed model -> generic done warn (no model_mismatch)"; Write-Output "  got: $out"
}
Remove-Item -LiteralPath $sub22 -Force -ErrorAction SilentlyContinue
$out = Invoke-Hook 'warn-gate-tamper.ps1' $mm22 $fx22
if (($out -match [regex]::Escape('明示承認')) -and ($out -notmatch 'model_mismatch')) {
  $script:pass++; Write-Output "PASS: gate-tamper: no subagent record for the session -> generic done warn (no model_mismatch)"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: no subagent record for the session -> generic done warn (no model_mismatch)"; Write-Output "  got: $out"
}
Remove-Item Env:HARNESS_HOOK_LOG_DIR -ErrorAction SilentlyContinue
# python 系(2)(4)(5)。解決順は run-python.sh と同じ python→python3、--version で実行可否を確認(無ければ SKIP)
$py22 = $null
foreach ($cand in 'python', 'python3') {
  $c22 = Get-Command $cand -ErrorAction SilentlyContinue
  if ($c22) { try { & $c22.Source --version *> $null; if ($LASTEXITCODE -eq 0) { $py22 = $c22.Source; break } } catch {} }
}
function Invoke-PyHook22($scriptName, $payload, $workDir, $extraArgs) {
  # stdin は BOM なし UTF-8 のバイト列を直接書く。記録先は HARNESS_HOOK_LOG_DIR で一時ディレクトリに向ける
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $py22
  $a = '"' + (Join-Path $scriptsDir $scriptName) + '"'
  if ($extraArgs) { $a = $a + ' ' + $extraArgs }
  $psi.Arguments = $a
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding $false
  $psi.EnvironmentVariables['HARNESS_HOOK_LOG_DIR'] = $logs22
  if ($workDir) { $psi.WorkingDirectory = $workDir } else { $psi.WorkingDirectory = (Get-Location).Path }
  $proc = [System.Diagnostics.Process]::Start($psi)
  if ($payload) { $bytes = $utf822.GetBytes($payload); $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length) }
  $proc.StandardInput.Close()
  $o = $proc.StandardOutput.ReadToEnd()
  $proc.StandardError.ReadToEnd() | Out-Null
  $proc.WaitForExit()
  return @{ out = $o; rc = $proc.ExitCode }
}
if ($py22) {
  # (2) stdin UTF-8(D074 の横展開): 日本語の last_assistant_message がトランスクリプト末尾と一致して block(remind-record)、
  #     日本語の error_message が化けずに残る(mark-abnormal-stop)
  [IO.File]::WriteAllText((Join-Path $fx22 'jp\docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`n-->`n", $utf822)
  [IO.File]::WriteAllText((Join-Path $fx22 'jp\t.jsonl'), ('{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"' + $p22 + '/jp/src/main.py"}}]}}' + "`n" + '{"type":"assistant","message":{"content":[{"type":"text","text":"実装を変更しました。記録は後で行います。"}]}}' + "`n"), $utf822)
  $r = Invoke-PyHook22 'remind-record.py' ('{"transcript_path":"' + $p22 + '/jp/t.jsonl","cwd":"' + $p22 + '/jp","last_assistant_message":"実装を変更しました。記録は後で行います。"}') $null $null
  if ($r.rc -eq 0 -and ($r.out -match '"block"')) {
    $script:pass++; Write-Output "PASS: remind-record: Japanese last_assistant_message (UTF-8 stdin) matches transcript -> block"
  } else {
    $script:fail++; Write-Output "FAIL: remind-record: Japanese last_assistant_message (UTF-8 stdin) matches transcript -> block"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  $r = Invoke-PyHook22 'mark-abnormal-stop.py' ('{"session_id":"selftest-jp","cwd":"' + $p22 + '/jp","hook_event_name":"StopFailure","error_type":"rate_limit","error_message":"レート制限に達しました"}') $null $null
  $ab22 = $null
  try { $ab22 = [IO.File]::ReadAllText((Join-Path $logs22 'abnormal-stop-selftest-jp.json'), $utf822) | ConvertFrom-Json } catch {}
  if ($r.rc -eq 0 -and (-not $r.out.Trim()) -and $ab22 -and ($ab22.error_message -eq 'レート制限に達しました')) {
    $script:pass++; Write-Output "PASS: mark-abnormal-stop: Japanese error_message survives stdin decode (UTF-8) -> stored verbatim"
  } else {
    $script:fail++; Write-Output "FAIL: mark-abnormal-stop: Japanese error_message survives stdin decode (UTF-8) -> stored verbatim"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  # (4) SubagentStop の verdict 強制(PF-10 / D073 open)
  $r = Invoke-PyHook22 'guard-subagent-output.py' '{"session_id":"selftest-so","hook_event_name":"SubagentStop","agent_id":"r1","agent_type":"reviewer","last_assistant_message":"読みました。特に言うことはありません。"}' $null $null
  if ($r.rc -eq 0 -and ($r.out -match '"block"') -and ($r.out -match 'verdict')) {
    $script:pass++; Write-Output "PASS: subagent-output: reviewer without verdict token -> block with reason (verdict required)"
  } else {
    $script:fail++; Write-Output "FAIL: subagent-output: reviewer without verdict token -> block with reason (verdict required)"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  $r = Invoke-PyHook22 'guard-subagent-output.py' '{"session_id":"selftest-so","hook_event_name":"SubagentStop","agent_id":"r2","agent_type":"reviewer","last_assistant_message":"### 2026-09-14 10:00 / implementation / 対象: TASK-1 / verdict: 承認"}' $null $null
  if ($r.rc -eq 0 -and (-not $r.out.Trim())) {
    $script:pass++; Write-Output "PASS: subagent-output: reviewer with verdict (approval token) -> silent allow"
  } else {
    $script:fail++; Write-Output "FAIL: subagent-output: reviewer with verdict (approval token) -> silent allow"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  $r = Invoke-PyHook22 'guard-subagent-output.py' '{"session_id":"selftest-so","hook_event_name":"SubagentStop","agent_id":"w1","agent_type":"task-worker","last_assistant_message":"done"}' $null $null
  if ($r.rc -eq 0 -and (-not $r.out.Trim())) {
    $script:pass++; Write-Output "PASS: subagent-output: task-worker -> out of scope, silent exit 0"
  } else {
    $script:fail++; Write-Output "FAIL: subagent-output: task-worker -> out of scope, silent exit 0"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  & $py22 (Join-Path $scriptsDir 'guard-subagent-output.py') --selftest *> $null
  if ($LASTEXITCODE -eq 0) {
    $script:pass++; Write-Output "PASS: guard-subagent-output.py --selftest"
  } else {
    $script:fail++; Write-Output "FAIL: guard-subagent-output.py --selftest"
  }
  & $py22 (Join-Path $scriptsDir '_log.py') --selftest *> $null
  if ($LASTEXITCODE -eq 0) {
    $script:pass++; Write-Output "PASS: _log.py --selftest (JSONL format, redaction, legacy TSV reader, trim)"
  } else {
    $script:fail++; Write-Output "FAIL: _log.py --selftest (JSONL format, redaction, legacy TSV reader, trim)"
  }
  # (5) PreModelSwitch の ask モード(D074 open): --mode ask + in_progress + 方針外の切替先 → ask、既定(log)は無出力
  $ms22 = '{"session_id":"selftest-ms","hook_event_name":"PreModelSwitch","cwd":"' + $p22 + '","from_model":"claude-fable-5-1","to_model":"claude-haiku-4-5-20251001"}'
  $r = Invoke-PyHook22 'log-model-switch.py' $ms22 $null '--mode ask'
  if ($r.rc -eq 0 -and ($r.out -match '"permissionDecision":\s*"ask"')) {
    $script:pass++; Write-Output "PASS: log-model-switch: --mode ask, in_progress + out-of-policy to_model -> permissionDecision ask"
  } else {
    $script:fail++; Write-Output "FAIL: log-model-switch: --mode ask, in_progress + out-of-policy to_model -> permissionDecision ask"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  $r = Invoke-PyHook22 'log-model-switch.py' $ms22 $null $null
  if ($r.rc -eq 0 -and (-not $r.out.Trim())) {
    $script:pass++; Write-Output "PASS: log-model-switch: default mode (log) -> silent even for out-of-policy to_model"
  } else {
    $script:fail++; Write-Output "FAIL: log-model-switch: default mode (log) -> silent even for out-of-policy to_model"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
} else {
  Write-Output "SKIP: wave-6 python cases (remind-record / mark-abnormal-stop / subagent-output / _log / log-model-switch ask) (python not found)"
}
Remove-Item -Recurse -Force $fx22 -ErrorAction SilentlyContinue

# 第6波(2026-09-10 doctor / 配布鮮度。再監査 RD-4 / A6-15): inject-progress.ps1 の SessionStart が
# harness-origin.md の latest_decision と本体(path:)の DECISIONS.md の最新 D 番号を比べ、閾値以上古ければ
# 「/91 を先に実行」を注入する。selftest.sh と同一の 5 ケース・同一の期待値。
# =====================================================================================
$fx22 = Join-Path $env:TEMP ("hooktest22-" + [IO.Path]::GetRandomFileName())
foreach ($d in 'body\.github\harness','proj\docs\00-overview','proj\requirements') { New-Item -ItemType Directory -Force (Join-Path $fx22 $d) | Out-Null }
[IO.File]::WriteAllText((Join-Path $fx22 'body\DECISIONS.md'), "## D003: a`n`n## D007: b`n", $utf8nb)
[IO.File]::WriteAllText((Join-Path $fx22 'body\.github\harness\USAGE.md'), "usage", $utf8nb)
[IO.File]::WriteAllText((Join-Path $fx22 'proj\requirements\memo.md'), "# 要件メモ`nCLI ツールを作りたい`n", $utf8nb)
[IO.File]::WriteAllText((Join-Path $fx22 'proj\docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: in_progress`n-->`n", $utf8nb)
$body22 = (Join-Path $fx22 'body') -replace '\\', '/'
$origin22 = Join-Path $fx22 'proj\docs\00-overview\harness-origin.md'
[IO.File]::WriteAllText($origin22, "<!-- HARNESS_ORIGIN`npath: $body22`nversion: D003`nsynced: 2026-09-10`nlatest_decision: D003`n-->`n", $utf8nb)
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx22 'proj')
if (($out -match '91-sync-from-harness') -and ($out -match '4 世代')) {
  $script:pass++; Write-Output "PASS: inject-progress.ps1: origin D003 vs body D007 -> '/91 first' injected (4 generations behind)"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress.ps1: origin D003 vs body D007 -> '/91 first' injected (4 generations behind)"; Write-Output "  got: $out"
}
$env:HARNESS_STALE_GENERATIONS = '5'
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx22 'proj')
Remove-Item Env:HARNESS_STALE_GENERATIONS -ErrorAction SilentlyContinue
if ($out -notmatch '91-sync-from-harness') {
  $script:pass++; Write-Output "PASS: inject-progress.ps1: 4 generations behind but threshold 5 -> not injected"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress.ps1: 4 generations behind but threshold 5 -> not injected"; Write-Output "  got: $out"
}
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx22 'proj') 'PreCompact'
if ($out -and ($out -notmatch '91-sync-from-harness')) {
  $script:pass++; Write-Output "PASS: inject-progress.ps1: stale origin is not injected on PreCompact"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress.ps1: stale origin is not injected on PreCompact"; Write-Output "  got: $out"
}
[IO.File]::WriteAllText($origin22, "<!-- HARNESS_ORIGIN`npath: $body22`nversion: D007`nsynced: 2026-09-10`nlatest_decision: D007`n-->`n", $utf8nb)
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx22 'proj')
if (($out -notmatch '91-sync-from-harness') -and ($out -match 'GATE_STATUS')) {
  $script:pass++; Write-Output "PASS: inject-progress.ps1: origin D007 = body D007 -> not injected, GATE_STATUS still injected"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress.ps1: origin D007 = body D007 -> not injected, GATE_STATUS still injected"; Write-Output "  got: $out"
}
[IO.File]::WriteAllText($origin22, "<!-- HARNESS_ORIGIN`npath: $body22/nowhere`nversion: D001`nsynced: 2026-09-10`nlatest_decision: D001`n-->`n", $utf8nb)
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx22 'proj')
if ($out -and ($out -notmatch '91-sync-from-harness')) {
  $script:pass++; Write-Output "PASS: inject-progress.ps1: harness path unreachable -> not injected (fail-open)"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress.ps1: harness path unreachable -> not injected (fail-open)"; Write-Output "  got: $out"
}
Remove-Item -Recurse -Force $fx22
# 第2波 w2-supply(2026-09-14): 供給網・リリース権限・証拠ゲート(codex 監査 2026-08-31 C-01/C-02 =
# IA-20260831-01/02、A2-4b、R-02)。期待値は selftest.sh の同名ケースと同一(1 ケース 1 増分行)。
# =====================================================================================
# --- guard-external-effect.ps1: 外部反映は exact-action の ask、読み取り・plan・dry-run・ループバック宛は allow、
#     承認バイパス下は deny(codex C-01 の回帰 4 条件) ---
Check "external-effect: terraform apply -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"terraform apply -auto-approve"}}' 'ask'
Check "external-effect: terraform plan -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"terraform plan -out=tfplan"}}' 'allow'
Check "external-effect: kubectl apply -f -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"kubectl apply -f k8s/deploy.yaml -n prod"}}' 'ask'
Check "external-effect: kubectl apply --dry-run=client -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"kubectl apply -f k8s/deploy.yaml --dry-run=client"}}' 'allow'
Check "external-effect: curl -X POST to external host -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"curl -X POST -d @payload.json https://api.example.com/v1/deploy"}}' 'ask'
Check "external-effect: curl GET -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"curl -s https://api.example.com/v1/status"}}' 'allow'
Check "external-effect: curl -X POST to localhost (loopback) -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"curl -X POST http://localhost:3000/api/items -d {}"}}' 'allow'
Check "external-effect: npm publish -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"npm publish --access public"}}' 'ask'
Check "external-effect: npm publish --dry-run -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"npm publish --dry-run"}}' 'allow'
Check "external-effect: docker build && docker push -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"docker build -t registry.example.com/app:1.2.0 . && docker push registry.example.com/app:1.2.0"}}' 'ask'
Check "external-effect: docker build only -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"docker build -t app:dev ."}}' 'allow'
Check "external-effect: gh release create -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"gh release create v1.2.0 --notes-file notes.md"}}' 'ask'
Check "external-effect: gh run list -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"gh run list --limit 5"}}' 'allow'
Check "external-effect: gh api -X POST -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"gh api -X POST /repos/o/r/issues -f title=x"}}' 'ask'
Check "external-effect: gh api GET -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"gh api /repos/o/r/actions/runs"}}' 'allow'
Check "external-effect: ssh remote command -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"ssh deploy@prod.example.com \"sudo systemctl restart app\""}}' 'ask'
Check "external-effect: ssh -T git@github.com (auth probe) -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"ssh -T git@github.com"}}' 'allow'
Check "external-effect: scp to remote -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"scp dist/app.tar.gz deploy@prod.example.com:/srv/app/"}}' 'ask'
Check "external-effect: production marker + migrate -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"RAILS_ENV=production bundle exec rake db:migrate"}}' 'ask'
Check "external-effect: npx vercel deploy --prod (wrapper) -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"npx vercel deploy --prod"}}' 'ask'
Check "external-effect: vercel ls -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"vercel ls"}}' 'allow'
Check "external-effect: aws s3 sync to bucket -> ask" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"aws s3 sync ./dist s3://my-bucket/ --delete"}}' 'ask'
Check "external-effect: aws s3 ls -> allow" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"aws s3 ls s3://my-bucket/"}}' 'allow'
Check "external-effect: Invoke-RestMethod -Method Post -> ask" 'guard-external-effect.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"Invoke-RestMethod -Method Post -Uri https://api.example.com/v1/x -Body $b"}}' 'ask'
Check "external-effect: git push is left to guard-dangerous-git -> allow (no double ask)" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"git push origin main"}}' 'allow'
Check "external-effect: MCP write tool name (create_issue) -> ask" 'guard-external-effect.ps1' '{"tool_name":"mcp__github__create_issue","tool_input":{"title":"x"}}' 'ask'
Check "external-effect: MCP read tool name (get_issue) -> allow" 'guard-external-effect.ps1' '{"tool_name":"mcp__github__get_issue","tool_input":{"number":1}}' 'allow'
Check "external-effect: read-only tool name with command -> allow (CP-1)" 'guard-external-effect.ps1' '{"tool_name":"readFile","tool_input":{"command":"terraform apply"}}' 'allow'
Check "external-effect: permission_mode bypassPermissions + terraform apply -> deny" 'guard-external-effect.ps1' '{"tool_name":"Bash","permission_mode":"bypassPermissions","tool_input":{"command":"terraform apply"}}' 'deny'
$env:HARNESS_EXTERNAL_EFFECT_MODE = 'deny'
Check "external-effect: HARNESS_EXTERNAL_EFFECT_MODE=deny + npm publish -> deny" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"npm publish"}}' 'deny'
Remove-Item Env:HARNESS_EXTERNAL_EFFECT_MODE -ErrorAction SilentlyContinue
Check "external-effect: broken JSON with terraform apply -> ask (grep fallback stays on the protected side)" 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"terraform apply"' 'ask'
Check "external-effect: CI fixed payload (git status) -> allow" 'guard-external-effect.ps1' '{"tool_input":{"command":"git status","file_path":"src/app.ts","content":"hello"}}' 'allow'
# セグメント数上限(48)の fail-closed: 49 セグメント以上は評価前に ask、48 以下の無害コマンドは allow(SC-5 と同じ流儀)
$segs49 = ('echo x; ' * 49)
Check "external-effect: 49 harmless segments -> ask (segment cap, fail-closed)" 'guard-external-effect.ps1' ('{"tool_name":"Bash","tool_input":{"command":"' + $segs49 + '"}}') 'ask'
$segs48 = ('echo x; ' * 48)
Check "external-effect: 48 harmless segments -> allow (within cap)" 'guard-external-effect.ps1' ('{"tool_name":"Bash","tool_input":{"command":"' + $segs48 + '"}}') 'allow'
# 理由文に「何を・どこへ」(exact-action の承認単位)が入ること
$out = Invoke-Hook 'guard-external-effect.ps1' '{"tool_name":"Bash","tool_input":{"command":"curl -X PUT https://api.example.com/v1/config -d @c.json"}}'
if (($out -match '何を: curl') -and ($out -match 'どこへ: host api\.example\.com')) {
  $script:pass++; Write-Output "PASS: external-effect: ask reason names what (curl) and where (host api.example.com)"
} else {
  $script:fail++; Write-Output "FAIL: external-effect: ask reason names what (curl) and where (host api.example.com)"; Write-Output "  got: $out"
}
# --- guard-dangerous-git.ps1: 承認バイパス下は deny、通常は理由文に「何を」を含む ask(codex C-01) ---
Check "dangerous-git: permission_mode bypassPermissions + git push -> deny" 'guard-dangerous-git.ps1' '{"tool_name":"Bash","permission_mode":"bypassPermissions","tool_input":{"command":"git push origin main"}}' 'deny'
$env:HARNESS_EXTERNAL_EFFECT_MODE = 'deny'
Check "dangerous-git: HARNESS_EXTERNAL_EFFECT_MODE=deny + git tag -> deny" 'guard-dangerous-git.ps1' '{"tool_name":"Bash","tool_input":{"command":"git tag -a v1.2.0 -m v1.2.0"}}' 'deny'
Remove-Item Env:HARNESS_EXTERNAL_EFFECT_MODE -ErrorAction SilentlyContinue
$out = Invoke-Hook 'guard-dangerous-git.ps1' '{"tool_name":"Bash","tool_input":{"command":"git push origin v1.2.0"}}'
if (($out -match '"permissionDecision":\s*"ask"') -and ($out -match '何を: git push origin v1\.2\.0')) {
  $script:pass++; Write-Output "PASS: dangerous-git: ask reason names the exact command (exact-action)"
} else {
  $script:fail++; Write-Output "FAIL: dangerous-git: ask reason names the exact command (exact-action)"; Write-Output "  got: $out"
}
# --- guard-done-evidence.ps1(A2-4b / RG-13): done 遷移・[x] 追加の書込は、同じ書込の新内容に証拠 3 点セットが
#     無ければ deny。判定は書込の差分に対して行う(.sh と同一ケース) ---
$fx30 = Join-Path $env:TEMP ("hooktest30-" + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Force (Join-Path $fx30 'docs\03-implementation') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $fx30 'docs\00-overview') | Out-Null
[IO.File]::WriteAllText((Join-Path $fx30 'docs\03-implementation\tasks.md'), "- [ ] TASK-001: ログイン画面`n- [x] TASK-000: 雛形`n  - 証拠: ``npm test`` → 3 passed (2026-09-10 09:00)`n", $utf8nb)
[IO.File]::WriteAllText((Join-Path $fx30 'docs\00-overview\progress.md'), "# 進捗`n`n<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: in_progress`ntest: not_started`nrelease: not_started`n-->`n", $utf8nb)
Check "done-evidence: tasks.md [ ]->[x] without evidence -> deny" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"docs/03-implementation/tasks.md","old_string":"- [ ] TASK-001: ログイン画面","new_string":"- [x] TASK-001: ログイン画面"}}' 'deny' $fx30
Check "done-evidence: Copilot CLI Edit(path/old_str/new_str) [ ]->[x] without evidence -> deny (2026-09-21 実測の項目名)" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"path":"docs/03-implementation/tasks.md","old_str":"- [ ] TASK-001: ログイン画面","new_str":"- [x] TASK-001: ログイン画面"}}' 'deny' $fx30
Check "done-evidence: Copilot CLI Write(path/file_text) adds [x] without evidence -> deny" 'guard-done-evidence.ps1' '{"tool_name":"Write","tool_input":{"path":"docs/03-implementation/tasks.md","file_text":"- [x] TASK-001: ログイン画面\n- [x] TASK-000: 雛形\n  - 証拠: `npm test` → 3 passed (2026-09-10 09:00)\n"}}' 'deny' $fx30
Check "done-evidence: tasks.md [x] with command/output/timestamp -> allow" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"docs/03-implementation/tasks.md","old_string":"- [ ] TASK-001: ログイン画面","new_string":"- [x] TASK-001: ログイン画面\n  - 証拠: `npm test` → 28 passed (2026-09-10 14:02)"}}' 'allow' $fx30
Check "done-evidence: tasks.md [x] with label-form evidence (コマンド: / 結果:) -> allow" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"docs/03-implementation/tasks.md","old_string":"- [ ] TASK-001: ログイン画面","new_string":"- [x] TASK-001: ログイン画面\n  - コマンド: pytest tests/ / 結果: 12 passed / 2026-09-10 14:02"}}' 'allow' $fx30
Check "done-evidence: tasks.md [x] with evidence but no timestamp -> deny" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"docs/03-implementation/tasks.md","old_string":"- [ ] TASK-001: ログイン画面","new_string":"- [x] TASK-001: ログイン画面\n  - 証拠: `npm test` → 28 passed"}}' 'deny' $fx30
Check "done-evidence: progress.md implementation in_progress->done without evidence -> deny" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"docs/00-overview/progress.md","old_string":"implementation: in_progress","new_string":"implementation: done"}}' 'deny' $fx30
Check "done-evidence: progress.md done with evidence in the same write -> allow" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"docs/00-overview/progress.md","old_string":"implementation: in_progress","new_string":"implementation: done\n備考: 承認 user / 証拠: `npm test` → 28 passed (2026-09-10 14:02)"}}' 'allow' $fx30
Check "done-evidence: progress.md in_progress->pending_approval (no done) -> allow" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"docs/00-overview/progress.md","old_string":"implementation: in_progress","new_string":"implementation: pending_approval"}}' 'allow' $fx30
Check "done-evidence: Write tasks.md adding [x] vs disk without evidence -> deny" 'guard-done-evidence.ps1' '{"tool_name":"Write","tool_input":{"file_path":"docs/03-implementation/tasks.md","content":"- [x] TASK-001: ログイン画面\n- [x] TASK-000: 雛形\n  - 証拠: `npm test` → 3 passed (2026-09-10 09:00)\n"}}' 'deny' $fx30
Check "done-evidence: Write tasks.md same [x] count (annotation only) -> allow" 'guard-done-evidence.ps1' '{"tool_name":"Write","tool_input":{"file_path":"docs/03-implementation/tasks.md","content":"- [ ] TASK-001: ログイン画面 (着手)\n- [x] TASK-000: 雛形\n  - 証拠: `npm test` → 3 passed (2026-09-10 09:00)\n"}}' 'allow' $fx30
Check "done-evidence: Write tasks.md when no file on disk (all lines new) without evidence -> deny" 'guard-done-evidence.ps1' '{"tool_name":"Write","tool_input":{"file_path":"docs/03-implementation/tasks.md","content":"- [x] TASK-001: ログイン画面\n"}}' 'deny'
Check "done-evidence: MultiEdit edits[] adding [x] without evidence -> deny" 'guard-done-evidence.ps1' '{"tool_name":"MultiEdit","tool_input":{"file_path":"docs/03-implementation/tasks.md","edits":[{"old_string":"- [ ] TASK-001: ログイン画面","new_string":"- [x] TASK-001: ログイン画面"}]}}' 'deny' $fx30
Check "done-evidence: Windows backslash path to tasks.md -> deny" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"d:\\proj\\docs\\03-implementation\\tasks.md","old_string":"- [ ] TASK-001","new_string":"- [x] TASK-001"}}' 'deny'
Check "done-evidence: [x] in an unrelated doc -> allow" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"docs/notes.md","old_string":"- [ ] memo","new_string":"- [x] memo"}}' 'allow'
Check "done-evidence: read-only tool (readFile) with [x] content -> allow (CP-1)" 'guard-done-evidence.ps1' '{"tool_name":"readFile","tool_input":{"file_path":"docs/03-implementation/tasks.md","content":"- [x] TASK-001"}}' 'allow' $fx30
Check "done-evidence: broken JSON -> allow (fail-open)" 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"docs/03-implementation/tasks.md","new_string":"- [x] q"' 'allow'
Check "done-evidence: CI fixed payload (src/app.ts) -> allow" 'guard-done-evidence.ps1' '{"tool_input":{"command":"git status","file_path":"src/app.ts","content":"hello"}}' 'allow'
$out = Invoke-Hook 'guard-done-evidence.ps1' '{"tool_name":"Edit","tool_input":{"file_path":"docs/03-implementation/tasks.md","old_string":"- [ ] TASK-001: ログイン画面","new_string":"- [x] TASK-001: ログイン画面\n  - `npm test` → 28 passed"}}' $fx30
if (($out -match '不足: 実行日時') -and ($out -notmatch '再実行可能なコマンド\(')) {
  $script:pass++; Write-Output "PASS: done-evidence: deny reason lists only the missing item (timestamp)"
} else {
  $script:fail++; Write-Output "FAIL: done-evidence: deny reason lists only the missing item (timestamp)"; Write-Output "  got: $out"
}
Remove-Item -Recurse -Force $fx30
# --- R-02(codex §4): 壊れた JSON でも保護対象パスを含む書込は regex フォールバックで保護側(deny/ask)に落ちる。
#     path 系フィールド名が未知(fileName 等)の場合は候補が取れず allow(既知の限界=fail-open) ---
Check "harness-config-edit: broken JSON + .github/hooks path -> deny (R-02: parse failure stays protected)" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/hooks/x.sh","content":"x"' 'deny'
Check "harness-config-edit: broken JSON + command writing .github/hooks -> ask (R-02)" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"echo x > .github/hooks/x.sh"' 'ask'
Check "template-edit: broken JSON + *_template.md path -> deny (R-02)" 'guard-template-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":"docs/01-requirements/requirements_template.md","content":"x"' 'deny'
Check "harness-config-edit: unknown path field name (fileName) -> allow (documented fail-open limit, R-02)" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"fileName":".github/hooks/x.sh","content":"x"}}' 'allow'

# 第3波(状態機械の堅牢化。再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15 / A7-H-5。仕様は
# .github/harness/STATE-MACHINE.md): selftest.sh と同一の 16 ケース・同一の期待値(完全性・順序矛盾の warn、遷移ログ、
# 別セッション lock の warn と stale lock の無視、往復上限、注記つき done、復旧ドリル)。記録先は HARNESS_HOOK_LOG_DIR。
# =====================================================================================
$fx23 = Join-Path $env:TEMP ("hooktest23-" + [IO.Path]::GetRandomFileName())
foreach ($d in 'docs\00-overview', 'docs\01-requirements', 'logs', 'tools', '.github\hooks\scripts', '.github\harness') { New-Item -ItemType Directory -Force (Join-Path $fx23 $d) | Out-Null }
$utf823 = New-Object System.Text.UTF8Encoding $false
Copy-Item (Join-Path $scriptsDir '_log.py') (Join-Path $fx23 '.github\hooks\scripts\_log.py') -ErrorAction SilentlyContinue
Copy-Item (Join-Path $scriptsDir '..\..\harness\privacy-patterns.json') (Join-Path $fx23 '.github\harness\privacy-patterns.json') -ErrorAction SilentlyContinue
[IO.File]::WriteAllText((Join-Path $fx23 'tools\usage-config.json'), '{"implement_test_loop_max": 3, "session_lock_stale_minutes": 120}' + "`n", $utf823)
$logs23 = Join-Path $fx23 'logs'
$env:HARNESS_HOOK_LOG_DIR = $logs23
$gate23 = '{"session_id":"s-gate-A","hook_event_name":"PostToolUse","tool_name":"Edit","tool_input":{"file_path":"docs/00-overview/progress.md","new_string":"x"}}'
$prog23 = Join-Path $fx23 'docs\00-overview\progress.md'
$gl23 = Join-Path $logs23 'gate-transitions.jsonl'
$lock23 = Join-Path $logs23 'session.lock'
function Get-GateLogLines23 { if (Test-Path -LiteralPath $gl23) { return @([IO.File]::ReadAllLines($gl23, $utf823) | Where-Object { $_.Trim() }) } else { return @() } }
# (1) 完全性: キー欠落(test)と語彙外の値(implementation: dne)を progress.md 書込後に warn-stale-gate が警告する
[IO.File]::WriteAllText($prog23, "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: dne`nrelease: not_started`n-->`n", $utf823)
$out = Invoke-Hook 'warn-stale-gate.ps1' $gate23 $fx23
if (($out -match [regex]::Escape('キー欠落: test')) -and ($out -match 'implementation=dne')) {
  $script:pass++; Write-Output "PASS: stale-gate: progress.md with missing key + out-of-vocabulary value -> integrity warn"
} else {
  $script:fail++; Write-Output "FAIL: stale-gate: progress.md with missing key + out-of-vocabulary value -> integrity warn"; Write-Output "  got: $out"
}
# (2) 順序矛盾(規則 B): test done なのに implementation in_progress(注記なし) → warn
[IO.File]::WriteAllText($prog23, "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: in_progress`ntest: done`nrelease: not_started`n-->`n", $utf823)
$out = Invoke-Hook 'warn-stale-gate.ps1' $gate23 $fx23
if (($out -match [regex]::Escape('規則 B')) -and ($out -match [regex]::Escape('test: done なのに先行の implementation'))) {
  $script:pass++; Write-Output "PASS: stale-gate: test done while implementation in_progress -> order contradiction warn (rule B)"
} else {
  $script:fail++; Write-Output "FAIL: stale-gate: test done while implementation in_progress -> order contradiction warn (rule B)"; Write-Output "  got: $out"
}
# (3) 同じ状態でも「状態: 運用中」の注記(改修サイクル)があれば規則 B は免除 → allow
[IO.File]::WriteAllText($prog23, "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: in_progress`ntest: done`nrelease: done`n-->`n`n状態: 運用中（改修サイクル）`n", $utf823)
Check "stale-gate: same state with operating note -> allow (rule B exempt)" 'warn-stale-gate.ps1' $gate23 'allow' $fx23
# (4) 規則 A は注記があっても warn(design in_progress なのに requirements not_started)
[IO.File]::WriteAllText($prog23, "<!-- GATE_STATUS`nrequirements: not_started`ndesign: in_progress`nimplementation: not_started`ntest: not_started`nrelease: not_started`n-->`n`n状態: 運用中`n", $utf823)
$out = Invoke-Hook 'warn-stale-gate.ps1' $gate23 $fx23
if ($out -match [regex]::Escape('規則 A')) {
  $script:pass++; Write-Output "PASS: stale-gate: design in_progress while requirements not_started -> rule A warn even with note"
} else {
  $script:fail++; Write-Output "FAIL: stale-gate: design in_progress while requirements not_started -> rule A warn even with note"; Write-Output "  got: $out"
}
# (5) 正常な進行中状態(注記つきの値・GATE_COUNTERS 0)は allow
[IO.File]::WriteAllText($prog23, "<!-- GATE_STATUS`nrequirements: done 2026-08-01`ndesign: done`nimplementation: in_progress (CR-003)`ntest: not_started`nrelease: not_started`n-->`n<!-- GATE_COUNTERS`nimplement_test_loops: 0`n-->`n", $utf823)
Check "stale-gate: complete state with value notes + counters -> allow" 'warn-stale-gate.ps1' $gate23 'allow' $fx23
# (6) 注記つき done(「done 2026-08-01」)の承認済み文書編集で warn(値は最初のトークン。sh と対称)
Check "stale-gate: done doc when status has trailing note (done 2026-08-01) -> warn" 'warn-stale-gate.ps1' '{"tool_input":{"file_path":"docs/01-requirements/requirements.md"}}' 'warn' $fx23
# (7) 遷移ログ: progress.md 書込後の warn-gate-tamper が logs/gate-transitions.jsonl に rev 1・before null の 1 行を書く
$out = Invoke-Hook 'warn-gate-tamper.ps1' $gate23 $fx23
$gll = @(Get-GateLogLines23)
if (($out -notmatch '"systemMessage"') -and ($gll.Count -eq 1) -and ($gll[0] -match '"rev":1,"session_id":"s-gate-A"') -and ($gll[0] -match '"before":null') -and ($gll[0] -match '"implementation":"in_progress"') -and ($gll[0] -match '"loops":0')) {
  $script:pass++; Write-Output "PASS: gate-tamper: progress.md write -> 1 transition record (rev 1, before null, session_id, loops)"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: progress.md write -> 1 transition record (rev 1, before null, session_id, loops)"; Write-Output "  got: $out"; Write-Output "  log: $($gll -join ' | ')"
}
# (8) 状態が変わった 2 回目は rev 2・changed に差分だけ、同じ状態の 3 回目は追記しない
[IO.File]::WriteAllText($prog23, "<!-- GATE_STATUS`nrequirements: done 2026-08-01`ndesign: done`nimplementation: pending_approval`ntest: not_started`nrelease: not_started`n-->`n<!-- GATE_COUNTERS`nimplement_test_loops: 0`n-->`n", $utf823)
$out = Invoke-Hook 'warn-gate-tamper.ps1' $gate23 $fx23
$out2 = Invoke-Hook 'warn-gate-tamper.ps1' $gate23 $fx23
$gll = @(Get-GateLogLines23)
if (($gll.Count -eq 2) -and ($gll[1] -match '"rev":2') -and ($gll[1] -match '"changed":"implementation:in_progress->pending_approval"')) {
  $script:pass++; Write-Output "PASS: gate-tamper: changed state -> rev 2 with diff only; unchanged state -> no new record"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: changed state -> rev 2 with diff only; unchanged state -> no new record"; Write-Output "  log: $($gll -join ' | ')"
}
# (9) 別セッションの新しい lock がある状態で progress.md を書くと同時更新の警告(block しない)
$now23 = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
[IO.File]::WriteAllText($lock23, '{"session_id":"s-gate-B","pid":1,"ts":"x","epoch":' + $now23 + ',"cwd":"x"}' + "`n", $utf823)
$out = Invoke-Hook 'warn-gate-tamper.ps1' $gate23 $fx23
if (($out -match [regex]::Escape('別のセッション(s-gate-B')) -and ($out -match '"continue":\s*true')) {
  $script:pass++; Write-Output "PASS: gate-tamper: other session holds a fresh lock -> concurrent-update warn (non-blocking)"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: other session holds a fresh lock -> concurrent-update warn (non-blocking)"; Write-Output "  got: $out"
}
# (10) stale な lock(閾値 1 分・2 分前)は無視される
[IO.File]::WriteAllText($lock23, '{"session_id":"s-gate-B","pid":1,"ts":"x","epoch":' + ($now23 - 120) + ',"cwd":"x"}' + "`n", $utf823)
$env:HARNESS_SESSION_LOCK_STALE_MIN = '1'
$out = Invoke-Hook 'warn-gate-tamper.ps1' $gate23 $fx23
Remove-Item Env:HARNESS_SESSION_LOCK_STALE_MIN -ErrorAction SilentlyContinue
if ($out -and ($out -notmatch [regex]::Escape('別のセッション'))) {
  $script:pass++; Write-Output "PASS: gate-tamper: stale lock (older than threshold) -> ignored, no warn"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: stale lock (older than threshold) -> ignored, no warn"; Write-Output "  got: $out"
}
# (11) inject-progress(SessionStart): 別セッションの新しい lock → 警告を注入し、lock は上書きしない
[IO.File]::WriteAllText($lock23, '{"session_id":"s-gate-B","pid":1,"ts":"x","epoch":' + $now23 + ',"cwd":"x"}' + "`n", $utf823)
$out = Invoke-Hook 'inject-progress.ps1' '{"session_id":"s-gate-A","hook_event_name":"SessionStart","source":"startup"}' $fx23
if (($out -match [regex]::Escape('別のセッション(s-gate-B')) -and ([IO.File]::ReadAllText($lock23, $utf823) -match '"session_id":"s-gate-B"')) {
  $script:pass++; Write-Output "PASS: inject-progress: fresh lock of another session -> warning injected, lock not taken over"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress: fresh lock of another session -> warning injected, lock not taken over"; Write-Output "  got: $out"
}
# (12) inject-progress(SessionStart): stale な lock は無視して自セッションの lock を置く(session_id・epoch)
[IO.File]::WriteAllText($lock23, '{"session_id":"s-gate-B","pid":1,"ts":"x","epoch":' + ($now23 - 120) + ',"cwd":"x"}' + "`n", $utf823)
$env:HARNESS_SESSION_LOCK_STALE_MIN = '1'
$out = Invoke-Hook 'inject-progress.ps1' '{"session_id":"s-gate-A","hook_event_name":"SessionStart","source":"startup"}' $fx23
Remove-Item Env:HARNESS_SESSION_LOCK_STALE_MIN -ErrorAction SilentlyContinue
$lockText = [IO.File]::ReadAllText($lock23, $utf823)
if ($out -and ($out -notmatch [regex]::Escape('別のセッション')) -and ($lockText -match '"session_id":"s-gate-A"') -and ($lockText -match '"epoch":\d+')) {
  $script:pass++; Write-Output "PASS: inject-progress: stale lock -> ignored and replaced by own session lock"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress: stale lock -> ignored and replaced by own session lock"; Write-Output "  got: $out"; Write-Output "  lock: $lockText"
}
# (13) inject-progress(SessionStart): フック外で書き換わった GATE_STATUS を遷移ログの最終記録と突合し source=session-start で 1 行追記
[IO.File]::WriteAllText($prog23, "<!-- GATE_STATUS`nrequirements: done 2026-08-01`ndesign: done`nimplementation: done`ntest: in_progress`nrelease: not_started`n-->`n<!-- GATE_COUNTERS`nimplement_test_loops: 1`n-->`n", $utf823)
$out = Invoke-Hook 'inject-progress.ps1' '{"session_id":"s-gate-A","hook_event_name":"SessionStart","source":"startup"}' $fx23
$gll = @(Get-GateLogLines23)
if ($out -and ($gll.Count -eq 3) -and ($gll[2] -match '"source":"session-start"') -and ($gll[2] -match '"changed":"implementation:pending_approval->done,test:not_started->in_progress"') -and ($gll[2] -match '"loops":1')) {
  $script:pass++; Write-Output "PASS: inject-progress: out-of-band GATE change -> reconciled as a session-start transition record"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress: out-of-band GATE change -> reconciled as a session-start transition record"; Write-Output "  log: $($gll -join ' | ')"
}
# (14) 往復上限超(implement_test_loops 4 > 3)の progress.md 書込で warn-gate-tamper が上限警告を出す
[IO.File]::WriteAllText($prog23, "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementation: in_progress`ntest: in_progress`nrelease: not_started`n-->`n<!-- GATE_COUNTERS`nimplement_test_loops: 4`n-->`n", $utf823)
$out = Invoke-Hook 'warn-gate-tamper.ps1' $gate23 $fx23
if ($out -match [regex]::Escape('往復が 4 回で上限 3')) {
  $script:pass++; Write-Output "PASS: gate-tamper: implement_test_loops 4 > max 3 -> loop-cap warn (/13-converge or human)"
} else {
  $script:fail++; Write-Output "FAIL: gate-tamper: implement_test_loops 4 > max 3 -> loop-cap warn (/13-converge or human)"; Write-Output "  got: $out"
}
# (15) 復旧ドリル(STATE-MACHINE.md「復旧手順」): 壊れた GATE_STATUS → gate_status.py check が検出(exit 1) → recover --from log → check が通る
$py23 = $null
foreach ($cand in 'python', 'python3') {
  $c23 = Get-Command $cand -ErrorAction SilentlyContinue
  if ($c23) { try { & $c23.Source --version *> $null; if ($LASTEXITCODE -eq 0) { $py23 = $c23.Source; break } } catch {} }
}
$gs23 = Join-Path $scriptsDir '..\..\..\tools\gate_status.py'
if ($py23 -and (Test-Path -LiteralPath $gs23)) {
  & $py23 $gs23 --root $fx23 reconcile *> $null
  [IO.File]::WriteAllText($prog23, "<!-- GATE_STATUS`nrequirements: done`ndesign: done`nimplementaton: nto_started`ntest: in_progress`nrelease: not_started`n-->`n", $utf823)
  & $py23 $gs23 --root $fx23 check *> $null; $rc1 = $LASTEXITCODE
  & $py23 $gs23 --root $fx23 recover --from log *> $null; $rc2 = $LASTEXITCODE
  & $py23 $gs23 --root $fx23 check *> $null; $rc3 = $LASTEXITCODE
  $progText = [IO.File]::ReadAllText($prog23, $utf823)
  if ($rc1 -eq 1 -and $rc2 -eq 0 -and $rc3 -eq 0 -and ($progText -match '(?m)^implementation: in_progress') -and ($progText -notmatch 'implementaton')) {
    $script:pass++; Write-Output "PASS: recovery drill: broken GATE_STATUS -> check detects (exit 1) -> recover --from log -> check passes (exit 0)"
  } else {
    $script:fail++; Write-Output "FAIL: recovery drill: broken GATE_STATUS -> check detects (exit 1) -> recover --from log -> check passes (exit 0)"; Write-Output "  rc: $rc1 $rc2 $rc3"; Write-Output $progText
  }
  & $py23 $gs23 --selftest *> $null
  if ($LASTEXITCODE -eq 0) {
    $script:pass++; Write-Output "PASS: gate_status.py --selftest (completeness, order rules, counters, atomic set, recover, reconcile, unlock)"
  } else {
    $script:fail++; Write-Output "FAIL: gate_status.py --selftest (completeness, order rules, counters, atomic set, recover, reconcile, unlock)"
  }
} else {
  Write-Output "SKIP: recovery drill / gate_status.py --selftest (python not found)"
}
Remove-Item Env:HARNESS_HOOK_LOG_DIR -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $fx23 -ErrorAction SilentlyContinue

# =====================================================================================
# 第7波 w3-privacy(2026-09-17): プライバシー境界と供給網・書込経路の残り(codex 監査 2026-08-31 H-01 / IA-20260831-03、
# H-09 / IA-20260831-11、§4 R-01、再監査 2026-08-31 A7-G-3 / 2026-09-09 SC-2)。selftest.sh と同一ケース・同一の期待値。
# =====================================================================================
$utf8x = New-Object System.Text.UTF8Encoding $false
$py23 = $null
foreach ($cand in 'python', 'python3') {
  $c23 = Get-Command $cand -ErrorAction SilentlyContinue
  if ($c23) { try { & $c23.Source --version *> $null; if ($LASTEXITCODE -eq 0) { $py23 = $c23.Source; break } } catch {} }
}
function Invoke-PyHook23($scriptPath, $payload, $workDir, $logDir) {
  # stdin は BOM なし UTF-8 のバイト列を直接書く。記録先は HARNESS_HOOK_LOG_DIR で一時ディレクトリに向ける
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $py23
  $psi.Arguments = '"' + $scriptPath + '"'
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding $false
  if ($logDir) { $psi.EnvironmentVariables['HARNESS_HOOK_LOG_DIR'] = $logDir }
  if ($workDir) { $psi.WorkingDirectory = $workDir } else { $psi.WorkingDirectory = (Get-Location).Path }
  $proc = [System.Diagnostics.Process]::Start($psi)
  if ($payload) { $bytes = $utf8x.GetBytes($payload); $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length) }
  $proc.StandardInput.Close()
  $o = $proc.StandardOutput.ReadToEnd()
  $proc.StandardError.ReadToEnd() | Out-Null
  $proc.WaitForExit()
  return @{ out = $o; rc = $proc.ExitCode }
}
# --- (1) draft-learnings(IA-03): 候補は logs/ のローカル下書きに redaction 済みで書き、docs/ には書かない ---
if ($py23) {
  $fx23 = Join-Path $env:TEMP ("hooktest23-" + [IO.Path]::GetRandomFileName())
  foreach ($d in 'proj\docs\00-overview', 'logs') { New-Item -ItemType Directory -Force (Join-Path $fx23 $d) | Out-Null }
  $p23 = $fx23 -replace '\\', '/'
  [IO.File]::WriteAllText((Join-Path $fx23 'proj\docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`n-->`n", $utf8x)
  [IO.File]::WriteAllText((Join-Path $fx23 'proj\docs\00-overview\learnings.md'), "## 教訓`n- [2026-01-01] 既存の教訓`n", $utf8x)
  [IO.File]::WriteAllText((Join-Path $fx23 't.jsonl'), ('{"type":"user","message":{"content":[{"type":"text","text":"違う、鍵は sk-ant-api03-CANARYCANARYCANARY0123456789abcdef で URL は https://alice:hunter2-canary@example.com/repo.git にして"}]}}' + "`n" + '{"type":"assistant","message":{"content":[{"type":"text","text":"承知しました。修正します"}]}}' + "`n"), $utf8x)
  $r = Invoke-PyHook23 (Join-Path $scriptsDir 'draft-learnings.py') ('{"session_id":"selftest-dl","hook_event_name":"Stop","transcript_path":"' + $p23 + '/t.jsonl","cwd":"' + $p23 + '/proj"}') $null (Join-Path $fx23 'logs')
  $draft23 = Join-Path $fx23 'logs\learnings-draft.local.md'
  $dt23 = ''
  if (Test-Path -LiteralPath $draft23) { $dt23 = [IO.File]::ReadAllText($draft23, $utf8x) }
  if ($r.rc -eq 0 -and (-not $r.out.Trim()) -and $dt23 -and ($dt23 -match 'REDACTED') -and ($dt23 -notmatch 'hunter2-canary') -and ($dt23 -notmatch 'CANARYCANARY') -and ($dt23 -match 'session=selftest')) {
    $script:pass++; Write-Output "PASS: draft-learnings: canary correction -> local draft under logs/ with secrets redacted, no output"
  } else {
    $script:fail++; Write-Output "FAIL: draft-learnings: canary correction -> local draft under logs/ with secrets redacted, no output"; Write-Output "  rc=$($r.rc) out=$($r.out) draft=$dt23"
  }
  $learn23 = [IO.File]::ReadAllText((Join-Path $fx23 'proj\docs\00-overview\learnings.md'), $utf8x)
  if ((-not (Test-Path -LiteralPath (Join-Path $fx23 'proj\docs\00-overview\learnings-pending.md'))) -and ($learn23 -eq "## 教訓`n- [2026-01-01] 既存の教訓`n")) {
    $script:pass++; Write-Output "PASS: draft-learnings: docs/00-overview/learnings-pending.md is not written and learnings.md is unchanged"
  } else {
    $script:fail++; Write-Output "FAIL: draft-learnings: docs/00-overview/learnings-pending.md is not written and learnings.md is unchanged"
  }
  Remove-Item -Recurse -Force $fx23 -ErrorAction SilentlyContinue
  & $py23 (Join-Path $scriptsDir '..\..\..\tools\external_lock.py') --selftest *> $null
  if ($LASTEXITCODE -eq 0) {
    $script:pass++; Write-Output "PASS: external_lock.py --selftest (lock schema, floating/unlocked/mismatched references, local digest)"
  } else {
    $script:fail++; Write-Output "FAIL: external_lock.py --selftest (lock schema, floating/unlocked/mismatched references, local digest)"
  }
} else {
  Write-Output "SKIP: draft-learnings local draft / external_lock cases (python not found)"
}

# --- (2) R-01: reparse point の作成と git plumbing / worktree による保護域書換は ask、読み取り・保護域外は allow ---
Check "harness-config-edit: ln -s /tmp/x /tmp/y (reparse point, no protected path) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"ln -s /tmp/x /tmp/y"}}' 'ask'
Check "harness-config-edit: mklink /D alias -> .github (reparse point) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"mklink /D C:\\alias D:\\proj\\.github"}}' 'ask'
Check "harness-config-edit: New-Item -ItemType SymbolicLink (args reordered) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"New-Item -Path C:\\alias -ItemType SymbolicLink -Value D:\\proj"}}' 'ask'
Check "harness-config-edit: [IO.File]::CreateSymbolicLink -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"[IO.File]::CreateSymbolicLink(''C:\\x'', ''D:\\proj\\AGENTS.md'')"}}' 'ask'
Check "harness-config-edit: git worktree add inside .github/hooks -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git worktree add .github/hooks/wt feature"}}' 'ask'
Check "harness-config-edit: git update-index --cacheinfo AGENTS.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git update-index --add --cacheinfo 100644,abc,AGENTS.md"}}' 'ask'
Check "harness-config-edit: git restore .github/hooks/scripts/x.sh -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git restore .github/hooks/scripts/guard-x.sh"}}' 'ask'
Check "harness-config-edit: git hash-object -w AGENTS.md -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git hash-object -w AGENTS.md"}}' 'ask'
Check "harness-config-edit: echo >> external-lock.json (IA-11 lock protected) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"echo x >> .github/harness/external-lock.json"}}' 'ask'
Check "harness-config-edit: Write external-lock.json -> deny" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/harness/external-lock.json","content":"{}"}}' 'deny'
Check "harness-config-edit: git worktree add ../wt (outside protected) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git worktree add ../wt feature"}}' 'allow'
Check "harness-config-edit: git worktree list -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git worktree list"}}' 'allow'
Check "harness-config-edit: git switch -c feat -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"git switch -c feat"}}' 'allow'
Check "harness-config-edit: New-Item -ItemType Directory src/new (no link) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"PowerShell","tool_input":{"command":"New-Item -ItemType Directory -Path src\\new"}}' 'allow'
Check "harness-config-edit: cat external-lock.json (read) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Bash","tool_input":{"command":"cat .github/harness/external-lock.json"}}' 'allow'
Check "dangerous-git: git checkout -- . (discard-all) -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git checkout -- ."}}' 'ask'
Check "dangerous-git: git checkout . -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git checkout ."}}' 'ask'
Check "dangerous-git: git restore . -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git restore ."}}' 'ask'
Check "dangerous-git: git restore :/ -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git restore :/"}}' 'ask'
Check "dangerous-git: git restore --worktree --staged . -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git restore --worktree --staged ."}}' 'ask'
Check "dangerous-git: git checkout HEAD -- . -> ask" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git checkout HEAD -- ."}}' 'ask'
Check "dangerous-git: git checkout -- src/app.ts (single path) -> allow" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git checkout -- src/app.ts"}}' 'allow'
Check "dangerous-git: git restore --staged src/app.ts -> allow" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git restore --staged src/app.ts"}}' 'allow'
Check "dangerous-git: git checkout -- .gitignore (dot-file, not dot) -> allow" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git checkout -- .gitignore"}}' 'allow'
Check "dangerous-git: git restore .env -> allow" 'guard-dangerous-git.ps1' '{"tool_input":{"command":"git restore .env"}}' 'allow'

# --- (3) A7-G-3 / SC-2: skills の権限系 frontmatter は「拡張だけ ask、縮小・同値・既定値の明示は allow」(フィクスチャの cwd で実行) ---
$fx23 = Join-Path $env:TEMP ("hooktest23b-" + [IO.Path]::GetRandomFileName())
foreach ($d in '.github\skills\granted', '.github\skills\plain') { New-Item -ItemType Directory -Force (Join-Path $fx23 $d) | Out-Null }
[IO.File]::WriteAllText((Join-Path $fx23 '.github\skills\granted\SKILL.md'), "---`nname: granted`ndescription: g`nallowed-tools: Read Grep`ndisable-model-invocation: true`ndisallowed-tools: Bash`n---`nbody`n", $utf8x)
[IO.File]::WriteAllText((Join-Path $fx23 '.github\skills\plain\SKILL.md'), "---`nname: plain`ndescription: p`n---`nbody`n", $utf8x)
Check "skill-frontmatter: Write new skill with allowed-tools -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/skills/newskill/SKILL.md","content":"---\nname: newskill\ndescription: x\nallowed-tools: Bash(*)\n---\nrun\n"}}' 'ask' $fx23
Check "skill-frontmatter: Write granted allowed-tools grows (Bash added) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/skills/granted/SKILL.md","content":"---\nname: granted\ndescription: g\nallowed-tools: Read Grep Bash(git:*)\ndisable-model-invocation: true\ndisallowed-tools: Bash\n---\nbody\n"}}' 'ask' $fx23
Check "skill-frontmatter: Write granted disable-model-invocation true -> false -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/skills/granted/SKILL.md","content":"---\nname: granted\ndescription: g\nallowed-tools: Read Grep\ndisable-model-invocation: false\ndisallowed-tools: Bash\n---\nbody\n"}}' 'ask' $fx23
Check "skill-frontmatter: Write granted disallowed-tools removed -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/skills/granted/SKILL.md","content":"---\nname: granted\ndescription: g\nallowed-tools: Read Grep\ndisable-model-invocation: true\n---\nbody\n"}}' 'ask' $fx23
Check "skill-frontmatter: Edit granted new_string adds a tool -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Edit","tool_input":{"file_path":".github/skills/granted/SKILL.md","old_string":"allowed-tools: Read Grep","new_string":"allowed-tools: Read Grep Write"}}' 'ask' $fx23
Check "skill-frontmatter: Edit granted removes disallowed-tools line (empty new_string) -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Edit","tool_input":{"file_path":".github/skills/granted/SKILL.md","old_string":"disallowed-tools: Bash\n","new_string":""}}' 'ask' $fx23
Check "skill-frontmatter: Edit granted adds hooks: block -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Edit","tool_input":{"file_path":".github/skills/granted/SKILL.md","old_string":"description: g","new_string":"description: g\nhooks:\n  PreToolUse:\n    - matcher: Bash"}}' 'ask' $fx23
Check "skill-frontmatter: Edit plain adds context: fork + agent -> ask" 'guard-harness-config-edit.ps1' '{"tool_name":"Edit","tool_input":{"file_path":".github/skills/plain/SKILL.md","old_string":"description: p","new_string":"description: p\ncontext: fork\nagent: reviewer"}}' 'ask' $fx23
Check "skill-frontmatter: Write new skill with restrictions only (dmi true / ui false / disallowed-tools) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/skills/newskill/SKILL.md","content":"---\nname: newskill\ndescription: x\ndisable-model-invocation: true\nuser-invocable: false\ndisallowed-tools: Bash\n---\nrun\n"}}' 'allow' $fx23
Check "skill-frontmatter: Write new skill with default values (dmi false / ui true) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/skills/newskill/SKILL.md","content":"---\nname: newskill\ndescription: x\ndisable-model-invocation: false\nuser-invocable: true\n---\nrun\n"}}' 'allow' $fx23
Check "skill-frontmatter: Write granted same tools as YAML list + disallowed-tools grows -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/skills/granted/SKILL.md","content":"---\nname: granted\ndescription: g\nallowed-tools:\n  - Read\n  - Grep\ndisable-model-invocation: true\ndisallowed-tools: Bash Write\n---\nbody2\n"}}' 'allow' $fx23
Check "skill-frontmatter: Write granted allowed-tools shrinks -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Write","tool_input":{"file_path":".github/skills/granted/SKILL.md","content":"---\nname: granted\ndescription: g\nallowed-tools: Read\ndisable-model-invocation: true\ndisallowed-tools: Bash\n---\nbody\n"}}' 'allow' $fx23
Check "skill-frontmatter: Edit granted body only -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Edit","tool_input":{"file_path":".github/skills/granted/SKILL.md","old_string":"body","new_string":"body2"}}' 'allow' $fx23
Check "skill-frontmatter: Edit granted same allowed-tools reordered -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"Edit","tool_input":{"file_path":".github/skills/granted/SKILL.md","old_string":"allowed-tools: Read Grep","new_string":"allowed-tools: Grep Read"}}' 'allow' $fx23
Check "skill-frontmatter: MultiEdit plain adds user-invocable: false (restriction) -> allow" 'guard-harness-config-edit.ps1' '{"tool_name":"MultiEdit","tool_input":{"file_path":".github/skills/plain/SKILL.md","edits":[{"old_string":"description: p","new_string":"description: p\nuser-invocable: false"}]}}' 'allow' $fx23
Remove-Item -Recurse -Force $fx23 -ErrorAction SilentlyContinue

# 第3波 instr2(A6-19b / A7-M-8): (1) remind-session-boundary(Stop・warn のみ。D047 の 2 行様式の機械検査)の
# 陽性 2 種・陰性・欠落欄 fail-open、(2) check-doc-chars.ps1 の ZWSP / NBSP / 双方向制御文字、(3) inject-progress.ps1 の
# 注入上限(HARNESS_INJECT_MAX_CHARS > tools/usage-config.json の inject_progress_max_chars > 8700)。
# 期待値は selftest.sh の同名ケースと同一(sh / ps1 同挙動)。
# =====================================================================================
$fx23 = Join-Path $env:TEMP ("hooktest23-" + [IO.Path]::GetRandomFileName())
foreach ($d in 'docs','inj\docs\00-overview','inj\tools','logs') { New-Item -ItemType Directory -Force (Join-Path $fx23 $d) | Out-Null }
$utf823 = New-Object System.Text.UTF8Encoding $false
$py23 = $null
foreach ($cand in 'python', 'python3') {
  $c23 = Get-Command $cand -ErrorAction SilentlyContinue
  if ($c23) { try { & $c23.Source --version *> $null; if ($LASTEXITCODE -eq 0) { $py23 = $c23.Source; break } } catch {} }
}
function Invoke-PyHook23($scriptName, $payload) {
  # stdin は BOM なし UTF-8 のバイト列を直接書く。記録先は HARNESS_HOOK_LOG_DIR で一時ディレクトリに向ける
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $py23
  $psi.Arguments = '"' + (Join-Path $scriptsDir $scriptName) + '"'
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = $utf823
  $psi.EnvironmentVariables['HARNESS_HOOK_LOG_DIR'] = (Join-Path $fx23 'logs')
  $psi.WorkingDirectory = (Get-Location).Path
  $proc = [System.Diagnostics.Process]::Start($psi)
  if ($payload) { $bytes = $utf823.GetBytes($payload); $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length) }
  $proc.StandardInput.Close()
  $o = $proc.StandardOutput.ReadToEnd()
  $proc.StandardError.ReadToEnd() | Out-Null
  $proc.WaitForExit()
  return @{ out = $o; rc = $proc.ExitCode }
}
if ($py23) {
  $env:HARNESS_HOOK_LOG_DIR = (Join-Path $fx23 'logs')
  & $py23 (Join-Path $scriptsDir 'remind-session-boundary.py') --selftest *> $null
  $sbRc = $LASTEXITCODE
  Remove-Item Env:HARNESS_HOOK_LOG_DIR -ErrorAction SilentlyContinue
  if ($sbRc -eq 0) {
    $script:pass++; Write-Output "PASS: remind-session-boundary.py --selftest"
  } else {
    $script:fail++; Write-Output "FAIL: remind-session-boundary.py --selftest"
  }
  $sbOk = 'このセッションの作業はここで完了です。\n次にやること: 新しいチャットを開き、最初に『/06-implement-task』と入力してください'
  $r = Invoke-PyHook23 'remind-session-boundary.py' ('{"session_id":"sb1","hook_event_name":"Stop","last_assistant_message":"' + $sbOk + '\n\nこのチャットで続けて lint も実行してください。"}')
  if ($r.rc -eq 0 -and ($r.out -match '"systemMessage"') -and ($r.out -match '後ろに')) {
    $script:pass++; Write-Output "PASS: session-boundary: completion + trailing same-session instruction -> systemMessage (warn only)"
  } else {
    $script:fail++; Write-Output "FAIL: session-boundary: completion + trailing same-session instruction -> systemMessage (warn only)"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  $r = Invoke-PyHook23 'remind-session-boundary.py' ('{"session_id":"sb2","hook_event_name":"Stop","last_assistant_message":"git push は承認待ちのため未実施です。\n\n' + $sbOk + '"}')
  if ($r.rc -eq 0 -and ($r.out -match '"systemMessage"') -and ($r.out -match '外部反映')) {
    $script:pass++; Write-Output "PASS: session-boundary: completion while push is pending approval -> systemMessage (warn only)"
  } else {
    $script:fail++; Write-Output "FAIL: session-boundary: completion while push is pending approval -> systemMessage (warn only)"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  $r = Invoke-PyHook23 'remind-session-boundary.py' ('{"session_id":"sb3","hook_event_name":"Stop","last_assistant_message":"設定を反映しました。\n\n' + $sbOk + '\n（理由: 設定変更は新しいチャットで読み直される）"}')
  if ($r.rc -eq 0 -and -not $r.out) {
    $script:pass++; Write-Output "PASS: session-boundary: proper 2-line ending (+ reason line) -> silent"
  } else {
    $script:fail++; Write-Output "FAIL: session-boundary: proper 2-line ending (+ reason line) -> silent"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
  $r = Invoke-PyHook23 'remind-session-boundary.py' ('{"session_id":"sb4","hook_event_name":"Stop","transcript_path":"' + (($fx23 -replace '\\', '/') + '/none.jsonl') + '"}')
  if ($r.rc -eq 0 -and -not $r.out) {
    $script:pass++; Write-Output "PASS: session-boundary: last_assistant_message missing -> silent exit 0 (fail-open)"
  } else {
    $script:fail++; Write-Output "FAIL: session-boundary: last_assistant_message missing -> silent exit 0 (fail-open)"; Write-Output "  got(rc=$($r.rc)): $($r.out)"
  }
} else {
  Write-Output "SKIP: remind-session-boundary cases (python not found)"
}
# check-doc-chars.ps1: 新 3 文字種([char] から組み立て=このスクリプトに実文字を混入させない)
[IO.File]::WriteAllText((Join-Path $fx23 'docs\zwsp.md'), ('a' + [char]0x200B + 'b'), $utf823)
[IO.File]::WriteAllText((Join-Path $fx23 'docs\nbsp.md'), ('a' + [char]0x00A0 + 'b'), $utf823)
[IO.File]::WriteAllText((Join-Path $fx23 'docs\bidi.md'), ('a' + [char]0x202E + 'b' + [char]0x2066), $utf823)
$fx23s = $fx23 -replace '\\', '/'
Check "doc-chars: ZWSP (U+200B) in docs md -> warn" 'check-doc-chars.ps1' ('{"tool_input":{"file_path":"' + $fx23s + '/docs/zwsp.md"}}') 'warn'
Check "doc-chars: NBSP (U+00A0) in docs md -> warn" 'check-doc-chars.ps1' ('{"tool_input":{"file_path":"' + $fx23s + '/docs/nbsp.md"}}') 'warn'
Check "doc-chars: bidi control (U+202E / U+2066) in docs md -> warn" 'check-doc-chars.ps1' ('{"tool_input":{"file_path":"' + $fx23s + '/docs/bidi.md"}}') 'warn'
# inject-progress.ps1: 注入上限(教訓 60 件 × 約 120 文字 = 上限超え。打ち切り通知、環境変数 > usage-config > 既定)
$sbLessons = New-Object System.Text.StringBuilder
[void]$sbLessons.Append("## 教訓`n")
for ($i = 1; $i -le 60; $i++) { [void]$sbLessons.Append("- [L$i] " + ('x' * 110) + "`n") }
[IO.File]::WriteAllText((Join-Path $fx23 'inj\docs\00-overview\learnings.md'), $sbLessons.ToString(), $utf823)
[IO.File]::WriteAllText((Join-Path $fx23 'inj\docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: in_progress`n-->`n", $utf823)
[IO.File]::WriteAllText((Join-Path $fx23 'inj\tools\usage-config.json'), '{"inject_progress_max_chars": 900}', $utf823)
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx23 'inj')
if (($out -match '注入上限 900 文字') -and ($out -notmatch 'L60\]')) {
  $script:pass++; Write-Output "PASS: inject-progress.ps1: over usage-config inject_progress_max_chars (900) -> truncated + notice, valid JSON"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress.ps1: over usage-config inject_progress_max_chars (900) -> truncated + notice, valid JSON"; Write-Output "  got: $out"
}
$env:HARNESS_INJECT_MAX_CHARS = '300'
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx23 'inj')
Remove-Item Env:HARNESS_INJECT_MAX_CHARS -ErrorAction SilentlyContinue
if (($out -match '注入上限 300 文字') -and ($out -match 'GATE_STATUS')) {
  $script:pass++; Write-Output "PASS: inject-progress.ps1: HARNESS_INJECT_MAX_CHARS=300 overrides usage-config -> notice says 300, GATE_STATUS head kept"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress.ps1: HARNESS_INJECT_MAX_CHARS=300 overrides usage-config -> notice says 300, GATE_STATUS head kept"; Write-Output "  got: $out"
}
$env:HARNESS_INJECT_MAX_CHARS = '0'
$out = Invoke-Hook 'inject-progress.ps1' '{}' (Join-Path $fx23 'inj')
Remove-Item Env:HARNESS_INJECT_MAX_CHARS -ErrorAction SilentlyContinue
if (($out -notmatch '注入上限') -and ($out -match 'L60\]')) {
  $script:pass++; Write-Output "PASS: inject-progress.ps1: HARNESS_INJECT_MAX_CHARS=0 disables the cap -> all 50 newest lessons, no notice"
} else {
  $script:fail++; Write-Output "FAIL: inject-progress.ps1: HARNESS_INJECT_MAX_CHARS=0 disables the cap -> all 50 newest lessons, no notice"; Write-Output "  got: $out"
}
Remove-Item -Recurse -Force $fx23 -ErrorAction SilentlyContinue

# =====================================================================================
# 第7波(2026-09-17: R-09 / A2-7 / A2-1)の回帰テスト。selftest.sh と同一ケース(判定ログの duration_ms / host 欄は上の
# canary ケースで 10 欄に更新)。camelCase ペイロード(Copilot)の host 判定、hook-metrics.py の自己テストと 1 行要約、
# watchdog-continue の事故的停止記録(watchdog-stop-<sid>.json)、golden-eval の「事故的停止で終わったセッションの done 宣言は
# 達成扱いにしない」。記録先は一時ディレクトリ。
# =====================================================================================
$fx23 = Join-Path $env:TEMP ("hooktest23-" + [IO.Path]::GetRandomFileName())
foreach ($d in 'logs', 'proj\docs\00-overview', 'ge\docs\00-overview', 'ge\docs\03-implementation', 'ge\docs\04-test', 'ge\.github\hooks\logs\usage') { New-Item -ItemType Directory -Force (Join-Path $fx23 $d) | Out-Null }
$p23 = $fx23 -replace '\\', '/'
$logs23 = Join-Path $fx23 'logs'
$jl23 = Join-Path $logs23 'hook-decisions.jsonl'
$utf823 = New-Object System.Text.UTF8Encoding $false
$tools23 = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $scriptsDir))) 'tools'
$env:HARNESS_HOOK_LOG_DIR = $logs23
$out = Invoke-Hook 'guard-dangerous-git.ps1' '{"sessionId":"copilot-1","toolName":"Bash","tool_input":{"command":"git push origin main"}}'
Remove-Item Env:HARNESS_HOOK_LOG_DIR -ErrorAction SilentlyContinue
$last23 = ''
if (Test-Path -LiteralPath $jl23) { $last23 = [string]((([IO.File]::ReadAllText($jl23, $utf823) -split "`n") | Where-Object { $_.Trim() }) | Select-Object -Last 1) }
if (($out -match '"permissionDecision":\s*"ask"') -and ($last23 -match '"host":"copilot"') -and ($last23 -match '"session_id":null') -and ($last23 -match '"duration_ms":\d+')) {
  $script:pass++; Write-Output "PASS: hook-log: camelCase payload (Copilot) -> host copilot, session_id null, duration_ms int"
} else {
  $script:fail++; Write-Output "FAIL: hook-log: camelCase payload (Copilot) -> host copilot, session_id null, duration_ms int"; Write-Output "  log: $last23"
}
$py23 = $null
foreach ($cand in 'python', 'python3') {
  $c23 = Get-Command $cand -ErrorAction SilentlyContinue
  if ($c23) { try { & $c23.Source --version *> $null; if ($LASTEXITCODE -eq 0) { $py23 = $c23.Source; break } } catch {} }
}
function Invoke-Py23($scriptPath, $argList, $payload, $logDir) {
  # python を Process API で起動し stdout を BOM なし UTF-8 で読む(日本語の判定文言を照合するため)。stdin は payload があれば書く
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $py23
  $a = '"' + $scriptPath + '"'
  if ($argList) { $a = $a + ' ' + $argList }
  $psi.Arguments = $a
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = $utf823
  if ($logDir) { $psi.EnvironmentVariables['HARNESS_HOOK_LOG_DIR'] = $logDir }
  $psi.WorkingDirectory = (Get-Location).Path
  $proc = [System.Diagnostics.Process]::Start($psi)
  if ($payload) { $bytes = $utf823.GetBytes($payload); $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length) }
  $proc.StandardInput.Close()
  $o = $proc.StandardOutput.ReadToEnd()
  $proc.StandardError.ReadToEnd() | Out-Null
  $proc.WaitForExit()
  return @{ out = $o; rc = $proc.ExitCode }
}
if ($py23) {
  $r = Invoke-Py23 (Join-Path $tools23 'hook-metrics.py') '--selftest' $null $null
  if ($r.rc -eq 0) {
    $script:pass++; Write-Output "PASS: hook-metrics.py --selftest (P50/P95, decision/host distribution, false-ask approx, SLO WARN)"
  } else {
    $script:fail++; Write-Output "FAIL: hook-metrics.py --selftest (P50/P95, decision/host distribution, false-ask approx, SLO WARN)"
  }
  $r = Invoke-Py23 (Join-Path $tools23 'hook-metrics.py') ('--logs "' + $logs23 + '" --kpi-line') $null $null
  if (($r.out -match 'P95') -and ($r.out -match 'copilot 1')) {
    $script:pass++; Write-Output "PASS: hook-metrics.py --kpi-line reads the selftest log (P95 and host copilot 1)"
  } else {
    $script:fail++; Write-Output "FAIL: hook-metrics.py --kpi-line reads the selftest log (P95 and host copilot 1)"; Write-Output "  got: $($r.out)"
  }
  [IO.File]::WriteAllText((Join-Path $fx23 'proj\docs\00-overview\progress.md'), "<!-- GATE_STATUS`nrequirements: done`nimplementation: in_progress`n-->`n", $utf823)
  [IO.File]::WriteAllText((Join-Path $logs23 'watchdog-continue-wd1.count'), '3', $utf823)
  $r = Invoke-Py23 (Join-Path $scriptsDir 'watchdog-continue.py') $null ('{"session_id":"wd1","cwd":"' + $p23 + '/proj","hook_event_name":"Stop","last_assistant_message":"次のタスクに進みます。"}') $logs23
  $rec23 = Join-Path $logs23 'watchdog-stop-wd1.json'
  $recText23 = ''
  if (Test-Path -LiteralPath $rec23) { $recText23 = [IO.File]::ReadAllText($rec23, $utf823) }
  if (($r.rc -eq 0) -and (-not $r.out.Trim()) -and ($recText23 -match '"kind": "watchdog_limit"') -and ($recText23 -match 'implementation: in_progress')) {
    $script:pass++; Write-Output "PASS: watchdog-continue: limit reached with in_progress -> watchdog-stop-<sid>.json (kind watchdog_limit, GATE snapshot)"
  } else {
    $script:fail++; Write-Output "FAIL: watchdog-continue: limit reached with in_progress -> watchdog-stop-<sid>.json (kind watchdog_limit, GATE snapshot)"; Write-Output "  rc=$($r.rc) out=$($r.out)"
  }
  $r = Invoke-Py23 (Join-Path $scriptsDir 'watchdog-continue.py') $null ('{"session_id":"wd2","cwd":"' + $p23 + '/proj","hook_event_name":"Stop","last_assistant_message":"次のタスクに進みます。"}') $logs23
  if (($r.rc -eq 0) -and ($r.out -match '"block"') -and (-not (Test-Path -LiteralPath (Join-Path $logs23 'watchdog-stop-wd2.json')))) {
    $script:pass++; Write-Output "PASS: watchdog-continue: below the limit -> block (continue) and no stop record"
  } else {
    $script:fail++; Write-Output "FAIL: watchdog-continue: below the limit -> block (continue) and no stop record"; Write-Output "  rc=$($r.rc) out=$($r.out)"
  }
  $gate23 = "<!-- GATE_STATUS`nrequirements: not_started`ndesign: not_started`nimplementation: done`ntest: not_started`nrelease: not_started`n-->`n"
  [IO.File]::WriteAllText((Join-Path $fx23 'ge\docs\00-overview\progress.md'), $gate23, $utf823)
  [IO.File]::WriteAllText((Join-Path $fx23 'ge\docs\03-implementation\tasks.md'), "- [x] TASK-001: x（完了条件: npm test 12件成功 2026-09-10 09:00）`n", $utf823)
  [IO.File]::WriteAllText((Join-Path $fx23 'ge\docs\04-test\review-log.md'), "### 2026-09-10 10:30 / implementation / 対象: TASK-001 / verdict: 承認`n", $utf823)
  $snap23 = $gate23 -replace "`n", '\n'
  [IO.File]::WriteAllText((Join-Path $fx23 'ge\.github\hooks\logs\abnormal-stop-g1.json'), ('{"session_id":"g1","error_type":"rate_limit","recorded_at":"2026-09-17T10:00:00+09:00","gate_status":"' + $snap23 + '"}'), $utf823)
  [IO.File]::WriteAllText((Join-Path $fx23 'ge\.github\hooks\logs\usage\g1.baseline.json'), '{"session_id":"g1","gate":{"implementation":"in_progress"}}', $utf823)
  $r = Invoke-Py23 (Join-Path $tools23 'golden-eval.py') ('"' + (Join-Path $fx23 'ge') + '"') $null $null
  if (($r.rc -eq 1) -and ($r.out -match '(?m)^NG') -and ($r.out -match '達成扱いにしない')) {
    $script:pass++; Write-Output "PASS: golden-eval: done declared inside an abnormally stopped session (GATE unchanged) -> NG, exit 1"
  } else {
    $script:fail++; Write-Output "FAIL: golden-eval: done declared inside an abnormally stopped session (GATE unchanged) -> NG, exit 1"; Write-Output "  rc=$($r.rc) out: $($r.out)"
  }
}
Remove-Item -Recurse -Force $fx23 -ErrorAction SilentlyContinue

Write-Output ""
Write-Output ("selftest.ps1: {0} passed, {1} failed" -f $script:pass, $script:fail)
if ($script:fail -gt 0) { exit 1 } else { exit 0 }

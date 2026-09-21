# PreToolUse hook (Bash|PowerShell、任意で mcp__* ツール): 外部反映(deploy / publish / 外部 API の書込
# 呼出 / リモートシェル・リモートコピー / 外部送信)を exact-action approval で止める
# (guard-external-effect.sh と同一判定。selftest 両系で固定)。
#   ask  : 通常モード。理由文に「何を(コマンド種別)・どこへ(URL ホスト / 対象フラグ / 引数)」を含める。
#   deny : 承認バイパス下(permission_mode が bypassPermissions / dontAsk 等、または
#          環境変数 HARNESS_EXTERNAL_EFFECT_MODE=deny)。ask は消音されるため deny に倒す(C-01 / IA-20260831-01)。
#   allow: 読み取り・計画・dry-run・ループバック宛の HTTP・ローカル完結の作業。
# 役割分担: git の push / tag / force / reset --hard / rm -rf は guard-dangerous-git。
# fail-open: JSON が読めない・command が無いときは {"continue": true}。
$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
$raw = [Console]::In.ReadToEnd()
$raw = $raw.TrimStart([char]0xFEFF)

# 共通ライブラリ(無くても動く=fail-open)
try { $__paths = Join-Path $PSScriptRoot '_paths.ps1'; if (Test-Path $__paths) { . $__paths } } catch {}

$obj = $null
$cmd = $null
$pmode = ''
$toolName = ''
try {
  $obj = $raw | ConvertFrom-Json
  $cmd = $obj.tool_input.command
  if ($obj.permission_mode) { $pmode = [string]$obj.permission_mode }
} catch {
  if ($raw -match '"command"\s*:\s*"([^"]*)"') { $cmd = $Matches[1] }
  if ($raw -match '"permission_mode"\s*:\s*"([^"]*)"') { $pmode = $Matches[1] }
}
if (Get-Command Get-ToolName -ErrorAction SilentlyContinue) { $toolName = [string](Get-ToolName $obj) }
if ((-not $toolName) -and ($raw -match '"(tool_name|toolName)"\s*:\s*"([^"]*)"')) { $toolName = $Matches[2] }

# 判定ログの共通実装(_log.ps1 → logs/hook-decisions.jsonl。A6-20 / D085)。無ければ無記録で動く(fail-open)
try { $__log = Join-Path $PSScriptRoot '_log.ps1'; if (Test-Path $__log) { . $__log } } catch {}
if (-not (Get-Command Write-HookLog -ErrorAction SilentlyContinue)) { function Write-HookLog($decision, $target) { } }

# 読み取り系ツール(VS Code は matcher を無視して全ツールで発火する)は対象外
if ((Get-Command Test-ReadOnlyTool -ErrorAction SilentlyContinue) -and (Test-ReadOnlyTool ([string]$toolName))) {
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}

# ---- 判定モード ----
$mode = 'ask'
if ($pmode -imatch 'bypass|dontask|dont_ask|autopilot|yolo') { $mode = 'deny' }
if ($env:HARNESS_EXTERNAL_EFFECT_MODE -eq 'deny') { $mode = 'deny' }

function Emit($kind, $what, $where, $excerpt) {
  $ex = ([string]$excerpt)
  if ($ex.Length -gt 160) { $ex = $ex.Substring(0, 160) }
  $ex = $ex -replace '[\r\n\t]', ' '
  if ($mode -eq 'deny') {
    $decision = 'deny'
    $pm = $pmode; if (-not $pm) { $pm = 'env HARNESS_EXTERNAL_EFFECT_MODE' }
    $msg = "外部反映($kind)を承認バイパス下で検知したため拒否しました: $what → $where"
    $reason = "承認バイパス(permission_mode=$pm)下では外部反映の ask が消音されるため deny します。外部反映(${kind}: $what → $where)は通常モードで人間の exact-action 承認を経て実行してください(AGENTS.md / release.agent.md)。コマンド: $ex"
  } else {
    $decision = 'ask'
    $msg = "外部反映($kind)のため確認します: $what → $where"
    $reason = "外部反映($kind)。何を: $what / どこへ: $where / コマンド: $ex 。承認は『この操作・この対象・この引数』に対する一回限り(exact-action)で、対象や引数が変わればその呼出で再度確認します。environment.md の「自動」分類でも外部反映は承認を経ます(AGENTS.md)。dry-run / plan / 読み取りは確認なしで通ります。"
  }
  $logEx = ([string]$excerpt) -replace '[\r\n\t]', ' '
  if ($logEx.Length -gt 200) { $logEx = $logEx.Substring(0, 200) }
  Write-HookLog $decision "${kind}:$logEx"
  $out = @{
    continue = $true
    systemMessage = $msg
    hookSpecificOutput = @{ hookEventName = 'PreToolUse'; permissionDecision = $decision; permissionDecisionReason = $reason }
  }
  $out | ConvertTo-Json -Depth 5 -Compress
  exit 0
}

# ---- MCP ツール(mcp__<server>__<tool>)は名前で分類する ----
$mcpWriteRe = '(^|_)(deploy|publish|push|create|update|delete|remove|merge|release|send|post|put|patch|write|upload|invoke|run|execute|trigger|dispatch|set|add|rename|move|archive|close|reopen|assign|approve|submit|transfer|purge|rollback|restart|scale|start|stop|kill|destroy|apply|edit|insert|append)(_|$)'
if (-not $cmd) {
  if ($toolName -imatch '^mcp__([^_]+(_[^_]+)*)__(.+)$') {
    $server = $Matches[1]; $tool = $Matches[3]
    if ($tool -imatch $mcpWriteRe) { Emit 'mcp-write' $toolName "MCP server $server" "tool=$tool" }
  }
  @{ continue = $true } | ConvertTo-Json -Compress
  exit 0
}

# ---- 正規化: 行継続(\ / ` / ^ + 改行)を畳む ----
$cmdf = [string]$cmd -replace '(\\|`|\^)\r?\n', ' '

# ---- 分類規則(.sh と同一) ----
$q = '["'']'
$wrappers = '^((sudo|doas|command|builtin|time|nice|nohup|env|exec|&|timeout\s+[0-9]+[a-z]*|npx|bunx|uvx|pnpm\s+(dlx|exec)|yarn\s+(dlx|exec)|pipx\s+run|dotnet\s+tool\s+run)\s+(-\S+\s+)*|cmd(\.exe)?\s+/[ck]\s+|(powershell|pwsh)(\.exe)?\s+(-noprofile\s+|-nologo\s+|-noninteractive\s+|-executionpolicy\s+[a-z]+\s+)*-c(ommand)?\s+|(bash|sh|zsh|dash|ksh)\s+-c\s+)+' + $q + '?'
$reLead = '^[\s({!]+'
$reEnv = '^([A-Za-z_][A-Za-z0-9_]*=\S*\s+)*'
$loopbackRe = '^(localhost|127\.[0-9.]+|\[::1\]|::1|0\.0\.0\.0|host\.docker\.internal)(:[0-9]+)?$'
$urlRe = 'https?://([^/\s"'')]+)'
$dryrunRe = '(^|\s)--?dry-?run([\s=]|$)'
$prodMarkerRe = '((RAILS_ENV|NODE_ENV|APP_ENV|MIX_ENV|ASPNETCORE_ENVIRONMENT|DOTNET_ENVIRONMENT|FLASK_ENV|DJANGO_ENV|ENVIRONMENT|ENV|STAGE)=' + $q + '?(prod|production|live)|(^|\s)(--env|--environment|--stage|--target|-e)[\s=]+' + $q + '?(prod|production|live)([\s"'']|$))'
$prodVerbRe = '(migrat|deploy|seed|rollback|release|publish|provision|apply|sync)'
$awsVerbRe = '(^|\s)(deploy|sync|cp|mv|rm|mb|rb|invoke|publish|(create|update|delete|put|start|stop|terminate|reboot|invoke|publish|register|deregister|run|modify|associate|disassociate|attach|detach|set|tag|untag|import|restore|copy|send|execute|promote|cancel|apply|add|remove|enable|disable|reset|rotate|swap|release|upload|purge|accept|reject|batch-write|batch-delete)-[a-z0-9-]+)(\s|$)'
$gcloudVerbRe = '(^|\s)(deploy|create|delete|update|submit|rsync|rm|remove|start|stop|restart|resize|import|invoke|publish|push|upload|promote|migrate|rollback|cancel|reset|suspend|resume|attach|detach|move|apply|set-iam-policy|add-iam-policy-binding|remove-iam-policy-binding|add-tags|remove-tags|cp|mv)(\s|$)'
$azVerbRe = '(^|\s)(create|delete|update|deploy|up|start|stop|restart|scale|publish|push|upload|set|import|purge|restore|invoke|run|attach|detach|assign|remove|add|approve|reject|move|rotate|regenerate|swap|promote|cancel|sync|apply|config-zip)(\s|$)'
$httpMutRe = '((^|\s)(-X|--request)[\s=]*' + $q + '?(POST|PUT|PATCH|DELETE)|(^|\s)(-d|--data|--data-raw|--data-binary|--data-ascii|--data-urlencode|-F|--form|-T|--upload-file|--json)([\s=]|$))'
$psHttpMutRe = '(-Method[\s:]+' + $q + '?(Post|Put|Patch|Delete)|(^|\s)-(Body|InFile|Form)([\s:]|$))'
$remoteCopyRe = '([^\s/"'']+@[^\s"'']+:|rsync://|(^|\s)-e\s+' + $q + '?ssh|(^|\s)[a-z0-9][a-z0-9.-]*\.[a-z]{2,}:[^\s]*)'

$script:kKind = ''; $script:kWhat = ''; $script:kWhere = ''
function Where-Of($seg, $rest) {
  $w = ''
  if ($seg -imatch $urlRe) { $w = 'host ' + $Matches[1] }
  if ((-not $w) -and ($seg -imatch '(^|\s)([^\s@/"'']+@[^\s:"'']+)')) { $w = $Matches[2] }
  foreach ($f in @('--context','--kube-context','-n','--namespace','--registry','-R','--repo','--app','-a','--project','--profile','--subscription','-g','--resource-group','--stack','--env','--environment','--stage','--target','--site','--region','--workspace','--cluster','--org','--scope')) {
    $re = '(^|\s)' + [regex]::Escape($f) + '(=|\s+)(\S+)'
    if ($seg -imatch $re) { if ($w) { $w += ' ' }; $w += $f + '=' + $Matches[3] }
  }
  if (-not $w) {
    $r = ([string]$rest).TrimStart()
    if ($r.Length -gt 80) { $r = $r.Substring(0, 80) }
    if ($r.Trim()) { $w = '引数 ' + $r } else { $w = '(既定の対象。引数なし)' }
  }
  $script:kWhere = $w
}
function Hit($kind, $what, $seg, $rest) { $script:kKind = $kind; $script:kWhat = $what; Where-Of $seg $rest; return $true }

function Test-M($s, $re) { return ([bool]($s -imatch $re)) }

function Classify-Segment($seg) {
  $s = [string]$seg
  if ($s -imatch $reLead) { $s = $s.Substring($Matches[0].Length) }
  if ($s -imatch $reEnv) { $s = $s.Substring($Matches[0].Length) }
  if ($s -imatch $wrappers) { $s = $s.Substring($Matches[0].Length) }
  $s = $s.TrimStart()
  $head = ($s -split '\s', 2)[0]
  $rest = $s.Substring($head.Length)
  $head = $head -replace '["'']', ''
  $head = $head -replace '^.*[\\/]', ''
  $head = $head -replace '\.(exe|cmd|bat|ps1)$', ''
  if (-not $head) { return $false }
  if (($seg -imatch $prodMarkerRe) -and ($seg -imatch $prodVerbRe)) { return (Hit 'production-marker' "$head(本番マーカー付き)" $seg $rest) }
  switch -regex ($head) {
    '^(terraform|tofu|terragrunt)$' { if ($rest -imatch '(^|\s)(run-all\s+)?(apply|destroy|import|taint|untaint|force-unlock|state\s+(rm|mv|push|replace-provider))(\s|$)') { return (Hit 'deploy(IaC)' "$head $($Matches[3])" $seg $rest) }; break }
    '^pulumi$' { if ($rest -imatch '(^|\s)(up|update|destroy|refresh|import|cancel|state\s+(delete|rename|move))(\s|$)') { return (Hit 'deploy(IaC)' "pulumi $($Matches[2])" $seg $rest) }; break }
    '^(cdk|cdktf)$' { if ($rest -imatch '(^|\s)(deploy|destroy|bootstrap)(\s|$)') { return (Hit 'deploy(IaC)' "$head $($Matches[2])" $seg $rest) }; break }
    '^sam$' { if ($rest -imatch '(^|\s)(deploy|delete|sync)(\s|$)') { return (Hit 'deploy(IaC)' "sam $($Matches[2])" $seg $rest) }; break }
    '^(serverless|sls|sst)$' { if ($rest -imatch '(^|\s)(deploy|remove|rollback|secret\s+(set|remove))(\s|$)') { return (Hit 'deploy(PaaS)' "$head $($Matches[2])" $seg $rest) }; break }
    '^(kubectl|oc|k)$' {
      if ($rest -imatch $dryrunRe) { return $false }
      if ($rest -imatch '(^|\s)(rollout\s+(status|history))(\s|$)') { return $false }
      if ($rest -imatch '(^|\s)(apply|create|delete|patch|replace|rollout|scale|autoscale|set|drain|cordon|uncordon|taint|label|annotate|edit|expose|run|exec|cp|attach|debug)(\s|$)') { return (Hit 'deploy(k8s)' "$head $($Matches[2])" $seg $rest) }; break }
    '^helm$' {
      if ($rest -imatch $dryrunRe) { return $false }
      if ($rest -imatch '(^|\s)(install|upgrade|uninstall|delete|rollback|push)(\s|$)') { return (Hit 'deploy(k8s)' "helm $($Matches[2])" $seg $rest) }; break }
    '^argocd$' { if ($rest -imatch '(^|\s)(app\s+(sync|create|delete|set|rollback|patch|terminate-op|edit)|proj\s+(create|delete)|repo\s+add|cluster\s+add)(\s|$)') { return (Hit 'deploy(k8s)' "argocd $($Matches[2])" $seg $rest) }; break }
    '^flux$' { if ($rest -imatch '(^|\s)(reconcile|suspend|resume|create|delete|install|uninstall|bootstrap|push)(\s|$)') { return (Hit 'deploy(k8s)' "flux $($Matches[2])" $seg $rest) }; break }
    '^istioctl$' { if ($rest -imatch '(^|\s)(install|upgrade|uninstall)(\s|$)') { return (Hit 'deploy(k8s)' "istioctl $($Matches[2])" $seg $rest) }; break }
    '^nomad$' { if ($rest -imatch '(^|\s)(job\s+(run|stop|dispatch|revert|promote|scale)|run|stop|node\s+drain|system\s+gc)(\s|$)') { return (Hit 'deploy(infra)' "nomad $($Matches[2])" $seg $rest) }; break }
    '^consul$' { if ($rest -imatch '(^|\s)(kv\s+(put|delete|import)|config\s+(write|delete)|acl)(\s|$)') { return (Hit 'deploy(infra)' "consul $($Matches[2])" $seg $rest) }; break }
    '^vault$' { if ($rest -imatch '(^|\s)(write|delete|patch|kv\s+(put|delete|patch|destroy|undelete)|policy\s+write|auth\s+(enable|disable|tune)|secrets\s+(enable|disable|tune)|token\s+(create|revoke)|lease\s+revoke|operator)(\s|$)') { return (Hit 'secrets-store' "vault $($Matches[2])" $seg $rest) }; break }
    '^(docker|podman|nerdctl)$' {
      if ($rest -imatch '(^|\s)push(\s|$)') { return (Hit 'publish(registry)' "$head push" $seg $rest) }
      if ($rest -imatch '(^|\s)(buildx\s+build|build)\s.*--push(\s|$)') { return (Hit 'publish(registry)' "$head build --push" $seg $rest) }
      if ($rest -imatch '(^|\s)(manifest|compose)\s+(.*\s)?push(\s|$)') { return (Hit 'publish(registry)' "$head $($Matches[2]) push" $seg $rest) }; break }
    '^crane$' { if ($rest -imatch '(^|\s)(push|copy|cp|delete|tag|mutate|append)(\s|$)') { return (Hit 'publish(registry)' "crane $($Matches[2])" $seg $rest) }; break }
    '^skopeo$' { if ($rest -imatch '(^|\s)(copy|sync|delete)\s.*docker://') { return (Hit 'publish(registry)' 'skopeo copy' $seg $rest) }; break }
    '^oras$' { if ($rest -imatch '(^|\s)(push|attach|delete|cp|copy)(\s|$)') { return (Hit 'publish(registry)' "oras $($Matches[2])" $seg $rest) }; break }
    '^gcloud$' {
      if ($rest -imatch '^\s*(auth|config|components|init|info|version|help|topic|feedback|cheat-sheet|survey)(\s|$)') { return $false }
      if ($rest -imatch $dryrunRe) { return $false }
      if ($rest -imatch $gcloudVerbRe) { return (Hit 'deploy(cloud)' "gcloud $($Matches[2])" $seg $rest) }; break }
    '^gsutil$' { if (($rest -imatch '(^|\s)(cp|mv|rsync|rm|mb|rb|setmeta|acl|iam|retention|lifecycle|cors|web)(\s|$)') -and ($rest -imatch 'gs://')) { $v = ([regex]::Match($rest, '(^|\s)(cp|mv|rsync|rm|mb|rb|setmeta|acl|iam|retention|lifecycle|cors|web)(\s|$)', 'IgnoreCase')).Groups[2].Value; return (Hit 'deploy(cloud)' "gsutil $v" $seg $rest) }; break }
    '^aws$' {
      if ($rest -imatch '^\s*(configure|sso|login|logout|help|--version)(\s|$)') { return $false }
      if ($rest -imatch '(^|\s)--dryrun(\s|$)') { return $false }
      if ($rest -imatch '^\s*(s3|s3api)\s') {
        if (($rest -imatch $awsVerbRe) -and ($rest -imatch 's3://')) { $v = ([regex]::Match($rest, $awsVerbRe, 'IgnoreCase')).Groups[2].Value; return (Hit 'deploy(cloud)' "aws s3 $v" $seg $rest) }
        return $false
      }
      if ($rest -imatch $awsVerbRe) { return (Hit 'deploy(cloud)' "aws $($Matches[2])" $seg $rest) }; break }
    '^az$' {
      if ($rest -imatch '^\s*(login|logout|account|config|configure|version|upgrade|extension|find|feedback|interactive|self-test)(\s|$)') { return $false }
      if ($rest -imatch '(^|\s)(--dry-run|what-if|validate|show|list)(\s|$)') { return $false }
      if ($rest -imatch '^\s*rest\s') {
        if ($rest -imatch ('(-m|--method)\s+' + $q + '?(post|put|patch|delete)')) { return (Hit 'external-api' "az rest $($Matches[2])" $seg $rest) }
        return $false
      }
      if ($rest -imatch $azVerbRe) { return (Hit 'deploy(cloud)' "az $($Matches[2])" $seg $rest) }; break }
    '^azcopy$' { if (($rest -imatch '(^|\s)(copy|cp|sync|remove|rm|make)(\s|$)') -and ($rest -imatch 'https?://')) { return (Hit 'deploy(cloud)' "azcopy $($Matches[2])" $seg $rest) }; break }
    '^rclone$' { if (($rest -imatch '(^|\s)(copy|copyto|sync|move|moveto|delete|deletefile|purge|mkdir|rmdir|rmdirs|touch|dedupe|cleanup|settier)(\s|$)') -and ($rest -imatch '(^|\s)[A-Za-z0-9_-]{2,}:')) { $v = ([regex]::Match($rest, '(^|\s)(copy|copyto|sync|move|moveto|delete|deletefile|purge|mkdir|rmdir|rmdirs|touch|dedupe|cleanup|settier)(\s|$)', 'IgnoreCase')).Groups[2].Value; return (Hit 'deploy(cloud)' "rclone $v" $seg $rest) }; break }
    '^s3cmd$' { if ($rest -imatch '(^|\s)(put|sync|del|rm|mb|rb|cp|mv|modify|setacl|setpolicy)(\s|$)') { return (Hit 'deploy(cloud)' "s3cmd $($Matches[2])" $seg $rest) }; break }
    '^vercel$' {
      $sub = ([string]$rest).TrimStart()
      if ($sub -imatch '^(dev|build|login|logout|whoami|ls|list|inspect|logs|link|pull|switch|teams|telemetry|help|init|bisect|--version|-v|--help|-h|env\s+(ls|list|pull)|project\s+(ls|list)|domains\s+(ls|list|inspect)|certs\s+(ls|list)|dns\s+(ls|list)|alias\s+(ls|list)|integration\s+(list|balance|open))(\s|$)') { return $false }
      $first = ($sub -split '\s', 2)[0]
      return (Hit 'deploy(PaaS)' "vercel $first" $seg $rest) }
    '^(netlify|ntl)$' { if ($rest -imatch '(^|\s)(deploy|env:set|env:unset|env:import|env:clone|sites:create|sites:delete|functions:invoke|api|addons:create|addons:delete|blobs:set|blobs:delete|unlink|link|switch|recipes)(\s|$)') { return (Hit 'deploy(PaaS)' "$head $($Matches[2])" $seg $rest) }; break }
    '^firebase$' { if ($rest -imatch '(^|\s)(deploy|hosting:channel:deploy|hosting:disable|hosting:clone|functions:delete|functions:config:set|functions:config:unset|firestore:delete|database:set|database:push|database:remove|database:update|apphosting:rollouts:create|apphosting:backends:create|apphosting:backends:delete|appdistribution:distribute|ext:install|ext:uninstall|ext:configure|remoteconfig:rollback|crashlytics:symbols:upload)(\s|$)') { return (Hit 'deploy(PaaS)' "firebase $($Matches[2])" $seg $rest) }; break }
    '^(fly|flyctl)$' { if ($rest -imatch '(^|\s)(deploy|launch|scale|secrets\s+(set|unset|import|deploy)|machines?\s+(run|start|stop|destroy|update|kill|clone|restart)|apps\s+(create|destroy|restart)|volumes?\s+(create|destroy|extend)|releases?\s+rollback|certs?\s+(add|remove|create|delete)|ips\s+(allocate|release)|postgres\s+(create|attach|detach)|redis\s+create|regions\s+(add|remove|set)|ssh\s+console|console)(\s|$)') { return (Hit 'deploy(PaaS)' "$head $($Matches[2])" $seg $rest) }; break }
    '^heroku$' { if ($rest -imatch '(^|\s)(container:push|container:release|releases:rollback|rollback|config:set|config:unset|ps:scale|ps:restart|ps:stop|ps:kill|apps:create|apps:destroy|apps:rename|run|pipelines:promote|builds:create|addons:create|addons:destroy|addons:attach|addons:detach|domains:add|domains:remove|maintenance:on|maintenance:off|pg:reset|pg:push|pg:pull|pg:promote|git:push|labs:enable)(\s|$)') { return (Hit 'deploy(PaaS)' "heroku $($Matches[2])" $seg $rest) }; break }
    '^railway$' { if ($rest -imatch '(^|\s)(up|deploy|redeploy|down|delete|variables\s+(set|delete)|volume|domain|service\s+delete)(\s|$)') { return (Hit 'deploy(PaaS)' "railway $($Matches[2])" $seg $rest) }; break }
    '^wrangler$' {
      if ($rest -imatch $dryrunRe) { return $false }
      if ($rest -imatch '(^|\s)(deploy|publish|pages\s+(deploy|publish|project\s+(create|delete))|kv:key\s+(put|delete)|kv\s+(key|bulk)\s+(put|delete)|r2\s+(object\s+(put|delete)|bucket\s+(create|delete))|secret\s+(put|delete|bulk)|versions\s+(upload|deploy)|rollback|delete|queues\s+(create|delete)|d1\s+(create|delete)|d1\s+(execute|migrations\s+apply)[^;]*--remote)(\s|$)') { return (Hit 'deploy(PaaS)' "wrangler $($Matches[2])" $seg $rest) }; break }
    '^eb$' { if ($rest -imatch '(^|\s)(deploy|create|terminate|scale|setenv|swap|restore|abort|clone)(\s|$)') { return (Hit 'deploy(PaaS)' "eb $($Matches[2])" $seg $rest) }; break }
    '^copilot$' { if ($rest -imatch '(^|\s)((svc|env|app|job|pipeline|task)\s+(deploy|delete|run|exec|package)|deploy)(\s|$)') { return (Hit 'deploy(PaaS)' "copilot(AWS) $($Matches[2])" $seg $rest) }; break }
    '^kamal$' { if ($rest -imatch '(^|\s)(deploy|redeploy|rollback|setup|remove|app\s+(boot|start|stop|exec|remove)|env\s+push|accessory|traefik|proxy|server\s+(bootstrap|exec)|lock)(\s|$)') { return (Hit 'deploy(PaaS)' "kamal $($Matches[2])" $seg $rest) }; break }
    '^(cap|capistrano|mina)$' { if ($rest -imatch '(deploy|production|staging)') { $r30 = ([string]$rest); if ($r30.Length -gt 30) { $r30 = $r30.Substring(0, 30) }; return (Hit 'deploy(PaaS)' "$head $r30" $seg $rest) }; break }
    '^ansible-playbook$' {
      if ($rest -imatch '(^|\s)(--check|-C)(\s|$)') { return $false }
      return (Hit 'deploy(config-mgmt)' 'ansible-playbook' $seg $rest) }
    '^ansible$' { if ($rest -imatch ('(^|\s)-m\s+' + $q + '?(shell|command|raw|script|copy|template|file|lineinfile|blockinfile|apt|yum|dnf|pip|service|systemd|user|group|git|unarchive|synchronize)([\s"'']|$)')) { return (Hit 'deploy(config-mgmt)' "ansible -m $($Matches[2])" $seg $rest) }; break }
    '^amplify$' { if ($rest -imatch '(^|\s)(push|publish|delete|env\s+(add|remove))(\s|$)') { return (Hit 'deploy(PaaS)' "amplify $($Matches[2])" $seg $rest) }; break }
    '^supabase$' { if ($rest -imatch '(^|\s)(db\s+push|functions\s+deploy|secrets\s+(set|unset)|projects\s+(create|delete)|branches\s+(create|delete|merge)|db\s+reset[^;]*--linked)(\s|$)') { return (Hit 'deploy(PaaS)' "supabase $($Matches[2])" $seg $rest) }; break }
    '^npm$' {
      if ($rest -imatch $dryrunRe) { return $false }
      if ($rest -imatch '(^|\s)(publish|unpublish|deprecate|dist-tag\s+(add|rm)|owner\s+(add|rm)|access|token\s+(create|revoke)|hook)(\s|$)') { return (Hit 'publish(package)' "npm $($Matches[2])" $seg $rest) }; break }
    '^pnpm$' { if ($rest -imatch $dryrunRe) { return $false }
      if ($rest -imatch '(^|\s)(publish|unpublish|deprecate)(\s|$)') { return (Hit 'publish(package)' "pnpm $($Matches[2])" $seg $rest) }; break }
    '^yarn$' { if ($rest -imatch $dryrunRe) { return $false }
      if ($rest -imatch '(^|\s)(publish|npm\s+publish|npm\s+tag\s+add)(\s|$)') { return (Hit 'publish(package)' "yarn $($Matches[2])" $seg $rest) }; break }
    '^cargo$' { if ($rest -imatch $dryrunRe) { return $false }
      if ($rest -imatch '(^|\s)(publish|yank|owner)(\s|$)') { return (Hit 'publish(package)' "cargo $($Matches[2])" $seg $rest) }; break }
    '^twine$' { if ($rest -imatch '(^|\s)upload(\s|$)') { return (Hit 'publish(package)' 'twine upload' $seg $rest) }; break }
    '^(flit|poetry|uv|hatch|pdm|pub|conan)$' { if ($rest -imatch $dryrunRe) { return $false }
      if ($rest -imatch '(^|\s)(publish|upload)(\s|$)') { return (Hit 'publish(package)' "$head $($Matches[2])" $seg $rest) }; break }
    '^(dart|flutter)$' { if ($rest -imatch $dryrunRe) { return $false }
      if ($rest -imatch '(^|\s)pub\s+publish(\s|$)') { return (Hit 'publish(package)' "$head pub publish" $seg $rest) }; break }
    '^(python|python3|py)$' { if ($rest -imatch '^\s*(-m\s+(twine\s+upload|flit\s+publish|hatch\s+publish)|setup\.py\s+(upload|register))') { return (Hit 'publish(package)' "python $($Matches[1])" $seg $rest) }; break }
    '^gem$' { if ($rest -imatch '(^|\s)(push|yank|owner)(\s|$)') { return (Hit 'publish(package)' "gem $($Matches[2])" $seg $rest) }; break }
    '^rake$' { if ($rest -imatch '(^|\s)release(\s|$)') { return (Hit 'publish(package)' 'rake release' $seg $rest) }; break }
    '^bundle$' { if ($rest -imatch '(^|\s)exec\s+rake\s+release(\s|$)') { return (Hit 'publish(package)' 'rake release' $seg $rest) }; break }
    '^dotnet$' { if ($rest -imatch '(^|\s)nuget\s+(push|delete)(\s|$)') { return (Hit 'publish(package)' "dotnet nuget $($Matches[2])" $seg $rest) }; break }
    '^nuget$' { if ($rest -imatch '(^|\s)(push|delete)(\s|$)') { return (Hit 'publish(package)' "nuget $($Matches[2])" $seg $rest) }; break }
    '^(mvn|mvnw)$' { if ($rest -imatch '(^|\s)(deploy|release:perform|release:prepare|nexus-staging:[a-z-]+)(\s|$)') { return (Hit 'publish(package)' "mvn $($Matches[2])" $seg $rest) }; break }
    '^(gradle|gradlew)$' { if ($rest -imatch '(^|\s)(publish[A-Za-z]*|uploadArchives|bintrayUpload|closeAndReleaseRepository|jreleaser[A-Za-z]*)(\s|$)') { return (Hit 'publish(package)' "gradle $($Matches[2])" $seg $rest) }; break }
    '^goreleaser$' { if (($rest -imatch '(^|\s)release(\s|$)') -and -not ($rest -imatch '(--snapshot|--skip-publish|--skip[\s=]+[a-z,]*publish)')) { return (Hit 'publish(package)' 'goreleaser release' $seg $rest) }; break }
    '^(cabal|stack)$' { if ($rest -imatch '(^|\s)upload(\s|$)') { return (Hit 'publish(package)' "$head upload" $seg $rest) }; break }
    '^mix$' { if ($rest -imatch '(^|\s)hex\.publish(\s|$)') { return (Hit 'publish(package)' 'mix hex.publish' $seg $rest) }; break }
    '^gh$' {
      if ($rest -imatch '(^|\s)(release\s+(create|upload|edit|delete|delete-asset)|pr\s+merge|workflow\s+(run|enable|disable)|run\s+(rerun|cancel|delete)|repo\s+(create|delete|edit|rename|archive|unarchive|sync)|secret\s+(set|delete|remove)|variable\s+(set|delete)|ruleset\s+(create|edit|delete)|label\s+(create|delete|edit|clone)|gpg-key\s+(add|delete)|ssh-key\s+(add|delete)|codespace\s+(create|delete|rebuild)|cache\s+delete)(\s|$)') { return (Hit 'external-api(GitHub)' "gh $($Matches[2])" $seg $rest) }
      if (($rest -imatch '^\s*api\s') -and ($rest -imatch ('((-X|--method)[\s=]+' + $q + '?(POST|PUT|PATCH|DELETE)|(^|\s)(-f|-F|--field|--raw-field|--input)([\s=]|$))'))) { return (Hit 'external-api(GitHub)' 'gh api(書込メソッド)' $seg $rest) }; break }
    '^glab$' { if ($rest -imatch '(^|\s)(mr\s+merge|release\s+(create|upload|delete)|ci\s+(run|retry|cancel)|variable\s+(set|delete)|repo\s+(create|delete|archive))(\s|$)') { return (Hit 'external-api(GitLab)' "glab $($Matches[2])" $seg $rest) }; break }
    '^curl$' {
      if ($rest -imatch $httpMutRe) {
        if ($rest -imatch $urlRe) { if ($Matches[1] -imatch $loopbackRe) { return $false } }
        return (Hit 'external-api(HTTP 書込)' 'curl(POST/PUT/PATCH/DELETE または --data/--upload)' $seg $rest)
      }; break }
    '^wget$' {
      if ($rest -imatch '(--post-data|--post-file|--body-data|--body-file|--method=(POST|PUT|PATCH|DELETE))') {
        if ($rest -imatch $urlRe) { if ($Matches[1] -imatch $loopbackRe) { return $false } }
        return (Hit 'external-api(HTTP 書込)' 'wget(書込メソッド)' $seg $rest)
      }; break }
    '^(http|https|xh|httpie)$' {
      if ($rest -imatch '(^|\s)(POST|PUT|PATCH|DELETE)\s') {
        $verb = $Matches[2]
        if ($rest -imatch $urlRe) { if ($Matches[1] -imatch $loopbackRe) { return $false } }
        return (Hit 'external-api(HTTP 書込)' "$head $verb" $seg $rest)
      }; break }
    '^(invoke-webrequest|invoke-restmethod|iwr|irm)$' {
      if ($rest -imatch $psHttpMutRe) {
        if ($rest -imatch $urlRe) { if ($Matches[1] -imatch $loopbackRe) { return $false } }
        return (Hit 'external-api(HTTP 書込)' "$head(-Method Post/Put/Patch/Delete または -Body)" $seg $rest)
      }; break }
    '^start-bitstransfer$' { if ($rest -imatch ('-TransferType[\s:]+' + $q + '?Upload')) { return (Hit 'external-api(HTTP 書込)' 'Start-BitsTransfer Upload' $seg $rest) }; break }
    '^(sendmail|mailx|mutt|send-mailmessage)$' { return (Hit 'external-send(mail)' $head $seg $rest) }
    '^mail$' { if ($rest -imatch '(^|\s)-s\s') { return (Hit 'external-send(mail)' 'mail -s' $seg $rest) }; break }
    '^ssh$' {
      if ($rest -imatch '(^|\s)(-T\s+git@|-G(\s|$)|-Q\s|-V(\s|$)|-O\s+check)') { return $false }
      return (Hit 'remote-shell' 'ssh' $seg $rest) }
    '^ssh-copy-id$' { return (Hit 'remote-shell' 'ssh-copy-id' $seg $rest) }
    '^(scp|sftp|rsync)$' { if ($rest -imatch $remoteCopyRe) { return (Hit 'remote-copy' $head $seg $rest) }; break }
  }
  if ($seg -imatch '(\[(System\.)?Net\.WebClient\]|\.Upload(String|File|Data|Values)\()') { return (Hit 'external-api(HTTP 書込)' 'WebClient.Upload*' $seg $rest) }
  return $false
}

# ---- セグメント(; & | 改行)ごとに分類。上限 48 セグメント(49 以上は評価前に ask=fail-closed。SC-5) ----
$segs = $cmdf -split '[;|&\r\n]'
$n = 0
foreach ($seg in $segs) {
  if (-not ($seg -replace '\s', '')) { continue }
  $n++
  if ($n -gt 48) { Emit 'segment-cap' 'セグメント数が上限(48)を超過(検査しきれない分は評価前に確認=fail-closed。SC-5)' '分割して実行してください' $cmdf }
  if (Classify-Segment $seg) {
    # 理由文の抜粋は _log.ps1 の redaction(Protect-HookLogText = privacy-patterns.json。無ければそのまま)を通す
    $excerpt = [string]$seg
    if (Get-Command Protect-HookLogText -ErrorAction SilentlyContinue) { $excerpt = Protect-HookLogText $excerpt }
    Emit $script:kKind $script:kWhat $script:kWhere $excerpt.TrimStart()
  }
}
@{ continue = $true } | ConvertTo-Json -Compress

#!/usr/bin/env bash
# PreToolUse hook (Bash|PowerShell、任意で mcp__* ツール): 外部反映(deploy / publish / 外部 API の書込
# 呼出 / リモートシェル・リモートコピー / 外部送信)を exact-action approval で止める。
#   ask  : 通常モード。理由文に「何を(コマンド種別)・どこへ(URL ホスト / 対象フラグ / 引数)」を含め、
#          利用者が「この操作・この対象・この引数」に対して一回限りの承認を出せるようにする
#          (承認の単位はツール呼出 1 回。対象や引数が変わればその呼出で再度 ask になる)。
#   deny : 承認バイパス下(ペイロードの permission_mode が bypassPermissions / dontAsk 等、または
#          環境変数 HARNESS_EXTERNAL_EFFECT_MODE=deny)。ask は消音されて素通りになるため deny に倒す
#          (codex 監査 2026-08-31 C-01 / IA-20260831-01: 「Autopilot/承認バイパス下では deny 側」)。
#   allow: 読み取り・計画・dry-run(terraform plan / kubectl --dry-run / helm template / curl GET /
#          gh run list / docker build 等)、ループバック宛の HTTP、ローカル完結の作業。
# 規範の正: AGENTS.md「必ず止まる条件」2「外部反映: git push・タグ付け・デプロイ・外部送信・force 系操作は、
# environment.md の自動化可否分類に関わらず事前にユーザーへ確認する」。environment.md の「自動」は
# 「準備(ビルド・パッケージング・dry-run・チェックリスト)は確認なし」の意味で、外部反映の承認を
# 省略する意味ではない(release.agent.md 手順4 と同一文)。
# 役割分担: git の push / tag / force / reset --hard / rm -rf は guard-dangerous-git(従来どおり)。
# 本フックはそれ以外の外部反映(IaC・クラウド CLI・PaaS CLI・コンテナレジストリ・パッケージ公開・
# gh の書込系・HTTP の書込メソッド・ssh/scp/rsync・メール送信・本番マーカー付きの migrate/deploy)。
# 既知の限界(R-01 と同じ信頼境界): インタプリタ内の fetch()/requests.post() や、変数に隠した
# コマンド(`$cmd`)は文字列検査では見えない。境界は OS サンドボックス / Git 側保護 / 資格情報の
# 非付与(環境変数を渡さない)であり、本フックは多層防御の 1 層(PLATFORM.md「セキュリティの層構造」)。
# fail-open: JSON が読めない・command が無い(ファイル系ツール等)は {"continue": true}。
# 外部プロセスは JSON 解析の 1 回だけ(_paths.sh の parse_hook_input。以降は bash 組み込みの =~ /
# パラメータ展開。SC-5 の timeout 5s 対策)。.ps1 側の鏡は guard-external-effect.ps1(同一判定。selftest 両系で固定)。
input=$(cat)

# 共通ライブラリ(D072 / A6-20 / D085): 1 回解析 API と判定ログ(_log.sh → logs/hook-decisions.jsonl)。
# 無ければ grep 抽出・無記録で動く(fail-open)
# shellcheck source=_paths.sh
source "$(dirname "$0")/_paths.sh" 2>/dev/null || true
# shellcheck source=_log.sh
source "$(dirname "$0")/_log.sh" 2>/dev/null || true
type hook_log >/dev/null 2>&1 || hook_log() { :; }
type parse_hook_input >/dev/null 2>&1 && parse_hook_input "$input"
shopt -s nocasematch

# ---- 入力の取り出し ----
# permission_mode はトップレベルの単純な文字列なので bash 組み込みで拾う(parse_hook_input の 9 欄には無い)
pmode=""
if [[ $input =~ \"permission_mode\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]]; then pmode=${BASH_REMATCH[1]}; fi
tname=""
if [[ -n "${_HOOK_PARSED:-}" ]]; then tname=$_HOOK_TOOL_NAME; fi
if [[ -z "$tname" && $input =~ \"(tool_name|toolName)\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]]; then tname=${BASH_REMATCH[2]}; fi

# 読み取り系ツール(readFile 等。VS Code は matcher を無視して全ツールで発火する)は対象外
if type is_read_only_tool >/dev/null 2>&1 && is_read_only_tool "$tname"; then
  printf '%s\n' '{"continue": true}'
  exit 0
fi

# command は parse_hook_input(jq → node → python のどれか 1 プロセス)の結果。解析器が無い・JSON が壊れている
# ときだけ grep 抽出にフォールバックする(壊れた payload でも command を拾って保護側に倒す。guard-dangerous-git と同一)
cmd=""
if [[ -n "${_HOOK_PARSED:-}" ]]; then
  cmd=$_HOOK_COMMAND
else
  cmd=$(printf '%s' "$input" | grep -oE '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
fi

# ---- 判定モード(ask か deny か) ----
mode=ask
case "$pmode" in
  *bypass*|*dontask*|*dont_ask*|*autopilot*|*yolo*) mode=deny ;;
esac
if [[ "${HARNESS_EXTERNAL_EFFECT_MODE:-}" == "deny" ]]; then mode=deny; fi

# ---- 出力 ----
# JSON 文字列に入れる断片の無害化(引用符・バックスラッシュ・制御文字)
json_safe() {
  local s="$1"
  s=${s//\\/\/}
  s=${s//\"/\'}
  s=$(printf '%s' "$s" | tr '\r\n\t' '   ')
  printf '%s' "$s"
}
emit() { # $1=kind $2=what $3=where $4=excerpt
  local kind what where ex reason msg decision
  kind=$(json_safe "$1"); what=$(json_safe "$2"); where=$(json_safe "$3"); ex=$(json_safe "${4:0:160}")
  if [[ "$mode" == "deny" ]]; then
    decision=deny
    msg="外部反映($kind)を承認バイパス下で検知したため拒否しました: $what → $where"
    reason="承認バイパス(permission_mode=${pmode:-env HARNESS_EXTERNAL_EFFECT_MODE})下では外部反映の ask が消音されるため deny します。外部反映($kind: $what → $where)は通常モードで人間の exact-action 承認を経て実行してください(AGENTS.md / release.agent.md)。コマンド: $ex"
  else
    decision=ask
    msg="外部反映($kind)のため確認します: $what → $where"
    reason="外部反映($kind)。何を: $what / どこへ: $where / コマンド: $ex 。承認は『この操作・この対象・この引数』に対する一回限り(exact-action)で、対象や引数が変わればその呼出で再度確認します。environment.md の「自動」分類でも外部反映は承認を経ます(AGENTS.md)。dry-run / plan / 読み取りは確認なしで通ります。"
  fi
  hook_log "$decision" "$kind:$(json_safe "${4:0:200}")"
  printf '{"continue": true, "systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "%s", "permissionDecisionReason": "%s"}}\n' "$msg" "$decision" "$reason"
  exit 0
}

# ---- MCP ツール(mcp__<server>__<tool>)は名前で分類する(コマンド文字列を持たない) ----
# 書込系の動詞を含むツール名は ask、読み取り系(get/list/search/read/fetch/describe/query)は allow。
# 配線は .claude/settings.json の PreToolUse に matcher "mcp__.*" のグループで行う(任意)。
mcp_write_re='(^|_)(deploy|publish|push|create|update|delete|remove|merge|release|send|post|put|patch|write|upload|invoke|run|execute|trigger|dispatch|set|add|rename|move|archive|close|reopen|assign|approve|submit|transfer|purge|rollback|restart|scale|start|stop|kill|destroy|apply|edit|insert|append)(_|$)'
if [[ -z "$cmd" ]]; then
  if [[ $tname =~ ^mcp__([^_]+(_[^_]+)*)__(.+)$ ]]; then
    server=${BASH_REMATCH[1]}; tool=${BASH_REMATCH[3]}
    if [[ $tool =~ $mcp_write_re ]]; then
      emit "mcp-write" "$tname" "MCP server $server" "tool=$tool"
    fi
  fi
  printf '%s\n' '{"continue": true}'
  exit 0
fi

# ---- コマンド文字列の正規化 ----
# 行継続(\ / ` / ^ + 改行)を畳む(guard-dangerous-git と同じ 3 種)
cmdf=${cmd//$'\\\r\n'/ }
cmdf=${cmdf//$'\\\n'/ }
cmdf=${cmdf//$'`\r\n'/ }
cmdf=${cmdf//$'`\n'/ }
cmdf=${cmdf//$'^\r\n'/ }
cmdf=${cmdf//$'^\n'/ }

# ---- 分類規則(すべて ERE。nocasematch で大文字小文字非依存) ----
# 先頭のラッパ語(sudo / env / time / npx / bunx / cmd /c / powershell -c / bash -c 等)を読み飛ばして実コマンドを見る
q="[\"']"
wrappers="^((sudo|doas|command|builtin|time|nice|nohup|env|exec|&|timeout[[:space:]]+[0-9]+[a-z]*|npx|bunx|uvx|pnpm[[:space:]]+(dlx|exec)|yarn[[:space:]]+(dlx|exec)|pipx[[:space:]]+run|dotnet[[:space:]]+tool[[:space:]]+run)[[:space:]]+(-[^[:space:]]+[[:space:]]+)*|cmd(\\.exe)?[[:space:]]+/[ck][[:space:]]+|(powershell|pwsh)(\\.exe)?[[:space:]]+(-noprofile[[:space:]]+|-nologo[[:space:]]+|-noninteractive[[:space:]]+|-executionpolicy[[:space:]]+[a-z]+[[:space:]]+)*-c(ommand)?[[:space:]]+|(bash|sh|zsh|dash|ksh)[[:space:]]+-c[[:space:]]+)+${q}?"
re_lead='^[[:space:]({!]+'
re_env='^([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*'
loopback_re='^(localhost|127\.[0-9.]+|\[::1\]|::1|0\.0\.0\.0|host\.docker\.internal)(:[0-9]+)?$'
url_re="https?://([^/[:space:]\"')]+)"
dryrun_re='(^|[[:space:]])--?dry-?run([[:space:]=]|$)'
# 本番マーカー(env 代入・--env/--stage 等)と状態変更語の同居(migrate/deploy/seed/rollback/release/publish/provision/apply/sync)
prod_marker_re="((RAILS_ENV|NODE_ENV|APP_ENV|MIX_ENV|ASPNETCORE_ENVIRONMENT|DOTNET_ENVIRONMENT|FLASK_ENV|DJANGO_ENV|ENVIRONMENT|ENV|STAGE)=${q}?(prod|production|live)|(^|[[:space:]])(--env|--environment|--stage|--target|-e)[[:space:]=]+${q}?(prod|production|live)([[:space:]\"']|$))"
prod_verb_re='(migrat|deploy|seed|rollback|release|publish|provision|apply|sync)'
# aws / gcloud / az の状態変更動詞(サブコマンドの語)
aws_verb_re='(^|[[:space:]])(deploy|sync|cp|mv|rm|mb|rb|invoke|publish|(create|update|delete|put|start|stop|terminate|reboot|invoke|publish|register|deregister|run|modify|associate|disassociate|attach|detach|set|tag|untag|import|restore|copy|send|execute|promote|cancel|apply|add|remove|enable|disable|reset|rotate|swap|release|upload|purge|accept|reject|batch-write|batch-delete)-[a-z0-9-]+)([[:space:]]|$)'
gcloud_verb_re='(^|[[:space:]])(deploy|create|delete|update|submit|rsync|rm|remove|start|stop|restart|resize|import|invoke|publish|push|upload|promote|migrate|rollback|cancel|reset|suspend|resume|attach|detach|move|apply|set-iam-policy|add-iam-policy-binding|remove-iam-policy-binding|add-tags|remove-tags|cp|mv)([[:space:]]|$)'
az_verb_re='(^|[[:space:]])(create|delete|update|deploy|up|start|stop|restart|scale|publish|push|upload|set|import|purge|restore|invoke|run|attach|detach|assign|remove|add|approve|reject|move|rotate|regenerate|swap|promote|cancel|sync|apply|config-zip)([[:space:]]|$)'
http_mut_re="((^|[[:space:]])(-X|--request)[[:space:]=]*${q}?(POST|PUT|PATCH|DELETE)|(^|[[:space:]])(-d|--data|--data-raw|--data-binary|--data-ascii|--data-urlencode|-F|--form|-T|--upload-file|--json)([[:space:]=]|$))"
ps_http_mut_re="(-Method[[:space:]:]+${q}?(Post|Put|Patch|Delete)|(^|[[:space:]])-(Body|InFile|Form)([[:space:]:]|$))"
remote_copy_re="([^[:space:]/\"']+@[^[:space:]\"']+:|rsync://|(^|[[:space:]])-e[[:space:]]+${q}?ssh|(^|[[:space:]])[a-z0-9][a-z0-9.-]*\\.[a-z]{2,}:[^[:space:]]*)"

# セグメント $1 が外部反映なら 0 を返し、グローバル K_KIND / K_WHAT / K_WHERE に理由を残す
K_KIND=""; K_WHAT=""; K_WHERE=""
where_of() { # $1=セグメント $2=rest(動詞以降)。URL ホスト → user@host → 対象フラグ → 引数の順で「どこへ」を推定
  local seg="$1" rest="$2" w="" f re
  if [[ $seg =~ $url_re ]]; then w="host ${BASH_REMATCH[1]}"; fi
  re="(^|[[:space:]])([^[:space:]@/\"']+@[^[:space:]:\"']+)"
  if [[ -z "$w" && $seg =~ $re ]]; then w=${BASH_REMATCH[2]}; fi
  for f in --context --kube-context -n --namespace --registry -R --repo --app -a --project --profile --subscription -g --resource-group --stack --env --environment --stage --target --site --region --workspace --cluster --org --scope; do
    re="(^|[[:space:]])${f}(=|[[:space:]]+)([^[:space:]]+)"
    if [[ $seg =~ $re ]]; then w="${w:+$w }${f}=${BASH_REMATCH[3]}"; fi
  done
  if [[ -z "$w" ]]; then
    rest=${rest#"${rest%%[![:space:]]*}"}
    w="引数 ${rest:0:80}"
    [[ -z "${rest//[[:space:]]/}" ]] && w="(既定の対象。引数なし)"
  fi
  K_WHERE="$w"
}
hit() { K_KIND="$1"; K_WHAT="$2"; where_of "$3" "$4"; return 0; }
classify_segment() {
  local seg="$1" s head rest sub re
  s=$seg
  [[ $s =~ $re_lead ]] && s=${s:${#BASH_REMATCH[0]}}
  [[ $s =~ $re_env ]] && s=${s:${#BASH_REMATCH[0]}}
  [[ $s =~ $wrappers ]] && s=${s:${#BASH_REMATCH[0]}}
  s=${s#"${s%%[![:space:]]*}"}
  head=${s%%[[:space:]]*}
  rest=${s:${#head}}
  head=${head//[\"\']/}
  head=${head##*/}
  head=${head%.exe}; head=${head%.cmd}; head=${head%.bat}; head=${head%.ps1}
  [[ -z "$head" ]] && return 1
  # 本番マーカー + 状態変更語(migrate/deploy/seed/rollback/release/publish/provision/apply/sync)
  if [[ $seg =~ $prod_marker_re && $seg =~ $prod_verb_re ]]; then hit "production-marker" "$head(本番マーカー付き)" "$seg" "$rest"; return 0; fi
  case "$head" in
    terraform|tofu|terragrunt)
      [[ $rest =~ (^|[[:space:]])(run-all[[:space:]]+)?(apply|destroy|import|taint|untaint|force-unlock|state[[:space:]]+(rm|mv|push|replace-provider))([[:space:]]|$) ]] && { hit "deploy(IaC)" "$head ${BASH_REMATCH[3]}" "$seg" "$rest"; return 0; } ;;
    pulumi)
      [[ $rest =~ (^|[[:space:]])(up|update|destroy|refresh|import|cancel|state[[:space:]]+(delete|rename|move))([[:space:]]|$) ]] && { hit "deploy(IaC)" "pulumi ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    cdk|cdktf)
      [[ $rest =~ (^|[[:space:]])(deploy|destroy|bootstrap)([[:space:]]|$) ]] && { hit "deploy(IaC)" "$head ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    sam) [[ $rest =~ (^|[[:space:]])(deploy|delete|sync)([[:space:]]|$) ]] && { hit "deploy(IaC)" "sam ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    serverless|sls|sst)
      [[ $rest =~ (^|[[:space:]])(deploy|remove|rollback|secret[[:space:]]+(set|remove))([[:space:]]|$) ]] && { hit "deploy(PaaS)" "$head ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    kubectl|oc|k)
      [[ $rest =~ $dryrun_re ]] && return 1
      [[ $rest =~ (^|[[:space:]])(rollout[[:space:]]+(status|history))([[:space:]]|$) ]] && return 1
      [[ $rest =~ (^|[[:space:]])(apply|create|delete|patch|replace|rollout|scale|autoscale|set|drain|cordon|uncordon|taint|label|annotate|edit|expose|run|exec|cp|attach|debug)([[:space:]]|$) ]] && { hit "deploy(k8s)" "$head ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    helm)
      [[ $rest =~ $dryrun_re ]] && return 1
      [[ $rest =~ (^|[[:space:]])(install|upgrade|uninstall|delete|rollback|push)([[:space:]]|$) ]] && { hit "deploy(k8s)" "helm ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    argocd) [[ $rest =~ (^|[[:space:]])(app[[:space:]]+(sync|create|delete|set|rollback|patch|terminate-op|edit)|proj[[:space:]]+(create|delete)|repo[[:space:]]+add|cluster[[:space:]]+add)([[:space:]]|$) ]] && { hit "deploy(k8s)" "argocd ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    flux) [[ $rest =~ (^|[[:space:]])(reconcile|suspend|resume|create|delete|install|uninstall|bootstrap|push)([[:space:]]|$) ]] && { hit "deploy(k8s)" "flux ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    istioctl) [[ $rest =~ (^|[[:space:]])(install|upgrade|uninstall)([[:space:]]|$) ]] && { hit "deploy(k8s)" "istioctl ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    nomad) [[ $rest =~ (^|[[:space:]])(job[[:space:]]+(run|stop|dispatch|revert|promote|scale)|run|stop|node[[:space:]]+drain|system[[:space:]]+gc)([[:space:]]|$) ]] && { hit "deploy(infra)" "nomad ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    consul) [[ $rest =~ (^|[[:space:]])(kv[[:space:]]+(put|delete|import)|config[[:space:]]+(write|delete)|acl)([[:space:]]|$) ]] && { hit "deploy(infra)" "consul ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    vault) [[ $rest =~ (^|[[:space:]])(write|delete|patch|kv[[:space:]]+(put|delete|patch|destroy|undelete)|policy[[:space:]]+write|auth[[:space:]]+(enable|disable|tune)|secrets[[:space:]]+(enable|disable|tune)|token[[:space:]]+(create|revoke)|lease[[:space:]]+revoke|operator)([[:space:]]|$) ]] && { hit "secrets-store" "vault ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    docker|podman|nerdctl)
      [[ $rest =~ (^|[[:space:]])push([[:space:]]|$) ]] && { hit "publish(registry)" "$head push" "$seg" "$rest"; return 0; }
      [[ $rest =~ (^|[[:space:]])(buildx[[:space:]]+build|build)[[:space:]].*--push([[:space:]]|$) ]] && { hit "publish(registry)" "$head build --push" "$seg" "$rest"; return 0; }
      [[ $rest =~ (^|[[:space:]])(manifest|compose)[[:space:]]+(.*[[:space:]])?push([[:space:]]|$) ]] && { hit "publish(registry)" "$head ${BASH_REMATCH[2]} push" "$seg" "$rest"; return 0; } ;;
    crane) [[ $rest =~ (^|[[:space:]])(push|copy|cp|delete|tag|mutate|append)([[:space:]]|$) ]] && { hit "publish(registry)" "crane ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    skopeo) [[ $rest =~ (^|[[:space:]])(copy|sync|delete)[[:space:]].*docker:// ]] && { hit "publish(registry)" "skopeo copy" "$seg" "$rest"; return 0; } ;;
    oras) [[ $rest =~ (^|[[:space:]])(push|attach|delete|cp|copy)([[:space:]]|$) ]] && { hit "publish(registry)" "oras ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    gcloud)
      [[ $rest =~ ^[[:space:]]*(auth|config|components|init|info|version|help|topic|feedback|cheat-sheet|survey)([[:space:]]|$) ]] && return 1
      [[ $rest =~ $dryrun_re ]] && return 1
      [[ $rest =~ $gcloud_verb_re ]] && { hit "deploy(cloud)" "gcloud ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    gsutil) [[ $rest =~ (^|[[:space:]])(cp|mv|rsync|rm|mb|rb|setmeta|acl|iam|retention|lifecycle|cors|web)([[:space:]]|$) && $rest =~ gs:// ]] && { hit "deploy(cloud)" "gsutil ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    aws)
      [[ $rest =~ ^[[:space:]]*(configure|sso|login|logout|help|--version)([[:space:]]|$) ]] && return 1
      [[ $rest =~ (^|[[:space:]])--dryrun([[:space:]]|$) ]] && return 1
      if [[ $rest =~ ^[[:space:]]*(s3|s3api)[[:space:]] ]]; then
        [[ $rest =~ $aws_verb_re && $rest =~ s3:// ]] && { hit "deploy(cloud)" "aws s3 ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; }
        return 1
      fi
      [[ $rest =~ $aws_verb_re ]] && { hit "deploy(cloud)" "aws ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    az)
      [[ $rest =~ ^[[:space:]]*(login|logout|account|config|configure|version|upgrade|extension|find|feedback|interactive|self-test)([[:space:]]|$) ]] && return 1
      [[ $rest =~ (^|[[:space:]])(--dry-run|what-if|validate|show|list)([[:space:]]|$) ]] && return 1
      if [[ $rest =~ ^[[:space:]]*rest[[:space:]] ]]; then
        re="(-m|--method)[[:space:]]+${q}?(post|put|patch|delete)"
        [[ $rest =~ $re ]] && { hit "external-api" "az rest ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; }
        return 1
      fi
      [[ $rest =~ $az_verb_re ]] && { hit "deploy(cloud)" "az ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    azcopy) [[ $rest =~ (^|[[:space:]])(copy|cp|sync|remove|rm|make)([[:space:]]|$) && $rest =~ https?:// ]] && { hit "deploy(cloud)" "azcopy ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    rclone) [[ $rest =~ (^|[[:space:]])(copy|copyto|sync|move|moveto|delete|deletefile|purge|mkdir|rmdir|rmdirs|touch|dedupe|cleanup|settier)([[:space:]]|$) && $rest =~ (^|[[:space:]])[A-Za-z0-9_-]{2,}: ]] && { hit "deploy(cloud)" "rclone ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    s3cmd) [[ $rest =~ (^|[[:space:]])(put|sync|del|rm|mb|rb|cp|mv|modify|setacl|setpolicy)([[:space:]]|$) ]] && { hit "deploy(cloud)" "s3cmd ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    vercel)
      sub=${rest#"${rest%%[![:space:]]*}"}
      [[ $sub =~ ^(dev|build|login|logout|whoami|ls|list|inspect|logs|link|pull|switch|teams|telemetry|help|init|bisect|--version|-v|--help|-h|env[[:space:]]+(ls|list|pull)|project[[:space:]]+(ls|list)|domains[[:space:]]+(ls|list|inspect)|certs[[:space:]]+(ls|list)|dns[[:space:]]+(ls|list)|alias[[:space:]]+(ls|list)|integration[[:space:]]+(list|balance|open))([[:space:]]|$) ]] && return 1
      hit "deploy(PaaS)" "vercel ${sub%%[[:space:]]*}" "$seg" "$rest"; return 0 ;;
    netlify|ntl)
      [[ $rest =~ (^|[[:space:]])(deploy|env:set|env:unset|env:import|env:clone|sites:create|sites:delete|functions:invoke|api|addons:create|addons:delete|blobs:set|blobs:delete|unlink|link|switch|recipes)([[:space:]]|$) ]] && { hit "deploy(PaaS)" "$head ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    firebase)
      [[ $rest =~ (^|[[:space:]])(deploy|hosting:channel:deploy|hosting:disable|hosting:clone|functions:delete|functions:config:set|functions:config:unset|firestore:delete|database:set|database:push|database:remove|database:update|apphosting:rollouts:create|apphosting:backends:create|apphosting:backends:delete|appdistribution:distribute|ext:install|ext:uninstall|ext:configure|remoteconfig:rollback|crashlytics:symbols:upload)([[:space:]]|$) ]] && { hit "deploy(PaaS)" "firebase ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    fly|flyctl)
      [[ $rest =~ (^|[[:space:]])(deploy|launch|scale|secrets[[:space:]]+(set|unset|import|deploy)|machines?[[:space:]]+(run|start|stop|destroy|update|kill|clone|restart)|apps[[:space:]]+(create|destroy|restart)|volumes?[[:space:]]+(create|destroy|extend)|releases?[[:space:]]+rollback|certs?[[:space:]]+(add|remove|create|delete)|ips[[:space:]]+(allocate|release)|postgres[[:space:]]+(create|attach|detach)|redis[[:space:]]+create|regions[[:space:]]+(add|remove|set)|ssh[[:space:]]+console|console)([[:space:]]|$) ]] && { hit "deploy(PaaS)" "$head ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    heroku)
      [[ $rest =~ (^|[[:space:]])(container:push|container:release|releases:rollback|rollback|config:set|config:unset|ps:scale|ps:restart|ps:stop|ps:kill|apps:create|apps:destroy|apps:rename|run|pipelines:promote|builds:create|addons:create|addons:destroy|addons:attach|addons:detach|domains:add|domains:remove|maintenance:on|maintenance:off|pg:reset|pg:push|pg:pull|pg:promote|git:push|labs:enable)([[:space:]]|$) ]] && { hit "deploy(PaaS)" "heroku ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    railway) [[ $rest =~ (^|[[:space:]])(up|deploy|redeploy|down|delete|variables[[:space:]]+(set|delete)|volume|domain|service[[:space:]]+delete)([[:space:]]|$) ]] && { hit "deploy(PaaS)" "railway ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    wrangler)
      [[ $rest =~ $dryrun_re ]] && return 1
      [[ $rest =~ (^|[[:space:]])(deploy|publish|pages[[:space:]]+(deploy|publish|project[[:space:]]+(create|delete))|kv:key[[:space:]]+(put|delete)|kv[[:space:]]+(key|bulk)[[:space:]]+(put|delete)|r2[[:space:]]+(object[[:space:]]+(put|delete)|bucket[[:space:]]+(create|delete))|secret[[:space:]]+(put|delete|bulk)|versions[[:space:]]+(upload|deploy)|rollback|delete|queues[[:space:]]+(create|delete)|d1[[:space:]]+(create|delete)|d1[[:space:]]+(execute|migrations[[:space:]]+apply)[^;]*--remote)([[:space:]]|$) ]] && { hit "deploy(PaaS)" "wrangler ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    eb) [[ $rest =~ (^|[[:space:]])(deploy|create|terminate|scale|setenv|swap|restore|abort|clone)([[:space:]]|$) ]] && { hit "deploy(PaaS)" "eb ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    copilot) [[ $rest =~ (^|[[:space:]])((svc|env|app|job|pipeline|task)[[:space:]]+(deploy|delete|run|exec|package)|deploy)([[:space:]]|$) ]] && { hit "deploy(PaaS)" "copilot(AWS) ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    kamal) [[ $rest =~ (^|[[:space:]])(deploy|redeploy|rollback|setup|remove|app[[:space:]]+(boot|start|stop|exec|remove)|env[[:space:]]+push|accessory|traefik|proxy|server[[:space:]]+(bootstrap|exec)|lock)([[:space:]]|$) ]] && { hit "deploy(PaaS)" "kamal ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    cap|capistrano|mina) [[ $rest =~ (deploy|production|staging) ]] && { hit "deploy(PaaS)" "$head ${rest:0:30}" "$seg" "$rest"; return 0; } ;;
    ansible-playbook)
      [[ $rest =~ (^|[[:space:]])(--check|-C)([[:space:]]|$) ]] && return 1
      hit "deploy(config-mgmt)" "ansible-playbook" "$seg" "$rest"; return 0 ;;
    ansible)
      re="(^|[[:space:]])-m[[:space:]]+${q}?(shell|command|raw|script|copy|template|file|lineinfile|blockinfile|apt|yum|dnf|pip|service|systemd|user|group|git|unarchive|synchronize)([[:space:]\"']|$)"
      [[ $rest =~ $re ]] && { hit "deploy(config-mgmt)" "ansible -m ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    amplify) [[ $rest =~ (^|[[:space:]])(push|publish|delete|env[[:space:]]+(add|remove))([[:space:]]|$) ]] && { hit "deploy(PaaS)" "amplify ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    supabase) [[ $rest =~ (^|[[:space:]])(db[[:space:]]+push|functions[[:space:]]+deploy|secrets[[:space:]]+(set|unset)|projects[[:space:]]+(create|delete)|branches[[:space:]]+(create|delete|merge)|db[[:space:]]+reset[^;]*--linked)([[:space:]]|$) ]] && { hit "deploy(PaaS)" "supabase ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    npm)
      [[ $rest =~ $dryrun_re ]] && return 1
      [[ $rest =~ (^|[[:space:]])(publish|unpublish|deprecate|dist-tag[[:space:]]+(add|rm)|owner[[:space:]]+(add|rm)|access|token[[:space:]]+(create|revoke)|hook)([[:space:]]|$) ]] && { hit "publish(package)" "npm ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    pnpm) [[ $rest =~ $dryrun_re ]] && return 1
      [[ $rest =~ (^|[[:space:]])(publish|unpublish|deprecate)([[:space:]]|$) ]] && { hit "publish(package)" "pnpm ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    yarn) [[ $rest =~ $dryrun_re ]] && return 1
      [[ $rest =~ (^|[[:space:]])(publish|npm[[:space:]]+publish|npm[[:space:]]+tag[[:space:]]+add)([[:space:]]|$) ]] && { hit "publish(package)" "yarn ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    cargo) [[ $rest =~ $dryrun_re ]] && return 1
      [[ $rest =~ (^|[[:space:]])(publish|yank|owner)([[:space:]]|$) ]] && { hit "publish(package)" "cargo ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    twine) [[ $rest =~ (^|[[:space:]])upload([[:space:]]|$) ]] && { hit "publish(package)" "twine upload" "$seg" "$rest"; return 0; } ;;
    flit|poetry|uv|hatch|pdm|pub|conan) [[ $rest =~ $dryrun_re ]] && return 1
      [[ $rest =~ (^|[[:space:]])(publish|upload)([[:space:]]|$) ]] && { hit "publish(package)" "$head ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    dart|flutter) [[ $rest =~ $dryrun_re ]] && return 1
      [[ $rest =~ (^|[[:space:]])pub[[:space:]]+publish([[:space:]]|$) ]] && { hit "publish(package)" "$head pub publish" "$seg" "$rest"; return 0; } ;;
    python|python3|py) [[ $rest =~ ^[[:space:]]*(-m[[:space:]]+(twine[[:space:]]+upload|flit[[:space:]]+publish|hatch[[:space:]]+publish)|setup\.py[[:space:]]+(upload|register)) ]] && { hit "publish(package)" "python ${BASH_REMATCH[1]}" "$seg" "$rest"; return 0; } ;;
    gem) [[ $rest =~ (^|[[:space:]])(push|yank|owner)([[:space:]]|$) ]] && { hit "publish(package)" "gem ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    rake) [[ $rest =~ (^|[[:space:]])release([[:space:]]|$) ]] && { hit "publish(package)" "rake release" "$seg" "$rest"; return 0; } ;;
    bundle) [[ $rest =~ (^|[[:space:]])exec[[:space:]]+rake[[:space:]]+release([[:space:]]|$) ]] && { hit "publish(package)" "rake release" "$seg" "$rest"; return 0; } ;;
    dotnet) [[ $rest =~ (^|[[:space:]])nuget[[:space:]]+(push|delete)([[:space:]]|$) ]] && { hit "publish(package)" "dotnet nuget ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    nuget) [[ $rest =~ (^|[[:space:]])(push|delete)([[:space:]]|$) ]] && { hit "publish(package)" "nuget ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    mvn|mvnw) [[ $rest =~ (^|[[:space:]])(deploy|release:perform|release:prepare|nexus-staging:[a-z-]+)([[:space:]]|$) ]] && { hit "publish(package)" "mvn ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    gradle|gradlew) [[ $rest =~ (^|[[:space:]])(publish[A-Za-z]*|uploadArchives|bintrayUpload|closeAndReleaseRepository|jreleaser[A-Za-z]*)([[:space:]]|$) ]] && { hit "publish(package)" "gradle ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    goreleaser) [[ $rest =~ (^|[[:space:]])release([[:space:]]|$) && ! $rest =~ (--snapshot|--skip-publish|--skip[[:space:]=]+[a-z,]*publish) ]] && { hit "publish(package)" "goreleaser release" "$seg" "$rest"; return 0; } ;;
    cabal|stack) [[ $rest =~ (^|[[:space:]])upload([[:space:]]|$) ]] && { hit "publish(package)" "$head upload" "$seg" "$rest"; return 0; } ;;
    mix) [[ $rest =~ (^|[[:space:]])hex\.publish([[:space:]]|$) ]] && { hit "publish(package)" "mix hex.publish" "$seg" "$rest"; return 0; } ;;
    gh)
      [[ $rest =~ (^|[[:space:]])(release[[:space:]]+(create|upload|edit|delete|delete-asset)|pr[[:space:]]+merge|workflow[[:space:]]+(run|enable|disable)|run[[:space:]]+(rerun|cancel|delete)|repo[[:space:]]+(create|delete|edit|rename|archive|unarchive|sync)|secret[[:space:]]+(set|delete|remove)|variable[[:space:]]+(set|delete)|ruleset[[:space:]]+(create|edit|delete)|label[[:space:]]+(create|delete|edit|clone)|gpg-key[[:space:]]+(add|delete)|ssh-key[[:space:]]+(add|delete)|codespace[[:space:]]+(create|delete|rebuild)|cache[[:space:]]+delete)([[:space:]]|$) ]] && { hit "external-api(GitHub)" "gh ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; }
      re="((-X|--method)[[:space:]=]+${q}?(POST|PUT|PATCH|DELETE)|(^|[[:space:]])(-f|-F|--field|--raw-field|--input)([[:space:]=]|$))"
      [[ $rest =~ ^[[:space:]]*api[[:space:]] && $rest =~ $re ]] && { hit "external-api(GitHub)" "gh api(書込メソッド)" "$seg" "$rest"; return 0; } ;;
    glab) [[ $rest =~ (^|[[:space:]])(mr[[:space:]]+merge|release[[:space:]]+(create|upload|delete)|ci[[:space:]]+(run|retry|cancel)|variable[[:space:]]+(set|delete)|repo[[:space:]]+(create|delete|archive))([[:space:]]|$) ]] && { hit "external-api(GitLab)" "glab ${BASH_REMATCH[2]}" "$seg" "$rest"; return 0; } ;;
    curl)
      if [[ $rest =~ $http_mut_re ]]; then
        if [[ $rest =~ $url_re ]]; then [[ ${BASH_REMATCH[1]} =~ $loopback_re ]] && return 1; fi
        hit "external-api(HTTP 書込)" "curl(POST/PUT/PATCH/DELETE または --data/--upload)" "$seg" "$rest"; return 0
      fi ;;
    wget)
      if [[ $rest =~ (--post-data|--post-file|--body-data|--body-file|--method=(POST|PUT|PATCH|DELETE)) ]]; then
        if [[ $rest =~ $url_re ]]; then [[ ${BASH_REMATCH[1]} =~ $loopback_re ]] && return 1; fi
        hit "external-api(HTTP 書込)" "wget(書込メソッド)" "$seg" "$rest"; return 0
      fi ;;
    http|https|xh|httpie)
      if [[ $rest =~ (^|[[:space:]])(POST|PUT|PATCH|DELETE)[[:space:]] ]]; then
        sub=${BASH_REMATCH[2]}
        if [[ $rest =~ $url_re ]]; then [[ ${BASH_REMATCH[1]} =~ $loopback_re ]] && return 1; fi
        hit "external-api(HTTP 書込)" "$head $sub" "$seg" "$rest"; return 0
      fi ;;
    invoke-webrequest|invoke-restmethod|iwr|irm)
      if [[ $rest =~ $ps_http_mut_re ]]; then
        if [[ $rest =~ $url_re ]]; then [[ ${BASH_REMATCH[1]} =~ $loopback_re ]] && return 1; fi
        hit "external-api(HTTP 書込)" "$head(-Method Post/Put/Patch/Delete または -Body)" "$seg" "$rest"; return 0
      fi ;;
    start-bitstransfer)
      re="-TransferType[[:space:]:]+${q}?Upload"
      [[ $rest =~ $re ]] && { hit "external-api(HTTP 書込)" "Start-BitsTransfer Upload" "$seg" "$rest"; return 0; } ;;
    sendmail|mailx|mutt|send-mailmessage) hit "external-send(mail)" "$head" "$seg" "$rest"; return 0 ;;
    mail) [[ $rest =~ (^|[[:space:]])-s[[:space:]] ]] && { hit "external-send(mail)" "mail -s" "$seg" "$rest"; return 0; } ;;
    ssh)
      [[ $rest =~ (^|[[:space:]])(-T[[:space:]]+git@|-G([[:space:]]|$)|-Q[[:space:]]|-V([[:space:]]|$)|-O[[:space:]]+check) ]] && return 1
      hit "remote-shell" "ssh" "$seg" "$rest"; return 0 ;;
    ssh-copy-id) hit "remote-shell" "ssh-copy-id" "$seg" "$rest"; return 0 ;;
    scp|sftp|rsync)
      [[ $rest =~ $remote_copy_re ]] && { hit "remote-copy" "$head" "$seg" "$rest"; return 0; } ;;
  esac
  # .NET / PowerShell のアップロード API(セグメント内のどこにあっても)
  if [[ $seg =~ (\[(System\.)?Net\.WebClient\]|\.Upload(String|File|Data|Values)\() ]]; then hit "external-api(HTTP 書込)" "WebClient.Upload*" "$seg" "$rest"; return 0; fi
  return 1
}

# ---- セグメント(; & | 改行)ごとに分類。上限 48 セグメント(49 以上は評価前に ask=fail-closed。timeout 5s 内で
#      必ず終える。guard-harness-config-edit の 33 セグメント上限と同じ流儀。SC-5) ----
segs=()
{
  local_ifs=$IFS
  IFS=$';|&\r\n'
  read -r -d '' -a segs <<< "$cmdf" || true
  IFS=$local_ifs
}
n=0
for seg in "${segs[@]}"; do
  [[ -z "${seg//[[:space:]]/}" ]] && continue
  n=$((n+1))
  if [[ $n -gt 48 ]]; then emit "segment-cap" "セグメント数が上限(48)を超過(検査しきれない分は評価前に確認=fail-closed。SC-5)" "分割して実行してください" "$cmdf"; fi
  if classify_segment "$seg"; then
    # 理由文の抜粋は _log.sh の redaction(hook_log_redact = privacy-patterns.json。無ければそのまま)を通す
    excerpt=$seg
    if type hook_log_redact >/dev/null 2>&1; then hook_log_redact "$excerpt"; excerpt=$_LOG_RED; fi
    excerpt=${excerpt#"${excerpt%%[![:space:]]*}"}
    emit "$K_KIND" "$K_WHAT" "$K_WHERE" "$excerpt"
  fi
done

printf '%s\n' '{"continue": true}'

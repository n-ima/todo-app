#!/usr/bin/env bash
# PreToolUse hook: ハーネス自体の運用ルール(エージェント定義/フック/AGENTS.md等)への
# 無断編集をdenyする。プロンプトインジェクションによる自己権限昇格・ガードレール解除を防ぐ。
# 加えて、Bash/PowerShell ツール(ペイロードに command がある場合)のコマンド文字列を検査し、
# 保護対象パスが「読み取り専用の文脈」以外に現れたら ask を返す(第5回: C-1 封鎖)。
#   旧実装(第4回)は「リダイレクト / tee / cp・mv / sed -i / Set-Content …」の固定 allowlist で、
#   python -c / node -e / git apply / git checkout -- / dd / install / mklink / ln -s / perl -pi /
#   [IO.File]:: / 変数間接 / ヒアドキュメント の全ベクタが素通りした(2026-08-31 監査 C-1、
#   2026-09-09 再監査 RG-1/SC-4 で 20 ベクタ再現)。本実装は判定を反転し、
#   (1) 保護パスを含むコマンドは全セグメントの先頭語が読み取り専用語(cat/grep/head/tail/rg/
#       find/ls/diff/wc/stat/file/git diff|log|show|blame/type/Get-Content/Select-String 等)で、
#       保護パスへのリダイレクト・変数代入・書き込み語(python/node/perl/awk/sed/dd/install/cp/mv/
#       tee/rsync/mklink/ln/git apply|checkout|restore|…/Set-Content/[IO.File]:: 等)を含まない
#       場合だけ allow、それ以外は ask。
#   (2) デコードパイプ・難読化(base64 -d | sh、-EncodedCommand、Invoke-Expression、\x エスケープ
#       等)と、パッチ適用系 git(apply/am/read-tree/update-index/stash pop)は保護パス不在でも ask。
# deny でなく ask なのは、読み取り(cat/grep)や言及との誤検知を人間が即時に解消できるようにするため。
# 注意: .github/skills/ は動的なSkill追加を許容するため原則対象外(request-routing/gate-checkのみ例外)。
# ただし skills 配下でも権限系 frontmatter(hooks:/allowed-tools:/shell:/disable-model-invocation:)の
# 追加と hooks/hooks.json の書込は ask(SC-2)。
# パスの取り出しはJSON解析で行い、Windowsの \ 区切りを / に正規化してから照合する
# (素朴なgrep抽出 + / 前提パターンでは \ 区切りパスに一致せず fail-open していた)。
# パス候補は _paths.sh(D072)で file_path/filePath/path/uri/notebook_path/apply_patch 本文から
# 網羅収集する(H-2)。読み取り系ツール名(readFile 等)は冒頭で除外する(CP-1。VS Code は
# matcher を無視して全ツールで発火するため、読取まで deny すると PLATFORM.md すら読めなくなる)。
# 8.3 短縮名(GITHUB~1/hooks/x 等)は _paths.sh の展開で長形式に直してから照合する(H-10/RG-7 の横展開)。
# 実行時間(SC-5): フックの timeout 5s を超えると fail-open で素通りするため、候補・セグメントごとの
# 照合はすべて bash 組み込み(=~ / パラメータ展開)で行い外部プロセスを起動しない(Windows 実測
# 2026-09-10: grep/sed 版は 1 セグメント約 370ms で 16 セグメントが 6.5s、組み込み版は 32 セグメントでも
# 基本コスト 1.1s 前後)。それでも検査しきれない量(33 セグメント超)は評価前に ask に倒す(fail-closed)。
input=$(cat)

# 共通ライブラリ(無ければ従来の単一フィールド判定だけで動く=fail-open)
# shellcheck source=_paths.sh
source "$(dirname "$0")/_paths.sh" 2>/dev/null || true
# shellcheck source=_log.sh
source "$(dirname "$0")/_log.sh" 2>/dev/null || true
type hook_log >/dev/null 2>&1 || hook_log() { :; }
# 1 回解析(A6-20): 以降の get_tool_name / collect_path_candidates / collect_new_text はキャッシュを返す
type parse_hook_input >/dev/null 2>&1 && parse_hook_input "$input"

# 以降の [[ =~ ]] / [[ == ]] / case はすべて大文字小文字非依存(grep -i と等価)。Windows のパスと
# コマンド語(Get-Content / TYPE / AGENTS.MD 等)を小文字化のための外部プロセス無しで照合するため。
shopt -s nocasematch

# ---- 読み取り系ツールの除外 (CP-1 / D059) ----
tname=""
type get_tool_name >/dev/null 2>&1 && tname=$(get_tool_name "$input")
if type is_read_only_tool >/dev/null 2>&1 && is_read_only_tool "$tname"; then
  hook_log allow "read-tool:$tname"
  printf '%s\n' '{"continue": true}'
  exit 0
fi

file=""
if [[ -n "${_HOOK_PARSED:-}" ]]; then
  file=$_HOOK_FILE
else
  # 解析器(jq/node/python)がすべて無い環境だけ従来のgrep抽出にフォールバック
  file=$(printf '%s' "$input" | grep -oE '"(file_path|filePath|path|notebook_path)"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
fi
# \ 区切り(Windows)を / に正規化。grepフォールバック経由のJSONエスケープ済み \\ が
# // になるため、連続する / は1つに畳む。
file=${file//\\//}
file=$(printf '%s' "$file" | sed -E 's|/{2,}|/|g')

# Bash/PowerShell ツールのペイロードはファイル系フィールドを持たないため、file が
# 取れなかった場合のみ command を取り出す(取り出しの流儀は guard-dangerous-git.sh と同一。
# grep フォールバックは jq/node/python がすべて無い環境限定=値中の \" で切れる制約も同じ)。
cmd=""
if [[ -z "$file" ]]; then
  if [[ -n "${_HOOK_PARSED:-}" ]]; then
    cmd=$_HOOK_COMMAND
  else
    cmd=$(printf '%s' "$input" | grep -oE '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
  fi
fi

# .github/prompts/ も正レイヤ(起動指示)のため保護対象(D046。スキルは動的追加のため原則対象外)
# 例外: request-routing/gate-check はゲート契約の実体のため、動的追加を許すスキルの中でこの2つだけ保護する。
# .github/harness/PLATFORM.md と .github/instructions/ は D050 で AGENTS.md から移設した規範の実体
# のため保護対象(第2回監査で保護非対称を検出)。
# 第5回(2026-09-09 再監査 SC-1/SC-6/MP-3): 常駐ロード面の新経路 .claude/rules/、Copilot CLI の
# 高権限設定 .github/copilot/(settings.json / settings.local.json)、常駐指示 .github/copilot-instructions.md /
# CLAUDE.local.md / GEMINI.md、役割別モデル方針の正 .github/harness/model-policy.yml を追加
# (.claude/settings.json の permissions.deny・validate の CROSS_ITEMS・CODEOWNERS と 4 面同時)。
# 第7波(2026-09-17 IA-20260831-11): 外部 Skill / MCP のロック .github/harness/external-lock.json を保護対象に追加
# (deny / CODEOWNERS / CROSS_ITEMS は統合時の断片)。
protected_pattern='(^|/)\.github/agents/|(^|/)\.github/hooks/|(^|/)\.github/workflows/|(^|/)\.github/prompts/|(^|/)\.github/harness/PLATFORM\.md$|(^|/)\.github/harness/model-policy\.yml$|(^|/)\.github/harness/external-lock\.json$|(^|/)\.github/instructions/|(^|/)\.github/copilot/|(^|/)\.github/copilot-instructions\.md$|(^|/)AGENTS\.md$|(^|/)CLAUDE\.md$|(^|/)CLAUDE\.local\.md$|(^|/)GEMINI\.md$|(^|/)plugin\.json$|(^|/)\.vscode/settings\.json$|(^|/)\.claude/settings(\.local)?\.json$|(^|/)\.claude/agents/|(^|/)\.claude/commands/|(^|/)\.claude/rules/|(^|/)\.agents/workflows/|(^|/)\.github/skills/(request-routing|gate-check)/|(^|/)\.claude/skills/(request-routing|gate-check)/'

# skills 配下(中核2スキル以外も)で ask にする書込(SC-2): hooks/hooks.json への書込、および
# 権限系 frontmatter の**拡張**(第7波 A7-G-3。2026-09-17)。plugin.json(.claude-plugin/plugin.json を含む)は
# protected_pattern で deny 済み。
# 権限系キーは公式仕様(Claude Code skills リファレンス / VS Code agent-skills / agentskills.io。2026-09-17 取得)から列挙し、
# guard-config-change.py の GRANT_KEYS / BOOL_EXPAND / RESTRICT_KEYS・.ps1 と同じ集合に保つ(selftest 両系で意味照合):
#   付与系(値のトークン集合が増えたら拡張): allowed-tools tools permissions permission-mode hooks context agent shell mcp mcp-servers
#   真偽値: disable-model-invocation true→false / user-invocable false→true が拡張(既定値の明示は拡張でない)
#   制限系: disallowed-tools の削除(Write / 置換元にあって置換先に無い)・縮小が拡張
# 旧内容はディスク上の現ファイル(無ければ新規=空)、置換元は old_string(_paths.sh の collect_old_text)。
# 縮小・同値の書き直しは allow(読み取りは冒頭の読取除外で allow)。
skills_pattern='(^|/)(\.claude|\.github)/skills/'
skills_hooks_pattern='(^|/)(\.claude|\.github)/skills/([^/]+/)+hooks/hooks\.json$'
privileged_fm='^[[:space:]]*(allowed-tools|tools|permissions|permission-?mode|hooks|context|agent|shell|mcp|mcp-?servers|disable-model-invocation|user-invocable|disallowed-tools)[[:space:]]*:'
fm_grant_keys='allowed-tools tools permissions permission-mode permissionmode hooks context agent shell mcp mcp-servers mcpservers'

# fm_value_into <text> <key>: 権限系キーの値ブロブ(キー行の値 + 続く字下げ行 / リスト行)を _FM_VAL に、
# キーの有無を _FM_HAS(0/1)に返す(guard-config-change.py の fm_values と同じ規則。bash 組み込みのみ)
_FM_VAL=""; _FM_HAS=0
fm_value_into() {
  local text="$1" key="$2" line acc="" in=0 re_key re_cont='^([[:space:]]|-)'
  re_key="^[[:space:]]*${key}[[:space:]]*:(.*)$"
  _FM_VAL=""; _FM_HAS=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    if [[ $in -eq 1 ]]; then
      if [[ "$line" == "---" || ! $line =~ $re_cont ]]; then in=0; else acc="${acc}"$'\n'"${line}"; continue; fi
    fi
    if [[ $line =~ $re_key ]]; then _FM_HAS=1; acc="${acc}"$'\n'"${BASH_REMATCH[1]}"; in=1; fi
  done <<EOF_FM
$text
EOF_FM
  _FM_VAL=$acc
}
# fm_tokens_into <blob>: トークン集合を "|tok|tok|" 形で _FM_TOKS に返す(空白・カンマ・括弧・引用符で分割。
# YAML リストの `-` 単独は除く。比較は nocasematch で大文字小文字非依存。glob 展開は止める)
_FM_TOKS=""
fm_tokens_into() {
  local s="$1" t acc="|" was_f=0
  [[ $- == *f* ]] && was_f=1
  set -f
  s=${s//[,\[\]\"\'\`]/ }; s=${s//$'\n'/ }; s=${s//$'\t'/ }
  for t in $s; do
    [[ "$t" == "-" ]] && continue
    [[ "$acc" == *"|$t|"* ]] || acc="${acc}${t}|"
  done
  [[ $was_f -eq 0 ]] && set +f
  _FM_TOKS=$acc
}
# fm_subset <A> <B>: A ⊆ B なら 0("|a|b|" 形)
fm_subset() {
  local b="$2" t rest=${1#|}
  while [[ -n "$rest" ]]; do
    t=${rest%%|*}; rest=${rest#*|}
    [[ -z "$t" ]] && continue
    [[ "$b" == *"|$t|"* ]] || return 1
  done
  return 0
}
# fm_truthy <blob>: 先頭トークンが true/yes/on/1 なら 0
fm_truthy() {
  local s="$1" t was_f=0
  [[ $- == *f* ]] && was_f=1
  set -f
  s=${s//[,\[\]\"\'\`#]/ }; s=${s//$'\n'/ }
  for t in $s; do
    [[ $was_f -eq 0 ]] && set +f
    case "$t" in true|yes|on|1) return 0 ;; *) return 1 ;; esac
  done
  [[ $was_f -eq 0 ]] && set +f
  return 1
}
# fm_expansion <旧内容> <新内容> <full 0/1> <置換元>: 権限を拡張するキーの一覧を _FM_EXP(空白区切り。空=拡張なし)に返す。
# full=1 は新内容がファイル全体(Write)、0 は置換断片(Edit の new_string。無いキーは変更なし)
_FM_EXP=""
fm_expansion() {
  local old="$1" new="$2" full="$3" oldfrag="$4" k ntoks otoks exp=""
  for k in $fm_grant_keys; do
    fm_value_into "$new" "$k"
    [[ $_FM_HAS -eq 1 ]] || continue
    fm_tokens_into "$_FM_VAL"; ntoks=$_FM_TOKS
    fm_value_into "$old" "$k"
    if [[ $_FM_HAS -eq 0 ]]; then
      [[ "$ntoks" != "|" ]] && exp="${exp} ${k}"
    else
      fm_tokens_into "$_FM_VAL"; fm_subset "$ntoks" "$_FM_TOKS" || exp="${exp} ${k}"
    fi
  done
  fm_value_into "$new" "disable-model-invocation"
  if [[ $_FM_HAS -eq 1 ]] && ! fm_truthy "$_FM_VAL"; then
    fm_value_into "$old" "disable-model-invocation"
    if [[ $_FM_HAS -eq 1 ]] && fm_truthy "$_FM_VAL"; then exp="${exp} disable-model-invocation"; fi
  fi
  fm_value_into "$new" "user-invocable"
  if [[ $_FM_HAS -eq 1 ]] && fm_truthy "$_FM_VAL"; then
    fm_value_into "$old" "user-invocable"
    if [[ $_FM_HAS -eq 1 ]] && ! fm_truthy "$_FM_VAL"; then exp="${exp} user-invocable"; fi
  fi
  fm_value_into "$old" "disallowed-tools"
  if [[ $_FM_HAS -eq 1 ]]; then
    fm_tokens_into "$_FM_VAL"; otoks=$_FM_TOKS
    fm_value_into "$new" "disallowed-tools"
    if [[ $_FM_HAS -eq 0 ]]; then
      if [[ "$full" -eq 1 ]]; then
        exp="${exp} disallowed-tools"
      else
        fm_value_into "$oldfrag" "disallowed-tools"; [[ $_FM_HAS -eq 1 ]] && exp="${exp} disallowed-tools"
      fi
    else
      fm_tokens_into "$_FM_VAL"; fm_subset "$otoks" "$_FM_TOKS" || exp="${exp} disallowed-tools"
    fi
  fi
  _FM_EXP=${exp# }
}

deny_out() { # $1=logタグ
  hook_log deny "$1"
  printf '%s\n' '{"continue": true, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "ハーネスの運用ルール自体(agents/hooks/workflows/prompts/commands/rules/AGENTS.md/CLAUDE.md/plugin.json/settings.json/copilot 設定)はエージェントが自動で書き換えません。変更が必要な場合は人間が直接編集するか、明示的な指示のもとで行ってください。"}}'
  exit 0
}
ask_out() { # $1=logタグ $2=systemMessage $3=理由
  hook_log ask "$1"
  printf '{"continue": true, "systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "%s"}}\n' "$2" "$3"
  exit 0
}

# ---- ファイル系ツール: パス候補の網羅判定 (H-2 / D072) ----
cands="$file"
if [[ -z "$cmd" ]] && type collect_path_candidates >/dev/null 2>&1; then
  rc=$(collect_path_candidates "$input" auto)
  [[ -n "$rc" ]] && cands="${cands}${cands:+
}${rc}"
fi
# 照合は bash 組み込みの =~(nocasematch=grep -i 相当)で行い候補ごとの外部プロセスを避ける
# (旧実装は 1 候補 3 grep で、候補 64 件のペイロードだと Windows で timeout 5s に届き fail-open した)。
# 8.3 短縮名(GITHUB~1/hooks/x、CLAUDE~1/rules/x、C:/…/TMP~1/… 等)は _paths.sh の展開で長形式に
# 直してから照合する(H-10/RG-7 の横展開。展開できなければ字面のまま=fail-open。~数字を含まない候補は無変更)。
has83=0
type expand_candidate_83_into >/dev/null 2>&1 && has83=1
skill_hit=""
while IFS= read -r c; do
  [[ -z "$c" ]] && continue
  if [[ $has83 -eq 1 ]]; then expand_candidate_83_into "$c"; c=$_PATHS_LONG; fi
  if [[ $c =~ $protected_pattern ]]; then
    deny_out "$c"
  fi
  if [[ $c =~ $skills_hooks_pattern ]]; then
    ask_out "skill-hooks-json:$c" "スキル配下の hooks/hooks.json への書き込みを検知しました(スキル経由の任意コマンド実行=自己権限昇格の経路)。" "skills 配下の hooks/hooks.json はスキル読込時に任意コマンドを実行できる設定です。人間の明示指示による追加であれば許可してください(SC-2)。"
  fi
  if [[ -z "$skill_hit" && $c =~ $skills_pattern ]]; then
    skill_hit="$c"
  fi
done <<EOF_CANDS
$cands
EOF_CANDS
if [[ -n "$skill_hit" ]] && type collect_new_text >/dev/null 2>&1; then
  newtext=$(collect_new_text "$input")
  oldfrag=""
  type collect_old_text >/dev/null 2>&1 && oldfrag=$(collect_old_text "$input")
  # 置換元(old_string)に権限系キーがある場合も見る(制限キーだけを消す Edit=new_string が空)
  if [[ -n "$newtext$oldfrag" ]] && printf '%s\n%s' "$newtext" "$oldfrag" | grep -Eiq "$privileged_fm"; then
    # 拡張だけ ask(A7-G-3): 旧内容=ディスク上の現ファイル(無ければ新規=空)、置換元=old_string。
    # Write(old_string 無し)はファイル全体の置換(full=1)、Edit / MultiEdit は断片(full=0)
    oldtext=""; full=1
    [[ -f "$skill_hit" ]] && oldtext=$(<"$skill_hit")
    [[ -n "$oldfrag" ]] && full=0
    fm_expansion "$oldtext" "$newtext" "$full" "$oldfrag"
    if [[ -n "$_FM_EXP" ]]; then
      ask_out "skill-privileged-frontmatter(${_FM_EXP// /,}):$skill_hit" "スキル定義への権限系 frontmatter の拡張(${_FM_EXP// /,})を検知しました。" "skills 配下への権限系 frontmatter の追加・拡張(allowed-tools / hooks / context: fork / agent / shell / mcp / permissions、disable-model-invocation を false へ、user-invocable を true へ、disallowed-tools の削除)は、スキル読込時のツール権限拡張・任意コマンド実行につながります。人間の明示指示による追加であれば許可してください(SC-2 / A7-G-3。縮小・同値の書き直しは確認なし)。"
    fi
    hook_log allow "skill-frontmatter-no-expansion:$skill_hit"
  fi
fi

# ---- コマンド文字列内の「保護対象パスへの書き込み」検知(第4回・第5回) ----
# 照合用コピー: 行継続(\ / ` / ^ +改行。H-1: .ps1 と同じ3種)を畳み、重複リダイレクト(2>&1 等)を
# 落とし、\ 区切りを / に正規化する(元の $cmd はログ用に保持)。デコード/難読化の検査は
# \x エスケープを見る必要があるため、\→/ 変換前のコピー(cmdf)に対して行う。
cmdf=""
cmdn=""
if [[ -n "$cmd" ]]; then
  cmdf=${cmd//$'\\\r\n'/ }
  cmdf=${cmdf//$'\\\n'/ }
  cmdf=${cmdf//$'`\r\n'/ }
  cmdf=${cmdf//$'`\n'/ }
  cmdf=${cmdf//$'^\r\n'/ }
  cmdf=${cmdf//$'^\n'/ }
  cmdn=$(printf '%s' "$cmdf" | sed -E 's/[0-9]?>&[0-9]//g; s/&>[[:space:]]*\/dev\/null//g')
  cmdn=${cmdn//\\//}
  cmdn=$(printf '%s' "$cmdn" | sed -E 's|/{2,}|/|g')
fi
# コマンド文字列内の 8.3 短縮名トークン(C:/x/GITHUB~1/hooks/a.sh、GITHUB~1/hooks/a.sh 等)も長形式に
# 展開してから照合する(H-10 の横展開。/ を含む ~数字トークンだけが対象、最大 8 個。失敗時は無変更)。
if [[ -n "$cmdn" && $cmdn == *~[0-9]* ]] && type expand_candidate_83_into >/dev/null 2>&1; then
  re_tok83='[^[:space:]"'"'"'<>|;&=()]*~[0-9]+[^[:space:]"'"'"'<>|;&=()]*'
  rest83=$cmdn; acc83=""; n83=0
  while [[ $n83 -lt 8 && $rest83 =~ $re_tok83 ]]; do
    tok83=${BASH_REMATCH[0]}
    pre83=${rest83%%"$tok83"*}
    rest83=${rest83#*"$tok83"}
    if [[ $tok83 == */* ]]; then expand_candidate_83_into "$tok83"; tok83=$_PATHS_LONG; fi
    acc83="${acc83}${pre83}${tok83}"
    n83=$((n83+1))
  done
  cmdn="${acc83}${rest83}"
fi
# 保護対象パス(protected_pattern と同じ集合のコマンド内出現形)。ディレクトリは末尾 / の無い
# 出現(mklink /J .github\hooks、ln -s x .claude/agents 等のディレクトリ自体への書込)も検知する
# ため、直後が / か境界(英数・._- 以外)であることを要求する。単独ファイル名は直後が英数字なら
# 別名(AGENTS.mdx 等)とみなし不一致。直前境界は各書き込み文脈側(mid)で担保する。
prot_dirs='\.github/(agents|hooks|workflows|prompts|instructions|copilot)(/|$|[^[:alnum:]_.-])|(\.github|\.claude)/skills/(request-routing|gate-check)(/|$|[^[:alnum:]_.-])|\.claude/(agents|commands|rules)(/|$|[^[:alnum:]_.-])|\.agents/workflows(/|$|[^[:alnum:]_.-])'
prot_files='(AGENTS\.md|CLAUDE\.md|CLAUDE\.local\.md|GEMINI\.md|plugin\.json|\.github/harness/PLATFORM\.md|\.github/harness/model-policy\.yml|\.github/harness/external-lock\.json|\.github/copilot-instructions\.md|\.vscode/settings\.json|\.claude/settings(\.local)?\.json)($|[^[:alnum:]_])'
prot_cmd="(${prot_dirs}|${prot_files})"
# mid: 書き込み文脈から保護パスまでの引数読み飛ばし。コマンド区切り(;&|)とリダイレクトは
# 跨がず、保護パス直前が英数字等なら別ファイル名の一部とみなして不一致にする。
mid='([^;&|<>]*[^[:alnum:]_.-])?'
sep='(^|[[:space:];&|(])'
# 書き込み文脈(第4回の固定リスト。第5回の反転判定と併用=多層): リダイレクト先 / tee の引数 /
# cp・mv・copy の第2引数以降 / sed -i の対象 / Set-Content・Out-File・Add-Content・New-Item(ni) の引数。
w_redirect=">>?[|]?[[:space:]]*${mid}${prot_cmd}"
w_tee="${sep}tee[[:space:]]${mid}${prot_cmd}"
w_copy="${sep}(cp|mv|copy)[[:space:]]+(-[^[:space:];&|<>]+[[:space:]]+)*[^[:space:];&|<>]+[[:space:]]${mid}${prot_cmd}"
w_sed="${sep}sed[[:space:]]+([^;&|<>]*[[:space:]])?(-i|--in-place)[^[:space:];&|<>]*[[:space:]]${mid}${prot_cmd}"
w_ps="${sep}(Set-Content|Add-Content|Out-File|New-Item|ni)[[:space:]]${mid}${prot_cmd}"
write_pattern="${w_redirect}|${w_tee}|${w_copy}|${w_sed}|${w_ps}"

# --- 第5回: allowlist の反転 ---
# 読み取り専用の先頭語(小文字比較。ディレクトリ・.exe・( 以降は落として比較する)
ro_heads='^(cat|tac|head|tail|less|more|grep|egrep|fgrep|rg|ag|ack|find|fd|ls|dir|tree|diff|cmp|comm|wc|stat|file|md5sum|sha1sum|sha256sum|shasum|cksum|od|hexdump|xxd|strings|realpath|readlink|basename|dirname|test|\[|\[\[|cygpath|which|where|cd|pushd|popd|echo|printf|true|:|type|findstr|get-content|gc|select-string|sls|get-childitem|gci|get-item|gi|test-path|compare-object|get-filehash|resolve-path|format-hex|measure-object|measure|select-object|select|sort-object|sort|where-object|where|\?|foreach-object|foreach|%|group-object|group|format-list|fl|format-table|ft|out-string|out-host|out-default|write-host|write-output|write-verbose|convertfrom-json|convertto-json|test-json|split-path|join-path|get-location|pwd|gl|get-date|get-command|gcm|\[(system\.)?io\.file\]::(readalltext|readalllines|readallbytes|exists|getlastwritetime)|\[(system\.)?io\.directory\]::(exists|getfiles))$'
# git は読み取り系サブコマンドのみ許可(グローバルオプション -C/-c/--git-dir 等は読み飛ばす)
git_ro='^(diff|log|show|blame|status|grep|ls-files|ls-tree|cat-file|rev-parse|describe|shortlog|check-ignore|check-attr|branch|remote|reflog|for-each-ref|name-rev|rev-list|count-objects|var|version|help)$'
git_skip='^git(\.exe)?[[:space:]]+((-c|-C)[[:space:]]+[^[:space:]]+[[:space:]]+|--(git-dir|work-tree|namespace)=[^[:space:]]+[[:space:]]+|--no-pager[[:space:]]+|-p[[:space:]]+)*'
# 読み取り専用の先頭語であっても、セグメント内にこれらの語(インタプリタ/複製/削除/リンク/
# .NET 書込/コマンド置換 等)があれば書き込み文脈とみなす(小文字比較)
write_words='(^|[^[:alnum:]_.-])(python[0-9.]*|py|node|nodejs|deno|bun|perl|ruby|php|awk|gawk|mawk|nawk|sed|dd|install|cp|mv|copy|move|ren|rename|xcopy|robocopy|tee|rsync|scp|curl|wget|mklink|ln|patch|truncate|shred|rm|del|erase|rmdir|rd|chmod|chown|attrib|icacls|xargs|eval|exec|source|set-content|out-file|add-content|copy-item|move-item|rename-item|new-item|ni|remove-item|ri|clear-content|clc|set-item|set-itemproperty|invoke-expression|iex|invoke-webrequest|iwr|invoke-restmethod|irm|start-process|saps|invoke-command|icm|invoke-item|ii|expand-archive|writealltext|writeallbytes|writealllines|appendalltext|appendalllines|createsymboliclink|createhardlink|copyfile|writefilesync|writefile|appendfilesync|appendfile|renamesync|unlinksync|shutil|subprocess|os\.system|os\.remove|os\.rename|os\.replace|os\.symlink|os\.link|os\.unlink)([^[:alnum:]_-]|$)|open\(|\.write\(|\$\(|`|[[:space:]]-(delete|exec|execdir|ok|okdir)([[:space:]]|$)'
# 先頭のラッパ語(sudo/env/cmd /c/powershell -c/bash -c 等)は読み飛ばして実コマンドを見る
wrappers='^((sudo|doas|command|builtin|time|nice|nohup|env|&)[[:space:]]+|cmd(\.exe)?[[:space:]]+/[ck][[:space:]]+|(powershell|pwsh)(\.exe)?[[:space:]]+(-noprofile[[:space:]]+|-nologo[[:space:]]+|-noninteractive[[:space:]]+|-executionpolicy[[:space:]]+[a-z]+[[:space:]]+)*-c(ommand)?[[:space:]]+|(bash|sh|zsh|dash|ksh)[[:space:]]+-c[[:space:]]+)+["'"'"']?'
# 保護パス不在でも ask: デコードパイプ・難読化・パッチ適用系 git(\→/ 変換前の cmdf に対して照合)
decode_pattern='((base64|base32|base32hex)[^;&|]*(-d|--decode)([[:space:]]|$)|uudecode|xxd[[:space:]]+-r|certutil[^;&|]*-decode|openssl[[:space:]]+(enc|base64)[^;&|]*-d|frombase64string|\[text\.encoding\]::[a-z0-9]+\.getstring|-encodedcommand|-enc(odedcommand)?[[:space:]]+[a-z0-9+/=]{16,}|-e[[:space:]]+[a-z0-9+/=]{16,}|invoke-expression|(^|[^[:alnum:]_.-])iex([^[:alnum:]_-]|$)|(^|[^[:alnum:]_.-])eval([[:space:]]|$)|\$'"'"'[^'"'"']*\\x|\\x[0-9a-f]{2}|\\u[0-9a-f]{4}|\[char\][[:space:]]*[0-9]|\.decode\(|b64decode|codecs\.|rot13|(^|[^[:alnum:]_.-])rev([[:space:]]*\||$)|\|[[:space:]]*(ba|z|da|k)?sh(\.exe)?([[:space:]]+-[a-z]+)*[[:space:]]*($|[;&|)])|\|[[:space:]]*(python[0-9.]*|py|node|deno|bun|perl|ruby|php|pwsh|powershell(\.exe)?|cmd(\.exe)?)([[:space:]]+-[a-z]+)*[[:space:]]*(-[[:space:]]*)?($|[;&|)]))'
git_patch_pattern="${sep}git(\.exe)?[[:space:]]+((-c|-C)[[:space:]]+[^[:space:]]+[[:space:]]+|--(git-dir|work-tree)=[^[:space:]]+[[:space:]]+)*(apply|am|read-tree|update-index|stash[[:space:]]+(pop|apply|branch))([[:space:]]|$)"
# 第7波(R-01。codex 監査 2026-08-31 §4): reparse point(symlink / junction / hardlink)の作成は保護パス不在でも ask。
# 作成後は保護ディレクトリの別名(C:/alias → .github)経由で書けるため、文字列検査では追えない
# (境界は OS sandbox / read-only mount / branch protection=PLATFORM.md「セキュリティの層構造」)。
# ln -s / ln -sf / --symbolic、mklink(cmd /c 経由含む)、New-Item -ItemType SymbolicLink|Junction|HardLink(引数順不同)、
# [IO.File|Directory]::CreateSymbolicLink、os.symlink / os.link、fs.symlink(Sync)。.ps1 と同一集合。
reparse_pattern="${sep}(ln[[:space:]]+(-[a-z]*s[a-z]*|--symbolic)|mklink([[:space:]]|$)|new-item[^;&|]*-itemtype[[:space:]]+(symboliclink|junction|hardlink)|\[(system\.)?io\.(file|directory)\]::createsymboliclink|os\.(symlink|link)\(|fs\.symlink(sync)?\(|symlinkat\()"
# git plumbing / worktree で保護パスを名指しする形(checkout -- <保護パス> / restore / switch / update-index /
# worktree add <保護域内> / hash-object)。反転判定(write-context)でも ask になるが、判定ログのタグと理由文を
# git-plumbing 専用にする(git apply / am / read-tree / update-index / stash pop の path-less 形は上の git_patch_pattern)。
git_plumbing_pattern="${sep}git(\.exe)?[[:space:]]+((-c|-C)[[:space:]]+[^[:space:]]+[[:space:]]+|--(git-dir|work-tree)=[^[:space:]]+[[:space:]]+)*(checkout|restore|switch|update-index|worktree[[:space:]]+add|hash-object)[^;&|]*${prot_cmd}"

# セグメント(; & | 改行で分割)が読み取り専用の文脈かを判定する。0=読み取り専用。
# 照合はすべて bash 組み込み(=~ / パラメータ展開。nocasematch で大文字小文字非依存)で行い
# 外部プロセスを起動しない。旧実装(grep/sed/awk)は 1 セグメントあたり 9〜13 プロセス≈370ms
# (Windows 実測 2026-09-10: 基本 1.1s + 8 セグメントで 3.9s、16 で 6.5s)で、フックの timeout 5s を
# 超えると fail-open で素通りしていた(SC-5)。判定規則そのものは旧実装・.ps1 と同一。
re_assign_prot="^[[:space:]({!]*([a-z_][a-z0-9_]*=[^[:space:]]*[[:space:]]+)*[a-z_][a-z0-9_]*=[^[:space:]]*${prot_cmd}"
re_assign_var='^[[:space:]({!]*(\$[a-z_:][a-z0-9_:]*[[:space:]]*=|set[[:space:]]+[a-z_][a-z0-9_]*=|export[[:space:]]+|setx[[:space:]]+)'
# 先頭の (.*) が最長一致で「最後の >」を捉えるため、末尾側から 1 件ずつリダイレクト先を取り出せる
re_redir_last='(.*)([0-9]?>>?[|]?[[:space:]]*[^[:space:];&|]*)'
re_lead='^[[:space:]({!]+'
re_env='^([a-z_][a-z0-9_]*=[^[:space:]]*[[:space:]]+)*'
segment_is_readonly() {
  local seg="$1" head sub rest t s
  # 代入の右辺に保護パス(f=AGENTS.md; $f='AGENTS.md'; set f=...; export F=...)は書き込み文脈
  [[ $seg =~ $re_assign_prot ]] && return 1
  [[ $seg =~ $re_assign_var ]] && return 1
  # リダイレクト: 宛先が保護パス・変数・不明なら書き込み文脈(/dev/null・nul・リテラルの非保護パスは可)
  rest=$seg
  while [[ $rest =~ $re_redir_last ]]; do
    t=${BASH_REMATCH[2]}; rest=${BASH_REMATCH[1]}
    t=${t#[0-9]}; t=${t#>}; t=${t#>}; t=${t#|}
    t=${t#"${t%%[![:space:]]*}"}
    case "$t" in
      "") return 1 ;;
      /dev/null|nul) continue ;;
      \$*|\"\$*|\'\$*|%*|\"%*|\'%*) return 1 ;;
    esac
    [[ $t =~ $prot_cmd ]] && return 1
  done
  # 書き込み語
  [[ $seg =~ $write_words ]] && return 1
  # 先頭語(空白・括弧・環境変数代入・ラッパ語を読み飛ばし、パス/.exe/( 以降を落とす)
  s=$seg
  [[ $s =~ $re_lead ]] && s=${s:${#BASH_REMATCH[0]}}
  [[ $s =~ $re_env ]] && s=${s:${#BASH_REMATCH[0]}}
  [[ $s =~ $wrappers ]] && s=${s:${#BASH_REMATCH[0]}}
  s=${s#"${s%%[![:space:]]*}"}
  head=${s%%[[:space:]]*}
  head=${head//[\"\']/}
  head=${head%%(*}
  if [[ "$head" != \[* ]]; then head=${head##*/}; fi
  head=${head%.exe}
  [[ -z "$head" ]] && return 1
  if [[ "$head" == "git" ]]; then
    sub=$s
    [[ $sub =~ $git_skip ]] && sub=${sub:${#BASH_REMATCH[0]}}
    sub=${sub#"${sub%%[![:space:]]*}"}
    sub=${sub%%[[:space:]]*}
    [[ $sub =~ $git_ro ]] && return 0
    return 1
  fi
  [[ $head =~ $ro_heads ]] && return 0
  return 1
}

# 保護パスを含むコマンドの全セグメントが読み取り専用か。0=すべて読み取り専用。
# セグメント数が上限(32)を超えるコマンドは検査しきれない(フックの timeout 5s を超えると
# fail-open で素通りする)ため、評価に入る前に数えて「読み取り専用でない」=ask に倒す(fail-closed。
# 旧実装は評価しながら数えていたため 33 セグメントでも 12s かかり上限が効いていなかった)。
command_is_readonly() {
  local seg n=0
  local -a segs
  local IFS=$';|&\r\n'
  read -r -d '' -a segs <<< "$1" || true
  for seg in "${segs[@]}"; do
    [[ -z "${seg//[[:space:]]/}" ]] && continue
    n=$((n+1))
  done
  [[ $n -gt 32 ]] && return 1
  for seg in "${segs[@]}"; do
    [[ -z "${seg//[[:space:]]/}" ]] && continue
    segment_is_readonly "$seg" || return 1
  done
  return 0
}

if [[ -n "$cmdn" ]]; then
  # 判定ログの redaction(URL 埋め込み認証・鍵形式)と先頭 120 字への切り詰めは _log.sh(privacy-patterns.json)が行う。
  # タグは 1 行に畳む(改行・タブは空白)
  safe_cmd=${cmd:0:200}
  safe_cmd=${safe_cmd//[$'\r\n\t']/ }
  if printf '%s' "$cmdn" | grep -Eiq "$write_pattern"; then
    ask_out "$safe_cmd" "コマンドがハーネス保護対象(AGENTS.md/.github/hooks 等)へ書き込む可能性があるため確認します。" "保護対象パスへの書き込みパターン(リダイレクト/tee/cp・mv/sed -i/Set-Content 等)を検知しました。読み取りや言及のみであれば許可してください。"
  fi
  lcf=$(printf '%s' "$cmdf" | tr '[:upper:]' '[:lower:]')
  if printf '%s' "$lcf" | grep -Eq "$decode_pattern"; then
    ask_out "decode-pipe:$safe_cmd" "デコードパイプ・難読化・インタプリタへの標準入力実行(base64 -d | sh、-EncodedCommand、Invoke-Expression、\\\\x エスケープ等)を検知しました。" "内容を機械検査できない実行形式(デコード後実行・難読化・標準入力からのスクリプト実行)です。ハーネス保護対象へ書き込まないことを確認して許可してください(SC-4)。"
  fi
  if printf '%s' "$cmdn" | grep -Eiq "$git_patch_pattern"; then
    ask_out "git-patch:$safe_cmd" "パッチ適用系の git(apply/am/read-tree/update-index/stash pop)を検知しました。" "パッチ本文の宛先はコマンド文字列から判定できません。AGENTS.md や .github/hooks 等のハーネス保護対象を書き換えないことを確認して許可してください(RG-1)。"
  fi
  if printf '%s' "$cmdn" | grep -Eiq "$reparse_pattern"; then
    ask_out "reparse-point:$safe_cmd" "reparse point(symlink / junction / hardlink)の作成を検知しました(ln -s / mklink / New-Item -ItemType SymbolicLink|Junction 等)。" "リンクの作成後はハーネス保護対象(.github/hooks / .claude 等)を別名経由で書き換えられ、文字列検査では追えません。保護ディレクトリを指さない・保護域内に作らないことを確認して許可してください(R-01)。"
  fi
  if printf '%s' "$cmdn" | grep -Eiq "$git_plumbing_pattern"; then
    ask_out "git-plumbing:$safe_cmd" "git plumbing / worktree による保護対象パスの書き換え(checkout -- / restore / update-index / worktree add / hash-object)を検知しました。" "git の checkout -- <保護パス> / restore / update-index / worktree add <保護域内> は AGENTS.md や .github/hooks 等を HEAD やインデックスの内容で上書きします。人間の明示指示であれば許可してください(R-01)。"
  fi
  if [[ $cmdn =~ $prot_cmd ]] && ! command_is_readonly "$cmdn"; then
    ask_out "write-context:$safe_cmd" "コマンドがハーネス保護対象(AGENTS.md/.github/hooks/.claude/settings.json 等)を読み取り専用でない文脈で扱うため確認します。" "保護対象パスが読み取り専用語(cat/grep/head/tail/diff/git diff|log|show/Get-Content 等)以外の文脈(インタプリタ・複製・リンク・変数代入・リダイレクト等)に現れました。読み取りや言及のみであれば許可してください(C-1 封鎖: 判定は allowlist 反転)。"
  fi
fi

printf '%s\n' '{"continue": true}'

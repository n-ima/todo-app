#!/usr/bin/env bash
# PreToolUse hook (Edit|Write|MultiEdit|NotebookEdit): evidence-gated deny(A2-4b。再監査 2026-09-09 RG-13)。
# docs/00-overview/progress.md の done 遷移、または docs/03-implementation/tasks.md への完了マーク
# [x]/[X] の追加を含む書込は、その書込の新内容(追加・変更される行)に証拠 3 点セット
#   (1) 再実行可能なコマンド … バッククォートで囲んだコマンド(`npm test` 等)または「コマンド:」ラベル
#   (2) 出力の要約           … 「→ 12 passed」「結果: OK」「exit 0」「N 件」等
#   (3) 実行日時             … YYYY-MM-DD HH:MM
# が揃っていなければ deny する(理由文に不足項目と書式を示す)。gate-check スキルの「完了マークは必ず
# 完了条件の証拠とセットで付ける」を機械強制する層。判定は「書込の差分」に対して行う:
#   - Edit / MultiEdit: new_string(edits[].new_string)の行のうち old_string に無い行
#   - Write(全文): content の行のうち、ディスク上の現ファイルに無い行(ファイルが無ければ全行)
#   - done / [x] の「追加」は、新内容と旧内容(old_string またはディスクの現ファイル)の件数比較で判定する
#     (既に [x] の行への注記追記や、done 済みフェーズの表の更新では発火しない)
# 役割分担: warn-gate-tamper(PostToolUse・警告のみ)は「承認の有無・独立レビュー記録・形式リント・
# in_progress 遷移」を人に気づかせる層、本フック(PreToolUse・deny)は「証拠の書式が揃っているか」だけを
# 書込前に機械判定する層。証拠の真偽(実行痕跡との一致)は reviewer 観点5 と golden-eval が見る。
# 対象はエージェントのツール呼出だけ(人間の直接編集には発火しない=緊急経路)。
# fail-open: JSON が読めない・新内容が取れない・対象パスでない場合は {"continue": true}。
# .ps1 側の鏡は guard-done-evidence.ps1(同一判定。selftest 両系で固定)。
input=$(cat)

# 共通ライブラリ(D072 / A6-20 / D085): パス候補・新内容の 1 回解析(_paths.sh の parse_hook_input)と判定ログ
# (_log.sh → logs/hook-decisions.jsonl)。無ければ従来の単一フィールド判定・無記録で動く(fail-open)
# shellcheck source=_paths.sh
source "$(dirname "$0")/_paths.sh" 2>/dev/null || true
# shellcheck source=_log.sh
source "$(dirname "$0")/_log.sh" 2>/dev/null || true
type hook_log >/dev/null 2>&1 || hook_log() { :; }
type parse_hook_input >/dev/null 2>&1 && parse_hook_input "$input"
allow() { printf '%s\n' '{"continue": true}'; exit 0; }

# ---- 読み取り系ツールの除外 (CP-1 / D059) ----
tname=""
type get_tool_name >/dev/null 2>&1 && tname=$(get_tool_name "$input")
if type is_read_only_tool >/dev/null 2>&1 && is_read_only_tool "$tname"; then allow; fi

# ---- 対象パス(progress.md / tasks.md)の特定 ----
cands=""
if type collect_path_candidates >/dev/null 2>&1; then
  cands=$(collect_path_candidates "$input" auto)
else
  cands=$(printf '%s' "$input" | grep -oE '"(file_path|filePath|path)"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
  cands=${cands//\\//}
fi
target=""; kind=""
while IFS= read -r c; do
  [[ -z "$c" ]] && continue
  case "$c" in
    *docs/00-overview/progress.md) target="$c"; kind=progress; break ;;
    *docs/03-implementation/tasks.md) target="$c"; kind=tasks; break ;;
  esac
done <<EOF_CANDS
$cands
EOF_CANDS
[[ -z "$target" ]] && allow

# ---- 新内容(差分)と旧内容 ----
newtext=""
type collect_new_text >/dev/null 2>&1 && newtext=$(collect_new_text "$input")
[[ -z "${newtext//[[:space:]]/}" ]] && allow

oldtext=""
has_old=0
pyb=""
if command -v jq >/dev/null 2>&1; then
  oldtext=$(printf '%s' "$input" | jq -r '[.tool_input.old_string // "", .tool_input.old_str // "", ((.tool_input.edits // []) | map(.old_string // "") | join("\n"))] | join("\n")' 2>/dev/null)
  printf '%s' "$input" | jq -e '(.tool_input.old_string != null) or (.tool_input.old_str != null) or ((.tool_input.edits // []) | map(.old_string) | any(. != null))' >/dev/null 2>&1 && has_old=1
elif command -v node >/dev/null 2>&1; then
  oldtext=$(printf '%s' "$input" | node -e 'try{const j=JSON.parse(require("fs").readFileSync(0,"utf8"));const t=j.tool_input||{};const p=[];let h=false;if(t.old_string!=null){p.push(String(t.old_string));h=true}if(t.old_str!=null){p.push(String(t.old_str));h=true}for(const e of (t.edits||[])){if(e&&e.old_string!=null){p.push(String(e.old_string));h=true}}process.stdout.write((h?"1":"0")+"\n"+p.join("\n"))}catch(e){}' 2>/dev/null)
  has_old=${oldtext%%$'\n'*}; oldtext=${oldtext#*$'\n'}; [[ "$has_old" == "1" ]] || has_old=0
elif command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then
  pyb=$(command -v python 2>/dev/null || command -v python3)
  oldtext=$(printf '%s' "$input" | "$pyb" -c 'import sys,json
try:
    t=json.load(sys.stdin).get("tool_input",{}) or {}
    p=[]; h=False
    if t.get("old_string") is not None: p.append(str(t["old_string"])); h=True
    if t.get("old_str") is not None: p.append(str(t["old_str"])); h=True
    for e in (t.get("edits") or []):
        if e and e.get("old_string") is not None: p.append(str(e["old_string"])); h=True
    print(("1" if h else "0")+"\n"+"\n".join(p),end="")
except Exception:
    pass' 2>/dev/null)
  has_old=${oldtext%%$'\n'*}; oldtext=${oldtext#*$'\n'}; [[ "$has_old" == "1" ]] || has_old=0
fi
# Write(全文)は old_string を持たない: ディスク上の現ファイル(cwd 相対 / 絶対)を旧内容にする
if [[ $has_old -eq 0 ]]; then
  disk=""
  if [[ -f "$target" ]]; then disk="$target"
  elif [[ "$kind" == "progress" && -f "docs/00-overview/progress.md" ]]; then disk="docs/00-overview/progress.md"
  elif [[ "$kind" == "tasks" && -f "docs/03-implementation/tasks.md" ]]; then disk="docs/03-implementation/tasks.md"
  fi
  if [[ -n "$disk" ]]; then oldtext=$(cat "$disk" 2>/dev/null); has_old=1; fi
fi

# ---- done / [x] の「追加」判定(新旧の件数比較) ----
count_marks() { local t="$1" s; s=${t//\[[xX]\]/}; printf '%s' $(( (${#t} - ${#s}) / 3 )); }
phases_done() { printf '%s\n' "$1" | grep -oE '^[[:space:]]*(requirements|design|implementation|test|release):[[:space:]]*done' | sed -E 's/^[[:space:]]*//; s/:.*$//' | LC_ALL=C sort -u | tr '\n' ' '; }
added=""
if [[ "$kind" == "tasks" ]]; then
  nm=$(count_marks "$newtext"); om=0
  [[ $has_old -eq 1 ]] && om=$(count_marks "$oldtext")
  [[ $nm -gt $om ]] && added="[x] +$((nm-om))"
else
  nd=$(phases_done "$newtext"); od=""
  [[ $has_old -eq 1 ]] && od=$(phases_done "$oldtext")
  for ph in $nd; do
    case " $od " in *" $ph "*) ;; *) added="${added}${added:+ }${ph}: done" ;; esac
  done
fi
[[ -z "$added" ]] && allow

# ---- 証拠 3 点セットの検査(書込で新しく入る行だけを見る) ----
scope="$newtext"
if [[ $has_old -eq 1 && -n "${oldtext//[[:space:]]/}" ]]; then
  tmpold=$(mktemp 2>/dev/null) || tmpold=""
  if [[ -n "$tmpold" ]]; then
    printf '%s\n' "$oldtext" >"$tmpold"
    scope=$(printf '%s\n' "$newtext" | grep -vxF -f "$tmpold" 2>/dev/null)
    rm -f "$tmpold"
  fi
fi
ts_re='[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}'
cmd_re='`[^`]+`|(コマンド|command|cmd|検証)[[:space:]]*[:：]'
out_re='(→|->|=>|出力|結果|output|result|passed|failed|pass|fail|OK|NG|成功|失敗|exit[[:space:]]*(code)?[[:space:]]*[0-9]|[0-9]+[[:space:]]*(件|tests?|passed|errors?|failures?))'
missing=""
[[ $scope =~ $ts_re ]] || missing="実行日時(YYYY-MM-DD HH:MM)"
[[ $scope =~ $cmd_re ]] || missing="${missing}${missing:+・}再実行可能なコマンド(\`...\` またはコマンド: ラベル)"
shopt -s nocasematch
[[ $scope =~ $out_re ]] || missing="${missing}${missing:+・}出力の要約(→ / 結果: / passed / OK 等)"
shopt -u nocasematch
[[ -z "$missing" ]] && { hook_log allow "$kind evidence-ok ($added)"; allow; }

hook_log deny "$target $kind evidence-missing: $missing"
label="完了マーク [x] の追加"
[[ "$kind" == "progress" ]] && label="GATE_STATUS の done 遷移($added)"
printf '{"continue": true, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "%s を検知しましたが、この書込の新内容に証拠 3 点セットが揃っていません(不足: %s)。同じ書込に「証拠: `<再実行可能なコマンド>` → <出力の要約> (YYYY-MM-DD HH:MM)」の形で証拠を含めてください(gate-check スキル: 完了マークは必ず完了条件の証拠とセットで付け、done への遷移はユーザーの明示承認と当日の証拠を要する。証拠なし・古い証拠での完了化は不可)。人間が直接編集する場合はこのガードの対象外です(A2-4b)。"}}\n' "$label" "$missing"
exit 0

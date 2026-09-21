#!/usr/bin/env bash
# PostToolUse hook: フェーズゲートの状態を書き換える編集を検知したら、承認・証拠の
# 確認を促す非ブロッキングの警告を出す(編集自体は妨げない。P2-4)。
# (a) docs/00-overview/progress.md の新内容に「: done」への変更が含まれる場合
# (b) docs/03-implementation/tasks.md の新内容に完了マーク [x]/[X] の追加が含まれる場合
# 照合規則: 「: done」は大文字小文字を区別、完了マークは [x]/[X] の両対応(.ps1 版も同一規則)。
# パスの取り出しはJSON解析で行い、Windowsの \ 区切りを / に正規化してから照合する
# (warn-stale-gate.sh と同一方針)。新内容は new_string / content / edits[].new_string を
# 対象とし、JSON解析できない環境では新内容の判定をせず安全側(警告なし)に倒す。
# 第5回(2026-09-09 再監査 RG-4/CP-1): パス候補は _paths.sh(D072)の collect_path_candidates を
# 正とし(無い環境では従来の内蔵収集にフォールバック)、読み取り系ツール名は冒頭で除外する。
input=$(cat)

# 共通ライブラリ(無ければ従来の内蔵収集で動く=fail-open)
# shellcheck source=_paths.sh
source "$(dirname "$0")/_paths.sh" 2>/dev/null || true
# shellcheck source=_log.sh
source "$(dirname "$0")/_log.sh" 2>/dev/null || true
type hook_log >/dev/null 2>&1 || hook_log() { :; }
# 1 回解析(A6-20): 以降の get_tool_name / collect_path_candidates / collect_new_text はキャッシュを返す
type parse_hook_input >/dev/null 2>&1 && parse_hook_input "$input"
# 読み取り系ツールは対象外(CP-1 / D059)
tname=""
type get_tool_name >/dev/null 2>&1 && tname=$(get_tool_name "$input")
if type is_read_only_tool >/dev/null 2>&1 && is_read_only_tool "$tname"; then
  printf '%s\n' '{"continue": true}'
  exit 0
fi

file=""
newtext=""
if [[ -n "${_HOOK_PARSED:-}" ]]; then
  file=$_HOOK_FILE
  newtext=$_HOOK_NEW_TEXT
else
  # grepフォールバックではパスのみ拾う(複数行の新内容は素朴なgrepで抽出できないため
  # 判定せず警告なし=安全側。README「JSONペイロードを素朴なgrepで読まない」参照)
  file=$(printf '%s' "$input" | grep -oE '"(file_path|filePath|path)"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
fi
# \ 区切り(Windows)を / に正規化。連続する / は1つに畳む(warn-stale-gate.sh と同一)。
file=${file//\\//}
file=$(printf '%s' "$file" | sed -E 's|/{2,}|/|g')

# 警告は systemMessage(ユーザー向け)に加えて hookSpecificOutput.additionalContext にも併記し、
# モデルにも同じ警告が届くようにする(第2回監査の出力契約統一)。
# extra は状態機械の付帯警告(別セッションの lock / 往復上限超。下記)で、他の警告文の末尾に添える(単独なら単独で出す)。
extra=""
warn_out() {
  local m="$1"
  [[ -n "$extra" ]] && m="$m $extra"
  printf '{"continue": true, "systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "%s"}}\n' "$m" "$m"
}
finish_ok() { # 警告なしで終わる(付帯警告だけがあればそれを出す)
  if [[ -n "$extra" ]]; then
    local m="$extra"; extra=""
    warn_out "$m"
  else
    printf '%s\n' '{"continue": true}'
  fi
  exit 0
}

# 一括編集/パッチ系(D066): トップレベルにパスが無い場合、tool_input 全体から候補を
# 収集し、progress.md / tasks.md を対象に含む編集を検知する。新内容にはパッチ本文
# (input/patch/content/diff)も充てる(ゲート改竄の検知漏れ対策。非ブロッキング警告の
# ため拾いすぎは許容)。
# 第5回: 共通ライブラリ(_paths.sh)があればそちらの収集を正とする(D072。1箇所直せば全ガードに反映)
if [[ -z "$file" ]] && type collect_path_candidates >/dev/null 2>&1; then
  file=$(collect_path_candidates "$input" auto | grep -iE '(^|/)docs/00-overview/progress\.md$|(^|/)docs/03-implementation/tasks\.md$' | head -1)
  [[ -n "$file" && -z "$newtext" ]] && newtext=${_HOOK_PATCH_TEXT:-}
fi

if [[ -z "$file" ]]; then
  printf '%s\n' '{"continue": true}'
  exit 0
fi

# 形式リント(D059): progress.md が編集されたのに、実ファイルに正準の
# <!-- GATE_STATUS --> ブロックが無ければ警告する。エージェントがテンプレを使わず
# 独自形式で progress.md を自作し、全フックが機械読取不能になる実失敗を
# Copilot 手動 E2E が検出したことへの機械的な再発防止。
case "$file" in
  *docs/00-overview/progress.md)
    if [[ -f "docs/00-overview/progress.md" ]] && ! grep -q '<!-- GATE_STATUS' "docs/00-overview/progress.md" 2>/dev/null; then
      hook_log warn "$file gate-format"
      warn_out "progress.md に正準の <!-- GATE_STATUS --> コメントブロックがありません。docs/00-overview/progress_template.md の形式(キー: requirements/design/implementation/test/release)に戻してください。フックとゲート判定はこのブロックだけを機械読取します。復旧手順は .github/harness/STATE-MACHINE.md(python tools/gate_status.py recover)。"
      exit 0
    fi
    ;;
esac

# 状態機械の堅牢化(再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15。仕様の正は .github/harness/STATE-MACHINE.md):
# progress.md の書込後に実ファイルの GATE_STATUS を読み(PostToolUse なので実ファイルが正。Write / Edit / パッチ系の
# どれで書かれても同じ経路)、遷移ログ logs/gate-transitions.jsonl の最終記録と違えば「前→後」の差分・通番(rev)・
# session_id・ts を _log.sh の gate_log で 1 行追記する。あわせて (a) 別セッションの stale でない session.lock が
# あれば同時更新の警告、(b) implement↔test の往復カウンタ(GATE_COUNTERS の implement_test_loops。無ければ 0)が上限
# (HARNESS_LOOP_MAX > tools/usage-config.json の implement_test_loop_max > 3)超なら警告を extra に積み、他の警告文の
# 末尾に添える(単独なら単独で出す)。どれも非ブロッキング・fail-open(.ps1 と同一判定)。
case "$file" in
  *docs/00-overview/progress.md)
    if [[ -f "docs/00-overview/progress.md" ]] && type gate_file_state_into >/dev/null 2>&1; then
      gate_file_state_into "docs/00-overview/progress.md"
      cur_state=$_GATE_STATE; cur_loops=$_GATE_LOOPS
      if [[ -n "$cur_state" ]]; then
        gate_last_into
        [[ "$cur_state" != "$_GATE_LAST_AFTER" ]] && gate_log "$_GATE_LAST_AFTER" "$cur_state" "$cur_loops" hook
      fi
      if type session_lock_other >/dev/null 2>&1 && session_lock_other "${_HOOK_SESSION_ID:-}"; then
        hook_log warn "$file gate-lock-other:${_LOCK_SID:0:8}"
        extra="${extra}別のセッション(${_LOCK_SID:0:8}…)が ${_LOCK_AGE_MIN} 分前から同じ作業ツリーで作業中です(.github/hooks/logs/session.lock)。GATE_STATUS の同時更新は後勝ちで前の遷移が失われます。片方のセッションだけが書き、必要なら python tools/gate_status.py reconcile で遷移ログを突き合わせてください(STATE-MACHINE.md「並行更新」)。"
      fi
      loop_max="${HARNESS_LOOP_MAX:-}"
      if [[ ! $loop_max =~ ^[0-9]+$ && -r tools/usage-config.json ]]; then
        loop_max=$(grep -oE '"implement_test_loop_max"[[:space:]]*:[[:space:]]*[0-9]+' tools/usage-config.json 2>/dev/null | grep -oE '[0-9]+$' | head -1)
      fi
      [[ $loop_max =~ ^[0-9]+$ ]] || loop_max=3
      if [[ -n "$cur_loops" && "$cur_loops" -gt "$loop_max" ]]; then
        hook_log warn "$file gate-loop-cap:$cur_loops"
        extra="${extra}implement↔test の往復が ${cur_loops} 回で上限 ${loop_max} を超えています(GATE_COUNTERS の implement_test_loops)。自動の差し戻しを止め、/13-converge(乖離の棚卸し)か人の判断に上げてください(STATE-MACHINE.md「往復上限」)。"
      fi
    fi
    ;;
esac

[[ -z "$newtext" ]] && finish_ok

# 独立レビュー記録の検査(A6-14 / RD-2): implementation または test を done にする書込で、
# docs/04-test/review-log.md の日時付きエントリ(YYYY-MM-DD HH:MM)も
# security-review-report.md も無ければ警告文を返す(問題なしなら空文字)。
# 「当該フェーズ開始以降」は機械可読な開始日が無いため、「そのフェーズの最新の完了証拠
# (tasks.md / test-report.md の YYYY-MM-DD HH:MM の最大値)と同日以降」で近似する
# (実運用で reviewer 起動 0 回のまま実装フェーズが done になった実例への機械検査。
#  ファイル読取の失敗は記録なし/証拠なしとして扱い、判定は非ブロッキングの警告のみ)。
review_record_gap() {
  local phase="$1" ts='[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}'
  local log="docs/04-test/review-log.md" rep="docs/04-test/security-review-report.md"
  local art="" latest_review="" latest_evidence=""
  latest_review=$(cat "$log" "$rep" 2>/dev/null | grep -oE "$ts" | LC_ALL=C sort | tail -1)
  if [[ -z "$latest_review" && ! -s "$rep" ]]; then
    printf '%s' "$phase の done 遷移を検知しましたが、独立レビューの記録がありません（docs/04-test/review-log.md に日時付きエントリが無く、security-review-report.md もありません）。reviewer を起動し、結果を review-log.md に追記してから done にしてください（gate-check スキルの done 条件。A6-14）。"
    return 0
  fi
  case "$phase" in
    implementation) art="docs/03-implementation/tasks.md" ;;
    test) art="docs/04-test/test-report.md" ;;
  esac
  [[ -n "$art" ]] && latest_evidence=$(grep -oE "$ts" "$art" 2>/dev/null | LC_ALL=C sort | tail -1)
  if [[ -n "$latest_review" && -n "$latest_evidence" && "${latest_review:0:10}" < "${latest_evidence:0:10}" ]]; then
    printf '%s' "$phase の done 遷移を検知しましたが、最新の完了証拠（${latest_evidence:0:10}）以降の独立レビューの記録がありません（review-log.md の最新エントリは ${latest_review:0:10}）。最終タスク完了後に reviewer を再度起動し、記録してから done にしてください（gate-check スキルの done 条件。A6-14）。"
  fi
  return 0
}

# done 契約の model_mismatch(D077 決定3 の残: 三層強制の done 契約側。A6-10): implementation / test の done 遷移で、
# このセッションの logs/usage/<session_id>.subagents.jsonl(log-subagent.py が PostToolUse(Agent)/SubagentStop で
# 記録)にある直近の reviewer / spec-critic の resolved_model(null なら requested_model)が、役割別モデル方針の
# allowed(python tools/model_policy.py --print-role-table。読めなければ隣の guard-subagent-model.sh に埋め込まれた
# 同じ生成表)に無ければ警告文を返す(警告のみ・block しない)。記録が無い(Copilot 経路等)・session_id が無い・
# 表が読めないときは空=警告しない(記録の無いホストで毎回鳴らす警報疲れを避ける)。
# 記録先は log-subagent.py と同じく HARNESS_HOOK_LOG_DIR で差し替え可(selftest が実 logs/ を汚さない)。
model_mismatch_gap() {
  local phase="$1" sid="${_HOOK_SESSION_ID:-}" f line role model last_role="" last_model="" table="" py=""
  local re_role='"agent_type":[[:space:]]*"(reviewer|spec-critic)"' re_res='"resolved_model":[[:space:]]*"([^"]+)"'
  local re_req='"requested_model":[[:space:]]*"([^"]+)"' exact prefix p hit=0
  [[ -n "$sid" ]] || return 0
  sid=${sid//[^A-Za-z0-9_-]/_}
  f="${HARNESS_HOOK_LOG_DIR:-$(dirname "$0")/../logs}/usage/${sid}.subagents.jsonl"
  [[ -s "$f" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ $line =~ $re_role ]] || continue
    role=${BASH_REMATCH[1]}
    model=""
    if [[ $line =~ $re_res ]]; then model=${BASH_REMATCH[1]}
    elif [[ $line =~ $re_req ]]; then model=${BASH_REMATCH[1]}; fi
    [[ -n "$model" ]] && { last_role=$role; last_model=$model; }
  done < "$f"
  [[ -n "$last_model" ]] || return 0
  if [[ -f "tools/model_policy.py" ]]; then
    py=$(command -v python 2>/dev/null || command -v python3 2>/dev/null)
    [[ -n "$py" ]] && table=$("$py" tools/model_policy.py --print-role-table 2>/dev/null)
  fi
  if [[ -z "$table" && -f "$(dirname "$0")/guard-subagent-model.sh" ]]; then
    table=$(grep -oE "^role_table='[^']*'" "$(dirname "$0")/guard-subagent-model.sh" 2>/dev/null | head -1)
    table=${table#role_table=\'}; table=${table%\'}
  fi
  [[ -n "$table" ]] || return 0
  local re_tbl="\"$last_role\":\\{\"allowed_exact\":\\[([^]]*)\\],\"allowed_prefix\":\\[([^]]*)\\]"
  [[ $table =~ $re_tbl ]] || return 0
  exact=${BASH_REMATCH[1]}; prefix=${BASH_REMATCH[2]}
  [[ ",$exact," == *",\"$last_model\","* ]] && hit=1
  if [[ $hit -eq 0 ]]; then
    local IFS=','
    for p in $prefix; do
      p=${p#\"}; p=${p%\"}
      [[ -n "$p" && $last_model == "$p"* ]] && { hit=1; break; }
    done
  fi
  [[ $hit -eq 1 ]] && return 0
  printf '%s' "$phase の done 遷移を検知しましたが、このセッションの独立レビュー（${last_role}）の実行モデル ${last_model} は役割別モデル方針（.github/harness/model-policy.yml）の allowed 外です（model_mismatch）。方針どおりのモデルで ${last_role} を再実行して記録するか、方針を変更して python tools/generate-adapters.py を再実行してください（D077 三層強制の done 契約側。警告のみ）。"
  return 0
}

case "$file" in
  *docs/00-overview/progress.md)
    # リリースゲート統合(D070): レポート無き release done を先に検知する(一般 done 警告より優先)
    if printf '%s' "$newtext" | grep -qE '^release:[[:space:]]*done' && [[ ! -f "docs/04-test/security-review-report.md" ]]; then
      hook_log warn "$file release-done-without-security-report"
      warn_out "release の done 遷移を検知しましたが docs/04-test/security-review-report.md がありません。リリース前に release-security-review スキル(独立セキュリティレビュー)を実施し、レポートを作成してください(D070)。"
      exit 0
    fi
    # 独立レビュー記録(A6-14): implementation/test の done 遷移で記録が無ければ専用警告
    # (一般 done 警告より優先。判定は上の review_record_gap)
    for review_phase in $(printf '%s' "$newtext" | grep -oE '^(implementation|test):[[:space:]]*done' | cut -d: -f1 | LC_ALL=C sort -u); do
      review_msg=$(review_record_gap "$review_phase")
      if [[ -n "$review_msg" ]]; then
        hook_log warn "$file $review_phase-done-without-review-log"
        warn_out "$review_msg"
        exit 0
      fi
      # done 契約の model_mismatch(D077): 記録が方針外モデルなら専用警告(記録が無ければ鳴らさない)
      mm_msg=$(model_mismatch_gap "$review_phase")
      if [[ -n "$mm_msg" ]]; then
        hook_log warn "$file $review_phase-done-model-mismatch"
        warn_out "$mm_msg"
        exit 0
      fi
    done
    # 照合はキー行アンカー(値の凡例文「(in_progress)」等への部分一致誤警告の防止。D066)
    if printf '%s' "$newtext" | grep -qE '^(requirements|design|implementation|test|release):[[:space:]]*done'; then
      hook_log warn "$file gate-done"
      warn_out "GATE_STATUS の変更を検知。done への遷移はユーザーの明示承認と証拠が必要（gate-check スキル参照）。"
      exit 0
    fi
    # 遷移の責務(STATE-MACHINE.md): pending_approval は各フェーズの最終ステップ(成果物確定後・承認前)、in_progress は
    # 入口(該当フェーズのコマンド / /12 / /13)の最初のステップとしての正規遷移(D067)。文言を分け、後者だけ改竄の注意を添える
    if printf '%s' "$newtext" | grep -qE '^(requirements|design|implementation|test|release):[[:space:]]*pending_approval'; then
      hook_log warn "$file gate-pending-approval"
      warn_out "pending_approval(ゲート承認待ち)への遷移を検知。そのフェーズの成果物が確定し、独立レビュー記録(implementation/test は review-log.md)が揃っているか確認してください。done への遷移は人の承認発言を得てから行います(.github/harness/STATE-MACHINE.md)。"
      exit 0
    fi
    if printf '%s' "$newtext" | grep -qE '^(requirements|design|implementation|test|release):[[:space:]]*in_progress'; then
      hook_log warn "$file gate-in-progress"
      warn_out "in_progress への遷移を検知。入口(該当フェーズのコマンド / /12-change-request / /13-converge)の最初のステップとしての正規の遷移か、変更履歴/CR記録があるかを確認。入口を経ずにガードを通すためだけに書き換えるのはゲート改竄（D063/D066。.github/harness/STATE-MACHINE.md）。"
      exit 0
    fi
    ;;
  *docs/03-implementation/tasks.md)
    if printf '%s' "$newtext" | grep -qE '\[[xX]\]'; then
      hook_log warn "$file task-done-mark"
      warn_out "完了マークの追加を検知。完了条件（done契約）の証拠が併記されているか確認してください。"
      exit 0
    fi
    ;;
esac

finish_ok

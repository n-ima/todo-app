#!/usr/bin/env bash
# 判定ログ(hook_log)の共通実装(sh 系。ps1 は _log.ps1、python は _log.py が同じ契約を持つ)。
# 2026-09-09 再監査 RC-5(hook-decisions.log は TSV 破損・session_id 無し・hook_log が 18〜20 スクリプトに
# 重複)/ RC-11(共通ライブラリの置き場未定義)/ A6-20 の実装。各ガードは _paths.sh と同様に
#   source "$(dirname "$0")/_log.sh" 2>/dev/null || true
# で読み込み、従来どおり `hook_log <decision> <target>` を呼ぶ(呼び出し側の書き換えなし)。
# 無い環境では `type hook_log >/dev/null 2>&1 || hook_log() { :; }` で無記録・無害(fail-open)。
#
# 書式(1 行 JSONL。欄の順序も固定。_log.py と同一):
#   {"ts","session_id","hook_event","tool_name","script","decision","target","tool_use_id","duration_ms","host"}
#   target は redaction(privacy-patterns.json(.github/harness))→ 先頭 120 字 → JSON エスケープ(改行は \n)。
#   duration_ms は本ファイルを source した時点(=スクリプト開始の近似。bash 起動分は含まない)からの経過ミリ秒
#   (bash 5 の $EPOCHREALTIME → 無ければ date +%s%N → どちらも無ければ null)、host はペイロードの欄名
#   (snake_case=claude-code / camelCase=copilot)→ 環境変数 CLAUDE_PROJECT_DIR → unknown の近似判定
#   (R-09 / A2-7。欄は末尾に足し、旧行は欠落=null で読める。3 系統の欄順は validate (k-4) が照合する)。
# 置き場: logs/hook-decisions.jsonl(gitignore 済)。HARNESS_HOOK_LOG_DIR で差し替え可(selftest 用)。
# 512KB 超は書き込み前に後半 256KB(行境界)だけ残す。
# session_id / hook_event / tool_name / tool_use_id は _paths.sh の parse_hook_input が済んでいれば
# その変数(_HOOK_*)を使い、無ければ呼び出し側の $input(各ガードの生ペイロード変数名)から bash
# 組み込みの正規表現で拾う(追加プロセス起動ゼロ)。
# 外部プロセスは wc(サイズ確認)と、必要なときだけ mkdir / tail / mv。date は bash 組み込み printf %(…)T。

HOOK_LOG_NAME="hook-decisions.jsonl"
HOOK_LOG_TARGET_MAX=120
# duration_ms の起点(source 時点)。$EPOCHREALTIME は bash 5 の組み込み(マイクロ秒。ロケールの小数点記号は数字以外を
# 落として吸収)。無ければ GNU date の %N(外部プロセス 1 回。macOS の date は N を出さないので null になる)
_LOG_T0_US=""
if [[ -n "${EPOCHREALTIME:-}" ]]; then
  _LOG_T0_US=${EPOCHREALTIME//[!0-9]/}
else
  _LOG_T0_US=$(date +%s%N 2>/dev/null)
  if [[ $_LOG_T0_US =~ ^[0-9]{16,}$ ]]; then _LOG_T0_US=$((_LOG_T0_US / 1000)); else _LOG_T0_US=""; fi
fi
_log_self_dir=${BASH_SOURCE[0]%/*}
[[ "$_log_self_dir" == "${BASH_SOURCE[0]}" ]] && _log_self_dir=.

_LOG_PATS=(); _LOG_REPL=(); _LOG_IC=(); _LOG_PATS_LOADED=0
# privacy-patterns.json(.github/harness) は 1 エントリ 1 行なので、jq 不在でも行単位の正規表現で pattern / replace /
# ignore_case を拾える(JSON エスケープ \" と \\ だけ復号する)
_LOG_RE_PAT='"pattern"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)"'
_LOG_RE_REP='"replace"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)"'
_LOG_RE_IC='"ignore_case"[[:space:]]*:[[:space:]]*true'
_log_load_patterns() {
  [[ "$_LOG_PATS_LOADED" -eq 1 ]] && return 0
  _LOG_PATS_LOADED=1
  local f="$_log_self_dir/../../harness/privacy-patterns.json" line p r ic  # .github/harness/(2026-09-21 移動)
  [[ -r "$f" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ $line =~ $_LOG_RE_PAT ]] || continue
    p=${BASH_REMATCH[1]}; p=${p//\\\"/\"}; p=${p//\\\\/\\}
    r='[REDACTED]'
    if [[ $line =~ $_LOG_RE_REP ]]; then r=${BASH_REMATCH[1]}; r=${r//\\\"/\"}; r=${r//\\\\/\\}; fi
    ic=0; [[ $line =~ $_LOG_RE_IC ]] && ic=1
    _LOG_PATS+=("$p"); _LOG_REPL+=("$r"); _LOG_IC+=("$ic")
  done < "$f"
}

# 秘密値らしき文字列を伏せる(結果は変数 _LOG_RED)。bash 組み込みの =~ だけで行い外部プロセスを起動しない。
_LOG_RED=""
hook_log_redact() {
  local s="$1" i p r m n was=0
  _log_load_patterns
  shopt -q nocasematch && was=1
  for i in "${!_LOG_PATS[@]}"; do
    p=${_LOG_PATS[$i]}; r=${_LOG_REPL[$i]}
    if [[ ${_LOG_IC[$i]} == 1 ]]; then shopt -s nocasematch; else shopt -u nocasematch; fi
    n=0
    while [[ $n -lt 8 && $s =~ $p ]]; do
      m=${BASH_REMATCH[0]}
      [[ -z "$m" ]] && break
      s=${s//"$m"/$r}
      n=$((n+1))
    done
  done
  if [[ $was -eq 1 ]]; then shopt -s nocasematch; else shopt -u nocasematch; fi
  _LOG_RED=$s
}

# JSON 文字列のエスケープ(結果は変数 _LOG_ESC): \ " 改行 CR タブ → \\ \" \n \r \t、他の制御文字は空白
_LOG_ESC=""
_log_json_escape() {
  local s="$1"
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  s=${s//[[:cntrl:]]/ }
  _LOG_ESC=$s
}

# 呼び出し側の生ペイロード($input)から欄を 1 つ拾う(結果は _LOG_FIELD。無ければ空)
_LOG_FIELD=""
_log_field_from_raw() {
  local raw="$1" key="$2" re
  _LOG_FIELD=""
  re="\"$key\"[[:space:]]*:[[:space:]]*\"([^\"\\\\]*)\""
  [[ $raw =~ $re ]] && _LOG_FIELD=${BASH_REMATCH[1]}
}

# null または JSON 文字列(結果は _LOG_JV)
_LOG_JV=""
_log_json_value() {
  if [[ -z "$1" ]]; then _LOG_JV=null; else _log_json_escape "$1"; _LOG_JV="\"$_LOG_ESC\""; fi
}

# 経過ミリ秒(結果は _LOG_MS。起点が取れなければ null)
_LOG_MS=null
_log_elapsed_ms() {
  local now=""
  _LOG_MS=null
  [[ -n "$_LOG_T0_US" ]] || return 0
  if [[ -n "${EPOCHREALTIME:-}" ]]; then
    now=${EPOCHREALTIME//[!0-9]/}
  else
    now=$(date +%s%N 2>/dev/null)
    if [[ $now =~ ^[0-9]{16,}$ ]]; then now=$((now / 1000)); else now=""; fi
  fi
  if [[ -n "$now" ]]; then
    _LOG_MS=$(( (now - _LOG_T0_US) / 1000 ))
    [[ $_LOG_MS -lt 0 ]] && _LOG_MS=0
  fi
  return 0
}

# ホストの近似判定(結果は _LOG_HOST。_log.py の detect_host と同じ規則。bash 組み込みの =~ だけで外部プロセスなし):
# ペイロードの欄名が snake_case(hook_event_name / session_id / tool_use_id / transcript_path)なら claude-code、
# camelCase(hookEventName / sessionId / toolName / transcriptPath)なら copilot、どちらも無ければ環境変数
# CLAUDE_PROJECT_DIR(公式: Claude Code のフックに常に渡る)があれば claude-code、無ければ unknown
_LOG_RE_SNAKE='"(hook_event_name|session_id|tool_use_id|transcript_path)"[[:space:]]*:'
_LOG_RE_CAMEL='"(hookEventName|sessionId|toolName|transcriptPath)"[[:space:]]*:'
_LOG_HOST=unknown
_log_detect_host() {
  local raw="$1"
  _LOG_HOST=unknown
  if [[ -n "${COPILOT_CLI:-}" ]]; then _LOG_HOST=copilot  # Copilot CLI 1.0.86 は Claude 互換ペイロード+CLAUDE_PROJECT_DIR を渡す(2026-09-21 実測)
  elif [[ $raw =~ $_LOG_RE_SNAKE ]]; then _LOG_HOST=claude-code
  elif [[ $raw =~ $_LOG_RE_CAMEL ]]; then _LOG_HOST=copilot
  elif [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then _LOG_HOST=claude-code
  fi
  return 0
}

hook_log() { # $1=decision $2=target
  {
    local d="${HARNESS_HOOK_LOG_DIR:-$_log_self_dir/../logs}" f ts sid ev tn tid script raw dec tgt
    [[ -d "$d" ]] || mkdir -p "$d" || return 0
    f="$d/$HOOK_LOG_NAME"
    if [[ -f "$f" && $(wc -c <"$f") -gt 524288 ]]; then
      { tail -c 262144 "$f" | { IFS= read -r _drop; cat; } >"$f.tmp"; } && mv "$f.tmp" "$f"
    fi
    if ! printf -v ts '%(%Y-%m-%dT%H:%M:%S%z)T' -1 2>/dev/null; then ts=$(date +%Y-%m-%dT%H:%M:%S%z); fi
    [[ ${#ts} -eq 24 ]] && ts="${ts:0:22}:${ts:22}"
    raw=${_HOOK_RAW_INPUT:-${input:-}}
    if [[ -n "${_HOOK_PARSED:-}" ]]; then
      sid=${_HOOK_SESSION_ID:-}; ev=${_HOOK_EVENT:-}; tn=${_HOOK_TOOL_NAME:-}; tid=${_HOOK_TOOL_USE_ID:-}
    else
      _log_field_from_raw "$raw" session_id; sid=$_LOG_FIELD
      _log_field_from_raw "$raw" hook_event_name; ev=$_LOG_FIELD
      _log_field_from_raw "$raw" tool_name; tn=$_LOG_FIELD
      _log_field_from_raw "$raw" tool_use_id; tid=$_LOG_FIELD
    fi
    script=${HOOK_LOG_SCRIPT:-${0##*/}}
    dec=$1
    hook_log_redact "${2:-}"; tgt=${_LOG_RED:0:$HOOK_LOG_TARGET_MAX}
    _log_json_value "$sid"; sid=$_LOG_JV
    _log_json_value "$ev"; ev=$_LOG_JV
    _log_json_value "$tn"; tn=$_LOG_JV
    _log_json_value "$tid"; tid=$_LOG_JV
    _log_json_escape "$script"; script=$_LOG_ESC
    _log_json_escape "$dec"; dec=$_LOG_ESC
    _log_json_escape "$tgt"; tgt=$_LOG_ESC
    _log_elapsed_ms; _log_detect_host "$raw"
    printf '{"ts":"%s","session_id":%s,"hook_event":%s,"tool_name":%s,"script":"%s","decision":"%s","target":"%s","tool_use_id":%s,"duration_ms":%s,"host":"%s"}\n' \
      "$ts" "$sid" "$ev" "$tn" "$script" "$dec" "$tgt" "$tid" "$_LOG_MS" "$_LOG_HOST" >>"$f"
  } 2>/dev/null || true
  return 0
}

# ---- ゲート遷移ログ(gate-transitions.jsonl)と session.lock(状態機械の堅牢化。再監査 2026-08-31 §6 A7-M-3 /
#      codex R-04 / IA-20260831-15。仕様の正は .github/harness/STATE-MACHINE.md、python 鏡は _log.py、ps1 鏡は _log.ps1) ----
# 書式(1 行 JSONL。欄の順序固定):
#   {"ts","rev","session_id","hook_event","tool_name","script","source","before","after","changed","loops"}
#   rev は同一ファイル内の通番(最終行の rev + 1)、before / after は {phase: 値}(before は初回 null)、changed は
#   "phase:前->後" のカンマ連結、loops は GATE_COUNTERS の implement_test_loops(無ければ null)、source は
#   hook(warn-gate-tamper)/ session-start(inject-progress の突合)/ cli / reconcile / recover:<元>。
# 呼び出し側は compact 形('requirements=done,design=in_progress,…')で渡す(bash 組み込みだけで組み立てる)。
GATE_LOG_NAME="gate-transitions.jsonl"
SESSION_LOCK_NAME="session.lock"
GATE_PHASE_RE='^[[:space:]]*(requirements|design|implementation|test|release)[[:space:]]*:[[:space:]]*([^[:space:]]*)'

# progress.md から現在の状態を読む(結果: _GATE_STATE=compact 正準順・初出優先 / _GATE_LOOPS=GATE_COUNTERS の値(無ければ空))。
# ブロックが無ければ _GATE_STATE は空。値は最初のトークン(後ろの注記は落とす)、英数字と _ - 以外は _ に置換。
_GATE_STATE=""; _GATE_LOOPS=""
gate_file_state_into() { # $1=progress.md のパス
  local f="$1" line k v inblock=0 incnt=0 acc=""
  local s_requirements="" s_design="" s_implementation="" s_test="" s_release=""
  _GATE_STATE=""; _GATE_LOOPS=""
  [[ -r "$f" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    if [[ $inblock -eq 0 && $incnt -eq 0 ]]; then
      [[ $line == *'<!-- GATE_STATUS'* ]] && { inblock=1; continue; }
      [[ $line == *'<!-- GATE_COUNTERS'* ]] && { incnt=1; continue; }
      continue
    fi
    if [[ $line == *'-->'* ]]; then inblock=0; incnt=0; continue; fi
    if [[ $inblock -eq 1 && $line =~ $GATE_PHASE_RE ]]; then
      k=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}; v=${v//[^A-Za-z0-9_-]/_}
      case "$k" in
        requirements) [[ -z "$s_requirements" ]] && s_requirements=$v ;;
        design) [[ -z "$s_design" ]] && s_design=$v ;;
        implementation) [[ -z "$s_implementation" ]] && s_implementation=$v ;;
        test) [[ -z "$s_test" ]] && s_test=$v ;;
        release) [[ -z "$s_release" ]] && s_release=$v ;;
      esac
    elif [[ $incnt -eq 1 && $line =~ ^[[:space:]]*implement_test_loops[[:space:]]*:[[:space:]]*([0-9]+) ]]; then
      [[ -z "$_GATE_LOOPS" ]] && _GATE_LOOPS=${BASH_REMATCH[1]}
    fi
  done < "$f"
  for k in requirements design implementation test release; do
    v=""
    case "$k" in
      requirements) v=$s_requirements ;; design) v=$s_design ;; implementation) v=$s_implementation ;;
      test) v=$s_test ;; release) v=$s_release ;;
    esac
    [[ -n "$v" ]] && acc+="$k=$v,"
  done
  _GATE_STATE=${acc%,}
  return 0
}

# 最終の遷移記録を読む(結果: _GATE_LAST_AFTER=compact / _GATE_LAST_REV=通番。記録が無ければ空 / 0)
_GATE_LAST_AFTER=""; _GATE_LAST_REV=0
gate_last_into() {
  local d="${HARNESS_HOOK_LOG_DIR:-$_log_self_dir/../logs}" f line rest acc=""
  _GATE_LAST_AFTER=""; _GATE_LAST_REV=0
  f="$d/$GATE_LOG_NAME"
  [[ -s "$f" ]] || return 0
  line=$(tail -n 1 "$f" 2>/dev/null)
  [[ $line =~ \"rev\":([0-9]+) ]] && _GATE_LAST_REV=${BASH_REMATCH[1]}
  [[ $line =~ \"after\":\{([^}]*)\} ]] || return 0
  rest=${BASH_REMATCH[1]}
  while [[ $rest =~ \"([A-Za-z_]+)\":\"([^\"]*)\" ]]; do
    acc+="${BASH_REMATCH[1]}=${BASH_REMATCH[2]},"
    rest=${rest#*"${BASH_REMATCH[0]}"}
  done
  _GATE_LAST_AFTER=${acc%,}
  return 0
}

# compact → JSON オブジェクト(結果: _GATE_JOBJ。空なら null)
_GATE_JOBJ=""
_gate_compact_to_json() {
  local s="$1" pair k v out=""
  if [[ -z "$s" ]]; then _GATE_JOBJ=null; return 0; fi
  local IFS=','
  for pair in $s; do
    k=${pair%%=*}; v=${pair#*=}
    k=${k//[^A-Za-z0-9_-]/_}; v=${v//[^A-Za-z0-9_-]/_}
    [[ -n "$k" ]] && out+="\"$k\":\"$v\","
  done
  _GATE_JOBJ="{${out%,}}"
}

# 差分 "phase:前->後,…"(結果: _GATE_CHANGED。after の順。before に無い phase は none)
_GATE_CHANGED=""
_gate_diff_into() {
  local b="$1" a="$2" pair k v bk bv acc=""
  local IFS=','
  for pair in $a; do
    k=${pair%%=*}; v=${pair#*=}; bv=""
    for bk in $b; do [[ ${bk%%=*} == "$k" ]] && bv=${bk#*=}; done
    [[ "$bv" != "$v" ]] && acc+="$k:${bv:-none}->$v,"
  done
  _GATE_CHANGED=${acc%,}
}

gate_log() { # $1=before(compact|空) $2=after(compact) $3=loops(数値|空) $4=source(hook|session-start|cli|reconcile)
  {
    local d="${HARNESS_HOOK_LOG_DIR:-$_log_self_dir/../logs}" f ts sid ev tn script raw rev before after loops src changed
    [[ -d "$d" ]] || mkdir -p "$d" || return 0
    f="$d/$GATE_LOG_NAME"
    if [[ -f "$f" && $(wc -c <"$f") -gt 524288 ]]; then
      { tail -c 262144 "$f" | { IFS= read -r _drop; cat; } >"$f.tmp"; } && mv "$f.tmp" "$f"
    fi
    gate_last_into; rev=$((_GATE_LAST_REV + 1))
    if ! printf -v ts '%(%Y-%m-%dT%H:%M:%S%z)T' -1 2>/dev/null; then ts=$(date +%Y-%m-%dT%H:%M:%S%z); fi
    [[ ${#ts} -eq 24 ]] && ts="${ts:0:22}:${ts:22}"
    if [[ -n "${_HOOK_PARSED:-}" ]]; then
      sid=${_HOOK_SESSION_ID:-}; ev=${_HOOK_EVENT:-}; tn=${_HOOK_TOOL_NAME:-}
    else
      raw=${_HOOK_RAW_INPUT:-${input:-}}
      _log_field_from_raw "$raw" session_id; sid=$_LOG_FIELD
      _log_field_from_raw "$raw" hook_event_name; ev=$_LOG_FIELD
      _log_field_from_raw "$raw" tool_name; tn=$_LOG_FIELD
    fi
    script=${HOOK_LOG_SCRIPT:-${0##*/}}
    _gate_compact_to_json "$1"; before=$_GATE_JOBJ
    _gate_compact_to_json "$2"; after=$_GATE_JOBJ
    _gate_diff_into "$1" "$2"; _log_json_escape "$_GATE_CHANGED"; changed=$_LOG_ESC
    loops=${3:-}; [[ $loops =~ ^[0-9]+$ ]] || loops=null
    _log_json_escape "${4:-hook}"; src=$_LOG_ESC
    _log_json_value "$sid"; sid=$_LOG_JV
    _log_json_value "$ev"; ev=$_LOG_JV
    _log_json_value "$tn"; tn=$_LOG_JV
    _log_json_escape "$script"; script=$_LOG_ESC
    printf '{"ts":"%s","rev":%s,"session_id":%s,"hook_event":%s,"tool_name":%s,"script":"%s","source":"%s","before":%s,"after":%s,"changed":"%s","loops":%s}\n' \
      "$ts" "$rev" "$sid" "$ev" "$tn" "$script" "$src" "$before" "$after" "$changed" "$loops" >>"$f"
  } 2>/dev/null || true
  return 0
}

# session.lock({"session_id","pid","ts","epoch","cwd"} の 1 行 JSON)を読む
# (結果: _LOCK_SID / _LOCK_EPOCH / _LOCK_AGE_MIN(分)。無い・読めなければ 1 を返す)
_LOCK_SID=""; _LOCK_EPOCH=""; _LOCK_AGE_MIN=""
session_lock_read_into() {
  local d="${HARNESS_HOOK_LOG_DIR:-$_log_self_dir/../logs}" f line now
  _LOCK_SID=""; _LOCK_EPOCH=""; _LOCK_AGE_MIN=""
  f="$d/$SESSION_LOCK_NAME"
  [[ -s "$f" ]] || return 1
  line=$(head -c 4096 "$f" 2>/dev/null)
  [[ $line =~ \"session_id\":\"([^\"]*)\" ]] && _LOCK_SID=${BASH_REMATCH[1]}
  [[ $line =~ \"epoch\":([0-9]+) ]] && _LOCK_EPOCH=${BASH_REMATCH[1]}
  [[ -n "$_LOCK_SID" && -n "$_LOCK_EPOCH" ]] || return 1
  if ! printf -v now '%(%s)T' -1 2>/dev/null; then now=$(date +%s); fi
  _LOCK_AGE_MIN=$(( (now - _LOCK_EPOCH) / 60 ))
  [[ $_LOCK_AGE_MIN -lt 0 ]] && _LOCK_AGE_MIN=0
  return 0
}

# stale 閾値(分): HARNESS_SESSION_LOCK_STALE_MIN > tools/usage-config.json の session_lock_stale_minutes > 120
session_lock_stale_min() {
  local v="${HARNESS_SESSION_LOCK_STALE_MIN:-}"
  if [[ ! $v =~ ^[0-9]+$ && -r tools/usage-config.json ]]; then
    v=$(grep -oE '"session_lock_stale_minutes"[[:space:]]*:[[:space:]]*[0-9]+' tools/usage-config.json 2>/dev/null | grep -oE '[0-9]+$' | head -1)
  fi
  [[ $v =~ ^[0-9]+$ ]] || v=120
  printf '%s' "$v"
}

# 別セッションの新しい(stale でない)lock があれば 0(真)。_LOCK_SID / _LOCK_AGE_MIN を設定する
session_lock_other() { # $1=自分の session_id(空なら常に 1=判定しない)
  [[ -n "${1:-}" ]] || return 1
  session_lock_read_into || return 1
  [[ "$_LOCK_SID" == "$1" ]] && return 1
  [[ "$_LOCK_AGE_MIN" -lt "$(session_lock_stale_min)" ]] || return 1
  return 0
}

# 自セッションの lock を書く(一時ファイル→rename。書けなければ無視)
session_lock_write() { # $1=session_id
  {
    local d="${HARNESS_HOOK_LOG_DIR:-$_log_self_dir/../logs}" f ts now sid cwd
    [[ -n "${1:-}" ]] || return 0
    [[ -d "$d" ]] || mkdir -p "$d" || return 0
    f="$d/$SESSION_LOCK_NAME"
    sid=${1//[^A-Za-z0-9_-]/_}
    if ! printf -v ts '%(%Y-%m-%dT%H:%M:%S%z)T' -1 2>/dev/null; then ts=$(date +%Y-%m-%dT%H:%M:%S%z); fi
    [[ ${#ts} -eq 24 ]] && ts="${ts:0:22}:${ts:22}"
    if ! printf -v now '%(%s)T' -1 2>/dev/null; then now=$(date +%s); fi
    _log_json_value "$PWD"; cwd=$_LOG_JV
    printf '{"session_id":"%s","pid":%s,"ts":"%s","epoch":%s,"cwd":%s}\n' "$sid" "$PPID" "$ts" "$now" "$cwd" >"$f.tmp" && mv -f "$f.tmp" "$f"
  } 2>/dev/null || true
  return 0
}

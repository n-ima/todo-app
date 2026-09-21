#!/usr/bin/env bash
# 共通ライブラリ(D072): tool_input(JSON)から編集対象のパス候補を網羅収集し正規化する。
# 全ガードフックがこれを source して使う。1箇所直せば全ガードに反映される=N面鏡を断つ。
# 発端: guard-phase-scope に D063/D066 で入れた収集を guard-harness-config-edit 等に
# 展開し忘れ、apply_patch/uri 経由で保護域が素通りする穴が横断監査で発覚した
# (2026-08-31 再監査 H-2、2026-09-09 再監査 RG-4 で回帰確認)。
# .ps1 側の鏡は _paths.ps1(関数名・判定規則を同一に保ち、selftest 両系で固定する)。
#
# 提供する関数(source した側は fail-open のため、関数が無くても落ちないように
# `type <fn> >/dev/null 2>&1 &&` で存在確認してから呼ぶ):
#   get_tool_name <json>              : tool_name / toolName を出力(取れなければ空)
#   is_read_only_tool <tool_name>     : 読み取り系ツール名なら 0(真)。二段判定(D059):
#                                       読み取り系の接頭辞/語に一致し、かつ書き込み系の語を含まない
#   collect_path_candidates <json> [auto|always|never]
#                                     : 標準出力に正規化済み候補を1行1件で返す(重複排除・最大64)。
#   collect_new_text <json>           : 新内容(new_string / content / edits[].new_string)を連結して出力
#   path_has_traversal <path>         : '..' セグメントを含めば 0(真)
#   win_longpath <path>               : 8.3 短縮名(C:/Users/RUNNER~1/… 等)を長形式に展開して出力(H-10/RG-7。
#                                       guard-phase-scope.sh の expand_83 と同一契約。非 Windows は no-op)
#   expand_candidate_83_into <path>   : 候補パス向け(~数字を含む相対パスは cwd 結合)。結果は変数 _PATHS_LONG
#                                       に返す(コマンド置換のフォークを避ける版。ガードの候補ループはこちら)
#   parse_hook_input <json>           : 1 回解析 API(A6-20 / D080 残課題)。jq → node → python のどれか 1 プロセスで
#                                       ペイロードを解析し、変数群 _HOOK_TOOL_NAME / _HOOK_SESSION_ID / _HOOK_EVENT /
#                                       _HOOK_TOOL_USE_ID / _HOOK_FILE / _HOOK_COMMAND / _HOOK_NEW_TEXT /
#                                       _HOOK_FIELD_CANDS / _HOOK_PATCH_TEXT に返す(_HOOK_PARSED=1)。解析器が
#                                       1 つも無ければ 1 を返す(各関数は grep フォールバックで動く)。
#                                       ガードは source 直後に `parse_hook_input "$input"` を親シェルで 1 回呼ぶ。
#                                       以降の get_tool_name / collect_path_candidates / collect_new_text は
#                                       同じ入力ならキャッシュを返し、追加プロセスを起動しない(旧実装は 1 ガード
#                                       あたり node/python を 3〜4 回起動し、Windows 実測で基本コスト約 0.6〜1.2s)。
#   collect_path_candidates_into <json> [mode]
#                                     : collect_path_candidates の変数版。_PATHS_CANDS(改行区切り・最大 64)と
#                                       _PATHS_CAND_TOTAL(上限で切る前の重複排除後の件数)に返す(guard-phase-scope
#                                       の候補過多 deny はこちらを使う)
#   collect_old_text <json>           : 旧内容(old_string + edits[].old_string を改行連結。Edit / MultiEdit の置換元。
#                                       Write には無い=空)を出力。第7波(A7-G-3): skills の権限系 frontmatter の
#                                       「拡張だけ ask・縮小は allow」判定で、置換元から消えた制限キーを見るために使う
#
# collect_path_candidates:
#   収集元: tool_input 全体を再帰探索した file_path/filePath/path/notebook_path/uri、
#           および パッチ本文(input/patch/content/diff)内の "*** Add/Update/Delete File:" 行。
#   第2引数(既定 auto): パッチ本文の走査条件。auto=ツール名に patch を含む、または
#           フィールド候補が0件の場合のみ走査(Write の content 内に偶然マーカー文字列がある
#           docs 編集を誤検知しないため。guard-phase-scope と同じ規則) / always=常に / never=しない。
#   正規化: %エンコード復号(: / \ . 空白)→ file:// 除去 → /C: のドライブ先頭 / 除去 →
#           \ を / へ → 連続 / の圧縮。'..' セグメントは残す(呼び出し側がトラバーサル判定)。

# python の解決順は run-python.sh と同じ python→python3(Windows の Store スタブ python3 誤検出回避)
_paths_pybin() {
  if command -v python >/dev/null 2>&1; then printf 'python'; return 0; fi
  if command -v python3 >/dev/null 2>&1; then printf 'python3'; return 0; fi
  return 1
}

# ---- 1 回解析 API(A6-20。旧実装は関数ごとに jq/node/python を起動していた) ----
# 解析器は「10 欄を US(0x1f)区切りで 1 行に」出力する。欄の順序は 3 系統(jq / node / python)で同一:
#   tool_name, session_id, hook_event_name, tool_use_id, file(file_path//filePath//path//notebook_path),
#   command, new_text(new_string + content + edits[].new_string を改行連結),
#   field_cands(tool_input 全体の file_path/filePath/path/notebook_path/uri を改行連結),
#   patch_text(input/patch/content/diff の文字列値を改行連結。マーカー抽出は bash 側),
#   old_text(old_string + edits[].old_string を改行連結。第7波 A7-G-3 で末尾に追加=既存欄の順序は不変)
# 値中の US は空白に置換する(区切りの衝突防止)。stdin は UTF-8 で明示復号し、出力もバイト列で書く
# (Windows の python は既定で CP932 復号・CRLF 変換するため=D074 の指摘と同根)。
_HOOK_PARSED=""; _HOOK_RAW_INPUT=""
_HOOK_TOOL_NAME=""; _HOOK_SESSION_ID=""; _HOOK_EVENT=""; _HOOK_TOOL_USE_ID=""
_HOOK_FILE=""; _HOOK_COMMAND=""; _HOOK_NEW_TEXT=""; _HOOK_FIELD_CANDS=""; _HOOK_PATCH_TEXT=""; _HOOK_OLD_TEXT=""
_paths_us=$'\x1f'
_PATHS_JQ_PROGRAM='
def s: if type == "string" then . else "" end;
def ti: if (.tool_input | type) == "object" then .tool_input else {} end;
def cands: [ti | .. | objects | (.file_path?, .filePath?, .path?, .notebook_path?, .uri?)] | map(select(type == "string")) | join("\n");
def ptext: [ti | (.input?, .patch?, .content?, .diff?, .file_text?)] | map(select(type == "string")) | join("\n");
def edits: (ti | .edits) as $e | if ($e | type) == "array" then ($e | map(if type == "object" then (.new_string | s) else "" end) | join("\n")) else "" end;
def oedits: (ti | .edits) as $e | if ($e | type) == "array" then ($e | map(if type == "object" then (.old_string | s) else "" end) | join("\n")) else "" end;
([ (.tool_name // .toolName | s),
   (.session_id | s), (.hook_event_name | s), (.tool_use_id | s),
   (ti | (.file_path // .filePath // .path // .notebook_path) | s),
   (ti | .command | s),
   ([ (ti | .new_string | s), (ti | .content | s), (ti | .file_text | s), (ti | .new_str | s), edits ] | join("\n")),
   (try cands catch ""),
   (try ptext catch ""),
   ([ (ti | .old_string | s), (ti | .old_str | s), oedits ] | join("\n"))
 ] | map(gsub("\u001f"; " ")) | join("\u001f")) + "\u001f"'
_PATHS_NODE_PROGRAM='try{const j=JSON.parse(require("fs").readFileSync(0,"utf8"));const s=(v)=>(typeof v==="string"?v:"");const t=(j.tool_input&&typeof j.tool_input==="object"&&!Array.isArray(j.tool_input))?j.tool_input:{};const acc=[];const walk=(o)=>{if(Array.isArray(o)){for(const v of o)walk(v);}else if(o&&typeof o==="object"){for(const k of Object.keys(o)){const v=o[k];if((k==="file_path"||k==="filePath"||k==="path"||k==="notebook_path"||k==="uri")&&typeof v==="string")acc.push(v);else walk(v);}}};walk(t);const pt=["input","patch","content","diff","file_text"].map(k=>t[k]).filter(v=>typeof v==="string");const ed=Array.isArray(t.edits)?t.edits.map(e=>(e&&typeof e==="object")?s(e.new_string):""):[];const od=Array.isArray(t.edits)?t.edits.map(e=>(e&&typeof e==="object")?s(e.old_string):""):[];const f=[s(j.tool_name||j.toolName),s(j.session_id),s(j.hook_event_name),s(j.tool_use_id),s(t.file_path||t.filePath||t.path||t.notebook_path),s(t.command),[s(t.new_string),s(t.content),s(t.file_text),s(t.new_str),ed.join("\n")].join("\n"),acc.join("\n"),pt.join("\n"),[s(t.old_string),s(t.old_str),od.join("\n")].join("\n")];process.stdout.write(f.map(v=>v.split("\x1f").join(" ")).join("\x1f")+"\x1f");}catch(e){}'
_PATHS_PY_PROGRAM='import sys, json
try:
    j = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace").lstrip("\ufeff"))
    s = lambda v: v if isinstance(v, str) else ""
    t = j.get("tool_input") if isinstance(j.get("tool_input"), dict) else {}
    acc = []
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("file_path", "filePath", "path", "notebook_path", "uri") and isinstance(v, str):
                    acc.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(t)
    pt = [t.get(k) for k in ("input", "patch", "content", "diff", "file_text") if isinstance(t.get(k), str)]
    eds = t.get("edits") if isinstance(t.get("edits"), list) else []
    ed = [s((e or {}).get("new_string")) if isinstance(e, dict) else "" for e in eds]
    od = [s((e or {}).get("old_string")) if isinstance(e, dict) else "" for e in eds]
    f = [s(j.get("tool_name") or j.get("toolName")), s(j.get("session_id")), s(j.get("hook_event_name")), s(j.get("tool_use_id")),
         s(t.get("file_path") or t.get("filePath") or t.get("path") or t.get("notebook_path")), s(t.get("command")),
         "\n".join([s(t.get("new_string")), s(t.get("content")), s(t.get("file_text")), s(t.get("new_str")), "\n".join(ed)]), "\n".join(acc), "\n".join(pt),
         "\n".join([s(t.get("old_string")), s(t.get("old_str")), "\n".join(od)])]
    sys.stdout.buffer.write(("\x1f".join(v.replace("\x1f", " ") for v in f) + "\x1f").encode("utf-8"))
except Exception:
    pass'
_PATHS_REST=""; _PATHS_FIELD=""; _PATHS_STRIPPED=""
_paths_take() { _PATHS_FIELD=${_PATHS_REST%%"$_paths_us"*}; _PATHS_REST=${_PATHS_REST#*"$_paths_us"}; }
_paths_rstrip_nl_into() { local v="$1"; while [[ $v == *[$'\r\n'] ]]; do v=${v%[$'\r\n']}; done; _PATHS_STRIPPED=$v; }
parse_hook_input() {
  local input="$1" out="" pyb=""
  if [[ -n "$_HOOK_PARSED" && "$_HOOK_RAW_INPUT" == "$input" ]]; then return 0; fi
  _HOOK_PARSED=""; _HOOK_RAW_INPUT=""
  _HOOK_TOOL_NAME=""; _HOOK_SESSION_ID=""; _HOOK_EVENT=""; _HOOK_TOOL_USE_ID=""
  _HOOK_FILE=""; _HOOK_COMMAND=""; _HOOK_NEW_TEXT=""; _HOOK_FIELD_CANDS=""; _HOOK_PATCH_TEXT=""; _HOOK_OLD_TEXT=""
  if command -v jq >/dev/null 2>&1; then
    out=$(printf '%s' "$input" | jq -r "$_PATHS_JQ_PROGRAM" 2>/dev/null)
  fi
  if [[ -z "$out" ]] && command -v node >/dev/null 2>&1; then
    out=$(printf '%s' "$input" | node -e "$_PATHS_NODE_PROGRAM" 2>/dev/null)
  fi
  if [[ -z "$out" ]] && pyb=$(_paths_pybin); then
    out=$(printf '%s' "$input" | "$pyb" -c "$_PATHS_PY_PROGRAM" 2>/dev/null)
  fi
  [[ -n "$out" && "${out: -1}" == "$_paths_us" ]] || return 1
  _PATHS_REST=$out
  _paths_take; _HOOK_TOOL_NAME=${_PATHS_FIELD//[$'\r\n']/}
  _paths_take; _HOOK_SESSION_ID=${_PATHS_FIELD//[$'\r\n']/}
  _paths_take; _HOOK_EVENT=${_PATHS_FIELD//[$'\r\n']/}
  _paths_take; _HOOK_TOOL_USE_ID=${_PATHS_FIELD//[$'\r\n']/}
  _paths_take; _HOOK_FILE=${_PATHS_FIELD//[$'\r\n']/}
  # 複数行の値は末尾の改行を落とす(旧実装の $(…) コマンド置換と同じ。new_string/content/edits が
  # すべて空のとき new_text が改行だけの非空値になり「新内容あり」と誤判定するのを防ぐ)
  _paths_take; _paths_rstrip_nl_into "$_PATHS_FIELD"; _HOOK_COMMAND=$_PATHS_STRIPPED
  _paths_take; _paths_rstrip_nl_into "$_PATHS_FIELD"; _HOOK_NEW_TEXT=$_PATHS_STRIPPED
  _paths_take; _HOOK_FIELD_CANDS=$_PATHS_FIELD
  _paths_take; _paths_rstrip_nl_into "$_PATHS_FIELD"; _HOOK_PATCH_TEXT=$_PATHS_STRIPPED
  # 第7波(A7-G-3)で末尾に足した old_text。旧い解析器出力(9 欄)でも _PATHS_REST が空になるだけで壊れない
  _paths_take; _paths_rstrip_nl_into "$_PATHS_FIELD"; _HOOK_OLD_TEXT=$_PATHS_STRIPPED
  _PATHS_REST=""
  _HOOK_PARSED=1; _HOOK_RAW_INPUT=$input
  return 0
}

get_tool_name() {
  local input="$1" t=""
  if parse_hook_input "$input"; then
    t=$_HOOK_TOOL_NAME
  else
    t=$(printf '%s' "$input" | grep -oE '"(tool_name|toolName)"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
  fi
  printf '%s' "${t//[$'\r\n']/}"
}

# 読み取り系ツール名の判定規則(D059。guard-phase-scope.sh から共通化。CP-1)。
# 読み取りまで ask/deny すると警報疲れ→「すべて許可」の包括承認を誘発し、本命の書き込み
# ガードまで無効化される連鎖が実機 E2E で観測された。VS Code は matcher を無視して全ツールで
# フックを発火させるため、deny 型ガードの冒頭でこの除外を通す。
READ_TOOL_RE='^(read|get|list|find|glob|grep|fetch|view|codebase|usages|problems)|(^|[_-])search'
WRITE_TOOL_WORD_RE='edit|write|creat|replace|insert|apply|delet|patch|run|exec|update|remove|rename|move|upload|save|modify|set|index|add'
is_read_only_tool() {
  [[ -n "$1" ]] || return 1
  printf '%s' "$1" | grep -qiE "$READ_TOOL_RE" || return 1
  printf '%s' "$1" | grep -qiE "$WRITE_TOOL_WORD_RE" && return 1
  return 0
}

# パッチ本文から "*** Add|Update|Delete File: <path>" 行を拾う(結果は _PATHS_MARKERS。改行区切り)。
# 旧実装の grep -iE / python (?im) / ps1 (?im) と同じ規則(先頭空白・大文字小文字のゆらぎを許容)を
# bash 組み込みで行い外部プロセスを起動しない。
_PATHS_MARKERS=""
_paths_patch_markers_into() {
  local t="$1" line p acc="" was=0 re='^[[:space:]]*\*\*\*[[:space:]]+(add|update|delete)[[:space:]]+file:[[:space:]]*(.*)$'
  _PATHS_MARKERS=""
  [[ -z "$t" ]] && return 0
  shopt -q nocasematch && was=1
  shopt -s nocasematch
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    if [[ $line =~ $re ]]; then
      p=${BASH_REMATCH[2]}
      p=${p%"${p##*[![:space:]]}"}
      [[ -n "$p" ]] && acc="${acc}${p}"$'\n'
    fi
  done <<EOF_PATCH
$t
EOF_PATCH
  if [[ $was -eq 1 ]]; then shopt -s nocasematch; else shopt -u nocasematch; fi
  _PATHS_MARKERS=${acc%$'\n'}
}

# 候補の収集(変数版)。結果は _PATHS_CANDS(正規化・重複排除済み。最大 64。改行区切り)と
# _PATHS_CAND_TOTAL(上限で切る前の重複排除後の件数)。1 回解析 API のキャッシュがあれば追加プロセスを
# 起動しない。解析器が 1 つも無い環境だけ素朴な grep にフォールバックする(壊れた JSON でも最低限の
# パス名は拾う=安全側)。
_PATHS_CANDS=""; _PATHS_CAND_TOTAL=0
collect_path_candidates_into() {
  local input="$1" mode="${2:-auto}"
  local raw="" tname="" scan=0 fields=""
  _PATHS_CANDS=""; _PATHS_CAND_TOTAL=0
  if parse_hook_input "$input"; then
    fields=$_HOOK_FIELD_CANDS
    raw=$fields
    case "$mode" in
      always) scan=1 ;;
      never) scan=0 ;;
      *)
        tname=$_HOOK_TOOL_NAME
        if [[ $tname == *[Pp][Aa][Tt][Cc][Hh]* || -z "${fields//[[:space:]]/}" ]]; then scan=1; fi ;;
    esac
    if [[ "$scan" -eq 1 && -n "$_HOOK_PATCH_TEXT" ]]; then
      _paths_patch_markers_into "$_HOOK_PATCH_TEXT"
      [[ -n "$_PATHS_MARKERS" ]] && raw="${raw}${raw:+
}${_PATHS_MARKERS}"
    fi
  else
    # 最終フォールバック: 素朴な grep(壊れたJSONでも最低限のパス名は拾う=安全側)
    raw=$(printf '%s' "$input" | grep -oE '"(file_path|filePath|path|notebook_path|uri)"[[:space:]]*:[[:space:]]*"[^"]*"' | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
    if [[ "$mode" == "always" || ( "$mode" != "never" && -z "$(printf '%s' "$raw" | tr -d '[:space:]')" ) ]]; then
      # JSON 文字列内では改行が \n のままなので行アンカーは使えない。マーカー直後の
      # パス文字列(引用符・バックスラッシュまで)を拾う
      local pf
      pf=$(printf '%s' "$input" | grep -oiE '\*\*\* (add|update|delete) file: [^"\]+' | sed -E 's/^\*\*\* [A-Za-z]+ [Ff]ile: //')
      [[ -n "$pf" ]] && raw="${raw}${raw:+
}${pf}"
    fi
  fi

  # 正規化 + 重複排除 + 上限64。すべて bash 組み込み(パラメータ展開 / =~)で行い外部プロセスを
  # 起動しない: 旧実装(1 候補あたり tr + sed×2、末尾に awk + head)は候補 64 件のペイロードで
  # Windows 実測 3s 超となり、フックの timeout 5s(超過は fail-open)を候補数で押し切れた(SC-5)。
  # 重複排除は bash 3.2(macOS 既定)でも動く改行区切りの既出リストで行う(連想配列は 4.0+)。
  local c n=0 seen=$'\n' acc=""
  while IFS= read -r c; do
    [[ -z "$c" ]] && continue
    _paths_norm_into "$c"; c=$_PATHS_NORM
    [[ -z "${c//[[:space:]]/}" ]] && continue
    [[ "$seen" == *$'\n'"$c"$'\n'* ]] && continue
    seen="${seen}${c}"$'\n'
    n=$((n+1))
    [[ $n -le 64 ]] && acc="${acc}${c}"$'\n'
  done <<EOF_RAW
$raw
EOF_RAW
  _PATHS_CAND_TOTAL=$n
  _PATHS_CANDS=${acc%$'\n'}
}

collect_path_candidates() {
  collect_path_candidates_into "$1" "${2:-auto}"
  [[ -n "$_PATHS_CANDS" ]] && printf '%s\n' "$_PATHS_CANDS"
  return 0
}

# 候補 1 件の正規化(結果は変数 _PATHS_NORM に返す。外部プロセスを起動しない)。規則は従来の
# sed 版と同一: %エンコード復号(: / \ . 空白)→ file:// 除去 → /C: のドライブ先頭 / 除去 →
# \ を / へ → 連続 / の圧縮。
_PATHS_NORM=""
_paths_norm_into() {
  local c="$1" sl='/' bs='\'
  c=${c//$'\r'/}
  c=${c//%3[Aa]/:}
  c=${c//%2[Ff]/$sl}
  c=${c//%5[Cc]/$bs}
  c=${c//%2[Ee]/.}
  c=${c//%20/ }
  c=${c#file://}
  if [[ $c =~ ^/[A-Za-z]: ]]; then c=${c:1}; fi
  c=${c//\\//}
  while [[ $c == *"$sl$sl"* ]]; do c=${c//"$sl$sl"/$sl}; done
  _PATHS_NORM=$c
}

# 新内容の収集(new_string / content / edits[].new_string、および Copilot CLI の file_text / new_str を改行連結。
# Copilot CLI 1.0.86 の Write は path+file_text、Edit は path+old_str+new_str = 2026-09-21 実測)。JSON 解析できない
# 環境では空(呼び出し側は「判定せず安全側」に倒す。warn-gate-tamper.sh と同じ規則)。
collect_new_text() {
  local input="$1"
  if parse_hook_input "$input"; then
    printf '%s' "$_HOOK_NEW_TEXT"
  fi
}

# 旧内容の収集(old_string / edits[].old_string / Copilot CLI の old_str を改行連結。Write には無い=空。第7波 A7-G-3)。
collect_old_text() {
  local input="$1"
  if parse_hook_input "$input"; then
    printf '%s' "$_HOOK_OLD_TEXT"
  fi
}

# path_has_traversal <path> : '..' セグメントを含めば 0(真)
path_has_traversal() {
  printf '%s' "$1" | grep -qE '(^|/)\.\.(/|$)'
}

# ---- 8.3 短縮名の長形式展開(H-10/RG-7。guard-phase-scope.sh の expand_83 / .ps1 の ConvertTo-LongPath と
# 同一契約) ----
# win_longpath <path>: 8.3 短縮名(C:/Users/RUNNER~1/… や C:/proj/GITHUB~1/hooks/x 等)を長形式に展開して
# 標準出力に返す。Windows(MSYS/Cygwin)で、かつ ~数字セグメント(拡張子つき NAME~N.EXT 含む)を持つ
# 絶対パスだけが対象(それ以外は追加プロセス起動ゼロ)。cygpath -l は存在するパスしか展開しないため、
# 存在する最深の祖先だけ展開して残りを再結合する。失敗時は入力のまま(fail-open)。非 Windows は no-op。
# 保護ディレクトリ名そのもの(.github→GITHUB~1、.claude→CLAUDE~1、*_template.md→REQUIR~1.MD)が
# 短縮されると字面照合が外れるため、guard-harness-config-edit / guard-template-edit は比較前にこれを通す。
_paths_is_windows=0
case "${OSTYPE:-}" in msys*|cygwin*) _paths_is_windows=1 ;; esac
if [[ "$_paths_is_windows" -eq 0 && -z "${OSTYPE:-}" ]]; then
  case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) _paths_is_windows=1 ;; esac
fi
_paths_re83='^[A-Za-z]:/.*~[0-9]+(\.[^/]*)?(/|$)'
_paths_re83_any='~[0-9]+(\.[^/]*)?(/|$)'
_PATHS_LONG=""
# 結果を変数 _PATHS_LONG に返す版(コマンド置換のフォークを避ける。ガードの候補ループはこちらを使う)
win_longpath_into() {
  local p="$1" head="$1" tail="" long=""
  _PATHS_LONG="$p"
  [[ "$_paths_is_windows" -eq 1 ]] || return 0
  command -v cygpath >/dev/null 2>&1 || return 0
  [[ $p =~ $_paths_re83 ]] || return 0
  while [[ ! -e "$head" ]]; do
    case "$head" in */*) ;; *) return 0 ;; esac
    tail="${head##*/}${tail:+/$tail}"
    head="${head%/*}"
    if [[ -z "$head" || "$head" == [A-Za-z]: ]]; then return 0; fi
  done
  long=$(cygpath -m -l "$head" 2>/dev/null)
  [[ -n "$long" ]] || return 0
  _PATHS_LONG="${long}${tail:+/$tail}"
}
win_longpath() {
  win_longpath_into "$1"
  printf '%s' "$_PATHS_LONG"
}
# expand_candidate_83_into <path>: 候補パス向けの展開(結果は _PATHS_LONG)。~数字セグメントを含む
# 相対パス(GITHUB~1/hooks/x)は cwd に結合し、/d/x 形は d:/x に、それ以外の POSIX 絶対パス(/tmp/x 等の
# MSYS マウント)は cygpath -m で Windows 形式に直してから win_longpath を通す
# (cwd 結合・マウント解決は Windows でだけ行う。~数字を含まないパスは無変更で追加プロセス起動ゼロ)。
expand_candidate_83_into() {
  local c="$1" base=""
  _PATHS_LONG="$c"
  [[ "$_paths_is_windows" -eq 1 ]] || return 0
  [[ $c =~ $_paths_re83_any ]] || return 0
  if [[ $c =~ ^/[A-Za-z]/ ]]; then
    c="${c:1:1}:${c:2}"
  elif [[ $c == /* ]]; then
    base=$(cygpath -m "$c" 2>/dev/null) || base=""
    [[ -n "$base" ]] || return 0
    c="$base"
  elif [[ $c != [A-Za-z]:/* ]]; then
    base=$(cygpath -m "$PWD" 2>/dev/null) || base=""
    [[ -n "$base" ]] || return 0
    c="${base%/}/$c"
  fi
  win_longpath_into "$c"
}

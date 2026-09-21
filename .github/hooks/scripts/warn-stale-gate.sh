#!/usr/bin/env bash
# PostToolUse hook: 承認済み(done)のフェーズ文書が編集されたら、後続フェーズとの
# 整合確認を促す非ブロッキングの警告を出す(手動編集自体は妨げない)。
# パスの取り出しはJSON解析で行い、Windowsの \ 区切りを / に正規化してから照合する
# (素朴なgrep抽出 + / 前提パターンでは \ 区切りパスに一致せず警告が出なかった)。
# 第5回(2026-09-09 再監査 RG-4/CP-1): パス候補は _paths.sh(D072)で uri/notebook_path/apply_patch
# 本文からも収集し、読み取り系ツール名は冒頭で除外する。
input=$(cat)

# 共通ライブラリ(無ければ従来の単一フィールド判定だけで動く=fail-open)
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
if [[ -n "${_HOOK_PARSED:-}" ]]; then
  file=$_HOOK_FILE
else
  # 解析器(jq/node/python)がすべて無い環境だけ従来のgrep抽出にフォールバック
  file=$(printf '%s' "$input" | grep -oE '"(file_path|filePath|path)"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
fi
# \ 区切り(Windows)を / に正規化。grepフォールバック経由のJSONエスケープ済み \\ が
# // になるため、連続する / は1つに畳む。
file=${file//\\//}
file=$(printf '%s' "$file" | sed -E 's|/{2,}|/|g')

# 一括編集/パッチ系・uri 経由(第5回・D072): 候補の中にフェーズ配下の実体文書があればそれを対象にする
if type collect_path_candidates >/dev/null 2>&1; then
  pc=$(collect_path_candidates "$input" auto | grep -iE '(^|/)docs/0[1-5]-[a-z]+/' | grep -viE '_template\.md$' | head -1)
  [[ -n "$pc" ]] && file="$pc"
fi

progress="docs/00-overview/progress.md"
if [[ -z "$file" || ! -f "$progress" ]]; then
  printf '%s\n' '{"continue": true}'
  exit 0
fi

# GATE_STATUS の完全性・順序矛盾(状態機械の堅牢化。再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15。
# 規則の正は tools/gate_status.py(validate (r) が同じ規則で ERROR/WARN)と .github/harness/STATE-MACHINE.md、
# ここは bash の鏡(.ps1 と同一判定。selftest 両系で固定)): progress.md 自身が書かれた直後に実ファイルを読み、
#   完全性: 5 キー(requirements/design/implementation/test/release)の欠落・重複、未知のキー、読めない行、
#           語彙外の値(値は行の最初のトークン。後ろの注記「done 2026-08-01」は許容)、GATE_COUNTERS の不正
#   順序矛盾: 後続フェーズが着手済みなのに先行が not_started(規則 A)、後続が done なのに先行が done でない
#           (規則 B。progress.md 本文に「状態: 運用中」の注記があれば改修サイクル=該当フェーズだけを戻す運用・
#           brownfield 取り込み残余の test として免除)
# を非ブロッキングの 1 行で警告する(fail-open。警告文に " と \ を入れない)。
_gate_val() { # $1=phase → その値(呼び出し側の v_* 変数)
  case "$1" in
    requirements) printf '%s' "$v_requirements" ;; design) printf '%s' "$v_design" ;;
    implementation) printf '%s' "$v_implementation" ;; test) printf '%s' "$v_test" ;; release) printf '%s' "$v_release" ;;
  esac
}
gate_integrity_msg() { # $1=progress.md のパス → 警告文(問題なしなら空)
  local f="$1" line k v inblock=0 incnt=0 has_block=0 opnote=0 msg="" missing="" unknown="" bad="" dup="" junk="" cbad="" ord=""
  local v_requirements="" v_design="" v_implementation="" v_test="" v_release="" i j earlier later vi vj
  local re='^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*:[[:space:]]*([^[:space:]]*)'
  local -a arr=(requirements design implementation test release)
  [[ -r "$f" ]] || return 0
  head -c 262144 "$f" 2>/dev/null | grep -q '状態: 運用中' && opnote=1
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    if [[ $inblock -eq 0 && $incnt -eq 0 ]]; then
      [[ $line == *'<!-- GATE_STATUS'* ]] && { inblock=1; has_block=1; continue; }
      [[ $line == *'<!-- GATE_COUNTERS'* ]] && { incnt=1; continue; }
      continue
    fi
    if [[ $line == *'-->'* ]]; then inblock=0; incnt=0; continue; fi
    [[ -z "${line//[[:space:]]/}" ]] && continue
    if [[ $inblock -eq 1 ]]; then
      if [[ $line =~ $re ]]; then
        k=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}
        case "$k" in
          requirements|design|implementation|test|release)
            if [[ -n "$(_gate_val "$k")" ]]; then dup="${dup}${k} "; continue; fi
            case "$v" in not_started|in_progress|pending_approval|done) ;; *) bad="${bad}${k}=${v:-(空)} " ;; esac
            case "$k" in
              requirements) v_requirements=$v ;; design) v_design=$v ;; implementation) v_implementation=$v ;;
              test) v_test=$v ;; release) v_release=$v ;;
            esac ;;
          *) unknown="${unknown}${k} " ;;
        esac
      else
        junk="${junk}'${line:0:20}' "
      fi
    elif [[ $incnt -eq 1 ]]; then
      if [[ $line =~ $re ]]; then
        k=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}
        if [[ "$k" != implement_test_loops ]]; then cbad="${cbad}未知のキー ${k} "
        elif [[ ! $v =~ ^[0-9]+$ ]]; then cbad="${cbad}${k}=${v:-(空)} が非負整数でない "; fi
      else
        cbad="${cbad}読めない行 '${line:0:20}' "
      fi
    fi
  done < "$f"
  [[ $has_block -eq 0 ]] && { printf '%s' "GATE_STATUS ブロックがありません(docs/00-overview/progress_template.md の形式に戻す。復旧は python tools/gate_status.py recover)。"; return 0; }
  for k in "${arr[@]}"; do [[ -z "$(_gate_val "$k")" ]] && missing="${missing}${k} "; done
  [[ -n "$missing" ]] && msg="${msg}キー欠落: ${missing}"
  [[ -n "$dup" ]] && msg="${msg}キー重複: ${dup}"
  [[ -n "$unknown" ]] && msg="${msg}未知のキー: ${unknown}"
  [[ -n "$junk" ]] && msg="${msg}読めない行: ${junk}"
  [[ -n "$bad" ]] && msg="${msg}語彙外の値: ${bad}(値は not_started/in_progress/pending_approval/done。注記は値の後ろに空白区切りで)"
  [[ -n "$cbad" ]] && msg="${msg}GATE_COUNTERS: ${cbad}"
  if [[ -z "$msg" ]]; then
    for ((j = 0; j < 5; j++)); do
      later=${arr[$j]}; vj=$(_gate_val "$later")
      for ((i = 0; i < j; i++)); do
        earlier=${arr[$i]}; vi=$(_gate_val "$earlier")
        if [[ "$vj" != not_started && "$vi" == not_started ]]; then
          ord="${ord}${later}: ${vj} なのに先行の ${earlier} が not_started(規則 A)。"
        elif [[ "$vj" == done && "$vi" != done && $opnote -eq 0 ]]; then
          ord="${ord}${later}: done なのに先行の ${earlier} が ${vi}(規則 B。改修サイクルなら progress.md に「状態: 運用中」の注記を置く)。"
        fi
      done
    done
    [[ -n "$ord" ]] && msg="順序矛盾: ${ord}"
  fi
  [[ -n "$msg" ]] && printf '%s' "GATE_STATUS の完全性検査: ${msg}（正は .github/harness/STATE-MACHINE.md。python tools/gate_status.py check で再確認、壊れていれば recover）"
  return 0
}
case "$file" in
  *docs/00-overview/progress.md)
    gmsg=$(gate_integrity_msg "$progress")
    if [[ -n "$gmsg" ]]; then
      hook_log warn "$file gate-integrity"
      printf '{"continue": true, "systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "%s"}}\n' "$gmsg" "$gmsg"
    else
      printf '%s\n' '{"continue": true}'
    fi
    exit 0
    ;;
esac

# 対象はフェーズ配下の実体文書すべて(nfr/environment/detailed-design/ADR/ICD等を含む)。
# 従来は代表5ファイルのみで、AGENTS.mdの「承認済み文書の編集で警告」の主張より
# 実装が狭かった(D046。テンプレートは実体ではないため対象外)。
# 照合は大文字小文字非依存で行う(.ps1 の -like/-match の既定と等価にする。Windows の
# 大文字パスやステータス表記ゆらぎで .sh だけ素通しする非対称の修正。第2回監査)
shopt -s nocasematch
phase=""
case "$file" in
  *_template.md) : ;;
  *docs/01-requirements/*) phase="requirements" ;;
  *docs/02-design/*) phase="design" ;;
  *docs/03-implementation/*) phase="implementation" ;;
  *docs/04-test/*) phase="test" ;;
  *docs/05-release/*) phase="release" ;;
esac
shopt -u nocasematch

if [[ -z "$phase" ]]; then
  printf '%s\n' '{"continue": true}'
  exit 0
fi

# 大文字小文字非依存の前置詞剥がしは「tr で小文字化 → 小文字の ${phase}: を剥がす」の順で行う。
# 旧実装の sed の s///I は GNU 拡張で、BSD sed(macOS) ではエラーになり status が常に空
# = warn が全損していた(第3回監査)。POSIX 互換の書き方に固定する。
status=$(grep -iE "^${phase}:" "$progress" | head -1 | tr -d '\r' | tr '[:upper:]' '[:lower:]' | sed -E "s/^${phase}:[[:space:]]*//")
# 値は最初のトークン(注記つき「done 2026-08-01」でも done。.ps1 の (\S+) と同じ規則。再監査 2026-08-31 の sh/ps1 非対称の修正)
status=${status%%[[:space:]]*}

if [[ "$status" == "done" ]]; then
  hook_log warn "$file"
  # 警告は systemMessage(ユーザー向け)に加えて hookSpecificOutput.additionalContext にも併記し、
  # モデルにも同じ警告が届くようにする(第2回監査の出力契約統一)。
  msg="この文書(${phase})は承認済み(done)ですが編集されました。後続フェーズとの整合を確認してください（必要ならdocs/00-overview/progress.mdのGATE_STATUSも見直してください）。"
  printf '{"continue": true, "systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "%s"}}\n' "$msg" "$msg"
else
  printf '%s\n' '{"continue": true}'
fi

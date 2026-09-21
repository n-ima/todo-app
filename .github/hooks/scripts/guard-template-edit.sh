#!/usr/bin/env bash
# PreToolUse hook: *_template.md への直接編集をブロックする。
# 想定外のペイロード形状でも安全側(継続許可)に倒す。
# パスの取り出しはJSON解析で行い、Windowsの \ 区切りを / に正規化してから照合する。
# 第5回(2026-09-09 再監査 RG-4/CP-1): パス候補は _paths.sh(D072)で file_path/filePath/path/
# uri/notebook_path/apply_patch 本文から網羅収集し(uri・パッチ経由のテンプレ編集が素通り
# していた H-2 の封鎖)、読み取り系ツール名(readFile 等)は冒頭で除外する(VS Code は matcher を
# 無視して全ツールで発火するため、読取まで deny するとテンプレートを参照できなくなる)。
input=$(cat)

# 共通ライブラリ(無ければ従来の単一フィールド判定だけで動く=fail-open)
# shellcheck source=_paths.sh
source "$(dirname "$0")/_paths.sh" 2>/dev/null || true
# shellcheck source=_log.sh
source "$(dirname "$0")/_log.sh" 2>/dev/null || true
type hook_log >/dev/null 2>&1 || hook_log() { :; }
# 1 回解析(A6-20): 以降の get_tool_name / collect_path_candidates / collect_new_text はキャッシュを返す
type parse_hook_input >/dev/null 2>&1 && parse_hook_input "$input"

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
  file=$(printf '%s' "$input" | grep -oE '"(file_path|filePath|path)"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
fi
file=${file//\\//}
file=$(printf '%s' "$file" | sed -E 's|/{2,}|/|g')

# パス候補の網羅収集(H-2 / D072)。従来の単一フィールドも候補に含める
cands="$file"
if type collect_path_candidates >/dev/null 2>&1; then
  rc=$(collect_path_candidates "$input" auto)
  [[ -n "$rc" ]] && cands="${cands}${cands:+
}${rc}"
fi

# 照合は大文字小文字非依存で行う(.ps1 の -like の既定と等価にする。Windows で
# REQUIREMENTS_TEMPLATE.MD 等の大文字パスが .sh だけ素通しする非対称の修正。第2回監査)
# 8.3 短縮名(docs/01-REQ~1/REQUIR~1.MD 等)は _paths.sh の展開で長形式に直してから照合する
# (H-10/RG-7 の横展開。展開できなければ字面のまま=fail-open。~数字を含まない候補は無変更)。
is_template=0
hit=""
has83=0
type expand_candidate_83_into >/dev/null 2>&1 && has83=1
shopt -s nocasematch
while IFS= read -r c; do
  [[ -z "$c" ]] && continue
  if [[ $has83 -eq 1 ]]; then expand_candidate_83_into "$c"; c=$_PATHS_LONG; fi
  if [[ "$c" == *_template.md ]]; then is_template=1; hit="$c"; break; fi
done <<EOF_CANDS
$cands
EOF_CANDS
shopt -u nocasematch

if [[ "$is_template" -eq 1 ]]; then
  hook_log deny "$hit"
  printf '%s\n' '{"continue": true, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "テンプレートファイルは直接編集せず、コピーして実体ファイル(例: requirements_template.md -> requirements.md)を作成してください。"}}'
else
  printf '%s\n' '{"continue": true}'
fi

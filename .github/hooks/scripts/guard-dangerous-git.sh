#!/usr/bin/env bash
# PreToolUse hook: push/tag/force系のgit操作は毎回ユーザーに確認(ask)させる。
# denyではなくaskにしているのは、リリースフェーズなど正当なタイミングもあるため。
# commandの取り出しはJSON解析で行う(素朴なgrep抽出は値中のエスケープ済み引用符 \" で
# 切れて危険判定に到達しないまま fail-open する。実例: cd "D:/…" && git push が素通し)。
input=$(cat)

# 共通ライブラリ(D072/A6-20): 1 回解析 API と判定ログ。無ければ従来の grep 抽出・無記録で動く(fail-open)
# shellcheck source=_paths.sh
source "$(dirname "$0")/_paths.sh" 2>/dev/null || true
# shellcheck source=_log.sh
source "$(dirname "$0")/_log.sh" 2>/dev/null || true
type hook_log >/dev/null 2>&1 || hook_log() { :; }
type parse_hook_input >/dev/null 2>&1 && parse_hook_input "$input"

cmd=""
if [[ -n "${_HOOK_PARSED:-}" ]]; then
  cmd=$_HOOK_COMMAND
else
  # jq/node/python がすべて無い・すべて失敗した環境だけ従来のgrep抽出にフォールバック
  cmd=$(printf '%s' "$input" | grep -oE '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')
fi
# 照合前に行継続を空白に畳む(`git \` + 改行 + `push` のように分割された危険コマンドが
# 行単位照合をすり抜けるのを防ぐ。第2回監査)。バックスラッシュ(sh)に加えて、バッククォート
# (PowerShell)とキャレット(cmd)の行継続も畳む(ps1 版 :44 と同一の3種。ps1 側だけ直して
# sh に展開されていなかった H-1/RG-3 の是正。settings.json は Windows でも sh 版を配線する)。
cmd=${cmd//$'\\\r\n'/ }
cmd=${cmd//$'\\\n'/ }
cmd=${cmd//$'`\r\n'/ }
cmd=${cmd//$'`\n'/ }
cmd=${cmd//$'^\r\n'/ }
cmd=${cmd//$'^\n'/ }

# git のグローバルオプション(-C <path> / -c <k=v> / --git-dir / --work-tree)を挟んだ
# push/tag も検知する(`git -C repo push` で素通しだった迂回の修正。D046)。
# rm はフラグの順序・分割(-rf/-fr/-r -f/-f -r/--recursive --force)に依らず検知する。
# PowerShell/cmd の再帰削除(Remove-Item の -Recurse/-Force 併用は順不同、rd /s・del /s)も検知する。
# git clean の -f 系(未追跡ファイルの一括削除)も対象(第2回監査)。
git_opt='((-C|-c)[[:space:]]+[^[:space:]]+|--(git-dir|work-tree)(=[^[:space:]]+|[[:space:]]+[^[:space:]]+))'
rm_rf='rm[[:space:]]+(-[[:alnum:]]*(r[[:alnum:]]*f|f[[:alnum:]]*r)[[:alnum:]]*|-[[:alnum:]]*r[[:alnum:]]*[[:space:]]+-[[:alnum:]]*f[[:alnum:]]*|-[[:alnum:]]*f[[:alnum:]]*[[:space:]]+-[[:alnum:]]*r[[:alnum:]]*|--recursive[[:space:]]+--force|--force[[:space:]]+--recursive)'
remove_item='Remove-Item[^|;&]*-Recurse[^|;&]*-Force|Remove-Item[^|;&]*-Force[^|;&]*-Recurse'
rd_del='(^|[;&|[:space:]])(rd|del)[[:space:]]+(/[[:alnum:]]+[[:space:]]+)*/s([[:space:]]|[;&|]|$)'
# clean は分割フラグ(-d -f)と --force も検知する(-[a-zA-Z]*f 単独では clean -d -f /
# clean --force が素通しだった。第3回監査)。
# 第7波(R-01。codex 監査 2026-08-31 §4): 作業ツリー全体を HEAD の内容で書き戻す discard-all 形
# (git checkout -- . / git checkout . / git restore . / git restore :/ / git restore --worktree .)は
# 保護対象(AGENTS.md / .github/hooks 等)を名指ししないまま上書きするため ask(git plumbing 経由の
# 保護域書換の一種)。単一パスの restore(git checkout -- src/x)は対象外(保護パスなら
# guard-harness-config-edit が ask)。.ps1 と同一規則。
discard_all="git[[:space:]]+(${git_opt}[[:space:]]+)*(checkout|restore)([[:space:]]+(-[^[:space:];&|]+|[^-[:space:];&|][^[:space:];&|]*))*[[:space:]]+(--[[:space:]]+)?(\.|\./|:/|\*)([[:space:]]|[;&|]|$)"
danger_pattern="git[[:space:]]+(${git_opt}[[:space:]]+)*push|reset[[:space:]]+--hard|push[[:space:]]+(-f|--force)|clean[[:space:]]+(-[a-zA-Z]+[[:space:]]+)*(-[a-zA-Z]*f[a-zA-Z]*|--force)|${rm_rf}|${remove_item}|${rd_del}|${discard_all}"

# git tag は一覧表示(引数なしの `git tag` 単体、および -l/--list/-n 系)を対象外にし、
# 作成/削除系(引数あり)のみ ask する。全出現が一覧形と確定できた場合だけ除外し、
# 迷う形は従来どおり ask に倒す。
tag_any="git[[:space:]]+(${git_opt}[[:space:]]+)*tag"
tag_list="${tag_any}([[:space:]]*([;&|]|$)|[[:space:]]+(-l|--list|-n[0-9]*)([[:space:]]|[;&|]|$))"

is_danger=0
if printf '%s' "$cmd" | grep -Eiq "$danger_pattern"; then
  is_danger=1
elif printf '%s' "$cmd" | grep -Eiq "$tag_any"; then
  tag_total=$(printf '%s' "$cmd" | grep -Eoi "$tag_any" | wc -l)
  tag_listed=$(printf '%s' "$cmd" | grep -Eoi "$tag_list" | wc -l)
  [[ "$tag_total" -gt "$tag_listed" ]] && is_danger=1
fi

if [[ "$is_danger" -eq 1 ]]; then
  # 判定ログの redaction(URL 埋め込み認証・鍵形式)と先頭 120 字への切り詰めは _log.sh(privacy-patterns.json)が行う。
  # 理由文に「何を」(コマンドの先頭 160 文字)を含め、exact-action の承認単位(この操作・この対象・この引数)を
  # 利用者が確認できるようにする(codex 監査 2026-08-31 C-01 / IA-20260831-01)。理由文の抜粋も _log.sh の
  # redaction(hook_log_redact。無ければそのまま)を通し、JSON 用に引用符・バックスラッシュ・改行を無害化する
  # (外部プロセスは起動しない)。
  ex=${cmd:0:160}
  if type hook_log_redact >/dev/null 2>&1; then hook_log_redact "$ex"; ex=$_LOG_RED; fi
  ex=${ex//\\/\/}; ex=${ex//\"/\'}; ex=${ex//[$'\r\n\t']/ }
  # 承認バイパス下(payload の permission_mode が bypassPermissions / dontAsk / autopilot 等、または環境変数
  # HARNESS_EXTERNAL_EFFECT_MODE=deny)では ask が消音されて素通りになるため deny に倒す(guard-external-effect と同一規則)。
  pmode=""
  if [[ $input =~ \"permission_mode\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]]; then pmode=${BASH_REMATCH[1]}; fi
  pm=${pmode//[^A-Za-z0-9_-]/}
  bypass=0
  case "${pm,,}" in *bypass*|*dontask*|*dont_ask*|*autopilot*|*yolo*) bypass=1 ;; esac
  [[ "${HARNESS_EXTERNAL_EFFECT_MODE:-}" == "deny" ]] && bypass=1
  if [[ $bypass -eq 1 ]]; then
    hook_log deny "bypass:${cmd:0:200}"
    printf '{"continue": true, "systemMessage": "承認バイパス下で push/tag/force系またはrm -rfを検知したため拒否しました: %s", "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "承認バイパス(permission_mode=%s)下では外部/履歴に影響する操作の ask が消音されるため deny します。通常モードで人間の exact-action 承認を経て実行してください(AGENTS.md「必ず止まる条件」2)。コマンド: %s"}}\n' "$ex" "${pm:-env HARNESS_EXTERNAL_EFFECT_MODE}" "$ex"
    exit 0
  fi
  hook_log ask "${cmd:0:200}"
  printf '{"continue": true, "systemMessage": "push/tag/force系またはrm -rfはAGENTS.mdの方針により都度確認が必要です。", "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "外部/履歴に影響する可能性がある操作のため確認します。何を: %s (承認は『この操作・この対象・この引数』に対する一回限り。environment.md の「自動」分類でも push/タグ付けは承認を経ます=AGENTS.md「必ず止まる条件」2)"}}\n' "$ex"
else
  printf '%s\n' '{"continue": true}'
fi

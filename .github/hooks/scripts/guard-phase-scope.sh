#!/usr/bin/env bash
# PreToolUse hook: 進行中フェーズが無い状態でのアプリコード編集を状態依存で止める。
# 「入口(該当フェーズのコマンド)に入ってから作業する」を指示ではなく機械で担保する(D043決定5)。
# 判定: in_progress/pending_approval あり=allow / progress.md ありで進行中なし=deny(D062) /
# 未初期化=ask。deny の緊急経路は人間の直接編集(フックはエージェントのツールだけを見る)。
# 一括編集・パッチ系ツールは tool_input 全体からパス候補を収集し、いずれかがアプリ
# スコープなら同じ状態判定を適用する(D063/D066。状態判定はパス非依存のため子プロセス
# 再帰は不要 — 旧D063の自己再帰は性能・エンコーディング・環境変数の3系統の穴があり廃止)。
# 注意: Bash経由のファイル書き込み(echo > 等)はこのフックの対象外(既知の限界。.github/harness/PLATFORM.md 参照)。
# 第6波(A6-20 / D080 残課題): ツール名・パス候補・8.3 短縮名展開・読み取り系除外の複製を _paths.sh の同名契約
# 関数(parse_hook_input / collect_path_candidates_into / win_longpath_into / is_read_only_tool)に差し替え、
# 判定ログは _log.sh(JSONL)に一本化した。共通ライブラリが無い配布物では grep 抽出の最小判定で動く(fail-open)。
input=$(cat)

# 共通ライブラリ(D072/A6-20)。無ければ grep 抽出・無記録で動く(fail-open)
# shellcheck source=_paths.sh
source "$(dirname "$0")/_paths.sh" 2>/dev/null || true
# shellcheck source=_log.sh
source "$(dirname "$0")/_log.sh" 2>/dev/null || true
type hook_log >/dev/null 2>&1 || hook_log() { :; }
# 1 回解析(A6-20): 旧実装は tname / file / 候補 / パッチ本文で node/python を 4 回起動していた(Windows 実測 約 1.2s)
type parse_hook_input >/dev/null 2>&1 && parse_hook_input "$input"

# 本体判定の補助(D058): requirements/memo.md がテンプレートのまま
# (「（ここから記入）」マーカーの後に実内容が無い、またはファイル自体が無い)なら 0。
# 実メモが書かれていれば 1。USAGE.md は全配布物に含まれるため単独では本体の
# 識別子にならない(D053 の過剰修正でテンプレ由来の新規プロジェクトを本体と誤検知し
# /00 を拒否する回帰を、Copilot 手動 E2E が検出)。
memo_is_pristine() {
  local memo="requirements/memo.md"
  [[ -f "$memo" ]] || return 0
  grep -q "（ここから記入）" "$memo" 2>/dev/null || return 1
  local tail_text
  tail_text=$(awk 'f{print} /（ここから記入）/{f=1}' "$memo" 2>/dev/null | tr -d '[:space:]')
  [[ -z "$tail_text" ]]
}

allow() { printf '%s\n' '{"continue": true}'; exit 0; }

emit_ask() { # $1=logタグ $2=理由文
  hook_log ask "$1"
  printf '{"continue": true, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "%s"}}\n' "$2"
  exit 0
}

emit_deny() { # $1=logタグ $2=理由文
  hook_log deny "$1"
  printf '{"continue": true, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "%s"}}\n' "$2"
  exit 0
}

# ---- ツール名の取得と読み取り系の除外 (D059 / CP-1) ----
# 読み取りまで ask すると警報疲れ→「すべて許可」の包括承認を誘発し、本命の書き込み ask
# まで無効化される連鎖が実機 E2E で観測された。二段判定の正は _paths.sh の is_read_only_tool
# (読み取り系パターン一致 かつ 書き込み系の語を含まない)。tool_name が取れなければパス判定に落ちる(安全側)。
tname=""
type get_tool_name >/dev/null 2>&1 && tname=$(get_tool_name "$input")
if type is_read_only_tool >/dev/null 2>&1 && is_read_only_tool "$tname"; then
  hook_log allow "read-tool:$tname"
  allow
fi

# ---- パス候補の収集 (D063/D066 → _paths.sh に一本化) ----
# 単一フィールド(file_path 等)に加え、tool_input 全体から path 系フィールドを再帰収集する。
# パッチ本文(*** Add/Update/Delete File: 行)は「ツール名に patch を含む」か「フィールド
# 候補が0件」の場合のみ走査する(auto。Write の content 内に偶然マーカー文字列がある docs 編集を
# 誤検知しないため)。候補は正規化・重複排除済みで最大 64 件、ccount は上限で切る前の件数。
cands=""
ccount=0
if type collect_path_candidates_into >/dev/null 2>&1; then
  collect_path_candidates_into "$input" auto
  cands=$_PATHS_CANDS
  ccount=$_PATHS_CAND_TOTAL
else
  # grep フォールバックは「ゲート判定の入力」にのみ使う(allow の根拠にはしない=安全側)
  cands=$(printf '%s' "$input" | grep -oE '"(file_path|filePath|path|notebook_path|uri)"[[:space:]]*:[[:space:]]*"[^"]*"' | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/' | awk 'NF && !seen[$0]++')
  ccount=$(printf '%s' "$cands" | grep -c . || true)
fi

# 候補ゼロ: パスを持たないツール(Bash 等)は対象外。ただし編集系の名前なのにパスが
# 取れない場合は検査不能として ask に倒す(fail-closed。旧D063の無記録 fail-open の是正)。
if [[ "$ccount" -eq 0 ]]; then
  if [[ -n "$tname" ]] && printf '%s' "$tname" | grep -qiE 'edit|write|creat|replace|insert|apply|patch|notebook'; then
    emit_ask "no-path write-tool:$tname" "編集系ツール($tname)のペイロードから対象ファイルパスを取得できませんでした。フェーズ外編集ガードの検査対象外になるため確認します。対象ファイルがハーネス管理領域(docs/等)なら承認して構いません。アプリコードなら該当フェーズのコマンドを経由してください。"
  fi
  allow
fi

# 候補過多はガードが全対象を検査しきれないため fail-closed(細工した一括編集への防御)
if [[ "$ccount" -gt 64 ]]; then
  emit_deny "too-many-candidates(${ccount}, tool=${tname:-?})" "一括編集の対象が多すぎます(候補${ccount}件>64)。ガードが全対象を検査できないため拒否しました。編集を分割して実行してください。"
fi

# ---- 候補の正規化とアプリスコープ判定 (D066) ----
# 正規化: %エンコード復号(: / \\ . 空白)→ file:// 除去→ /C: 形のドライブ先頭スラッシュ
# 除去→ \\→/ →連続/圧縮→ 8.3 短縮名の長形式展開。'..' セグメントが残るパスは
# トラバーサルとして常にアプリスコープ扱い(fail-closed。docs/../app 等の字句的すり抜けの封鎖)。

# 8.3 短縮名(C:/Users/RUNNER~1/… 等)の長形式展開(H-10/RG-7)は _paths.sh の win_longpath_into(旧 expand_83 と
# 同一契約: Windows で ~数字セグメントを持つ絶対パスだけ cygpath -m -l で展開。失敗時は入力のまま=fail-open)。
# cygpath -m は区切りしか正規化せず、短縮名のままだと pwdn との前方一致が外れて「リポジトリ外」= allow に
# 落ちる fail-open が sh/ps1 双方向で再現した(CI windows の 2 FAIL)。
pwdn=$PWD
command -v cygpath >/dev/null 2>&1 && pwdn=$(cygpath -m "$PWD" 2>/dev/null || printf '%s' "$PWD")
pwdn=${pwdn//\\//}
if type win_longpath_into >/dev/null 2>&1; then win_longpath_into "$pwdn"; pwdn=$_PATHS_LONG; fi

# 候補 1 件の正規化(結果は _PS_NORM): _paths.sh の _paths_norm_into(%復号→file://除去→/C:→\→/→連続/圧縮)
# の後に 8.3 展開。collect_path_candidates_into 経由の候補は正規化済みなので冪等に再適用される。
_PS_NORM=""
norm_candidate_into() {
  local c="$1"
  if type _paths_norm_into >/dev/null 2>&1; then _paths_norm_into "$c"; c=$_PATHS_NORM; else c=${c//\\//}; fi
  if type win_longpath_into >/dev/null 2>&1; then win_longpath_into "$c"; c=$_PATHS_LONG; fi
  _PS_NORM=$c
}

re_traversal='(^|/)\.\.(/|$)'
APP_REL=""
is_app_scope() { # $1=正規化済みパス。アプリスコープなら 0 を返し APP_REL を設定
  local p="$1" rel=""
  [[ -z "$p" ]] && return 1
  if [[ $p =~ $re_traversal ]]; then
    APP_REL="$p (path traversal)"
    return 0
  fi
  shopt -s nocasematch
  if [[ "$p" == "$pwdn"/* ]]; then
    rel=${p:$((${#pwdn}+1))}
  elif [[ "$p" != /* && "$p" != [A-Za-z]:* ]]; then
    rel=$p  # 相対パスはルート相対とみなす
  else
    shopt -u nocasematch
    return 1  # リポジトリ外
  fi
  case "$rel" in
    docs/*|requirements/*|.github/*|.claude/*|.agents/*|tools/*|.vscode/*|\
    README.md|.gitignore|.gitattributes|DECISIONS.md|MEMORY.md)
      shopt -u nocasematch
      return 1 ;;
  esac
  shopt -u nocasematch
  APP_REL="$rel"
  return 0
}

app_hit=0
while IFS= read -r c; do
  [[ -z "$c" ]] && continue
  norm_candidate_into "$c"; nc=$_PS_NORM
  if is_app_scope "$nc"; then app_hit=1; break; fi
done <<EOF_CANDS
$cands
EOF_CANDS

[[ "$app_hit" -eq 0 ]] && allow

# ---- 状態判定(パス非依存。全アプリスコープ候補に同一の判定が適用される) ----
progress="docs/00-overview/progress.md"

if [[ ! -f "$progress" ]]; then
  # 取り込み済み・/11未完了のプロジェクトは「未初期化」として扱う。本体検知より先に判定
  # (D044。DECISIONS.md は D049 で export-ignore になったが、導入前の配布物から作られた
  #  既存プロジェクトは複製を持ち得る)
  if [[ ! -f "docs/00-overview/intake-report.md" ]]; then
    # ハーネス本体リポジトリでは発火させない。本体判定(D058/H-11): 「DECISIONS.md がある」
    # または「USAGE.md がある」、かつ memo がテンプレのまま。USAGE.md は全配布物に含まれる
    # ため単独では本体の識別子にならず(誤検知回帰の是正)、DECISIONS.md もテンプレート経路
    # (GitHub テンプレート/クローン)では残留するため、実メモが書かれたコピーは本体ではなく
    # 新規プロジェクトとして扱う(RG-6。route-request / inject-progress / request-routing と同一判定)。
    { { [[ -f "DECISIONS.md" ]] && memo_is_pristine; } || { [[ -f ".github/harness/USAGE.md" ]] && memo_is_pristine; }; } && allow
  fi
  emit_ask "$APP_REL (uninitialized)" "プロジェクトが未初期化です(progress.md なし)。コードに触る前に、新規開発なら /00-start-project、既存アプリの取り込みなら /11-brownfield-intake を実行してください(取り込み前のアプリ改変は brownfield-intake のアンチパターン)。緊急の場合はこの確認を承認して続行できます。"
fi

# 運用中注記(H-4/RG-6): /11-brownfield-intake は既存テストの状態により test を in_progress の
# まま「状態: 運用中」を書く(gate-check スキル「運用中の扱い」)。この残余の in_progress を
# 「進行中フェーズ」と数えると、brownfield 導入直後(最頻シナリオ)で運用中の deny が丸ごと
# 無効化される。注記があるときは test 行を除いて進行中を判定する(/12 が要件/設計/実装を
# in_progress にした改修サイクル中は従来どおり allow)。読み取りは先頭256KBに制限(route-request と同じ)。
active_re='^(requirements|design|implementation|test|release):[[:space:]]*(in_progress|pending_approval)'
operating_note=0
if head -c 262144 "$progress" 2>/dev/null | grep -q '状態: 運用中'; then
  operating_note=1
  active_re='^(requirements|design|implementation|release):[[:space:]]*(in_progress|pending_approval)'
fi

# いずれかのフェーズが進行中(in_progress/pending_approval)なら通常のフェーズ作業 → 許可
if awk '/<!-- GATE_STATUS/,/-->/' "$progress" | grep -Eq "$active_re"; then
  allow
fi

# ask から deny への格上げ(D062): Copilot 実機3セッションで「宣言素通り」が再発し続け、
# ask はホスト側の承認記憶(すべて許可/常に許可)で消音され得ることも観測された。
# deny の理由文はエージェント自身に届くため、/12 への誘導が機械の契約になる。
deny_reason="進行中のフェーズがありません(GATE_STATUSに in_progress がない=運用中または着手前)。アプリコードの編集はブロックされました(D062)。/12-change-request(運用中の変更請求)または該当フェーズのコマンドを実行してフェーズを in_progress にしてから編集してください(変更管理・回帰確認つきで同じ変更ができます)。緊急時は人間が直接エディタで編集するか、人間の明示指示のもとでフェーズコマンドを経由してください。in_progress への遷移は入口(該当フェーズのコマンド・/12・/13)の最初のステップとして行う正規の操作であり(D067。.github/harness/STATE-MACHINE.md)、入口を経ずに編集を通すためだけに progress.md を書き換えるのはゲート改竄(D063)として行わないこと。"
if [[ "$operating_note" -eq 1 ]]; then
  emit_deny "$APP_REL (operating note, no active phase)" "運用中注記あり(progress.md の「状態: 運用中」。test の in_progress は取り込み残余であり進行中フェーズに数えない)。${deny_reason}"
fi
emit_deny "$APP_REL (no active phase)" "$deny_reason"

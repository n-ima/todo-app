#!/usr/bin/env bash
# SessionStart/PreCompact hook: フェーズゲート状況(GATE_STATUS)と教訓ログ(learnings)を
# 会話開始時およびコンテキスト圧縮前に自動注入する(圧縮で注入済み情報が失われる穴を塞ぐ)。
event="${1:-SessionStart}"
progress="docs/00-overview/progress.md"
learnings="docs/00-overview/learnings.md"
# ペイロード(session_id 等)は stdin。端末から直接実行されたとき(stdin が tty)は読まず、パイプが閉じない環境でも
# 3 秒で読み切りを打ち切る(read -t。SessionStart の timeout 5s 内。CI の陽性確認ステップはパイプ無しで起動する)。
input=""
if [[ ! -t 0 ]]; then IFS= read -r -d '' -t 3 input || true; fi
# 判定ログ・遷移ログ・session.lock の共通実装(_log.sh。無ければ lock と遷移ログを扱わない=fail-open)
# shellcheck source=_log.sh
source "$(dirname "${BASH_SOURCE[0]}")/_log.sh" 2>/dev/null || true
type hook_log >/dev/null 2>&1 || hook_log() { :; }

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

# ctx は実改行($'\n')で組み立てる。リテラル \n + printf %b 展開は、教訓本文中の
# 実バックスラッシュ(\c=出力停止, \t, \n 等)まで展開して以降の注入を無音で破壊するため使わない。
nl=$'\n'
ctx=""
if [[ -f "$progress" ]]; then
  block=$(awk '/<!-- GATE_STATUS/,/-->/' "$progress")
  ctx="現在のフェーズゲート状況(docs/00-overview/progress.md):${nl}${block}"
  # 運用中判定は route-request.sh と同じ「全フェーズ done、または progress.md 本文に
  # 『状態: 運用中』の注記がある」の論理和(値への注記等で done を数え落としても取りこぼさない)。
  # 次の依頼の入口を明示する(入口が示されないと場当たり作業に落ちる)
  done_count=$(printf '%s\n' "$block" | grep -Ec '^(requirements|design|implementation|test|release):[[:space:]]*done' || true)
  if [[ "$done_count" -eq 5 ]] || grep -q '状態: 運用中' "$progress"; then
    ctx="${ctx}${nl}全フェーズ done または運用中注記 = 運用中です。変更依頼の入口は /12-change-request(受付の振り分けは request-routing スキル参照)。"
  fi
elif [[ -f "docs/00-overview/intake-report.md" ]]; then
  # 取り込み済み・/11未完了のプロジェクト。本体検知より先に判定(D044。DECISIONS.md は D049 で
  # export-ignore になったが、導入前の配布物から作られた既存プロジェクトは複製を持ち得る)
  ctx="既存アプリの取り込みが完了していません(intake-report.md あり・progress.md なし)。他の作業より先に /11-brownfield-intake を実行してください(Claude Code では自分で起動してよい。as-is逆起こし → 整合検証 → ゲート初期化。intake-report.md が入力になります)。"
elif { [[ -f "DECISIONS.md" ]] && memo_is_pristine; } || { [[ -f ".github/harness/USAGE.md" ]] && memo_is_pristine; }; then
  # ハーネス本体リポジトリ判定(D058/H-11): 「DECISIONS.md がある(実クローン)」または
  # 「USAGE.md がある(ZIP/archive 展開コピー)」、かつ memo がテンプレのまま。
  # progress.md / intake-report.md の不在は上の分岐で確定済み。実メモが書かれた
  # コピーは新規プロジェクトとして扱う(request-routing タイブレークの機械化。DECISIONS.md は
  # GitHub テンプレート経路でも残留するため、DECISIONS.md 分岐にも memo 判定を AND する=RG-6)。
  ctx="ここはハーネス本体リポジトリです(progress.md なし・本体シグネチャあり)。アプリ開発の入口(/00, /11, /12)は使いません。振り返りの還流適用は /90-apply-retrospective、ハーネス設定の変更は人間が tools/harness-maintenance.py で保守モードにしてから行います。"
else
  # brownfield(既存アプリ持ち込み)に /00 を案内すると、グリーンフィールドの一本道が
  # 始まってしまう(実装済みコードを無視した要件ヒアリング)。必ず両論併記する。
  ctx="docs/00-overview/progress.md が未作成です。新規開発なら /00-start-project、既存アプリの取り込みなら /11-brownfield-intake を実行してください(既存コードがあるのに /00 を実行しない)。"
fi

# 配布鮮度(再監査 2026-09-09 RD-4 / A6-15): docs/00-overview/harness-origin.md(sync-harness / intake-app /
# doctor --write-origin が書く)の latest_decision(旧形式は version)と、記録された本体パス(path:)の
# DECISIONS.md(無ければ CHANGELOG.md)の最新 D 番号を比べ、閾値(HARNESS_STALE_GENERATIONS。既定 1)以上
# 古ければ「/91 を先に実行」を 1 行注入する。SessionStart のみ。本体自身(origin 無し / path が自分)・
# 本体に到達できない・D 番号が読めないときは注入しない(fail-open)。同じ判定を python tools/doctor.py の
# distribution 行が表で出す。.ps1 と同一の判定・注入文。
origin="docs/00-overview/harness-origin.md"
if [[ "$event" == "SessionStart" && -f "$origin" ]]; then
  origin_path=$(grep -m1 -E '^path:' "$origin" 2>/dev/null | sed -E 's/^path:[[:space:]]*//; s/[[:space:]]+$//')
  origin_dec=$(grep -m1 -E '^latest_decision:' "$origin" 2>/dev/null | grep -oE 'D[0-9]+' | head -1)
  [[ -z "$origin_dec" ]] && origin_dec=$(grep -m1 -E '^version:' "$origin" 2>/dev/null | grep -oE 'D[0-9]+' | head -1)
  body_dec=""
  if [[ -n "$origin_path" && -n "$origin_dec" && -d "$origin_path" ]] \
     && [[ "$(cd "$origin_path" 2>/dev/null && pwd)" != "$(pwd)" ]]; then
    if [[ -f "$origin_path/DECISIONS.md" ]]; then
      body_dec=$(grep -oE '^## D[0-9]+' "$origin_path/DECISIONS.md" 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1)
    elif [[ -f "$origin_path/CHANGELOG.md" ]]; then
      body_dec=$(grep -oE '\bD[0-9]{3}\b' "$origin_path/CHANGELOG.md" 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1)
    fi
  fi
  if [[ -n "$body_dec" ]]; then
    have=$((10#${origin_dec#D})); latest=$((10#$body_dec)); threshold="${HARNESS_STALE_GENERATIONS:-1}"
    [[ "$threshold" =~ ^[0-9]+$ ]] || threshold=1
    if (( latest - have >= threshold && latest - have > 0 )); then
      ctx="${ctx}${nl}ハーネスコピーが本体より $((latest - have)) 世代古い可能性があります(コピー D$(printf '%03d' "$have") / 本体 D$(printf '%03d' "$latest"))。作業を始める前に /91-sync-from-harness を先に実行してください(判定は docs/00-overview/harness-origin.md の latest_decision と本体 DECISIONS.md の D 番号差。python tools/doctor.py の distribution 行と同じ)。"
    fi
  fi
fi

if [[ -f "$learnings" ]]; then
  # 「## 教訓」以降の箇条書きを注入する。肥大化対策の上限は「新しい50件」(tail)。
  # 以前は先頭(=最古)50件で打ち切っており、新しい教訓ほど注入されない欠陥があった
  # (総数207件のうち157件が一度も注入されないまま全工程が終わった実例あり)。
  # 上限を超えたら、打ち切ったことを注入文に必ず明示する(silentに取りこぼさない)。
  all=$(awk '/^## 教訓/{flag=1;next}flag' "$learnings" | grep -E '^- ')
  total=0
  [[ -n "$all" ]] && total=$(printf '%s\n' "$all" | wc -l | tr -d '[:space:]')
  lessons=$(printf '%s\n' "$all" | tail -50)
  if [[ -n "$lessons" ]]; then
    ctx="${ctx}${nl}${nl}このプロジェクトの教訓(docs/00-overview/learnings.md、必ず前提として扱うこと):${nl}${lessons}"
    if [[ "$total" -gt 50 ]]; then
      ctx="${ctx}${nl}（教訓 ${total}件中 新しい50件のみ表示。全文は docs/00-overview/learnings.md。上限到達につき振り返りでの棚卸しを推奨）"
    fi
  fi
fi

# 作業ノートパッド(A3-10/D065): セッション途中の未確定メモを SessionStart/PreCompact の
# 両方で再注入し、コンテキスト圧縮・セッション切替で途中状態が失われる穴を塞ぐ。
# 確定した決定・教訓は docs 本体(台帳/learnings)が正であり、ノートパッドは
# 「まだ docs に書ける形になっていない途中状態」専用。先頭2KBに制限(肥大対策)。
notepad="docs/00-overview/notepad.md"
if [[ -f "$notepad" ]]; then
  np=$(head -c 2048 "$notepad" | { iconv -f UTF-8 -t UTF-8 -c 2>/dev/null || cat; } | tr -d '\r')
  if [[ -n "${np//[[:space:]]/}" ]]; then
    ctx="${ctx}${nl}${nl}作業ノートパッド(docs/00-overview/notepad.md。未確定の途中メモ。圧縮・セッション切替でも本注入で保持される。確定したら docs 本体へ移して行を消すこと):${nl}${np}"
  fi
fi

# 異常終了の検知(再監査 CC-9): StopFailure フック(mark-abnormal-stop.py)が 24 時間以内に残した
# logs/abnormal-stop-<session_id>.json があれば、前回セッションが API エラーで途中終了した旨を注入する
# (Stop フック 3 本=記録催促/工数記録/教訓起草が走っていないため、記録と GATE_STATUS の整合が未確認)。
# SessionStart のみ。ファイルは消さない(24 時間で自然に対象外になる。原因調査用に残す)。
# 記録先は mark-abnormal-stop.py と同じく HARNESS_HOOK_LOG_DIR で差し替え可(selftest が実 logs/ を汚さない)。
if [[ "$event" == "SessionStart" ]]; then
  abn_dir="${HARNESS_HOOK_LOG_DIR:-$(dirname "${BASH_SOURCE[0]}")/../logs}"
  abn_file=$(find "$abn_dir" -maxdepth 1 -name 'abnormal-stop-*.json' -mmin -1440 2>/dev/null | head -1)
  if [[ -n "$abn_file" ]]; then
    abn_type=$(grep -o '"error_type": "[^"]*"' "$abn_file" 2>/dev/null | head -1 | sed 's/.*: "//; s/"$//')
    ctx="${ctx}${nl}${nl}前回のセッションは異常終了しました(API エラー: ${abn_type:-unknown}。記録: .github/hooks/logs/$(basename "$abn_file"))。Stop フックの記録処理が走っていないため、作業を始める前に docs/00-overview/progress.md の GATE_STATUS と docs/03-implementation/tasks.md の整合(実際の作業状態・未コミットの変更・記録漏れ)を先に確認してください。"
  fi
fi

# 並行更新の防止と遷移ログの突合(状態機械の堅牢化。再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15。
# 仕様の正は .github/harness/STATE-MACHINE.md。.ps1 と同一の判定・注入文): SessionStart で
# (1) logs/session.lock(session_id・pid・ts)を置く。別セッションの stale でない lock(既定 120 分。tools/usage-config.json の
#     session_lock_stale_minutes / HARNESS_SESSION_LOCK_STALE_MIN)があれば上書きせず「片方だけが GATE_STATUS を書く」を注入し、
#     stale なら取得する。
# (2) progress.md の GATE_STATUS が遷移ログ(logs/gate-transitions.jsonl)の最終記録と違えば source=session-start で 1 行追記する
#     (sed 等フック外の書換・別マシンでの更新も履歴に残る。初回は before null の初期スナップショット)。
# session_id が取れないとき(VS Code の SessionStart ペイロードに session_id が載るかは未検証)は lock を扱わない。PreCompact では
# 何もしない。書けない・読めないときは黙って続行する(fail-open)。
if [[ "$event" == "SessionStart" ]] && type session_lock_write >/dev/null 2>&1; then
  sid=""
  if [[ -n "$input" ]] && type _log_field_from_raw >/dev/null 2>&1; then _log_field_from_raw "$input" session_id; sid=$_LOG_FIELD; fi
  if [[ -n "$sid" ]]; then
    if session_lock_other "$sid"; then
      hook_log inject "session-lock-other:${_LOCK_SID:0:8}"
      ctx="${ctx}${nl}${nl}別のセッション(${_LOCK_SID:0:8}…)が ${_LOCK_AGE_MIN} 分前からこの作業ツリーで作業中です(.github/hooks/logs/session.lock)。GATE_STATUS(progress.md)の書換は片方のセッションだけで行い、こちらは読み取りに留めるか、相手が終わってから続けてください(同時更新は後勝ちで前の遷移が失われます。復旧は .github/harness/STATE-MACHINE.md「復旧手順」)。"
    else
      session_lock_write "$sid"
    fi
  fi
  if [[ -f "$progress" ]] && type gate_file_state_into >/dev/null 2>&1; then
    gate_file_state_into "$progress"
    if [[ -n "$_GATE_STATE" ]]; then
      gate_last_into
      [[ "$_GATE_STATE" != "$_GATE_LAST_AFTER" ]] && gate_log "$_GATE_LAST_AFTER" "$_GATE_STATE" "$_GATE_LOOPS" session-start
    fi
  fi
fi

# 注入量の上限(第3波 A7-M-8 / 再監査 2026-08-31 §6「inject-progress の注入量に実質上限なし」):
# 上限は tools/usage-config.json の inject_progress_max_chars(文字数。validate (h-2) の常駐トークン推定と同じ値)、
# 環境変数 HARNESS_INJECT_MAX_CHARS が優先、どちらも読めなければ 8700。上限を超えた注入文は先頭 max 文字で
# 打ち切り、打ち切った旨と全文の場所を末尾 1 行で明示する(GATE_STATUS の閉じタグ欠落で progress.md 全文が
# 注入される事故も、この上限で有界になる)。文字数は UTF-8 の文字単位(C.UTF-8 ロケール。無い環境ではバイト単位=
# 安全側に短い)。.ps1 と同一の判定・同一の注入文。
inj_max="${HARNESS_INJECT_MAX_CHARS:-}"
if [[ ! "$inj_max" =~ ^[0-9]+$ ]] && [[ -f "tools/usage-config.json" ]]; then
  inj_max=$(grep -oE '"inject_progress_max_chars"[[:space:]]*:[[:space:]]*[0-9]+' "tools/usage-config.json" 2>/dev/null | grep -oE '[0-9]+$' | head -1)
fi
[[ "$inj_max" =~ ^[0-9]+$ ]] || inj_max=8700
if (( inj_max > 0 )); then
  LC_ALL=C.UTF-8 2>/dev/null
  if (( ${#ctx} > inj_max )); then
    ctx_total=${#ctx}
    ctx="${ctx:0:inj_max}"
    ctx="${ctx}${nl}（注入上限 ${inj_max} 文字に達したため打ち切り: 全体 ${ctx_total} 文字。全文は docs/00-overview/ の progress.md / learnings.md / notepad.md を直接読むこと。上限は tools/usage-config.json の inject_progress_max_chars）"
  fi
fi

# 出力JSONの組み立て。python があれば json.dumps で厳密にエスケープする
# (改行と引用符だけの sed エスケープでは、教訓に D:\proj\x のような Windows パスが
#  入ると \ が素通しになり不正な JSON になる)。python の解決順は run-python.sh と
# 同じ python→python3(Store スタブ誤検出回避)とし、Windows の py ランチャも試す。
pybin=""
if command -v python >/dev/null 2>&1; then pybin="python"
elif command -v python3 >/dev/null 2>&1; then pybin="python3"
elif command -v py >/dev/null 2>&1; then pybin="py -3"
fi
out=""
if [[ -n "$pybin" ]]; then
  # ctx は生のまま渡す(%b 展開を挟むと教訓中の実バックスラッシュ \c/\t/\n 等が破壊される)
  out=$(printf '%s' "$ctx" | $pybin -c 'import sys, json
text = sys.stdin.buffer.read().decode("utf-8", "replace")
obj = {"hookSpecificOutput": {"hookEventName": sys.argv[1], "additionalContext": text}}
sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))' "$event" 2>/dev/null)
fi
if [[ -n "$out" ]]; then
  printf '%s\n' "$out"
else
  # フォールバック(python なし): 先頭で \ を二重化 → 実タブを \t 化 → 改行・引用符をエスケープする
  # (実タブが残ると JSON 文字列中に制御文字が入り不正になる実測あり)
  tab=$(printf '\t')
  esc=$(printf '%s' "$ctx" | sed 's/\\/\\\\/g' | sed "s/$tab/\\\\t/g" | sed ':a;N;$!ba;s/\n/\\n/g' | sed 's/"/\\"/g')
  printf '{"hookSpecificOutput": {"hookEventName": "%s", "additionalContext": "%s"}}\n' "$event" "$esc"
fi

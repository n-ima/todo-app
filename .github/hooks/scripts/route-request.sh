#!/usr/bin/env bash
# UserPromptSubmit hook: ユーザーの依頼のたびに、ゲート状況の要約と受付ルーチンの契約を注入する。
# SessionStart の1回だけの注入は会話が伸びるほど薄まり、ハーネス外の場当たり作業に落ちる
# 失敗が実測されたため(D043)、依頼のたびに短く注入する。モデルを介さないため追加コストは
# 注入テキスト分のみ。長文にしない(毎ターン送られる)。
# 配線は2系統(D060): Claude Code は .claude/settings.json の UserPromptSubmit(この .sh)、
# Copilot は gate-hooks.json の UserPromptSubmit(.sh / Windows は route-request.ps1)。
# VS Code は .claude/settings.json のフックを opt-in でしか読まない(D058 実機確認)ため、
# 既定の二重発火は無い。Claude フックを有効化した場合のみ注入が重複する(無害)。
progress="docs/00-overview/progress.md"

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

# ctx は実改行($'\n')で組み立てる。リテラル \n + printf %b 展開は、注入文中の
# 実バックスラッシュ(\c=出力停止, \t 等)まで展開して以降を無音で破壊するため使わない。
nl=$'\n'
ctx=""
if [[ -f "$progress" ]]; then
  # GATE_STATUS を1行に要約する。読み取りは先頭256KBに制限し、キーごとに初出のみ・
  # 値は行末まで(64文字で打ち切り)。細工した progress.md による毎依頼のコンテキスト
  # 爆弾を防ぐ(第4回検証)。CR は落とす(Linux の awk は CR を保持するため)。
  vals=$(head -c 262144 "$progress" 2>/dev/null | tr -d '\r' | awk '
    /<!-- GATE_STATUS/ { f=1; next }
    f && /-->/ { exit }
    f && /^(requirements|design|implementation|test|release):/ && !seen[$1]++ {
      v = $0; sub(/^[a-z]+:[ \t]*/, "", v); if (length(v) > 64) v = substr(v, 1, 64)
      k = $0; sub(/:.*/, "", k)
      printf "%s=%s ", k, v
    }')
  ctx="[受付ルーチン] ゲート状況: ${vals}"
  # 全done判定はフェーズごとの値を数える(5フェーズ連結の固定文字列一致は行順序・
  # 値への注記・空白ゆらぎで運用中を取りこぼす)。運用中判定は「全done、または
  # progress.md 本文に『状態: 運用中』の注記がある」の論理和とし、in_progress より優先する。
  done_count=$(printf '%s\n' "$vals" | tr ' ' '\n' | grep -c '=done' || true)
  if [[ "$done_count" -eq 5 ]] || head -c 262144 "$progress" 2>/dev/null | grep -q '状態: 運用中'; then
    ctx="${ctx}（運用中）${nl}変更依頼の入口は /12-change-request。依頼を受けたら request-routing スキルに従い、応答の冒頭で「分類/入口/影響」を宣言してから入口コマンドを自分で起動すること（Claude Code では Skill ツール。自分で起動できない環境ではコマンド名を提示し、提示したターンではアプリコードを編集せず停止する=場当たり編集はフェーズ外ガードが止める）。セッション分割表が新規セッションを求める移行でも、現セッションが短い（最初の1〜2ターン）ならそのまま起動する。"
  elif printf '%s' "$vals" | grep -q 'in_progress\|pending_approval'; then
    ctx="${ctx}${nl}進行中フェーズの作業はそのフェーズのコマンドで続行する。新しい種類の依頼を受けたら request-routing スキルに従い、応答の冒頭で「分類/入口/影響」を宣言してから入口コマンドを自分で起動すること（Claude Code では Skill ツール。自分で起動できない環境ではコマンド名を提示）。セッション分割表が新規セッションを求める移行でも、現セッションが短い（最初の1〜2ターン）ならそのまま起動する。"
  else
    ctx="${ctx}${nl}依頼を受けたら request-routing スキルに従い、応答の冒頭で「分類/入口/影響」を宣言してから入口コマンドを自分で起動すること（Claude Code では Skill ツール。自分で起動できない環境ではコマンド名を提示）。セッション分割表が新規セッションを求める移行でも、現セッションが短い（最初の1〜2ターン）ならそのまま起動する。"
  fi
elif [[ -f "docs/00-overview/intake-report.md" ]]; then
  # 取り込み済み・/11未完了。本体検知より先に判定(D044。DECISIONS.md は D049 で
  # export-ignore になったが、導入前の配布物から作られた既存プロジェクトは複製を持ち得る)
  ctx="[受付ルーチン] 取り込み済み・未初期化(intake-report.md あり)。依頼の前に /11-brownfield-intake を自分で起動して（Claude Code では Skill ツール）as-is 逆起こしとゲート初期化を完了すること。"
elif { [[ -f "DECISIONS.md" ]] && memo_is_pristine; } || { [[ -f ".github/harness/USAGE.md" ]] && memo_is_pristine; }; then
  # ハーネス本体リポジトリ判定(D058/H-11): 「DECISIONS.md がある(実クローン)」または
  # 「USAGE.md がある(ZIP/archive 展開コピー)」、かつ memo がテンプレのまま。
  # progress.md / intake-report.md の不在は上の分岐で確定済み。実メモが書かれた
  # コピーは新規プロジェクトとして扱う(request-routing タイブレークの機械化。DECISIONS.md は
  # GitHub テンプレート経路でも残留するため、DECISIONS.md 分岐にも memo 判定を AND する=RG-6)。
  # アプリ開発の受付契約は注入しない(SessionStartの案内で足りる)。出力契約の統一のため
  # 無出力ではなく最小 JSON を返す(無出力はフック失敗と区別できない。D060)。
  printf '%s\n' '{"continue": true}'
  exit 0
else
  ctx="[受付ルーチン] progress.md 未作成。新規開発なら /00-start-project、既存アプリの取り込みなら /11-brownfield-intake を自分で起動する（Claude Code では Skill ツール。既存コードがあるのに /00 を実行しない）。"
fi

# 出力JSONの組み立て。python があれば json.dumps で厳密にエスケープする
# (改行と引用符だけの sed エスケープでは、D:\proj\x のような Windows パスが
#  入ると \ が素通しになり不正な JSON になる)。python の解決順は run-python.sh と
# 同じ python→python3(Store スタブ誤検出回避)とし、Windows の py ランチャも試す。
pybin=""
if command -v python >/dev/null 2>&1; then pybin="python"
elif command -v python3 >/dev/null 2>&1; then pybin="python3"
elif command -v py >/dev/null 2>&1; then pybin="py -3"
fi
out=""
if [[ -n "$pybin" ]]; then
  # ctx は生のまま渡す(%b 展開を挟むと注入文中の実バックスラッシュ \c/\t 等が破壊される)
  out=$(printf '%s' "$ctx" | $pybin -c 'import sys, json
text = sys.stdin.buffer.read().decode("utf-8", "replace")
obj = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}
sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))' 2>/dev/null)
fi
if [[ -n "$out" ]]; then
  printf '%s\n' "$out"
else
  # フォールバック(python なし): 先頭で \ を二重化 → 実タブを \t 化 → 改行・引用符をエスケープする
  # (実タブが残ると JSON 文字列中に制御文字が入り不正になる実測あり)
  tab=$(printf '\t')
  esc=$(printf '%s' "$ctx" | sed 's/\\/\\\\/g' | sed "s/$tab/\\\\t/g" | sed ':a;N;$!ba;s/\n/\\n/g' | sed 's/"/\\"/g')
  printf '{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "%s"}}\n' "$esc"
fi

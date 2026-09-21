#!/usr/bin/env bash
# PreToolUse hook: ハードコードされた認証情報っぽい文字列の書き込みを検知する。
# 高確度パターン(クラウドの鍵形式・秘密鍵ヘッダ等)はdeny、
# 汎用パターン(api_key=... 等、誤検知しうる)はaskに留める。
# 第5回(2026-09-09 再監査 RG-5/CP-1): 読み取り系ツール名(readFile 等)は冒頭で除外する
# (検索・読取のペイロードに含まれる既存の鍵文字列で deny しない)。
input=$(cat)

# 共通ライブラリ(無ければ従来どおり全ペイロードを走査する=fail-open)
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
  printf '%s\n' '{"continue": true}'
  exit 0
fi

# AIza(Google APIキー)・(sk|rk)_live_(Stripe 本番キー)・hooks.slack.com/services/(Slack
# Incoming Webhook)も高確度として検知する(第2回監査)。
# 第5回(RG-5): npm トークン(npm_ + 36 桁)・JWT(eyJ….eyJ….署名)・Azure Storage の AccountKey= ・
# SAS トークンの sig= ・OpenAI(sk-proj-/sk-)・GitLab(glpat-)・Hugging Face(hf_)・PyPI(pypi-)・
# SendGrid(SG.)・DigitalOcean(dop_v1_)・Google OAuth(ya29.)・AWS 一時鍵(ASIA)・Slack App(xapp-)・
# PGP 秘密鍵ブロック を追加。大文字小文字を区別して照合する(.ps1 の -cmatch と同一)。
high_confidence='AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|-----BEGIN( RSA| EC| OPENSSH| DSA| PGP)? PRIVATE KEY( BLOCK)?-----|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|xapp-[0-9]-[A-Za-z0-9-]{10,}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|(sk|rk)_live_[A-Za-z0-9]{20,}|hooks\.slack\.com/services/|npm_[A-Za-z0-9]{36}|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|AccountKey=[A-Za-z0-9+/=]{40,}|(\?|&|&amp;)sig=[A-Za-z0-9%+/=]{30,}|sk-proj-[A-Za-z0-9_-]{40,}|sk-[A-Za-z0-9]{48}|glpat-[A-Za-z0-9_-]{20}|hf_[A-Za-z0-9]{34}|pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}|SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}|dop_v1_[a-f0-9]{64}|ya29\.[A-Za-z0-9_-]{30,}'
# 汎用パターン(第5回で緩和): 値のクォートを必須にしない。クォート付きは従来どおり 16 文字以上で ask、
# クォート無しは「16 文字以上かつ数字を1つ以上含む」場合のみ ask(識別子・関数呼び出し・
# process.env.X 等の非秘密値への誤検知を抑える。RG-5 のプローブで確認した規則)。
# `\\?` は JSON ペイロード中のエスケープ済み引用符(\")を許容するため。
generic_key='(api[_-]?key|secret|token|password|passwd|client_secret|access_key)'
generic_quoted="${generic_key}"'\\?["'"'"']?[[:space:]]*[:=][[:space:]]*\\?["'"'"'][A-Za-z0-9/+=_-]{16,}\\?["'"'"']'
generic_bare="${generic_key}"'\\?["'"'"']?[[:space:]]*[:=][[:space:]]*[A-Za-z0-9/+=_-]{16,}'

if printf '%s' "$input" | grep -Eq "$high_confidence"; then
  hook_log deny "secret:high-confidence"
  printf '%s\n' '{"continue": true, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "クラウド認証情報/秘密鍵とみられる文字列を検出しました。認証情報はコードやドキュメントに直接書かず、environment.mdに記載したシークレット管理先(GitHub Secrets等)を参照してください。検知された鍵を削除する目的の編集であれば、Writeツールで該当箇所を除去した全文に置き換えてください(denyはペイロード全体を走査するため、Editのold_stringにもマッチします)。"}}'
  exit 0
fi

generic_hit=0
if printf '%s' "$input" | grep -Eiq "$generic_quoted"; then
  generic_hit=1
else
  # クォート無しの値は数字を含む場合のみ(値部分だけを取り出して判定)
  bare=$(printf '%s' "$input" | grep -Eio "$generic_bare" | sed -E 's/.*[:=][[:space:]]*//')
  if [[ -n "$bare" ]] && printf '%s' "$bare" | grep -q '[0-9]'; then generic_hit=1; fi
fi
if [[ "$generic_hit" -eq 1 ]]; then
  hook_log ask "secret:generic"
  printf '%s\n' '{"continue": true, "systemMessage": "ハードコードされた認証情報らしき文字列を検出しました(誤検知の可能性もあります)。意図した内容か確認してください。", "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "認証情報らしきパターンを検出したため確認します。"}}'
  exit 0
fi

printf '%s\n' '{"continue": true}'

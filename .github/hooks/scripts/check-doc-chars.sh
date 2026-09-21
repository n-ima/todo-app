#!/usr/bin/env bash
# PostToolUse hook: docs/ 配下の .md への書き込み後に、編集ツールが混入させがちな
# 不可視文字・文字化け(NUL / 本文中BOM / ハングル / 置換文字 / 生タブ / 全角空白 /
# BMP外漢字)を数え、0件でなければ非ブロッキングの警告を出す。
# docs/ は lint・テストの対象外のため、機械的な検出手段はこのフックだけ
# (半角スペースがNULになる・「データ」の「デ」がハングルになる実例あり)。
# 注意: 検査対象の文字はこのスクリプト自身に混入させないため、正規表現は
# 必ず \uXXXX エスケープ表記で書く(リテラルで書かない)。
# 第5回(2026-09-09 再監査 CP-1): 読み取り系ツール名(readFile 等)は冒頭で除外する(_paths.sh の
# 共通関数。VS Code は matcher を無視して全ツールで発火するため)。
input=$(cat)

# 共通ライブラリ(無ければ従来どおり=fail-open)
# shellcheck source=_paths.sh
source "$(dirname "$0")/_paths.sh" 2>/dev/null || true
# shellcheck source=_log.sh
source "$(dirname "$0")/_log.sh" 2>/dev/null || true
type hook_log >/dev/null 2>&1 || hook_log() { :; }
# 1 回解析(A6-20): 以降の get_tool_name / collect_path_candidates / collect_new_text はキャッシュを返す
type parse_hook_input >/dev/null 2>&1 && parse_hook_input "$input"
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
file=${file//\\//}
file=$(printf '%s' "$file" | sed -E 's|/{2,}|/|g')

# docs 配下の .md 以外は対象外(テンプレートも含めて検査する)
if [[ -z "$file" || "$file" != *docs/*.md || ! -f "$file" ]]; then
  printf '%s\n' '{"continue": true}'
  exit 0
fi

detail=""
if command -v node >/dev/null 2>&1; then
  # 注意: node -e は1行で書く(複数行 node -e は出力が丸ごと消えることがある。
  # windows-shell-conventions §5 の既知の落とし穴)
  # 第3波(A7-M-8 / 再監査 2026-08-31 §6): ZWSP(U+200B)・NBSP(U+00A0)・双方向制御文字(U+202A〜202E / U+2066〜2069。
  # U+202E 等はインジェクション隠蔽に使われる)を追加。.ps1 と同じ 3 クラス・同じ名前。
  detail=$(node -e 'try{const t=require("fs").readFileSync(process.argv[1],"utf8");const cs=[["NUL","\\u0000","g"],["hombunBOM","(?!^)\\uFEFF","g"],["hanguru","[\\u1100-\\u11FF\\uAC00-\\uD7AF]","g"],["chikanmoji-U+FFFD","\\uFFFD","g"],["nama-tab","\\t","g"],["zenkaku-kuhaku","\\u3000","g"],["BMP-gai-kanji","[\\u{20000}-\\u{2FFFF}]","gu"],["ZWSP","\\u200B","g"],["NBSP","\\u00A0","g"],["bidi-seigyo","[\\u202A-\\u202E\\u2066-\\u2069]","g"]];const hits=[];for(const[n,p,f]of cs){const m=t.match(new RegExp(p,f));if(m)hits.push(n+":"+m.length)}process.stdout.write(hits.join(" "))}catch(e){}' "$file" 2>/dev/null)
elif command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then
  # node が無い環境の python フォールバック(node 版と同一の検査。他スクリプトと段数を揃える。
  # 第2回監査)。パターンは \uXXXX エスケープ表記のまま python が復号する(リテラル混入なし)。
  py=$(command -v python 2>/dev/null || command -v python3)
  detail=$("$py" -c 'import re,sys
try:
    t=open(sys.argv[1],encoding="utf-8",errors="replace").read()
    cs=[("NUL","\u0000"),("hombunBOM","(?!^)\uFEFF"),("hanguru","[\u1100-\u11FF\uAC00-\uD7AF]"),("chikanmoji-U+FFFD","\uFFFD"),("nama-tab","\t"),("zenkaku-kuhaku","\u3000"),("BMP-gai-kanji","[\U00020000-\U0002FFFF]"),("ZWSP","\u200B"),("NBSP","\u00A0"),("bidi-seigyo","[\u202A-\u202E\u2066-\u2069]")]
    hits=["%s:%d"%(n,len(re.findall(p,t))) for n,p in cs if re.search(p,t)]
    sys.stdout.write(" ".join(hits))
except Exception:
    pass' "$file" 2>/dev/null)
else
  # node も python も無い環境では NUL だけ grep -P で検査(それも無ければ検査なし = 安全側で許可)
  if grep -qP '\x00' "$file" 2>/dev/null; then detail="NUL:1+"; fi
fi

if [[ -n "$detail" ]]; then
  hook_log warn "$file $detail"
  esc_file=$(printf '%s' "$file" | sed 's/"/\\"/g')
  esc_detail=$(printf '%s' "$detail" | sed 's/"/\\"/g')
  # 警告は systemMessage(ユーザー向け)に加えて hookSpecificOutput.additionalContext にも併記し、
  # モデルにも同じ警告が届くようにする(第2回監査の出力契約統一)。
  msg="docs への書き込みに不可視文字/文字化けの疑いがあります(${esc_file}): ${esc_detail}。意図した文字か確認し、混入なら除去してください(NUL=ヌル文字, hombunBOM=本文中BOM, hanguru=ハングル, nama-tab=タブ, zenkaku-kuhaku=全角空白, ZWSP=ゼロ幅スペース U+200B, NBSP=ノーブレークスペース U+00A0, bidi-seigyo=双方向制御文字 U+202A〜202E/U+2066〜2069)。"
  printf '{"continue": true, "systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "%s"}}\n' "$msg" "$msg"
else
  printf '%s\n' '{"continue": true}'
fi

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop フック用の教訓候補自動起草(A3-9。ECC の instinct 機構の安全な翻案)。
Claude Code の Stop に既定配線済み(.claude/settings.json。Copilot 側は Stop 未配線
のため対象外)。

トランスクリプト末尾を走査し、「ユーザーの訂正らしい発話(違う/ではなく/やり直し/
そうじゃない 等)の直後にアシスタントが方針変更した」パターンを検出したら、教訓の
「候補」を **ローカルの下書き** `.github/hooks/logs/learnings-draft.local.md` に 1 行 append する。

    - [YYYY-MM-DD] (自動起草・要キュレーション) session=<id 先頭8> 訂正語「<語>」 ユーザー訂正「<redaction 済み・先頭 N 字>」の直後に方針変更あり

プライバシー境界(codex 監査 2026-08-31 H-01 / IA-20260831-03、再監査 2026-09-09 §5.4):
- 旧実装は docs/00-overview/learnings-pending.md(コミット対象の docs/ 配下)に transcript 由来の
  ユーザー文を無加工で残していた。訂正発話には資格情報・顧客名・本番 URL が混じり得るため、
  **docs/ には書かない**。下書きは logs/ 配下(.gitignore 済み=「local」欄。session_id と同格)にだけ置く。
- 抜粋は privacy-patterns.json の redaction(_log.redact)を通し、先頭 N 字(tools/usage-config.json の
  learnings_draft_excerpt_chars。既定 60。0 で抜粋なし=構造化欄のみ)に切り詰める。redaction 実装
  (_log.py)が無い環境では抜粋を書かない(生のプロンプト本文を残さない側に倒す)。
- 保持期間は learnings_draft_rotate_days(既定 30 日)。書き込みのたびに期限切れの候補行を落とす
  (受領書 logs/usage の 90 日ローテーションと同じ「ローカル一次データは有期限」の規律)。
- learnings.md への転記(昇格)は自動では行わない。**人か /10-retrospective(harness-retrospective スキル)の
  明示操作だけ**が、候補を抽象化した 1 行として転記する(原文コピーはしない)。

設計方針(誤検出しても無害な設計):
- 下書きはセッション開始時に**注入されない**(注入されるのは learnings.md のみ。inject-progress.sh 参照)。
  誤検出された候補は後のキュレーションで捨てられるだけで、以後のセッションの前提を汚染しない。
- 重複起草の抑止: 起草は同一セッション1回まで(logs/ のマーカーファイルで抑止)。検出できなかった
  Stop ではマーカーを書かず、後続の Stop で再走査できるようにする。同文の候補が既にあれば追記しない。
- 解析・書き込みはいかなる失敗でも継続する fail-open。exit 0 固定で、block も additionalContext も
  返さない(Stop の流れに一切介入しない)。
- docs/00-overview/progress.md があるプロジェクトのみ対象(ハーネス本体は対象外)。
- ユーザー発話はテキストパートのみ見る(tool_result はユーザーロールで届くが発話ではないので無視する)。
- 当ターンの最終応答は Stop ペイロードの `last_assistant_message` を一次入力にする(CC-14/A6-18)。
- 記録先は HARNESS_HOOK_LOG_DIR で差し替え可(selftest が実 logs/ を汚さない)。
- 自己テスト: python .github/hooks/scripts/draft-learnings.py --selftest(selftest.sh / .ps1 からも呼ばれる)
"""
import datetime
import json
import os
import re
import sys

# ユーザーの「訂正らしい発話」に含まれやすい表現(部分一致)
CORRECTION_MARKERS = ("違う", "違います", "ではなく", "じゃなくて", "やり直し",
                      "やりなおし", "そうじゃない", "そうではない", "間違い", "間違え",
                      "戻して")
# 訂正を受けた直後のアシスタント応答に現れやすい「方針変更らしい表現」(部分一致)
PIVOT_MARKERS = ("修正", "訂正", "やり直", "変更し", "承知しました", "かしこまりました",
                 "失礼しました", "申し訳", "改めます", "切り替え")

DRAFT_NAME = "learnings-draft.local.md"
# 旧置き場(docs/ 配下。もう書かない。残っていれば人が棚卸しして消す)
LEGACY_PENDING_REL = os.path.join("docs", "00-overview", "learnings-pending.md")
DEFAULT_ROTATE_DAYS = 30
DEFAULT_EXCERPT_CHARS = 60
HEADER = """# 教訓候補の下書き（learnings-draft.local。ローカル専用）

Stop フック（draft-learnings.py）が会話ログから自動起草した教訓の**候補**。
- このファイルは `.github/hooks/logs/`（.gitignore 済み）にだけ置かれ、コミットされない。
  セッション開始時にも注入されない（注入されるのは learnings.md のみ）。
- 抜粋は `privacy-patterns.json` の redaction を通した先頭 N 字だけ（`tools/usage-config.json` の
  `learnings_draft_excerpt_chars`）。候補行は `learnings_draft_rotate_days`（既定 30 日）で自動的に消える。
- **`docs/00-overview/learnings.md` への転記は自動では行わない。** 人か `/10-retrospective`
  （harness-retrospective スキル）が候補を抽象化した 1 行に書き直して転記し、転記済みの行は削除する。
  原文のコピーはしない。不要な行は削除してよい。

## 候補

"""
ENTRY_RE = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})\]")

# 判定ログ・redaction の共通実装(_log.py → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _log as _hooklog
except Exception:  # noqa: BLE001
    _hooklog = None
_PAYLOAD = {}  # main() が解析したペイロード(session_id / hook_event_name / tool_use_id を判定ログに載せる)


# selftest がマーカー/ログの置き場を一時ディレクトリに差し替えるための上書き(本番は None)
_LOG_DIR_OVERRIDE = None


def log_dir():
    if _LOG_DIR_OVERRIDE:
        return _LOG_DIR_OVERRIDE
    # 記録先(マーカー・判定ログ・下書き)は HARNESS_HOOK_LOG_DIR で差し替え可(selftest が実 logs/ を汚さない)
    override = os.environ.get("HARNESS_HOOK_LOG_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")


def hook_log(decision, target):
    """判定ログ(ローカルのみ・gitignore 対象)。書式・redaction・置き場の正は _log.py。失敗しても判定に影響させない。"""
    if _hooklog is None:
        return
    try:
        _hooklog.hook_log(decision, target, payload=_PAYLOAD, script=os.path.basename(__file__),
                          log_dir_override=log_dir())
    except Exception:  # noqa: BLE001
        pass


def load_config(cwd):
    """tools/usage-config.json(閾値の唯一の正。D076)から本フックの設定を読む。無ければ既定値。"""
    cfg = {"rotate_days": DEFAULT_ROTATE_DAYS, "excerpt_chars": DEFAULT_EXCERPT_CHARS}
    try:
        with open(os.path.join(cwd, "tools", "usage-config.json"), encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            v = data.get("learnings_draft_rotate_days")
            if isinstance(v, int) and v >= 0:
                cfg["rotate_days"] = v
            v = data.get("learnings_draft_excerpt_chars")
            if isinstance(v, int) and v >= 0:
                cfg["excerpt_chars"] = v
    except Exception:  # noqa: BLE001
        pass
    return cfg


def transcript_messages(path):
    """トランスクリプト(JSONL)から (role, text) の時系列リストを返す(失敗時は取れた分だけ)。"""
    msgs = []
    if not path or not os.path.exists(path):
        return msgs
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                role = entry.get("type")
                if role not in ("user", "assistant") or entry.get("isMeta"):
                    continue
                content = (entry.get("message") or {}).get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    # テキストパートのみ(tool_result 等は発話ではないので拾わない)
                    text = "\n".join(i.get("text") or "" for i in content
                                     if isinstance(i, dict) and i.get("type") == "text")
                else:
                    text = ""
                text = text.strip()
                # スラッシュコマンドの展開などシステム由来のユーザー行は発話とみなさない
                if not text or "<command-name>" in text or "<local-command-stdout>" in text:
                    continue
                msgs.append((role, text))
    except Exception:
        return msgs
    return msgs


def merge_last_assistant(msgs, last_assistant_message):
    """Stop ペイロードの最終応答(一次入力)をトランスクリプト由来の時系列に反映する(CC-14)。
    末尾のアシスタント発話と本文が一致していれば何もしない(反映済み)。未反映なら末尾に補う。
    フィールドが無い(None)・空なら旧ホスト/情報なしとして時系列をそのまま返す。"""
    if not isinstance(last_assistant_message, str):
        return msgs
    text = last_assistant_message.strip()
    if not text:
        return msgs
    last_asst = next((t for r, t in reversed(msgs) if r == "assistant"), None)
    if last_asst is not None and " ".join(last_asst.split()) == " ".join(text.split()):
        return msgs
    return msgs + [("assistant", text)]


def detect_correction(msgs):
    """「ユーザーの訂正らしい発話 → 直後のアシスタント応答が方針変更らしい」パターンを探し、
    最後に見つかった (ユーザー発話テキスト, 一致した訂正語) を返す(無ければ (None, None))。"""
    found = (None, None)
    for i, (role, text) in enumerate(msgs):
        if role != "user":
            continue
        marker = next((m for m in CORRECTION_MARKERS if m in text), None)
        if marker is None:
            continue
        nxt = next((t for r, t in msgs[i + 1:] if r == "assistant"), None)
        if nxt and any(m in nxt for m in PIVOT_MARKERS):
            found = (text, marker)
    return found


def one_line(text, limit=60):
    s = re.sub(r"\s+", " ", text).strip()
    return s[:limit] + ("…" if len(s) > limit else "")


def redacted_excerpt(text, limit):
    """redaction(privacy-patterns.json)→ 1 行化 → 先頭 limit 字。切り詰めは redaction の後(境界で切れた
    秘密値の前半を残さない=_log.sanitize_target と同じ順序)。redaction 実装が無い・limit 0 なら空文字
    (抜粋を書かない側に倒す)。"""
    if limit <= 0 or _hooklog is None:
        return ""
    try:
        return one_line(_hooklog.redact(text), limit)
    except Exception:  # noqa: BLE001
        return ""


def rotate_lines(lines, today, rotate_days):
    """候補行(`- [YYYY-MM-DD]` 始まり)のうち保持期間を過ぎたものを落とす。それ以外の行(ヘッダ等)は残す。"""
    if rotate_days <= 0:
        return lines
    cutoff = today - datetime.timedelta(days=rotate_days)
    kept = []
    for ln in lines:
        m = ENTRY_RE.match(ln)
        if m:
            try:
                d = datetime.date.fromisoformat(m.group(1))
            except ValueError:
                d = None
            if d is not None and d < cutoff:
                continue
        kept.append(ln)
    return kept


def read_stdin_utf8():
    """stdin をバイト列で読み UTF-8 で明示復号する(Windows の python は既定で CP932 復号になり、日本語を含む
    ペイロードが化ける・落ちる=D074 の指摘)。先頭 BOM(PowerShell 5.1 のパイプ)は除去する。"""
    raw = getattr(sys.stdin, "buffer", None)
    data = raw.read() if raw is not None else sys.stdin.read().encode("utf-8", "replace")
    return data.decode("utf-8", "replace").lstrip(chr(0xFEFF))


def write_draft(path, entry, today, rotate_days):
    """下書きへ 1 行追記する(保持期間切れの行は同時に落とす。temp → replace で原子的に書く)。
    戻り値: True=追記した / False=同文が既にあり追記しなかった。"""
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            existing = f.read()
    lines = existing.splitlines() if existing.strip() else HEADER.rstrip("\n").splitlines()
    lines = rotate_lines(lines, today, rotate_days)
    body_hit = entry.split("ユーザー訂正", 1)[-1] if "ユーザー訂正" in entry else entry
    duplicate = any(ln.strip() == entry.strip() or (body_hit and body_hit in ln) for ln in lines if ENTRY_RE.match(ln))
    if not duplicate:
        lines.append(entry)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")
    os.replace(tmp, path)
    return not duplicate


def main():
    # WindowsのstdoutはcodepageがCP932になりうる(本スクリプトは通常何も出力しないが統一)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        payload = json.loads(read_stdin_utf8())
    except Exception:
        return
    global _PAYLOAD
    _PAYLOAD = payload if isinstance(payload, dict) else {}
    if payload.get("stop_hook_active"):
        return
    cwd = payload.get("cwd") or os.getcwd()
    if not os.path.exists(os.path.join(cwd, "docs", "00-overview", "progress.md")):
        return
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(payload.get("session_id") or "unknown"))
    marker = os.path.join(log_dir(), "draft-learnings-%s.drafted" % sid)
    try:
        if os.path.exists(marker):
            return  # 同一セッションでの起草は1回まで
    except Exception:
        pass
    msgs = merge_last_assistant(
        transcript_messages(payload.get("transcript_path") or ""),
        payload.get("last_assistant_message"))
    correction, marker_word = detect_correction(msgs)
    if not correction:
        return  # 検出なし: マーカーは書かず、後続の Stop で再走査できるようにする
    cfg = load_config(cwd)
    today = datetime.date.today()
    excerpt = redacted_excerpt(correction, cfg["excerpt_chars"])
    entry = "- [%s] (自動起草・要キュレーション) session=%s 訂正語「%s」" % (
        today.strftime("%Y-%m-%d"), sid[:8], marker_word)
    if excerpt:
        entry += " ユーザー訂正「%s」の直後に方針変更あり" % excerpt
    else:
        entry += " 直後に方針変更あり(抜粋なし)"
    draft = os.path.join(log_dir(), DRAFT_NAME)
    try:
        if write_draft(draft, entry, today, cfg["rotate_days"]):
            hook_log("draft", "learnings-draft.local session=%s marker=%s" % (sid, marker_word))
        else:
            hook_log("skip", "duplicate-candidate session=%s" % sid)
        # 起草済み(既出で見送った場合も含む)をマーカーに記録し、同一セッションの以後の Stop では再起草しない
        os.makedirs(log_dir(), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as f:
            f.write("drafted\n")
    except Exception:
        pass  # fail-open(下書きが書けない環境でも Stop の流れは妨げない)


# ---------------------------------------------------------------- selftest

def selftest():
    """一時ディレクトリにフィクスチャを作り、起草先(logs/ 配下・docs/ には書かない)/redaction/保持期間/
    1回限り/重複抑止/鮮度(last_assistant_message)/対象外を検証する。"""
    global _LOG_DIR_OVERRIDE
    import io
    import shutil
    import tempfile
    ok = True
    try:  # CP932 コンソールでも日本語の PASS/FAIL 行で落ちない
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + ((" (%s)" % detail) if detail and not cond else ""))
        ok = ok and cond

    tmp = tempfile.mkdtemp(prefix="draft-learnings-selftest-")
    _LOG_DIR_OVERRIDE = os.path.join(tmp, "logs")
    draft = os.path.join(_LOG_DIR_OVERRIDE, DRAFT_NAME)
    try:
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, "docs", "00-overview"))
        os.makedirs(os.path.join(proj, "tools"))
        with open(os.path.join(proj, "docs", "00-overview", "progress.md"), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\n-->\n")
        learnings = os.path.join(proj, "docs", "00-overview", "learnings.md")
        with open(learnings, "w", encoding="utf-8") as f:
            f.write("## 教訓\n- [2026-01-01] 既存の教訓\n")
        legacy = os.path.join(proj, LEGACY_PENDING_REL)
        tpath = os.path.join(tmp, "t.jsonl")

        def entry(role, text):
            return json.dumps({"type": role, "message": {"content": [{"type": "text", "text": text}]}},
                              ensure_ascii=False)

        def write_transcript(lines):
            with open(tpath, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

        def run(payload):
            payload = dict(payload)
            payload.setdefault("transcript_path", tpath)
            payload.setdefault("cwd", proj)
            old_in, old_out = sys.stdin, sys.stdout
            sys.stdin = io.StringIO(json.dumps(payload, ensure_ascii=False))
            sys.stdout = io.StringIO()
            try:
                main()
                return sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = old_in, old_out

        def draft_text():
            if not os.path.exists(draft):
                return ""
            with open(draft, encoding="utf-8") as f:
                return f.read()

        # 1. 訂正→方針変更が transcript に揃っている -> logs/ の下書きに起草。docs/ には何も書かない
        write_transcript([entry("user", "テストを先に書いて"), entry("assistant", "書きます"),
                          entry("user", "違う、先にテストではなく再現手順を書いて"),
                          entry("assistant", "承知しました。再現手順から書き直します")])
        out = run({"session_id": "d1"})
        check("訂正→方針変更(transcript 完備) -> logs/ の下書きに候補を起草", "再現手順を書いて" in draft_text(), draft_text()[-120:])
        check("候補行に session と訂正語が入る", "session=d1" in draft_text() and "訂正語「違う」" in draft_text(), draft_text()[-160:])
        check("docs/00-overview/learnings-pending.md は作らない(旧経路の停止)", not os.path.exists(legacy))
        with open(learnings, encoding="utf-8") as f:
            check("learnings.md は変更しない(昇格は人か /10 の明示操作だけ)", f.read() == "## 教訓\n- [2026-01-01] 既存の教訓\n")
        check("Stop の流れに介入しない(無出力)", out == "", out[:80])
        n1 = draft_text().count("(自動起草")
        # 2. 同一セッション2回目 -> マーカーにより再起草しない
        run({"session_id": "d1"})
        check("同一セッション2回目 -> 再起草しない(マーカー)", draft_text().count("(自動起草") == n1)
        # 3. 別セッションでも同文の候補は重複追記しない
        run({"session_id": "d2"})
        check("別セッション・同文 -> 重複追記しない", draft_text().count("(自動起草") == n1)
        # 4. 鮮度(CC-14): 当ターンの応答が transcript 未反映でも last_assistant_message で検出
        os.remove(draft)
        write_transcript([entry("user", "ログ形式はそうじゃない、JSON にして")])
        out = run({"session_id": "d3", "last_assistant_message": "失礼しました。JSON 形式に切り替えます"})
        check("last_assistant_message で当ターンの方針変更を検出 -> 起草", "JSON にして" in draft_text(), draft_text()[-80:])
        # 5. 鮮度: transcript に反映済みなら二重に数えない(末尾一致で補わない)
        msgs = merge_last_assistant([("user", "違う"), ("assistant", "  修正します  ")], "修正します")
        check("反映済みの最終応答は補わない", len(msgs) == 2)
        msgs = merge_last_assistant([("user", "違う")], "修正します")
        check("未反映の最終応答は末尾に補う", msgs[-1] == ("assistant", "修正します"))
        check("フィールド無し(旧ホスト)は時系列をそのまま返す", merge_last_assistant([("user", "a")], None) == [("user", "a")])
        # 6. 訂正なし -> 起草しない・マーカーも書かない(後続 Stop で再走査できる)
        os.remove(draft)
        write_transcript([entry("user", "続けて"), entry("assistant", "続けます")])
        run({"session_id": "d4", "last_assistant_message": "続けます"})
        check("訂正なし -> 起草しない", not os.path.exists(draft))
        check("訂正なし -> マーカーを書かない", not os.path.exists(os.path.join(_LOG_DIR_OVERRIDE, "draft-learnings-d4.drafted")))
        # 7. stop_hook_active -> 何もしない
        write_transcript([entry("user", "違う、やり直し"), entry("assistant", "修正します")])
        run({"session_id": "d5", "stop_hook_active": True})
        check("stop_hook_active -> 起草しない", not os.path.exists(draft))
        # 8. progress.md 無し -> 対象外
        proj2 = os.path.join(tmp, "proj2")
        os.makedirs(proj2)
        run({"session_id": "d6", "cwd": proj2})
        check("progress.md 無し -> 対象外", not os.path.exists(draft))
        # 9. redaction(H-01): 訂正発話に含まれる canary secret(URL 埋め込み認証・API キー)が下書きに平文で残らない
        canary_ant = "sk-ant-api03-CANARYCANARYCANARY0123456789abcdef"
        canary_url = "https://alice:hunter2-canary@example.com/repo.git"
        write_transcript([entry("user", "違う、鍵は %s で URL は %s にして" % (canary_ant, canary_url)),
                          entry("assistant", "承知しました。修正します")])
        run({"session_id": "d7"})
        dt = draft_text()
        check("canary secret は下書きに残らず [REDACTED] になる", "CANARYCANARY" not in dt and "hunter2-canary" not in dt
              and "[REDACTED]" in dt, dt[-200:])
        check("redaction の後に先頭 60 字へ切り詰め(境界の秘密値を残さない)",
              all(len(ln.split("ユーザー訂正「", 1)[1].split("」")[0]) <= 61 for ln in dt.splitlines() if "ユーザー訂正「" in ln), dt[-200:])
        # 10. 保持期間(30 日): 期限切れの候補行は書き込み時に落ちる。tools/usage-config.json の値を読む
        with open(os.path.join(proj, "tools", "usage-config.json"), "w", encoding="utf-8") as f:
            json.dump({"learnings_draft_rotate_days": 30, "learnings_draft_excerpt_chars": 60}, f)
        old = "- [2020-01-01] (自動起草・要キュレーション) session=old 訂正語「違う」 ユーザー訂正「古い候補」の直後に方針変更あり"
        recent = "- [%s] (自動起草・要キュレーション) session=rec 訂正語「違う」 ユーザー訂正「最近の候補」の直後に方針変更あり" % (
            datetime.date.today() - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        with open(draft, "a", encoding="utf-8") as f:
            f.write(old + "\n" + recent + "\n")
        write_transcript([entry("user", "違う、順序を戻して"), entry("assistant", "承知しました。修正します")])
        run({"session_id": "d8"})
        dt = draft_text()
        check("保持期間切れ(2020-01-01)の候補行は落ち、期間内の行と新規行は残る",
              "古い候補" not in dt and "最近の候補" in dt and "順序を戻して" in dt, dt[-300:])
        check("ヘッダは維持される", dt.startswith("# 教訓候補の下書き"), dt[:60])
        check("rotate_lines: 0 日は回転しない", rotate_lines([old], datetime.date.today(), 0) == [old])
        # 11. 抜粋 0 字 -> 構造化欄のみ(プロンプト本文を一切書かない設定)
        with open(os.path.join(proj, "tools", "usage-config.json"), "w", encoding="utf-8") as f:
            json.dump({"learnings_draft_excerpt_chars": 0}, f)
        write_transcript([entry("user", "違う、この文は残らないはず"), entry("assistant", "承知しました。修正します")])
        run({"session_id": "d9"})
        dt = draft_text()
        check("excerpt_chars=0 -> 抜粋なし(訂正語・session のみ)", "この文は残らない" not in dt and "session=d9" in dt
              and "(抜粋なし)" in dt, dt[-200:])
        check("redacted_excerpt: limit 0 は空", redacted_excerpt("x", 0) == "")
    finally:
        _LOG_DIR_OVERRIDE = None
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

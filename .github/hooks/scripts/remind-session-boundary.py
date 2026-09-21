#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code の Stop フック: セッション境界の固定様式(D047)を機械検査し、違反を systemMessage で warn する。

背景(A6-19b(1) / 再監査 2026-09-09 PF-4): D047 の 2 行様式(①「このセッションの作業はここで完了です。」
②「次にやること: 新しいチャットを開き、最初に『<1 行>』と入力してください」)は、機械検査が無いため文言を
AGENTS.md に残すしかなかった(D082 決定4)。本フックがその検査になる。

検査(いずれも当ターンの最終応答 `last_assistant_message` が一次入力。公式 hooks 文書: transcript は
非同期書込で Stop 時点の最終メッセージを含まないことがある=CC-14):
- 陽性 A(境界案内の後ろに現セッション向けの文が続く): 「このセッションの作業はここで完了です」の後ろに、
  ②の行と括弧書きの理由 1 行以外の実質的な行がある(D047「案内の後ろに現在セッション向けの指示を続ける
  ことを禁止」)。
- 陽性 B(承認待ちの外部反映が残ったまま完了宣言): 完了宣言があり、かつ (B1) このセッションの transcript で
  最後の `git push` / `git tag` のツール呼び出し(Bash / PowerShell)が ask の拒否(tool_result の
  is_error + 拒否文言)または結果未着、または (B2) 最終応答自体が push / タグを未実施・承認待ちと述べている
  (D047 追記: 「push 未実施のまま『作業完了』と言い切る違反実例」)。

設計方針:
- **block しない**(D077 決定3。Stop の block は D085 決定5 の verdict 欠落に限る)。出力は
  `{"systemMessage": "..."}` だけ。判定ログは `_log.py`(hook-decisions.jsonl。A6-20)。
- fail-open: 壊れた JSON・欄の欠落・transcript 不在・例外はすべて無出力 exit 0。
- 完了宣言が無い普通のターンでは何もしない(判定ログにも書かない=ログ肥大の回避)。
- 陰性(2 行様式どおりで push 待ちも無い)は `allow` を 1 行記録する(様式の遵守率を後から数えられる)。
- stdin は UTF-8 で明示復号(D074 / D085 決定3)。
- 自己テスト: python .github/hooks/scripts/remind-session-boundary.py --selftest
  (selftest.sh / selftest.ps1 からも呼ばれる)。実機 Stop での発火(欄名)は開発機 2.1.201 では未検証。
"""
import json
import os
import re
import sys

# 判定ログの共通実装(_log.py → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _log as _hooklog
except Exception:  # noqa: BLE001
    _hooklog = None
_PAYLOAD = {}
_LOG_DIR_OVERRIDE = None  # selftest が記録先を差し替える(本番は None → HARNESS_HOOK_LOG_DIR → logs/)

COMPLETION_RE = re.compile(r"この(?:セッション|チャット)の作業は(?:ここで)?完了(?:です|しました)")
NEXT_STEP_RE = re.compile(r"次にやること")
# 案内の後ろに残ってよい行: 空行・区切り・括弧書きの理由 1 行(harness-maintenance.py の様式「次にやること+理由1行」)・
# ②の続き(『…』の引用や ``` コード行)
BENIGN_LINE_RE = re.compile(r"^(?:[（(].*|[-*_=]{3,}|`.*|.*[『』].*|>.*)$")
SHELL_TOOLS = {"Bash", "PowerShell"}
GIT_EXTERNAL_RE = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?[^|;&\n]*?\b(push|tag)\b")
REJECT_RE = re.compile(r"doesn't want to proceed|rejected|denied|not allowed|拒否|許可されませんでした", re.I)
# B2: 最終応答が push / タグを未実施・承認待ちと述べている
PENDING_PUSH_RE = re.compile(
    r"(?:git push|push|プッシュ|git tag|タグ付け|タグ)[^。\n]{0,40}"
    r"(?:未実施|未了|未だ|まだ|していません|しておりません|行っていません|承認(?:待ち|が必要|をお願い|を求め)|"
    r"お願いします|実行してください|残って)")


def log_dir():
    if _LOG_DIR_OVERRIDE:
        return _LOG_DIR_OVERRIDE
    override = os.environ.get("HARNESS_HOOK_LOG_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")


def hook_log(decision, target):
    if _hooklog is None:
        return
    try:
        _hooklog.hook_log(decision, target, payload=_PAYLOAD, script=os.path.basename(__file__),
                          log_dir_override=log_dir())
    except Exception:  # noqa: BLE001
        pass


def read_stdin_utf8():
    raw = getattr(sys.stdin, "buffer", None)
    data = raw.read() if raw is not None else sys.stdin.read().encode("utf-8", "replace")
    return data.decode("utf-8", "replace").lstrip(chr(0xFEFF))


def trailing_instructions(message):
    """完了宣言の後ろに残る実質的な行(②と括弧書き以外)を返す。完了宣言が無ければ None。"""
    m = None
    for m in COMPLETION_RE.finditer(message):
        pass
    if m is None:
        return None
    rest = message[m.end():]
    lines = [ln.strip() for ln in rest.splitlines()]
    # 完了宣言と同じ行の残り(「。」等)は無視する
    if lines:
        lines[0] = re.sub(r"^[。.\s]*", "", lines[0])
    out = []
    for ln in lines:
        if not ln:
            continue
        if NEXT_STEP_RE.search(ln):
            continue
        if BENIGN_LINE_RE.match(ln):
            continue
        if len(ln) < 8:
            continue
        out.append(ln)
    return out


def scan_transcript(path):
    """(最後の git push/tag ツール呼び出しの状態, 末尾のアシスタント本文) を返す。
    状態は None(呼び出し無し) / 'ok' / 'rejected' / 'pending'。失敗時は取れた分だけ。"""
    calls = []  # (tool_use_id, command)
    results = {}  # tool_use_id -> (is_error, text)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or ('"tool_use"' not in line and '"tool_result"' not in line):
                continue
            try:
                entry = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            content = (entry.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use" and item.get("name") in SHELL_TOOLS:
                    cmd = str((item.get("input") or {}).get("command") or "")
                    if GIT_EXTERNAL_RE.search(cmd):
                        calls.append((item.get("id") or "", cmd))
                elif item.get("type") == "tool_result":
                    body = item.get("content")
                    if isinstance(body, list):
                        body = " ".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in body)
                    results[item.get("tool_use_id") or ""] = (bool(item.get("is_error")), str(body or ""))
    if not calls:
        return None
    last_id, _cmd = calls[-1]
    if last_id not in results:
        return "pending"
    is_error, text = results[last_id]
    if is_error and REJECT_RE.search(text):
        return "rejected"
    return "ok"


def evaluate(message, transcript_path=None):
    """(warn 種別のリスト, 完了宣言の有無)。種別: 'trailing' / 'push-pending'。"""
    trailing = trailing_instructions(message)
    if trailing is None:
        return [], False
    kinds = []
    if trailing:
        kinds.append("trailing")
    pending = bool(PENDING_PUSH_RE.search(message))
    if not pending and transcript_path and os.path.exists(transcript_path):
        try:
            state = scan_transcript(transcript_path)
        except Exception:  # noqa: BLE001
            state = None
        pending = state in ("rejected", "pending")
    if pending:
        kinds.append("push-pending")
    return kinds, True


def build_message(kinds):
    parts = []
    if "trailing" in kinds:
        parts.append("完了宣言「このセッションの作業はここで完了です」の後ろに、現セッション向けの文が続いています"
                     "(D047: 案内は応答の最後に 1 回だけ、②「次にやること: 新しいチャットを開き、最初に『<1 行>』と"
                     "入力してください」で終える。続きの作業があるなら完了を宣言しない)")
    if "push-pending" in kinds:
        parts.append("承認待ちの外部反映(git push / タグ)が残ったまま完了を宣言しています"
                     "(D047 追記: 先に push / タグの承認を求め、反映してから完了を宣言する)")
    return "remind-session-boundary: " + " / ".join(parts)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    try:
        payload = json.loads(read_stdin_utf8())
    except Exception:  # noqa: BLE001
        return
    if not isinstance(payload, dict):
        return
    global _PAYLOAD
    _PAYLOAD = payload
    message = payload.get("last_assistant_message")
    if not isinstance(message, str) or not message.strip():
        return  # 欄が無い旧ホスト / 空応答: transcript は当ターンを含まないことがあるため判定しない(fail-open)
    try:
        kinds, declared = evaluate(message, payload.get("transcript_path") or "")
    except Exception:  # noqa: BLE001
        return
    if not declared:
        return
    sid = payload.get("session_id") or "unknown"
    if not kinds:
        hook_log("allow", "session-boundary ok session=%s" % sid)
        return
    hook_log("warn", "session-boundary %s session=%s" % ("+".join(kinds), sid))
    print(json.dumps({"systemMessage": build_message(kinds)}, ensure_ascii=False))


# ---------------------------------------------------------------- selftest

def selftest():
    global _LOG_DIR_OVERRIDE
    import io
    import shutil
    import tempfile
    ok = True
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + ((" (%s)" % detail) if detail and not cond else ""))
        ok = ok and cond

    tmp = tempfile.mkdtemp(prefix="remind-session-boundary-selftest-")
    _LOG_DIR_OVERRIDE = os.path.join(tmp, "logs")
    try:
        tpath = os.path.join(tmp, "t.jsonl")

        def write_transcript(lines):
            with open(tpath, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

        def tool_use(tid, cmd):
            return json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}]}}, ensure_ascii=False)

        def tool_result(tid, text, is_error=False):
            return json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": is_error}]}}, ensure_ascii=False)

        def run(payload, raw=None):
            old_in, old_out = sys.stdin, sys.stdout
            sys.stdin = io.StringIO(raw if raw is not None else json.dumps(payload, ensure_ascii=False))
            sys.stdout = io.StringIO()
            try:
                main()
                return sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = old_in, old_out

        good = ("設定を反映しました。\n\nこのセッションの作業はここで完了です。\n"
                "次にやること: 新しいチャットを開き、最初に『/06-implement-task』と入力してください\n"
                "（理由: 設定変更は新しいチャットで読み直される）")
        # 1. 陰性: 2 行様式どおり -> 無出力
        out = run({"session_id": "s1", "last_assistant_message": good})
        check("陰性: 2 行様式どおり -> 無出力", out == "", out[:80])
        # 2. 陽性 A: 案内の後ろに現セッション向けの指示が続く
        bad_a = good + "\n\n続けて、このチャットでテストも実行しておきます。src/ を修正してください。"
        out = run({"session_id": "s2", "last_assistant_message": bad_a})
        check("陽性 A: 案内の後ろに現セッション向けの文 -> systemMessage", '"systemMessage"' in out and "後ろに" in out, out[:80])
        # 3. 陽性 B2: 応答自体が push 未実施を述べたまま完了宣言
        bad_b = ("実装が終わりました。git push は承認が必要なので未実施です。\n\n"
                 "このセッションの作業はここで完了です。\n次にやること: 新しいチャットを開き、最初に『/09-release-checklist』と入力してください")
        out = run({"session_id": "s3", "last_assistant_message": bad_b})
        check("陽性 B2: push 未実施の記述 + 完了宣言 -> systemMessage", '"systemMessage"' in out and "外部反映" in out, out[:80])
        # 4. 陽性 B1: transcript で最後の git push が ask 拒否 + 完了宣言
        write_transcript([tool_use("t1", "git push origin main"),
                          tool_result("t1", "The user doesn't want to proceed with this tool use. The tool use was rejected", True)])
        out = run({"session_id": "s4", "last_assistant_message": good, "transcript_path": tpath})
        check("陽性 B1: transcript の push が拒否 + 完了宣言 -> systemMessage", '"systemMessage"' in out and "外部反映" in out, out[:80])
        # 5. 陰性: 拒否の後に再実行が成功していれば鳴らない
        write_transcript([tool_use("t1", "git push origin main"),
                          tool_result("t1", "The user doesn't want to proceed with this tool use.", True),
                          tool_use("t2", "git push origin main"),
                          tool_result("t2", "To github.com:x/y.git\n   abc..def  main -> main")])
        out = run({"session_id": "s5", "last_assistant_message": good, "transcript_path": tpath})
        check("陰性: 拒否後に push が成功 -> 無出力", out == "", out[:80])
        # 6. 陽性 B1: push の結果が未着(pending)
        write_transcript([tool_use("t3", "git tag -a v1.0.0 -m x")])
        out = run({"session_id": "s6", "last_assistant_message": good, "transcript_path": tpath})
        check("陽性 B1: git tag の結果未着 + 完了宣言 -> systemMessage", '"systemMessage"' in out, out[:80])
        # 7. 陰性: 完了宣言が無い普通のターン -> 無出力(拒否された push があっても)
        write_transcript([tool_use("t1", "git push origin main"),
                          tool_result("t1", "The user doesn't want to proceed with this tool use.", True)])
        out = run({"session_id": "s7", "last_assistant_message": "テストを 3 件追加しました。次は結合テストです。", "transcript_path": tpath})
        check("陰性: 完了宣言なし -> 無出力", out == "", out[:80])
        # 8. 欠落欄: last_assistant_message 無し -> 無出力
        out = run({"session_id": "s8", "transcript_path": tpath})
        check("欠落欄: last_assistant_message 無し -> 無出力(fail-open)", out == "", out[:80])
        # 9. 壊れた JSON -> 無出力
        out = run(None, raw="{not json")
        check("壊れた JSON -> 無出力(fail-open)", out == "", out[:80])
        # 10. 空の payload / 空応答 -> 無出力
        out = run({"session_id": "s10", "last_assistant_message": "   "})
        check("空の最終応答 -> 無出力", out == "", out[:80])
        # 11. transcript_path が存在しない + 完了宣言のみ -> 陰性(fail-open で判定続行)
        out = run({"session_id": "s11", "last_assistant_message": good, "transcript_path": os.path.join(tmp, "none.jsonl")})
        check("transcript 不在 + 2 行様式 -> 無出力", out == "", out[:80])
        # 12. 陽性 A+B が同時 -> 1 つの systemMessage に両方
        out = run({"session_id": "s12", "last_assistant_message": bad_b + "\n\nこのチャットで続けて README も直します。"})
        check("陽性 A+B 同時 -> 両方の文言", "後ろに" in out and "外部反映" in out, out[:80])
        # 13. 判定ログ: warn と allow が残る(_log が無い環境ではスキップ)
        logp = os.path.join(_LOG_DIR_OVERRIDE, "hook-decisions.jsonl")
        if _hooklog is not None:
            rows = [json.loads(ln) for ln in open(logp, encoding="utf-8") if ln.strip()]
            check("判定ログ: warn / allow が JSONL に残る",
                  any(r.get("decision") == "warn" for r in rows) and any(r.get("decision") == "allow" for r in rows),
                  str(len(rows)))
        else:
            check("判定ログ: _log 不在のためスキップ", True)
        # 14. 変種: 「このチャットの作業はここで完了しました」+ 後続指示
        out = run({"session_id": "s14", "last_assistant_message": "このチャットの作業はここで完了しました。\nでは続けて lint を実行してください。"})
        check("変種の完了宣言 + 後続指示 -> systemMessage", '"systemMessage"' in out, out[:80])
    finally:
        _LOG_DIR_OVERRIDE = None
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    main()

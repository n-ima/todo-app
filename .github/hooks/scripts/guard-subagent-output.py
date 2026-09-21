#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SubagentStop フック: 判定役(reviewer / spec-critic)の終了時に、最終応答に重大度トークン(verdict)が無ければ
1 回 block して「verdict を明示して終了」を要求する(2026-09-09 再監査 PF-10 / D073 open の SubagentStop deny)。

背景: implement / test は reviewer の返答テキストを証拠の主経路にしており、Fable 5.1 はナレーションが減る
(PF-10)ため、verdict 無しの短い返答で「レビュー済み」が成立してしまう。D073 の出力契約
(verdict: BLOCKER / MAJOR / MINOR / 承認、根拠の重大度 CRITICAL / HIGH / MEDIUM / LOW / INFO)を機械で要求する。

判定(agent_type が reviewer | spec-critic のときだけ):
  - last_assistant_message(無ければ agent_transcript_path の末尾アシスタント本文)に重大度トークン
    (BLOCKER / MAJOR / MINOR / 承認 または CRITICAL / HIGH / MEDIUM / LOW / 問題なし。単語境界つき)が
    1 つも無ければ {"decision": "block", "reason": "…verdict を明示して終了してください"}(判定ログは deny)。
  - 上限 2 回: 同じ session_id + agent_id で 2 回 block したら以後は allow(無限ループ防止。
    stop_hook_active が真のときも継続判断はするが、回数は上限で束縛する)。
  - 他の agent_type / agent_type 欠落 / 壊れた JSON / 例外は無出力 exit 0(fail-open)。
カウンタは logs/subagent-output-<session>-<agent>.count(HARNESS_HOOK_LOG_DIR で差し替え可)。

配線(.claude/settings.json の hooks.SubagentStop。log-subagent.py と並べる):
    bash .github/hooks/scripts/run-python.sh .github/hooks/scripts/guard-subagent-output.py
自己テスト: python .github/hooks/scripts/guard-subagent-output.py --selftest
"""
from __future__ import annotations

import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
try:
    import _log as _hooklog
except Exception:  # noqa: BLE001
    _hooklog = None

JUDGE_ROLES = ("reviewer", "spec-critic")
BLOCK_LIMIT = 2
VERDICT_RE = re.compile(r"(?<![A-Za-z])(BLOCKER|MAJOR|MINOR|CRITICAL|HIGH|MEDIUM|LOW)(?![A-Za-z])|承認|問題なし")
REASON = ("レビュー結果の verdict を明示して終了してください（BLOCKER / MAJOR / MINOR / 承認 のいずれか。"
          "根拠は file:line と重大度 CRITICAL / HIGH / MEDIUM / LOW / INFO。reviewer.agent.md「記録」節の出力契約。"
          "問題が無ければ「承認」または「問題なし」と書いてください）。")
_PAYLOAD = {}


def read_stdin_utf8():
    raw = getattr(sys.stdin, "buffer", None)
    data = raw.read() if raw is not None else sys.stdin.read().encode("utf-8", "replace")
    return data.decode("utf-8", "replace").lstrip(chr(0xFEFF))


def log_dir():
    override = os.environ.get("HARNESS_HOOK_LOG_DIR")
    if override:
        return override
    return os.path.join(SCRIPT_DIR, "..", "logs")


def hook_log(decision, target):
    if _hooklog is None:
        return
    try:
        _hooklog.hook_log(decision, target, payload=_PAYLOAD, script=os.path.basename(__file__),
                          log_dir_override=log_dir())
    except Exception:  # noqa: BLE001
        pass


def last_assistant_text(path):
    """サブエージェントのトランスクリプト(JSONL)から末尾のアシスタント本文を返す(取れなければ None)。"""
    if not path or not os.path.exists(path):
        return None
    text = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or '"assistant"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") != "assistant":
                    continue
                content = (entry.get("message") or {}).get("content")
                if isinstance(content, str) and content:
                    text = content
                elif isinstance(content, list):
                    parts = [i.get("text") or "" for i in content if isinstance(i, dict) and i.get("type") == "text"]
                    if any(parts):
                        text = "\n".join(parts)
    except Exception:
        return None
    return text


def has_verdict(text):
    return bool(text) and VERDICT_RE.search(text) is not None


def counter_path(session_id, agent_id):
    safe = lambda v: re.sub(r"[^A-Za-z0-9_.-]", "_", str(v or "unknown"))  # noqa: E731
    return os.path.join(log_dir(), "subagent-output-%s-%s.count" % (safe(session_id), safe(agent_id)))


def decide(payload):
    """(decision, tag)。decision は 'block' | 'allow' | None(対象外)。"""
    agent_type = payload.get("agent_type")
    if agent_type not in JUDGE_ROLES:
        return None, "skip:%s" % (agent_type or "-")
    text = payload.get("last_assistant_message")
    if not (isinstance(text, str) and text.strip()):
        text = last_assistant_text(payload.get("agent_transcript_path") or "")
    if has_verdict(text):
        return "allow", "%s verdict-present" % agent_type
    cpath = counter_path(payload.get("session_id"), payload.get("agent_id"))
    count = 0
    try:
        with open(cpath, encoding="utf-8") as f:
            count = int(f.read().strip() or 0)
    except Exception:
        count = 0
    if count >= BLOCK_LIMIT:
        return "allow", "%s verdict-missing limit-reached %d/%d" % (agent_type, count, BLOCK_LIMIT)
    try:
        os.makedirs(os.path.dirname(cpath), exist_ok=True)
        with open(cpath, "w", encoding="utf-8") as f:
            f.write(str(count + 1))
    except Exception:
        # カウンタが書けない環境で block すると上限が効かない(無限ループの温床)ため allow に倒す
        return "allow", "%s verdict-missing counter-write-failed" % agent_type
    return "block", "%s verdict-missing %d/%d" % (agent_type, count + 1, BLOCK_LIMIT)


def main():
    global _PAYLOAD
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        payload = json.loads(read_stdin_utf8())
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    _PAYLOAD = payload
    if payload.get("hook_event_name") not in (None, "SubagentStop"):
        return
    decision, tag = decide(payload)
    if decision is None:
        return
    if decision == "block":
        hook_log("deny", tag)
        print(json.dumps({"decision": "block", "reason": REASON}, ensure_ascii=False))
    else:
        hook_log("allow", tag)


# ---------------------------------------------------------------- selftest

def selftest():
    import io
    import shutil
    import tempfile
    ok = True
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    def fire(payload):
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(json.dumps(payload, ensure_ascii=False) if payload is not None else "")
        sys.stdout = io.StringIO()
        try:
            main()
            return sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out

    tmp = tempfile.mkdtemp(prefix="subagent-output-selftest-")
    old_env = os.environ.get("HARNESS_HOOK_LOG_DIR")
    try:
        logs = os.path.join(tmp, "logs")
        os.environ["HARNESS_HOOK_LOG_DIR"] = logs
        base = {"session_id": "s1", "cwd": tmp, "hook_event_name": "SubagentStop", "agent_id": "a1"}
        out = fire(dict(base, agent_type="reviewer", last_assistant_message="読みました。特に言うことはありません。"))
        check("reviewer: verdict 無し -> block(1 回目)", '"block"' in out and "verdict" in out, out[:120])
        out = fire(dict(base, agent_type="reviewer", last_assistant_message="まだ結論は出ていません。"))
        check("reviewer: verdict 無し -> block(2 回目)", '"block"' in out, out[:120])
        out = fire(dict(base, agent_type="reviewer", last_assistant_message="まだです。"))
        check("reviewer: 3 回目は上限で allow(無限ループ防止)", out == "", out[:120])
        out = fire(dict(base, agent_id="a2", agent_type="reviewer",
                        last_assistant_message="### 2026-09-10 10:30 / implementation / 対象: TASK-1 / verdict: 承認"))
        check("reviewer: 承認 -> allow", out == "", out[:120])
        out = fire(dict(base, agent_id="a3", agent_type="spec-critic", last_assistant_message="MAJOR: 要件 R-3 が矛盾"))
        check("spec-critic: MAJOR -> allow", out == "", out[:120])
        out = fire(dict(base, agent_id="a4", agent_type="reviewer", last_assistant_message="src/app.py:10 — 問題なし（INFO）"))
        check("reviewer: 問題なし -> allow", out == "", out[:120])
        out = fire(dict(base, agent_id="a5", agent_type="reviewer", last_assistant_message="The highway is lowly lit; majority agreed."))
        check("reviewer: 語境界(HIGHway/LOWly/MAJORity は不一致) -> block", '"block"' in out, out[:120])
        out = fire(dict(base, agent_id="a6", agent_type="task-worker", last_assistant_message="done"))
        check("task-worker: 対象外 -> 無出力", out == "", out[:120])
        out = fire(dict(base, agent_id="a7", last_assistant_message="done"))
        check("agent_type 欠落 -> 無出力", out == "", out[:120])
        # last_assistant_message 無し -> agent_transcript_path の末尾アシスタント本文を見る
        tpath = os.path.join(tmp, "agent-a8.jsonl")
        with open(tpath, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "verdict: MINOR 指摘 1 件"}]}}) + "\n")
        out = fire(dict(base, agent_id="a8", agent_type="reviewer", agent_transcript_path=tpath))
        check("transcript 末尾に MINOR -> allow", out == "", out[:120])
        with open(tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "以上です"}]}}) + "\n")
        out = fire(dict(base, agent_id="a8", agent_type="reviewer", agent_transcript_path=tpath))
        check("transcript 末尾が verdict 無し -> block", '"block"' in out, out[:120])
        out = fire(dict(base, agent_id="a9", agent_type="reviewer", hook_event_name="Stop", last_assistant_message="x"))
        check("SubagentStop 以外のイベント -> 無出力", out == "", out[:120])
        out = fire(None)
        check("空入力 -> 無出力・クラッシュなし", out == "")
        old_in = sys.stdin
        sys.stdin = io.StringIO("{broken")
        try:
            buf = io.StringIO(); old_out = sys.stdout; sys.stdout = buf
            try:
                main()
            finally:
                sys.stdout = old_out
        finally:
            sys.stdin = old_in
        check("壊れた JSON -> 無出力", buf.getvalue() == "")
        logp = os.path.join(logs, _hooklog.LOG_NAME) if _hooklog else None
        check("判定ログ: deny(block)と allow が JSONL に残る", bool(logp) and os.path.exists(logp)
              and '"decision":"deny"' in open(logp, encoding="utf-8").read().replace(" ", "")
              and '"script":"guard-subagent-output.py"' in open(logp, encoding="utf-8").read().replace(" ", ""),
              str(logp))
    finally:
        if old_env is None:
            os.environ.pop("HARNESS_HOOK_LOG_DIR", None)
        else:
            os.environ["HARNESS_HOOK_LOG_DIR"] = old_env
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

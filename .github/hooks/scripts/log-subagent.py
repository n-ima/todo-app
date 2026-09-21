#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SubagentStop / PostToolUse(matcher `Agent|Task`) から呼ばれ、サブエージェントの種別・要求モデル・
実行モデル(resolvedModel / modelsUsed)・effort を `.github/hooks/logs/usage/<session_id>.subagents.jsonl`
に 1 行追記する(受領書の「委譲」欄と実行モデルの証拠。2026-09-09 再監査 §5.3-6 / CC-2 第三層)。

1 行 = {"ts": ISO8601, "session_id": "…", "event": "SubagentStop|PostToolUse",
        "tool_name": "Agent|Task|null", "agent_type": "task-worker|reviewer|…|null", "agent_id": "…|null",
        "requested_model": "…|null", "resolved_model": "…|null", "models_used": [...]|null,
        "effort": "…|null", "status": "async_launched|completed|null", "tool_use_id": "…|null"}
欠落は null。

設計:
- **tool_name の自己フィルタ必須**(Copilot CLI は `.claude/settings.json` を重複排除なしで読み、
  VS Code は matcher を無視するため、Agent/Task 以外の PostToolUse でも起動され得る)。
- トークンは記録しない: PostToolUse の `usage`/`totalTokens` は「最終 API リクエスト分」で累計ではない
  (公式)。累計は transcript(session-receipt.py)から取る。background 起動(既定)は `async_launched` で
  resolvedModel だけが来る。
- `docs/00-overview/progress.md` があるプロジェクトのみ。stdout には何も出さない。
- fail-open: 例外は無出力 exit 0。Copilot の camelCase ペイロード(sessionId)は無視する。

自己テスト: python .github/hooks/scripts/log-subagent.py --selftest
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.normpath(os.path.join(os.environ.get("HARNESS_HOOK_LOG_DIR") or os.path.join(SCRIPT_DIR, "..", "logs"), "usage"))
GATE_REL = os.path.join("docs", "00-overview", "progress.md")
AGENT_TOOLS = {"Agent", "Task"}


def safe_sid(sid):
    return re.sub(r"[^A-Za-z0-9_-]", "_", sid or "")


def effort_of(payload):
    eff = payload.get("effort")
    if isinstance(eff, dict):
        eff = eff.get("level")
    return eff if isinstance(eff, str) and eff else None


def record_of(payload):
    """ペイロードから 1 行分の dict を作る。対象外なら None。"""
    event = payload.get("hook_event_name")
    sid = payload.get("session_id") or ""
    if not sid or event not in ("SubagentStop", "PostToolUse"):
        return None
    row = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "session_id": sid, "event": event,
           "tool_name": None, "agent_type": None, "agent_id": None, "requested_model": None,
           "resolved_model": None, "models_used": None, "effort": effort_of(payload), "status": None,
           "tool_use_id": None}
    if event == "PostToolUse":
        tool = payload.get("tool_name")
        if tool not in AGENT_TOOLS:
            return None
        ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        tr = payload.get("tool_response") if isinstance(payload.get("tool_response"), dict) else {}
        row.update({
            "tool_name": tool,
            "agent_type": ti.get("subagent_type") or tr.get("agentType") or None,
            "agent_id": tr.get("agentId") or None,
            "requested_model": ti.get("model") or None,
            "resolved_model": tr.get("resolvedModel") or None,
            "models_used": tr.get("modelsUsed") if isinstance(tr.get("modelsUsed"), list) else None,
            "status": tr.get("status") or None,
            "tool_use_id": payload.get("tool_use_id") or None,
        })
    else:
        row.update({"agent_type": payload.get("agent_type") or None, "agent_id": payload.get("agent_id") or None})
    return row


def append_row(row, logs):
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, safe_sid(row["session_id"]) + ".subagents.jsonl"), "a",
              encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(payload, logs=None):
    cwd = payload.get("cwd") or os.getcwd()
    if not os.path.exists(os.path.join(cwd, GATE_REL)):
        return False
    row = record_of(payload)
    if row is None:
        return False
    append_row(row, logs or LOGS_DIR)
    return True


def read_stdin_utf8():
    """stdin をバイト列で読み UTF-8 で明示復号する(Windows の python は既定で CP932 復号になり、日本語を含む
    ペイロードが化ける・落ちる=D074 の指摘)。先頭 BOM(PowerShell 5.1 のパイプ)は除去する。"""
    raw = getattr(sys.stdin, "buffer", None)
    data = raw.read() if raw is not None else sys.stdin.read().encode("utf-8", "replace")
    return data.decode("utf-8", "replace").lstrip(chr(0xFEFF))


def main(logs=None):
    try:
        payload = json.loads(read_stdin_utf8())
    except Exception:
        return
    if isinstance(payload, dict):
        run(payload, logs)


# ---------------------------------------------------------------- selftest

def selftest():
    import io
    import shutil
    import tempfile
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    def fire(payload, logs):
        sys.stdin = io.StringIO(json.dumps(payload) if payload is not None else "")
        buf, old = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            main(logs=logs)
        finally:
            sys.stdout = old
        return buf.getvalue()

    def rows(logs, sid):
        p = os.path.join(logs, sid + ".subagents.jsonl")
        if not os.path.exists(p):
            return []
        return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]

    tmp = tempfile.mkdtemp(prefix="logsubagent-selftest-")
    try:
        logs = os.path.join(tmp, "usage")
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, "docs", "00-overview"))
        open(os.path.join(proj, GATE_REL), "w").write("# p\n")
        base = {"session_id": "s1", "cwd": proj, "effort": {"level": "high"}}
        out = fire(dict(base, hook_event_name="PostToolUse", tool_name="Agent", tool_use_id="toolu_1",
                        tool_input={"subagent_type": "task-worker", "model": "sonnet", "prompt": "x"},
                        tool_response={"status": "completed", "agentId": "a1", "resolvedModel": "claude-sonnet-5",
                                       "modelsUsed": ["claude-sonnet-5"], "usage": {"input_tokens": 9}}), logs)
        r = rows(logs, "s1")
        check("PostToolUse(Agent completed): 1 行・要求/実行モデル・status", out == "" and len(r) == 1
              and r[0]["agent_type"] == "task-worker" and r[0]["requested_model"] == "sonnet"
              and r[0]["resolved_model"] == "claude-sonnet-5" and r[0]["status"] == "completed"
              and r[0]["tool_use_id"] == "toolu_1" and r[0]["effort"] == "high", str(r))
        check("PostToolUse: トークンは記録しない", "usage" not in r[0] and "totalTokens" not in r[0])
        fire(dict(base, hook_event_name="PostToolUse", tool_name="Task",
                  tool_input={"subagent_type": "reviewer"},
                  tool_response={"status": "async_launched", "agentId": "a2", "resolvedModel": "claude-fable-5-1"}), logs)
        r = rows(logs, "s1")
        check("PostToolUse(Task async_launched): status と resolvedModel、要求モデル null", len(r) == 2
              and r[1]["status"] == "async_launched" and r[1]["requested_model"] is None and r[1]["models_used"] is None, str(r[-1]))
        fire(dict(base, hook_event_name="PostToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_response={}), logs)
        check("PostToolUse(Bash): tool_name 自己フィルタで記録しない", len(rows(logs, "s1")) == 2)
        fire(dict(base, hook_event_name="SubagentStop", agent_id="a1", agent_type="task-worker",
                  agent_transcript_path="/x/subagents/agent-a1.jsonl", last_assistant_message="done"), logs)
        r = rows(logs, "s1")
        check("SubagentStop: agent_type/agent_id/effort、他は null", len(r) == 3 and r[2]["event"] == "SubagentStop"
              and r[2]["agent_type"] == "task-worker" and r[2]["agent_id"] == "a1" and r[2]["tool_name"] is None
              and r[2]["status"] is None and r[2]["resolved_model"] is None, str(r[-1]))
        check("行の必須キー集合", set(r[0]) == {"ts", "session_id", "event", "tool_name", "agent_type", "agent_id",
                                                 "requested_model", "resolved_model", "models_used", "effort", "status", "tool_use_id"})
        fire(dict(base, hook_event_name="PreToolUse", tool_name="Agent", tool_input={"subagent_type": "x"}), logs)
        check("他イベント(PreToolUse): 記録しない", len(rows(logs, "s1")) == 3)
        proj2 = os.path.join(tmp, "proj2")
        os.makedirs(proj2)
        fire(dict(base, session_id="s2", cwd=proj2, hook_event_name="SubagentStop", agent_id="b", agent_type="reviewer"), logs)
        check("progress.md ゲート: 無ければ記録しない", rows(logs, "s2") == [])
        fire({"sessionId": "s3", "cwd": proj, "hook_event_name": "SubagentStop", "agent_type": "reviewer"}, logs)
        check("Copilot camelCase(sessionId): 無視", rows(logs, "s3") == [])
        out = fire(None, logs)
        check("空入力: 無出力・クラッシュなし", out == "")
    finally:
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

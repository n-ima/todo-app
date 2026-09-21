#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""StopFailure フック: 異常終了(API エラーによるターン終了)の痕跡を残す(再監査 CC-9)。

Stop フック 3 本(remind-record / log-effort / draft-learnings)は「正常に応答を終えた」
ときにしか走らない。rate_limit / overloaded / server_error 等で会話が途中で落ちると、
そのセッションの GATE_STATUS 更新・記録の整合が取れないまま次セッションが始まる
(クラッシュ検知の空白)。本フックはその空白を埋めるため、停止理由と現在の
GATE_STATUS スナップショット・HEAD sha を

    .github/hooks/logs/abnormal-stop-<session_id>.json

に保存する。次回 SessionStart の inject-progress が「24 時間以内の abnormal-stop-*.json が
あれば『前回は異常終了。progress.md/tasks.md の整合を先に確認』を注入する」役を担う
(判定条件と注入文は inject-progress.sh/.ps1 側に置く)。

公式仕様(https://code.claude.com/docs/en/hooks、2026-09-10 確認):
- 入力: 共通欄(session_id / transcript_path / cwd / hook_event_name)+ `error_type`
  (rate_limit / overloaded / authentication_failed / oauth_org_not_allowed /
  account_on_hold / billing_error / invalid_request / model_not_found / server_error /
  max_output_tokens / unknown)+ `error_message`。
- 出力・exit code は無視される(terminalSequence を除く)。block はできないし、しない。
- matcher は error_type(英数字と `_` `|` の完全一致集合)。本ハーネスは全 error_type を記録
  するので matcher を付けない。

設計方針: いかなる失敗でも無出力 exit 0 の fail-open。error_message は 500 文字で切る
(本文を大量に溜めない)。記録先は環境変数 HARNESS_HOOK_LOG_DIR で差し替え可(自己テスト用)。

配線(.claude/settings.json の hooks.StopFailure):
    bash .github/hooks/scripts/run-python.sh .github/hooks/scripts/mark-abnormal-stop.py
自己テスト: python .github/hooks/scripts/mark-abnormal-stop.py --selftest
"""
import datetime
import json
import os
import re
import subprocess
import sys

GATE_REL = os.path.join("docs", "00-overview", "progress.md")
MESSAGE_LIMIT = 500


def read_stdin_utf8():
    raw = getattr(sys.stdin, "buffer", None)
    data = raw.read() if raw is not None else sys.stdin.read().encode("utf-8", "replace")
    return data.decode("utf-8", "replace").lstrip(chr(0xFEFF))


# 判定ログの共通実装(_log.py → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _log as _hooklog
except Exception:  # noqa: BLE001
    _hooklog = None
_PAYLOAD = {}  # main() が解析したペイロード(session_id / hook_event_name / tool_use_id を判定ログに載せる)


def log_dir():
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


def gate_status_block(cwd):
    """progress.md の <!-- GATE_STATUS ... --> ブロックを文字列で返す(無ければ None)。"""
    path = os.path.join(cwd, GATE_REL)
    try:
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        m = re.search(r"<!--\s*GATE_STATUS.*?(?:-->|\Z)", text, re.DOTALL)
        return m.group(0).strip() if m else None
    except Exception:
        return None


def head_sha(cwd):
    try:
        proc = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"],
                              capture_output=True, timeout=5,
                              encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            sha = proc.stdout.strip()
            return sha if re.fullmatch(r"[0-9a-f]{7,64}", sha) else None
    except Exception:
        pass
    return None


def build_record(payload):
    cwd = payload.get("cwd") or os.getcwd()
    sid = str(payload.get("session_id") or "unknown")
    message = str(payload.get("error_message") or "")
    return {
        "recorded_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "hook_event_name": payload.get("hook_event_name") or "StopFailure",
        "session_id": sid,
        "error_type": str(payload.get("error_type") or "unknown"),
        "error_message": message[:MESSAGE_LIMIT],
        "error_message_truncated": len(message) > MESSAGE_LIMIT,
        "cwd": cwd,
        "head_sha": head_sha(cwd),
        "gate_status": gate_status_block(cwd),
        "transcript_path": payload.get("transcript_path"),
    }


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        # Windows の stdin は既定でコードページ(CP932)復号になり、日本語を含む
        # ペイロードが化ける/読めなくなるため、バイト列を UTF-8 で明示復号する
        payload = json.loads(read_stdin_utf8())
    except Exception:
        return
    global _PAYLOAD
    _PAYLOAD = payload if isinstance(payload, dict) else {}
    if not isinstance(payload, dict):
        return
    record = build_record(payload)
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", record["session_id"])
    try:
        d = log_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "abnormal-stop-%s.json" % sid), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            f.write("\n")
        hook_log("record", "abnormal-stop session=%s error_type=%s"
                 % (sid, record["error_type"]))
    except Exception:
        pass


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

    tmp = tempfile.mkdtemp(prefix="abnormal-stop-selftest-")
    old_env = os.environ.get("HARNESS_HOOK_LOG_DIR")
    old_stdin = sys.stdin
    try:
        logs = os.path.join(tmp, "logs")
        os.environ["HARNESS_HOOK_LOG_DIR"] = logs
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, "docs", "00-overview"))
        with open(os.path.join(proj, GATE_REL), "w", encoding="utf-8") as f:
            f.write("# progress\n<!-- GATE_STATUS\nrequirements: done\n"
                    "design: in_progress\n-->\n本文\n")

        # 空入力: 無出力・何も書かない
        sys.stdin = io.StringIO("")
        main()
        check("empty stdin: 何も記録しない", not os.path.exists(logs))

        # 代表ペイロード(公式の StopFailure 入力欄)
        payload = {"session_id": "sess/1", "cwd": proj, "hook_event_name": "StopFailure",
                   "error_type": "rate_limit", "error_message": "Rate limit exceeded",
                   "transcript_path": os.path.join(tmp, "t.jsonl")}
        sys.stdin = io.StringIO(json.dumps(payload))
        main()
        out = os.path.join(logs, "abnormal-stop-sess_1.json")
        check("record: session_id を無害化したファイル名で保存", os.path.exists(out))
        rec = {}
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                rec = json.load(f)
        check("record: error_type/error_message を保持",
              rec.get("error_type") == "rate_limit"
              and rec.get("error_message") == "Rate limit exceeded")
        check("record: GATE_STATUS スナップショットを保持",
              "design: in_progress" in (rec.get("gate_status") or ""), str(rec.get("gate_status")))
        check("record: head_sha は None または hex(git 不在でも落ちない)",
              rec.get("head_sha") is None or re.fullmatch(r"[0-9a-f]{7,64}", rec["head_sha"]) is not None)
        check("record: 判定ログ(JSONL)に記録行",
              os.path.exists(os.path.join(logs, _hooklog.LOG_NAME)) if _hooklog else False)

        # error_message は 500 文字で切る
        payload["session_id"] = "sess2"
        payload["error_message"] = "x" * 1200
        sys.stdin = io.StringIO(json.dumps(payload))
        main()
        with open(os.path.join(logs, "abnormal-stop-sess2.json"), encoding="utf-8") as f:
            rec2 = json.load(f)
        check("record: error_message を 500 文字で打ち切り", len(rec2["error_message"]) == 500
              and rec2["error_message_truncated"] is True)

        # progress.md が無い cwd でも記録する(ハーネス本体や未初期化でも停止理由は残す)
        payload["session_id"] = "sess3"
        payload["cwd"] = tmp
        sys.stdin = io.StringIO(json.dumps(payload))
        main()
        with open(os.path.join(logs, "abnormal-stop-sess3.json"), encoding="utf-8") as f:
            rec3 = json.load(f)
        check("record: progress.md 無しでも gate_status=None で記録", rec3["gate_status"] is None)
    finally:
        sys.stdin = old_stdin
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

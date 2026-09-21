#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart フックから呼ばれ、セッション開始時点の基準値を
`.github/hooks/logs/usage/<session_id>.baseline.json` に保存する(2026-09-09 再監査 §5.2)。

受領書(tools/session-receipt.py)はこの基準値との差分で GATE_STATUS 遷移・tasks [x] 増分・
コミット数・差し戻し(done→in_progress の逆遷移)を出す。

設計:
- `source` が `startup` / `clear` / `resume` / `fork` のときだけ書く。**`compact` では書かない**
  (SessionStart は圧縮後にも再発火し、基準値が遷移後の値に上書きされて増分が消える)。
- **既に存在すれば上書きしない**(resume の再発火でも最古の値=セッション開始時点を保持する)。
- 内容: GATE_STATUS ブロック・HEAD sha・tasks [x] 数・`git diff --numstat`(開始時点の未コミット差分の
  指紋。他ソースの変更を自セッションに帰属させないための検算用)・時刻・source・model(あれば)。
  resume/fork 時の再キャッシュ費用欄(2.1.251+: seconds_since_last_response / context_tokens /
  prompt_cache_likely_expired / estimated_cache_write_usd)は**記録のみ**(CC-8)。additionalContext で
  数値をモデルに見せる提案はしない(Fable 系の早期終了誘発を避ける原則)。stdout には何も出さない。
- git は timeout 3s、失敗は null。`docs/00-overview/progress.md` があるプロジェクトのみ。fail-open。

自己テスト: python .github/hooks/scripts/session-baseline.py --selftest
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "logs", "usage"))
GATE_REL = os.path.join("docs", "00-overview", "progress.md")
TASKS_REL = os.path.join("docs", "03-implementation", "tasks.md")
SOURCES = {"startup", "clear", "resume", "fork"}
GATE_PHASES = ("requirements", "design", "implementation", "test", "release")
RESUME_FIELDS = ("seconds_since_last_response", "context_tokens", "prompt_cache_likely_expired", "estimated_cache_write_usd")


def safe_sid(sid):
    return re.sub(r"[^A-Za-z0-9_-]", "_", sid or "")


def parse_gate(path):
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    m = re.search(r"<!--\s*GATE_STATUS(.*?)(?:-->|\Z)", text, re.S)
    if not m:
        return None
    gate = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^\s*([a-z_]+)\s*:\s*(.+?)\s*$", line)
        if km and km.group(1) in GATE_PHASES:
            gate[km.group(1)] = km.group(2)
    return gate


def tasks_done(path):
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    return len(re.findall(r"^\s*[-*]\s+\[[xX]\]", text, re.M))


def git(cwd, *args):
    try:
        proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=3)
        if proc.returncode == 0:
            return proc.stdout
    except Exception:
        pass
    return None


def numstat(cwd):
    out = git(cwd, "diff", "--numstat")
    if out is None:
        return None
    result = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            try:
                result[parts[2]] = [int(parts[0]), int(parts[1])]
            except ValueError:
                result[parts[2]] = [None, None]  # バイナリ("-")
    return result


def build(payload):
    cwd = payload.get("cwd") or os.getcwd()
    head = git(cwd, "rev-parse", "HEAD")
    data = {
        "session_id": payload.get("session_id"), "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": payload.get("source"), "model": payload.get("model"),
        "gate": parse_gate(os.path.join(cwd, GATE_REL)),
        "tasks_done": tasks_done(os.path.join(cwd, TASKS_REL)),
        "head": head.strip() if head else None,
        "numstat": numstat(cwd),
    }
    resume = {k: payload.get(k) for k in RESUME_FIELDS if k in payload}
    if resume:
        data["resume"] = resume
    return data


def write_atomic(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def run(payload, logs=None):
    sid = payload.get("session_id") or ""
    if not sid or payload.get("source") not in SOURCES:
        return False
    cwd = payload.get("cwd") or os.getcwd()
    if not os.path.exists(os.path.join(cwd, GATE_REL)):
        return False
    path = os.path.join(logs or LOGS_DIR, safe_sid(sid) + ".baseline.json")
    if os.path.exists(path):
        return False
    return write_atomic(path, build(payload))


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

    def load(logs, sid):
        p = os.path.join(logs, sid + ".baseline.json")
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None

    tmp = tempfile.mkdtemp(prefix="baseline-selftest-")
    try:
        logs = os.path.join(tmp, "usage")
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, "docs", "00-overview"))
        os.makedirs(os.path.join(proj, "docs", "03-implementation"))
        with open(os.path.join(proj, GATE_REL), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\ndesign: in_progress\nimplementation: not_started\ntest: not_started\nrelease: not_started\n-->\n")
        with open(os.path.join(proj, TASKS_REL), "w", encoding="utf-8") as f:
            f.write("- [x] T1\n- [X] T2\n- [ ] T3\n")
        out = fire({"session_id": "s1", "cwd": proj, "source": "startup", "model": "claude-opus-5", "hook_event_name": "SessionStart"}, logs)
        b = load(logs, "s1")
        check("startup: baseline を書く(GATE・tasks [x]=2・source・model)・無出力", out == "" and b
              and b["gate"]["design"] == "in_progress" and b["tasks_done"] == 2 and b["source"] == "startup"
              and b["model"] == "claude-opus-5", str(b))
        check("git 無しディレクトリ: head/numstat は null", b["head"] is None and b["numstat"] is None)
        with open(os.path.join(proj, GATE_REL), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\ndesign: done\nimplementation: in_progress\ntest: not_started\nrelease: not_started\n-->\n")
        fire({"session_id": "s1", "cwd": proj, "source": "resume", "seconds_since_last_response": 5400,
              "context_tokens": 182340, "prompt_cache_likely_expired": True, "estimated_cache_write_usd": 1.14}, logs)
        check("resume(既存あり): 上書きしない(最古の値を保持)", load(logs, "s1")["gate"]["design"] == "in_progress")
        fire({"session_id": "s2", "cwd": proj, "source": "compact"}, logs)
        check("compact: 書かない", load(logs, "s2") is None)
        fire({"session_id": "s3", "cwd": proj, "source": "resume", "seconds_since_last_response": 5400,
              "context_tokens": 182340, "prompt_cache_likely_expired": True, "estimated_cache_write_usd": 1.14}, logs)
        b3 = load(logs, "s3")
        check("resume(新規): 再キャッシュ費用欄を記録のみ", b3 and b3["resume"]["estimated_cache_write_usd"] == 1.14
              and b3["resume"]["prompt_cache_likely_expired"] is True, str(b3))
        fire({"session_id": "s4", "cwd": proj, "source": "clear"}, logs)
        fire({"session_id": "s5", "cwd": proj, "source": "fork"}, logs)
        check("clear/fork: 書く", load(logs, "s4") and load(logs, "s5"))
        proj2 = os.path.join(tmp, "proj2")
        os.makedirs(proj2)
        fire({"session_id": "s6", "cwd": proj2, "source": "startup"}, logs)
        check("progress.md ゲート: 無ければ書かない", load(logs, "s6") is None)
        fire({"sessionId": "s7", "cwd": proj, "source": "startup"}, logs)
        check("Copilot camelCase(sessionId): 無視", load(logs, "s7") is None)
        out = fire(None, logs)
        check("空入力: 無出力・クラッシュなし", out == "")
        check("tmp ファイルが残らない", not [p for p in os.listdir(logs) if p.endswith(".tmp")])
        # git リポジトリでの HEAD と numstat
        if git(tmp, "--version") is not None:
            repo = os.path.join(tmp, "repo")
            shutil.copytree(proj, repo)
            env_ok = (subprocess.run(["git", "-C", repo, "init", "-q"], capture_output=True).returncode == 0)
            if env_ok:
                subprocess.run(["git", "-C", repo, "-c", "user.email=t@x", "-c", "user.name=t", "add", "-A"], capture_output=True)
                subprocess.run(["git", "-C", repo, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-q", "-m", "init"], capture_output=True)
                with open(os.path.join(repo, TASKS_REL), "a", encoding="utf-8") as f:
                    f.write("- [ ] T4\n")
                fire({"session_id": "g1", "cwd": repo, "source": "startup"}, logs)
                g = load(logs, "g1")
                check("git: HEAD sha(40 hex)と numstat の未コミット差分", g and re.fullmatch(r"[0-9a-f]{40}", g["head"] or "")
                      and any(k.endswith("tasks.md") and v == [1, 0] for k, v in (g["numstat"] or {}).items()), str(g))
            else:
                print("SKIP git init failed")
        else:
            print("SKIP git not found")
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code の statusLine コマンド: 表示 + 永続化の二役(2026-09-09 再監査 §5.3-1、RC-13)。

対話モードで費用(cost.total_cost_usd)・文脈使用率(context_window.used_percentage)・行数を
機械採取できる公式経路は statusline の stdin JSON だけ(hooks 入力にコストは来ない=公式 not planned)。
そこでこのスクリプトが (1) 生の stdin JSON をまず temp→rename で
`.github/hooks/logs/usage/<session_id>.status.json` に保存し(書込 1 回)、(2) その後に表示用の
パースをして 1 行を出す。受領書(tools/session-receipt.py)はこの JSON だけを host 値の出所にする。

配線(opt-in。利用者自身の ~/.claude/settings.json の statusLine を置き換えるため):
  "statusLine": {"type": "command",
                 "command": "bash .github/hooks/scripts/run-python.sh .github/hooks/scripts/statusline.py"}
  既存の statusline を持つ人は末尾に `--chain "<既存コマンド>"` を足すと、その出力を 2 行目以降に連結する。

制約(設計レビューの訂正を反映):
- 追加プロセス起動ゼロ(git / jq を呼ばない)。Windows では Git Bash + python の起動に 0.1〜0.3s
  掛かるため「50ms 以内」は成立しない。公式の 300ms デバウンス・実行中キャンセルの範囲内で動かす。
- 失敗時(JSON 破損・例外)は無出力にせず前回表示(`<sid>.statusline.txt`)を出す(空欄化を避ける)。
- `.ps1` 鏡は作らない(公式: Windows では Git Bash があれば Git Bash 経由で実行。本ハーネスは
  Git Bash 必須)。python 1 本で log-effort と同じ言語に揃え、鏡をゼロにする。
- フック本体ではないため tools/gen-docs.py の HOOK_HELPER_SCRIPTS に登録してある(フック数に混入させない)。
- `context_window.total_*` / `current_usage` は累計ではない(直近呼出/現在の文脈窓)ので表示にも使わない。

表示例: [06-implement-task] impl:in_progress | $8.42 | ctx 62% | Fable high | +412/-38 | 2.1.234
自己テスト: python .github/hooks/scripts/statusline.py --selftest
"""
import glob
import json
import os
import re
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "logs", "usage"))
GATE_REL = os.path.join("docs", "00-overview", "progress.md")
PHASES = (("requirements", "req"), ("design", "design"), ("implementation", "impl"), ("test", "test"), ("release", "rel"))
PLACEHOLDER = "[harness] statusline: no data yet"


def safe_sid(sid):
    return re.sub(r"[^A-Za-z0-9_-]", "_", sid or "")


def persist_raw(raw, sid, logs):
    """生 JSON をそのまま保存(temp→rename)。受領書側が公式のキーをそのまま読む。"""
    os.makedirs(logs, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=logs, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.replace(tmp, os.path.join(logs, safe_sid(sid) + ".status.json"))
        return True
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def gate_summary(project_dir):
    """progress.md の GATE_STATUS を 1 語に要約: 最初の未完了フェーズ、全 done なら done:5/5。"""
    if not project_dir:
        return None
    try:
        text = open(os.path.join(project_dir, GATE_REL), encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    m = re.search(r"<!--\s*GATE_STATUS(.*?)(?:-->|\Z)", text, re.S)
    if not m:
        return None
    states = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^\s*([a-z_]+)\s*:\s*([a-z_]+)", line)
        if km:
            states[km.group(1)] = km.group(2)
    if not states:
        return None
    done = sum(1 for k, _ in PHASES if states.get(k) == "done")
    for key, short in PHASES:
        st = states.get(key)
        if st and st != "done":
            return f"{short}:{st}"
    return f"done:{done}/{len(PHASES)}"


def phase_of(sid, logs):
    for name in (".receipt.draft.json", ".receipt.json"):
        try:
            with open(os.path.join(logs, safe_sid(sid) + name), encoding="utf-8") as f:
                data = json.load(f)
            phase = (data.get("meta") or {}).get("phase")
            if phase:
                return phase
        except Exception:
            continue
    return None


def compose(data, logs):
    sid = data.get("session_id") or ""
    ws = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    project_dir = ws.get("project_dir") or data.get("cwd")
    parts = []
    phase = phase_of(sid, logs) if sid else None
    gate = gate_summary(project_dir)
    parts.append(f"[{phase or '-'}]" + (f" {gate}" if gate else ""))
    cost = data.get("cost") if isinstance(data.get("cost"), dict) else {}
    usd = cost.get("total_cost_usd")
    parts.append(f"${usd:.2f}" if isinstance(usd, (int, float)) else "$n/a")
    cw = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
    pct = cw.get("used_percentage")
    parts.append(f"ctx {pct:.0f}%" if isinstance(pct, (int, float)) else "ctx n/a")
    model = data.get("model") if isinstance(data.get("model"), dict) else {}
    eff = data.get("effort") if isinstance(data.get("effort"), dict) else {}
    label = model.get("display_name") or model.get("id") or "?"
    if eff.get("level"):
        label += f" {eff['level']}"
    parts.append(label)
    la, lr = cost.get("total_lines_added"), cost.get("total_lines_removed")
    if isinstance(la, int) and isinstance(lr, int):
        parts.append(f"+{la}/-{lr}")
    if data.get("version"):
        parts.append(str(data["version"]))
    return " | ".join(parts)


def previous_display(sid, logs):
    candidates = []
    if sid:
        candidates.append(os.path.join(logs, safe_sid(sid) + ".statusline.txt"))
    try:
        candidates += sorted(glob.glob(os.path.join(logs, "*.statusline.txt")), key=os.path.getmtime, reverse=True)
    except Exception:
        pass
    for p in candidates:
        try:
            with open(p, encoding="utf-8") as f:
                line = f.read().strip()
            if line:
                return line
        except OSError:
            continue
    return PLACEHOLDER


def run(raw, logs, chain=None):
    """生 stdin bytes → 表示文字列。例外はここで吸収して前回表示を返す。"""
    sid = ""
    try:
        data = json.loads(raw.decode("utf-8-sig", errors="replace"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        sid = data.get("session_id") or ""
        if sid:
            persist_raw(raw, sid, logs)
        display = compose(data, logs)
        try:
            os.makedirs(logs, exist_ok=True)
            with open(os.path.join(logs, safe_sid(sid or "unknown") + ".statusline.txt"), "w", encoding="utf-8") as f:
                f.write(display + "\n")
        except Exception:
            pass
    except Exception:
        display = previous_display(sid, logs)
    if chain:
        try:
            import subprocess
            proc = subprocess.run(chain, shell=True, input=raw, capture_output=True, timeout=2)
            extra = proc.stdout.decode("utf-8", errors="replace").rstrip()
            if extra:
                display += "\n" + extra
        except Exception:
            pass
    return display


def main(argv=None, logs=None):
    argv = sys.argv[1:] if argv is None else argv
    chain = None
    if "--chain" in argv:
        i = argv.index("--chain")
        chain = argv[i + 1] if i + 1 < len(argv) else None
    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        raw = b""
    out = run(raw, logs or LOGS_DIR, chain=chain)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(out)


# ---------------------------------------------------------------- selftest

MOCK = {  # 公式 statusline ページ(2026-09-09 閲覧)の JSON 例を縮約したもの
    "cwd": "/current/working/directory", "session_id": "abc123",
    "transcript_path": "/path/to/transcript.jsonl",
    "model": {"id": "claude-opus-5", "display_name": "Opus"},
    "workspace": {"current_dir": "/current/working/directory", "project_dir": "/original/project/directory"},
    "version": "2.1.90", "output_style": {"name": "default"},
    "cost": {"total_cost_usd": 0.01234, "total_duration_ms": 45000, "total_api_duration_ms": 2300,
             "total_lines_added": 156, "total_lines_removed": 23},
    "context_window": {"total_input_tokens": 15500, "total_output_tokens": 1200, "context_window_size": 200000,
                       "used_percentage": 8, "remaining_percentage": 92,
                       "current_usage": {"input_tokens": 8500, "output_tokens": 1200,
                                         "cache_creation_input_tokens": 5000, "cache_read_input_tokens": 2000}},
    "exceeds_200k_tokens": False, "effort": {"level": "high"},
}


def selftest():
    import shutil
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    tmp = tempfile.mkdtemp(prefix="statusline-selftest-")
    try:
        logs = os.path.join(tmp, "usage")
        raw = json.dumps(MOCK).encode("utf-8")
        out = run(raw, logs)
        check("表示: 公式モック JSON → $ と ctx% と model/effort", "$0.01" in out and "ctx 8%" in out and "Opus high" in out
              and "+156/-23" in out and "2.1.90" in out, out)
        status = os.path.join(logs, "abc123.status.json")
        check("永続化: <sid>.status.json が生 JSON とバイト同一", os.path.exists(status) and open(status, "rb").read() == raw)
        check("永続化: tmp ファイルが残らない", not glob.glob(os.path.join(logs, "*.tmp")))
        out2 = run(b"{broken", logs)
        check("失敗時: 前回表示を出す(空欄化しない)", out2 == out, out2)
        out3 = run(b"", os.path.join(tmp, "empty"))
        check("空入力・履歴なし: プレースホルダを出す(クラッシュしない)", out3 == PLACEHOLDER, out3)
        nosid = dict(MOCK)
        nosid.pop("session_id")
        out4 = run(json.dumps(nosid).encode("utf-8"), logs)
        check("session_id 無し: 表示はするが status.json は書かない", "$0.01" in out4
              and len(glob.glob(os.path.join(logs, "*.status.json"))) == 1)
        # progress.md の GATE 要約と受領書 draft の工程
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, "docs", "00-overview"))
        with open(os.path.join(proj, GATE_REL), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\ndesign: done\nimplementation: in_progress\ntest: not_started\nrelease: not_started\n-->\n")
        with open(os.path.join(logs, "abc123.receipt.draft.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": {"phase": "06-implement-task"}}, f)
        withproj = dict(MOCK, workspace={"project_dir": proj})
        out5 = run(json.dumps(withproj).encode("utf-8"), logs)
        check("表示: 工程(draft)と GATE 要約(最初の未完了フェーズ)", out5.startswith("[06-implement-task] impl:in_progress |"), out5)
        with open(os.path.join(proj, GATE_REL), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\ndesign: done\nimplementation: done\ntest: done\nrelease: done\n-->\n")
        out6 = run(json.dumps(withproj).encode("utf-8"), logs)
        check("表示: 全 done は done:5/5", "done:5/5" in out6, out6)
        nulls = dict(MOCK, context_window={"used_percentage": None, "current_usage": None}, cost={})
        out7 = run(json.dumps(nulls).encode("utf-8"), logs)
        check("null 耐性: used_percentage/cost 欠落でも表示", "ctx n/a" in out7 and "$n/a" in out7, out7)
        plain = run(raw, logs)
        out8 = run(raw, logs, chain=f'"{sys.executable}" -c "print(\'chained\')"')
        check("--chain: 既存 statusline の出力を 2 行目に連結", out8.splitlines()[0] == plain and out8.splitlines()[-1] == "chained", out8)
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
        try:
            print(previous_display("", LOGS_DIR))
        except Exception:
            pass
    sys.exit(0)

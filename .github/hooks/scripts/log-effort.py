#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code の Stop / SessionEnd フックから呼ばれる「受領書」の薄い呼び出し側(v2)。

v1(D040)は docs/00-overview/effort-log.csv に upsert していたが、2026-09-09 再監査で
(a) 3 実プロジェクトすべてで CSV が git 未追跡・未 ignore(RC-4)、(b) 取った情報が利用者に
一度も届いていない(TM-3/RC-6)ことが確定したため、一次データを `.github/hooks/logs/usage/`
(gitignore 済)の受領書 JSON に一本化し、区切りのターンだけ利用者へ 3 行で提示する形に改めた。
既存 CSV は `python tools/effort-report.py --migrate <csv>` で取り込む(CSV への書き込みは廃止)。

集計・レンダリング・トリガ判定の**正は tools/session-receipt.py**(1 か所)。このファイルは
配線と fail-open だけを担う(harness-stats スキル・/99-status も同じ関数を使う)。

- Stop: 受領書 draft(`<sid>.receipt.draft.json`)を temp→rename で書き、区切り条件
  (GATE_STATUS 変化 / フェーズコマンド起動 / Δ費用 ≥ 閾値 / Δトークン ≥ 閾値 / N 発話ごと /
  文脈 ≥ 閾値。閾値の正は tools/usage-config.json)を満たすときだけ
  `{"systemMessage": "<3 行以内・絵文字なし・採取: Stop(暫定)>"}` を出す。
  **`decision: block` は決して返さない**(fail-open 規律)。
  区切りの判定は `stop_reason` ではなく(Stop 入力に存在しない)`last_assistant_message` 非空
  かつ `background_tasks` 空で行う。`stop_hook_active` のターンは表示しない。
- SessionEnd: draft → `<sid>.receipt.json` の rename と `sessions.jsonl` への 1 行追記だけ
  (重い集計はしない。SessionEnd の既定猶予 1.5 秒に対し配線側で timeout 10 を明示)。
- remind-record(同じ Stop で並列実行)との協調は、マーカーファイル
  `logs/remind-record-<sid>.blocked` の存在と更新時刻の確認**だけ**(判定ロジックは複製しない)。
  block されたターンの受領書は次の Stop に持ち越す。
- `docs/00-overview/progress.md` があるプロジェクトのみ(ハーネス本体の保守セッションは記録しない)。
- 全経路 fail-open: 例外は無出力 exit 0。Copilot 経由(`sessionId`/`transcriptPath` の camelCase)や
  session-receipt.py が無い配布先では何もしない。

自己テスト: python .github/hooks/scripts/log-effort.py --selftest
"""
import importlib.util
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HARNESS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
GATE_REL = os.path.join("docs", "00-overview", "progress.md")


def load_receipt_module(*roots):
    """tools/session-receipt.py を配線先(cwd)→自分のルートの順で探して読み込む。無ければ None。"""
    for base in roots:
        if not base:
            continue
        path = os.path.join(base, "tools", "session-receipt.py")
        if not os.path.exists(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("session_receipt", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:
            continue
    return None


def block_marker(sid):
    return os.path.join(SCRIPT_DIR, "..", "logs",
                        "remind-record-" + re.sub(r"[^A-Za-z0-9_-]", "_", sid) + ".blocked")


def detect_block_turn(sid, prev_state):
    """(このターンが remind-record に block されたか, マーカーの mtime)。
    マーカーはセッションに 1 度しか作られないため、前回の draft に記録した mtime と同じなら
    「既に持ち越し済み」として block 扱いにしない。"""
    marker = block_marker(sid)
    if not os.path.exists(marker):
        return False, None
    try:
        mt = os.path.getmtime(marker)
    except OSError:
        return False, None
    seen = (prev_state or {}).get("block_marker_mtime")
    if isinstance(seen, (int, float)) and abs(mt - seen) < 0.001:
        return False, mt
    return True, mt


def run(payload, root=None):
    """1 回のフック起動。戻り値は stdout に出す JSON 文字列(無ければ None)。"""
    session_id = payload.get("session_id") or ""
    transcript = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or os.getcwd()
    if not session_id or not transcript:
        return None
    if not os.path.exists(os.path.join(cwd, GATE_REL)):
        return None
    mod = load_receipt_module(root, cwd, HARNESS_ROOT)
    if mod is None:
        return None
    root = root or mod.ROOT
    logs = mod.logs_dir(root)
    event = payload.get("hook_event_name") or "Stop"
    if event == "SessionEnd":
        mod.finalize(session_id, logs, reason=payload.get("reason"))
        return None
    if event != "Stop" or not os.path.exists(transcript):
        return None
    prev = mod.load_json(mod.draft_path(logs, session_id))
    prev_state = prev.get("_state") if isinstance(prev, dict) else None
    receipt = mod.build_receipt(session_id, transcript, cwd, root=root, payload=payload)
    cfg = mod.load_config(root)
    block_turn, marker_mtime = detect_block_turn(session_id, prev_state)
    show, reasons, state = mod.decide_show(receipt, prev_state, cfg, payload, block_turn=block_turn)
    if marker_mtime is not None:
        state["block_marker_mtime"] = marker_mtime
    state["last_decision"] = {"show": show, "reasons": reasons, "at": mod.utc_now()}
    mod.write_draft(receipt, state, logs)
    if show:
        return json.dumps({"systemMessage": mod.render_short(receipt, cfg)}, ensure_ascii=False)
    return None


def read_stdin_utf8():
    """stdin をバイト列で読み UTF-8 で明示復号する(Windows の python は既定で CP932 復号になり、日本語を含む
    ペイロードが化ける・落ちる=D074 の指摘)。先頭 BOM(PowerShell 5.1 のパイプ)は除去する。"""
    raw = getattr(sys.stdin, "buffer", None)
    data = raw.read() if raw is not None else sys.stdin.read().encode("utf-8", "replace")
    return data.decode("utf-8", "replace").lstrip(chr(0xFEFF))


def main(root=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        # PowerShell 5.1 のパイプ経由だと先頭に UTF-8 BOM が付くことがあるため除去する
        payload = json.loads(read_stdin_utf8())
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    out = run(payload, root=root)
    if out:
        print(out)


# ---------------------------------------------------------------- selftest

def selftest():
    """フィクスチャ: 2 フェーズ跨ぎ / meta.json 有無 / サブエージェントのみテスト / block ターン /
    resume・clear / -p(sdk-cli)/ progress.md ゲート / camelCase(Copilot) / SessionEnd 確定。"""
    import io
    import shutil
    import tempfile
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    def fire(payload, root):
        sys.stdin = io.StringIO(json.dumps(payload))
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            main(root=root)
        finally:
            sys.stdout = old
        return buf.getvalue().strip()

    mod = load_receipt_module(HARNESS_ROOT)
    if mod is None:
        print("FAIL tools/session-receipt.py を読み込めない")
        print("SELFTEST FAIL")
        return 1
    tmp = tempfile.mkdtemp(prefix="logeffort-selftest-")
    try:
        root = mod._mkroot(tmp)
        logs = mod.logs_dir(root)
        proj = mod._mkproject(tmp)
        tdir = os.path.join(tmp, "t")
        os.makedirs(os.path.join(tdir, "s1", "subagents"))
        main_path = os.path.join(tdir, "s1.jsonl")
        with open(main_path, "w", encoding="utf-8") as f:
            f.write("\n".join([
                mod._cmd("05-implementation-plan"),
                mod._usage_msg("m1", "claude-fable-5", 100, 50, cr=50, w5=10, w1=20,
                               extra={"attributionSkill": "05-implementation-plan"}),
                mod._cmd("06-implement-task"),
                mod._prompt("TASK-3"),
                mod._usage_msg("m2", "claude-fable-5", 1, 1, content=[{"type": "tool_use", "id": "t1", "name": "Agent",
                                                                      "input": {"subagent_type": "task-worker"}}],
                               extra={"attributionSkill": "06-implement-task"}),
                mod._tool_result("t1", "done", extra={"toolUseResult": {"status": "completed", "agentId": "aaa111",
                                                                        "agentType": "task-worker", "resolvedModel": "claude-fable-5"}}),
                mod._usage_msg("m3", "claude-fable-5", 1, 1, content=[{"type": "tool_use", "id": "t2", "name": "Agent",
                                                                      "input": {"subagent_type": "reviewer"}}],
                               extra={"attributionSkill": "06-implement-task"}),
                mod._tool_result("t2", "agentId: bbb222"),
            ]) + "\n")
        sub = os.path.join(tdir, "s1", "subagents")
        with open(os.path.join(sub, "agent-aaa111.jsonl"), "w", encoding="utf-8") as f:
            f.write(mod._usage_msg("s1", "claude-sonnet-5", 10, 20, content=[{"type": "tool_use", "id": "b1", "name": "Bash",
                                                                              "input": {"command": "pytest -q"}}]) + "\n")
            f.write(mod._tool_result("b1", "28 passed in 0.5s") + "\n")
        with open(os.path.join(sub, "agent-aaa111.meta.json"), "w", encoding="utf-8") as f:
            json.dump({"agentType": "task-worker", "spawnDepth": 1}, f)
        with open(os.path.join(sub, "agent-bbb222.jsonl"), "w", encoding="utf-8") as f:
            f.write(mod._usage_msg("s2", "claude-fable-5", 3, 4) + "\n")
        mod.write_json_atomic(os.path.join(logs, "s1.baseline.json"),
                              {"session_id": "s1", "source": "startup", "gate": mod.parse_gate(os.path.join(proj, GATE_REL)),
                               "tasks_done": 2, "head": None})
        base_payload = {"session_id": "s1", "transcript_path": main_path, "cwd": proj, "hook_event_name": "Stop",
                        "last_assistant_message": "TASK-3 done", "background_tasks": [], "effort": {"level": "high"}}

        out = fire(base_payload, root)
        check("Stop(1回目): フェーズコマンド起動 → systemMessage 3行", out.startswith("{") and "systemMessage" in out
              and len(json.loads(out)["systemMessage"].splitlines()) == 3, out[:200])
        check("Stop: decision(block) を決して返さない", '"decision"' not in out)
        check("Stop: 採取タグ Stop(暫定) と出所タグ", "Stop(暫定)" in out and "transcript" in out
              and ("[公式" in out or "statusline 未取得" in out))
        draft = mod.load_json(mod.draft_path(logs, "s1"))
        check("draft: 2フェーズ跨ぎ→06、meta.json→task-worker、regex→reviewer",
              draft["meta"]["phase"] == "06-implement-task" and {t["agent"] for t in draft["tokens"]} == {"main", "task-worker", "reviewer"},
              str(draft["meta"]["phase"]) + str([t["agent"] for t in draft["tokens"]]))
        check("draft: サブエージェントのみテスト → ran/unknown", draft["outcome_common"]["tests"]["ran"]
              and draft["outcome_common"]["tests"]["result"] == "unknown", str(draft["outcome_common"]["tests"]))
        out2 = fire(base_payload, root)
        check("Stop(2回目・変化なし): 無出力", out2 == "", out2[:120])
        out3 = fire(dict(base_payload, stop_hook_active=True), root)
        check("Stop: stop_hook_active → 無出力", out3 == "")

        # block ターン: remind-record のマーカーが新しく現れた Stop では持ち越し、次の Stop で表示
        with open(os.path.join(proj, GATE_REL), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\ndesign: done\nimplementation: done\ntest: not_started\nrelease: not_started\n-->\n")
        marker = block_marker("s1")
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        created_marker = not os.path.exists(marker)
        with open(marker, "w", encoding="utf-8") as f:
            f.write("blocked once\n")
        try:
            out4 = fire(base_payload, root)
            check("block ターン: GATE 遷移があっても表示せず持ち越し", out4 == "" and
                  mod.load_json(mod.draft_path(logs, "s1"))["_state"]["pending_show"] is True, out4[:120])
            out5 = fire(base_payload, root)
            check("block の次の Stop: 持ち越し分を表示(GATE 遷移含む)", "systemMessage" in out5 and "implementation:in_progress->done" in out5, out5[:200])
        finally:
            if created_marker:
                try:
                    os.remove(marker)
                except OSError:
                    pass

        # SessionEnd(clear): draft → 確定 rename + sessions.jsonl 1 行、出力なし
        out6 = fire({"session_id": "s1", "transcript_path": main_path, "cwd": proj, "hook_event_name": "SessionEnd", "reason": "clear"}, root)
        check("SessionEnd: 無出力・確定ファイル・draft 消滅・sessions.jsonl 1行", out6 == ""
              and os.path.exists(mod.final_path(logs, "s1")) and not os.path.exists(mod.draft_path(logs, "s1"))
              and os.path.exists(os.path.join(logs, "sessions.jsonl")))
        fin = mod.load_json(mod.final_path(logs, "s1"))
        check("SessionEnd: 終了理由 clear と確定タグ", fin["meta"]["end_reason"] == "clear" and fin["meta"]["capture"] == "SessionEnd(確定)")
        out7 = fire({"session_id": "s9", "transcript_path": main_path, "cwd": proj, "hook_event_name": "SessionEnd", "reason": "other"}, root)
        check("SessionEnd: draft の無いセッションは何もしない", out7 == "" and not os.path.exists(mod.final_path(logs, "s9")))

        # resume: baseline(source=resume)がある → resumed フラグ(host $ は再開以降の注記)
        mod.write_json_atomic(os.path.join(logs, "s2.baseline.json"), {"session_id": "s2", "source": "resume", "gate": None, "tasks_done": None})
        fire(dict(base_payload, session_id="s2", last_assistant_message="x"), root)
        d2 = mod.load_json(mod.draft_path(logs, "s2"))
        check("resume: resumed フラグが付く", d2 and "resumed" in d2["flags"], str(d2 and d2["flags"]))

        # -p(sdk-cli entrypoint)の transcript → product claude-p
        p3 = os.path.join(tdir, "s3.jsonl")
        with open(p3, "w", encoding="utf-8") as f:
            f.write(mod._usage_msg("m1", "claude-sonnet-5", 2, 5, extra={"entrypoint": "sdk-cli"}) + "\n")
        fire({"session_id": "s3", "transcript_path": p3, "cwd": proj, "hook_event_name": "Stop", "last_assistant_message": "x", "background_tasks": []}, root)
        d3 = mod.load_json(mod.draft_path(logs, "s3"))
        check("-p: entrypoint sdk-cli → product claude-p", d3 and d3["meta"]["host"]["product"] == "claude-p", str(d3 and d3["meta"]["host"]))

        # ゲート: progress.md が無ければ何も書かない / Copilot の camelCase は無視
        proj2 = os.path.join(tmp, "proj2")
        os.makedirs(os.path.join(proj2, "docs", "00-overview"))
        out8 = fire(dict(base_payload, session_id="s4", cwd=proj2), root)
        check("progress.md ゲート: 未作成なら記録しない", out8 == "" and not os.path.exists(mod.draft_path(logs, "s4")))
        out9 = fire({"sessionId": "s5", "transcriptPath": main_path, "cwd": proj}, root)
        check("Copilot camelCase ペイロード: 何もしない", out9 == "" and not os.path.exists(mod.draft_path(logs, "s5")))
        out10 = fire({}, root)
        check("空ペイロード: 無出力", out10 == "")
        check("tmp ファイルが残らない", not [p for p in os.listdir(logs) if p.endswith(".tmp")])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    # Windows コンソール(cp932)で日本語の PASS/FAIL 行が化けないよう、selftest 経路でも UTF-8 に固定する
    # (再監査 2026-09-09 RG-15。フック経路は main() 内で同じ処理をする)
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

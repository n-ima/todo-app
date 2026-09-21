#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreModelSwitch / PostModelSwitch フック: メイン会話のモデル切替を記録する(再監査 CC-8 の記録部分)。

A/B(EV-2)と受領書(effort-report)の前提「1 セッション 1 モデル」は、`/model` や
クライアント側の切替、resume 時の復元で無音に崩れる。本フックは切替 1 件ごとに

    .github/hooks/logs/model-switch.jsonl

へ 1 行追記する(既定は記録のみ。block しない)。第6波(D074 open): `--mode ask` または
tools/usage-config.json の `model_switch_policy: ask` のときだけ、PreModelSwitch で GATE_STATUS に in_progress が
あり切替先が model-policy の allowed 外なら permissionDecision: ask を返す(既定 log は挙動不変)。

公式仕様(https://code.claude.com/docs/en/hooks、2026-09-10 確認):
- 入力: 共通欄 + `from_model`(現モデルの正準名)+ `to_model`(切替先のフル ID)。
  再監査 §1.1 が挙げる `source` / `context_tokens` / `estimated_cache_write_usd`
  (再キャッシュ費用欄)は 2026-09-10 時点の公式ページに記載が無い。あれば拾い、無ければ
  null で記録する(欄の有無自体が実測になる)。
- PreModelSwitch は同期イベントで、exit 2 または permissionDecision=deny で切替を阻止できる。
  **タイムアウトで打ち切られると切替が阻止される**ため、本フックは何も出力せず即終了し、
  配線側の timeout も短く(5 秒)する。PostModelSwitch は非同期で出力・exit code は無視される。
- matcher は切替先の正準モデル名。全切替を記録するので matcher を付けない。
- サブエージェントには発火しない(MP-5)。サブエージェントの実行モデルは
  PostToolUse(Agent).resolvedModel 経路(CC-2)。

設計方針: いかなる失敗でも無出力 exit 0 の fail-open(exit 2 を返す経路を持たない)。
記録先は環境変数 HARNESS_HOOK_LOG_DIR で差し替え可(自己テスト用)。

配線(.claude/settings.json の hooks.PreModelSwitch と hooks.PostModelSwitch の両方):
    bash .github/hooks/scripts/run-python.sh .github/hooks/scripts/log-model-switch.py
自己テスト: python .github/hooks/scripts/log-model-switch.py --selftest
"""
import datetime
import json
import os
import re
import sys

LOG_NAME = "model-switch.jsonl"
# 公式欄(from_model / to_model)に加えて、再監査が言及する費用欄を「あれば」拾う候補
OPTIONAL_FIELDS = ("source", "context_tokens", "estimated_cache_write_usd")


def read_stdin_utf8():
    raw = getattr(sys.stdin, "buffer", None)
    data = raw.read() if raw is not None else sys.stdin.read().encode("utf-8", "replace")
    return data.decode("utf-8", "replace").lstrip(chr(0xFEFF))


def log_dir():
    override = os.environ.get("HARNESS_HOOK_LOG_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")


def find_field(payload, name):
    """トップレベル、次に hookSpecificOutput 風の入れ子(将来の欄移動)から値を探す。"""
    if name in payload:
        return payload.get(name)
    for key in ("model_switch", "switch", "details"):
        sub = payload.get(key)
        if isinstance(sub, dict) and name in sub:
            return sub.get(name)
    return None


def build_record(payload):
    rec = {
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "session_id": str(payload.get("session_id") or "unknown"),
        "hook_event_name": str(payload.get("hook_event_name") or "unknown"),
        "from_model": payload.get("from_model"),
        "to_model": payload.get("to_model"),
    }
    for name in OPTIONAL_FIELDS:
        rec[name] = find_field(payload, name)
    return rec


# ---------------------------------------------------------------- ask モード(D074 open: PreModelSwitch の ask)
# 既定は記録のみ(log)。tools/usage-config.json の model_switch_policy が "ask"、または --mode ask のときだけ、
# PreModelSwitch で「GATE_STATUS に in_progress があり、かつ切替先モデルが役割別モデル方針(model-policy.yml)の
# allowed 外」なら permissionDecision: ask を返す(A/B や下流プロジェクトで想定外の切替を止めたい要望向け。
# 記録は ask でも必ず残す)。方針の表は tools/model_policy.py(要 PyYAML)→ 隣の guard-subagent-model.sh に
# 埋め込まれた同じ生成表の順で読み、読めなければ記録のみ(fail-open)。同期イベントなのでタイムアウトが切替阻止に
# なる=表の探索は 2 段までで外部プロセスを起動しない。
POLICY_VALUES = ("log", "ask")
_PAYLOAD = {}
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _log as _hooklog
except Exception:  # noqa: BLE001
    _hooklog = None


def harness_root(cwd):
    """tools/usage-config.json を探す起点: payload.cwd → 本スクリプトの 3 つ上(.github/hooks/scripts)。"""
    cands = []
    if cwd:
        cands.append(cwd)
    cands.append(os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")))
    for c in cands:
        if os.path.exists(os.path.join(c, "tools", "usage-config.json")):
            return c
    return cands[0]


def switch_policy(cwd, override=None):
    if override in POLICY_VALUES:
        return override
    try:
        cfg = json.load(open(os.path.join(harness_root(cwd), "tools", "usage-config.json"), encoding="utf-8-sig"))
        v = str(cfg.get("model_switch_policy") or "log").lower()
        return v if v in POLICY_VALUES else "log"
    except Exception:
        return "log"


def gate_in_progress(cwd):
    try:
        with open(os.path.join(cwd, "docs", "00-overview", "progress.md"), encoding="utf-8", errors="replace") as f:
            text = f.read(262144)
        i = text.find("<!-- GATE_STATUS")
        if i < 0:
            return False
        j = text.find("-->", i)
        block = text[i:j if j >= 0 else None]
        return "in_progress" in block
    except Exception:
        return False


def load_role_table(cwd):
    """役割表 JSON(dict)。tools/model_policy.py(import。PyYAML 必須)→ guard-subagent-model.sh の埋め込み表。"""
    root = harness_root(cwd)
    try:
        tools = os.path.join(root, "tools")
        if os.path.exists(os.path.join(tools, "model_policy.py")):
            sys.path.insert(0, tools)
            import model_policy as mp  # noqa: WPS433
            return json.loads(mp.role_table_json(mp.load_policy(root)))
    except Exception:
        pass
    try:
        sib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guard-subagent-model.sh")
        m = re.search(r"^role_table='([^']*)'", open(sib, encoding="utf-8").read(), re.M)
        if m:
            return json.loads(m.group(1))
    except Exception:
        pass
    return None


def model_allowed(model, table):
    """いずれかの役割の allowed_exact / allowed_prefix に当たれば True(表が無ければ None=判定不能)。"""
    if not isinstance(table, dict) or not isinstance(table.get("roles"), dict):
        return None
    m = str(model)
    for r in table["roles"].values():
        if not isinstance(r, dict):
            continue
        if m in (r.get("allowed_exact") or []):
            return True
        for p in r.get("allowed_prefix") or []:
            if p and (m == p or m.startswith(str(p))):
                return True
    return False


def ask_decision(payload, mode):
    """(ask するか, 理由)。PreModelSwitch かつ ask モードのときだけ判定する。"""
    if mode != "ask" or payload.get("hook_event_name") != "PreModelSwitch":
        return False, None
    cwd = payload.get("cwd") or os.getcwd()
    to_model = payload.get("to_model")
    if not to_model or not gate_in_progress(cwd):
        return False, None
    allowed = model_allowed(to_model, load_role_table(cwd))
    if allowed is not False:
        return False, None
    return True, ("進行中フェーズ(GATE_STATUS に in_progress)の途中でモデルを %s → %s に切り替えようとしています。"
                  "切替先は役割別モデル方針(.github/harness/model-policy.yml)の allowed 外です。A/B の「1 セッション 1 モデル」"
                  "前提と受領書の単価が崩れるため確認します(tools/usage-config.json の model_switch_policy=ask)。"
                  % (payload.get("from_model"), to_model))


def main(mode_override=None):
    global _PAYLOAD
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        # Windows の stdin は既定でコードページ(CP932)復号になるため、バイト列を UTF-8 で明示復号する
        payload = json.loads(read_stdin_utf8())
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    if payload.get("from_model") is None and payload.get("to_model") is None:
        return  # モデル切替の入力ではない(誤配線・将来の欄変更)。何もしない
    _PAYLOAD = payload
    mode = switch_policy(payload.get("cwd") or os.getcwd(), mode_override)
    ask, reason = False, None
    try:
        ask, reason = ask_decision(payload, mode)
    except Exception:
        ask, reason = False, None
    record = build_record(payload)
    record["decision"] = "ask" if ask else "log"
    try:
        d = log_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, LOG_NAME), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
    if ask:
        if _hooklog is not None:
            try:
                _hooklog.hook_log("ask", "model-switch %s -> %s" % (payload.get("from_model"), payload.get("to_model")),
                                  payload=payload, script=os.path.basename(__file__), log_dir_override=log_dir())
            except Exception:
                pass
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreModelSwitch", "permissionDecision": "ask",
                                                 "permissionDecisionReason": reason}}, ensure_ascii=False))


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

    tmp = tempfile.mkdtemp(prefix="model-switch-selftest-")
    old_env = os.environ.get("HARNESS_HOOK_LOG_DIR")
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    try:
        logs = os.path.join(tmp, "logs")
        os.environ["HARNESS_HOOK_LOG_DIR"] = logs
        out = os.path.join(logs, LOG_NAME)

        sys.stdin = io.StringIO("")
        main()
        check("empty stdin: 何も記録しない", not os.path.exists(out))

        sys.stdin = io.StringIO(json.dumps({"session_id": "s1", "hook_event_name": "Stop"}))
        main()
        check("from/to 無し: 何も記録しない", not os.path.exists(out))

        # PreModelSwitch: 公式欄のみ。標準出力に何も出さない(出力=判定と誤解されない)
        payload = {"session_id": "s1", "cwd": tmp, "hook_event_name": "PreModelSwitch",
                   "from_model": "claude-opus-5", "to_model": "claude-fable-5-1"}
        sys.stdin = io.StringIO(json.dumps(payload))
        sys.stdout = io.StringIO()
        main()
        captured = sys.stdout.getvalue()
        sys.stdout = old_stdout
        check("PreModelSwitch: 標準出力は空(allow に介入しない)", captured == "", repr(captured))
        rec = {}
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                rec = json.loads(f.read().splitlines()[-1])
        check("record: from/to/イベント名を保持",
              rec.get("from_model") == "claude-opus-5" and rec.get("to_model") == "claude-fable-5-1"
              and rec.get("hook_event_name") == "PreModelSwitch", str(rec))
        check("record: 費用欄が無ければ null で記録(欄の有無が実測になる)",
              all(rec.get(k) is None for k in OPTIONAL_FIELDS), str(rec))

        # PostModelSwitch: 費用欄があれば拾う
        payload.update({"hook_event_name": "PostModelSwitch", "source": "user",
                        "context_tokens": 12345, "estimated_cache_write_usd": 0.42})
        sys.stdin = io.StringIO(json.dumps(payload))
        main()
        with open(out, encoding="utf-8") as f:
            recs = [json.loads(l) for l in f.read().splitlines()]
        check("record: 追記(2 行目)", len(recs) == 2)
        check("record: source/context_tokens/estimated_cache_write_usd を保持",
              recs[-1].get("source") == "user" and recs[-1].get("context_tokens") == 12345
              and recs[-1].get("estimated_cache_write_usd") == 0.42, str(recs[-1]))
        check("record: 既定(log)では decision=log", all(r.get("decision") == "log" for r in recs), str(recs))

        # ask モード(D074 open): in_progress + allowed 外の切替先 → ask。それ以外は無出力
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, "docs", "00-overview"))
        with open(os.path.join(proj, "docs", "00-overview", "progress.md"), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\nimplementation: in_progress\n-->\n")
        table = {"roles": {"reviewer": {"allowed_exact": ["inherit", "fable"], "allowed_prefix": ["claude-fable-5", "claude-opus-5"]}}}
        check("model_allowed: exact / prefix / 外", model_allowed("fable", table) is True
              and model_allowed("claude-opus-5-20260101", table) is True and model_allowed("claude-haiku-4-5", table) is False
              and model_allowed("x", None) is None)
        table_real = load_role_table(os.getcwd())
        check("role table: model_policy.py または guard-subagent-model.sh の埋め込み表が読める",
              isinstance(table_real, dict) and "reviewer" in (table_real.get("roles") or {}), str(type(table_real)))

        def fire(payload, mode=None):
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            try:
                main(mode_override=mode)
                return sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

        base = {"session_id": "s2", "cwd": proj, "hook_event_name": "PreModelSwitch",
                "from_model": "claude-fable-5-1", "to_model": "claude-haiku-4-5-20251001"}
        out = fire(base, "ask")
        check("ask: in_progress + allowed 外 → permissionDecision ask", '"permissionDecision": "ask"' in out
              and '"PreModelSwitch"' in out, out[:160])
        out = fire(dict(base, to_model="claude-fable-5"), "ask")
        check("ask: allowed 内の切替先 → 無出力", out == "", out[:120])
        out = fire(base, "log")
        check("log(既定): allowed 外でも無出力(記録のみ)", out == "", out[:120])
        out = fire(dict(base, hook_event_name="PostModelSwitch"), "ask")
        check("ask: PostModelSwitch では判定しない", out == "", out[:120])
        with open(os.path.join(proj, "docs", "00-overview", "progress.md"), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\nimplementation: done\n-->\n")
        out = fire(base, "ask")
        check("ask: in_progress が無ければ無出力", out == "", out[:120])
        with open(os.path.join(logs, LOG_NAME), encoding="utf-8") as f:
            recs2 = [json.loads(l) for l in f.read().splitlines()]
        check("record: ask 判定も記録(decision=ask が 1 件)", sum(1 for r in recs2 if r.get("decision") == "ask") == 1, str([r.get("decision") for r in recs2]))
        check("switch_policy: usage-config.json の既定は log", switch_policy(os.getcwd()) == "log")
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout
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
    _mode = None
    try:
        if "--mode" in sys.argv:
            _mode = sys.argv[sys.argv.index("--mode") + 1].lower()
    except Exception:
        _mode = None
    try:
        main(mode_override=_mode)
    except Exception:
        pass
    sys.exit(0)

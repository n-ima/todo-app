#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""フック計測の集計(R-09 / A2-7。codex 監査 2026-08-31 §4 R-09・第2回監査 A2-7)。

判定ログ `.github/hooks/logs/hook-decisions.jsonl`(書き手は _log.sh / _log.ps1 / _log.py)から
  - script 別の所要 P50 / P95 / max(duration_ms)
  - decision 分布(deny / ask / warn / allow / block …)
  - host 別発火数(claude-code / copilot / unknown。旧行は null)
  - 「false ask/deny」の近似
を集計し、tools/usage-config.json の `hook_slo`(P95 上限・ask 率・false ask 率・最小件数)と照合して超過を WARN する。

読み手と P50/P95 の定義(最近傍順位法)は .github/hooks/scripts/_log.py が正(importlib で読み込み、本ツールは複製しない)。

集計の意味(すべて近似。数値は一次データからの機械集計で、品質の主張ではない):
- duration_ms は各スクリプトが _log を読み込んだ時点からログ書込までの経過で、プロセス起動(bash / powershell /
  python の起動)は含まない。体感レイテンシは起動分を上乗せして読む。duration_ms / host は 2026-09-17 以降の行にだけ
  あり、旧行(8 欄)は null=所要の集計から除外し decision 分布には数える。
- host は判定ログの host 欄。公式にホスト製品を識別する欄・環境変数は無く、ペイロードの欄名(snake_case=Claude Code /
  camelCase=Copilot)からの近似。
- 「false ask/deny」は「同じ session_id・script の並びで、ある target への ask / deny の直後の記録が同じ target への
  allow(10 分以内)」の件数。ask されたが結局そのまま通った(=止める必要が無かった疑い)の近似で、allow を記録する
  スクリプト(guard-harness-config-edit / guard-phase-scope / guard-template-edit 等)にだけ現れる。
- ask 率は allow を記録するスクリプトに限って (ask+deny) / 全判定 で出す(ask / deny しか記録しないスクリプトでは
  分母が無く常に 100% になるため対象外)。

使い方(リポジトリルートで):
    python tools/hook-metrics.py                   # 表を出す(SLO 超過は WARN 行。exit 0)
    python tools/hook-metrics.py --strict          # SLO 超過があれば exit 1
    python tools/hook-metrics.py --session <id>    # 1 セッションに絞る
    python tools/hook-metrics.py --since 2026-09-01T00:00:00
    python tools/hook-metrics.py --json            # 機械可読(集計 + warnings)
    python tools/hook-metrics.py --kpi-line        # 1 行要約(effort-report --kpi と受領書の hooks 欄が同じ書式で使う)
    python tools/hook-metrics.py --logs DIR        # 判定ログの置き場を差し替え(selftest・他プロジェクト)
    python tools/hook-metrics.py --include-legacy  # 旧 TSV(hook-decisions.log)も decision 分布に数える
    python tools/hook-metrics.py --selftest

終了コード: 0 = 正常(--strict で超過なし)  1 = --strict で SLO 超過 / selftest 失敗  2 = 実行エラー(読み手 _log.py 不在等)
依存: Python 3.x 標準ライブラリのみ(PyYAML 不要。配布先でも動く)
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_SCRIPTS_REL = os.path.join(".github", "hooks", "scripts")
LOGS_REL = os.path.join(".github", "hooks", "logs")
CONFIG_REL = os.path.join("tools", "usage-config.json")
# SLO の既定値(正は tools/usage-config.json の hook_slo。無い環境の既定)
DEFAULT_SLO = {"p95_duration_ms": 1000, "ask_deny_rate_max": 0.5, "false_ask_rate_max": 0.2, "min_records": 20}
FALSE_ASK_WINDOW_S = 600


def load_hooklog(root=None):
    """読み手 .github/hooks/scripts/_log.py を読み込む(root → 本ツール自身のハーネスの順。無ければ None)。"""
    for base in (root, ROOT):
        if not base:
            continue
        path = os.path.join(base, HOOK_SCRIPTS_REL, "_log.py")
        if not os.path.exists(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("harness_hook_log", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:  # noqa: BLE001
            return None
    return None


def load_slo(root=None):
    cfg = dict(DEFAULT_SLO)
    try:
        with open(os.path.join(root or ROOT, CONFIG_REL), encoding="utf-8-sig") as f:
            data = json.load(f)
        h = data.get("hook_slo") if isinstance(data, dict) else None
        if isinstance(h, dict):
            for k in DEFAULT_SLO:
                v = h.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    cfg[k] = v
    except Exception:  # noqa: BLE001
        pass
    return cfg


def _epoch(ts):
    try:
        dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
        return dt.timestamp()
    except Exception:  # noqa: BLE001
        return None


def collect(mod, logs, session=None, since=None, include_legacy=False):
    recs = []
    for r in mod.iter_decisions(logs, include_legacy=include_legacy):
        if session is not None and r.get("session_id") != session:
            continue
        if since and str(r.get("ts") or "")[:19] < str(since)[:19]:
            continue
        recs.append(r)
    return recs


def detect_false_ask(records):
    """同じ (session_id, script) の並びで、target への ask / deny の直後の記録が同じ target への allow(10 分以内)なら 1 件。"""
    last = {}
    n = 0
    examples = []
    for r in records:
        key = (r.get("session_id"), str(r.get("script") or "?"))
        dec = str(r.get("decision") or "")
        tgt = str(r.get("target") or "")
        t = _epoch(r.get("ts"))
        prev = last.get(key)
        if prev and dec == "allow" and prev[0] in ("ask", "deny") and tgt and prev[1] == tgt:
            if prev[2] is None or t is None or 0 <= (t - prev[2]) <= FALSE_ASK_WINDOW_S:
                n += 1
                if len(examples) < 5:
                    examples.append({"script": key[1], "prev": prev[0], "target": tgt[:60]})
        last[key] = (dec, tgt, t)
    ask_deny = sum(1 for r in records if r.get("decision") in ("ask", "deny"))
    return {"n": n, "rate": (round(n / ask_deny, 3) if ask_deny else None), "examples": examples}


def compute(records, mod):
    by_script = {}
    decisions = {}
    hosts = {}
    events = {}
    sessions = set()
    durations_all = []
    for r in records:
        sc = str(r.get("script") or "?")
        dec = str(r.get("decision") or "?")
        b = by_script.setdefault(sc, {"n": 0, "durations": [], "decisions": {}})
        b["n"] += 1
        b["decisions"][dec] = b["decisions"].get(dec, 0) + 1
        decisions[dec] = decisions.get(dec, 0) + 1
        d = r.get("duration_ms")
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            b["durations"].append(d)
            durations_all.append(d)
        h = str(r.get("host") or "null")
        hosts[h] = hosts.get(h, 0) + 1
        ev = str(r.get("hook_event") or "null")
        events[ev] = events.get(ev, 0) + 1
        if r.get("session_id"):
            sessions.add(r["session_id"])
    scripts = {}
    ask_num = ask_den = 0
    for sc, b in by_script.items():
        durs = b["durations"]
        ask_deny = b["decisions"].get("ask", 0) + b["decisions"].get("deny", 0)
        logs_allow = b["decisions"].get("allow", 0) > 0
        if logs_allow:
            ask_num += ask_deny
            ask_den += b["n"]
        scripts[sc] = {"n": b["n"], "with_duration": len(durs), "p50_ms": mod.percentile(durs, 50),
                       "p95_ms": mod.percentile(durs, 95), "max_ms": max(durs) if durs else None,
                       "decisions": dict(sorted(b["decisions"].items())), "ask_deny": ask_deny,
                       "logs_allow": logs_allow,
                       "ask_deny_rate": (round(ask_deny / b["n"], 3) if logs_allow and b["n"] else None)}
    return {"records": len(records), "with_duration": len(durations_all), "sessions": len(sessions),
            "p50_ms": mod.percentile(durations_all, 50), "p95_ms": mod.percentile(durations_all, 95),
            "max_ms": max(durations_all) if durations_all else None,
            "decisions": dict(sorted(decisions.items())), "hosts": dict(sorted(hosts.items())),
            "events": dict(sorted(events.items())), "by_script": dict(sorted(scripts.items())),
            "ask_deny": {"n": decisions.get("ask", 0) + decisions.get("deny", 0),
                         "rate_where_allow_logged": (round(ask_num / ask_den, 3) if ask_den else None),
                         "denominator": ask_den},
            "false_ask": detect_false_ask(records)}


def slo_check(m, slo):
    """SLO 超過の WARN 文言(空なら ok)。件数が min_records 未満の指標は判定しない(少数で鳴らさない)。"""
    warns = []
    minr = int(slo.get("min_records") or 0)
    p95_max = slo["p95_duration_ms"]
    if m["p95_ms"] is not None and m["with_duration"] >= minr and m["p95_ms"] > p95_max:
        warns.append(f"duration P95 {m['p95_ms']} ms > SLO {p95_max} ms(所要つき {m['with_duration']} 件)")
    for sc, s in m["by_script"].items():
        if s["p95_ms"] is not None and s["with_duration"] >= minr and s["p95_ms"] > p95_max:
            warns.append(f"{sc}: P95 {s['p95_ms']} ms > SLO {p95_max} ms(n={s['with_duration']})")
    ad = m["ask_deny"]
    if ad["rate_where_allow_logged"] is not None and ad["denominator"] >= minr \
            and ad["rate_where_allow_logged"] > slo["ask_deny_rate_max"]:
        warns.append(f"ask+deny 率 {ad['rate_where_allow_logged']:.0%} > SLO {slo['ask_deny_rate_max']:.0%}"
                     f"(allow を記録するスクリプト、分母 {ad['denominator']})")
    fa = m["false_ask"]
    if fa["rate"] is not None and ad["n"] >= minr and fa["rate"] > slo["false_ask_rate_max"]:
        warns.append(f"false ask/deny 近似 {fa['n']} 件({fa['rate']:.0%}) > SLO {slo['false_ask_rate_max']:.0%}")
    return warns


def _ms(v):
    return "-" if v is None else str(int(v))


def kpi_line(m, warns, slo):
    """1 行要約(effort-report --kpi / 受領書の hooks 欄と同じ書式)。"""
    if m["with_duration"] == 0:
        head = f"フック所要: 未計測（duration 付き判定 0 件 / 判定 {m['records']} 件）"
    else:
        hosts = "/".join(f"{k} {v}" for k, v in m["hosts"].items())
        head = f"フック所要: P50 {_ms(m['p50_ms'])} ms / P95 {_ms(m['p95_ms'])} ms（n={m['with_duration']}、host {hosts}）"
    ad = m["ask_deny"]
    rate = ad["rate_where_allow_logged"]
    mid = f"・ask+deny {ad['n']} 件" + (f"（allow 記録スクリプトの ask 率 {rate:.0%}）" if rate is not None else "") \
        + f"・false ask 近似 {m['false_ask']['n']} 件"
    tail = f" → SLO P95 ≤ {slo['p95_duration_ms']} ms: " + ("WARN " + "; ".join(warns) if warns else "ok")
    return head + mid + tail


def render(m, warns, slo, logs):
    L = [f"hook-metrics: {logs}",
         f"records {m['records']} (with duration {m['with_duration']}) / sessions {m['sessions']}",
         f"duration P50 {_ms(m['p50_ms'])} / P95 {_ms(m['p95_ms'])} / max {_ms(m['max_ms'])} ms"
         f"  (SLO P95 <= {slo['p95_duration_ms']} ms, min_records {slo['min_records']})",
         "decisions: " + (", ".join(f"{k} {v}" for k, v in m["decisions"].items()) or "(none)"),
         "hosts: " + (", ".join(f"{k} {v}" for k, v in m["hosts"].items()) or "(none)"),
         "events: " + (", ".join(f"{k} {v}" for k, v in m["events"].items()) or "(none)"),
         "",
         f"{'script':<34} {'n':>5} {'dur':>5} {'P50':>6} {'P95':>6} {'max':>6}  decisions (ask+deny rate)"]
    for sc, s in m["by_script"].items():
        decs = ", ".join(f"{k} {v}" for k, v in s["decisions"].items())
        rate = f" ({s['ask_deny_rate']:.0%})" if s["ask_deny_rate"] is not None else ""
        L.append(f"{sc:<34} {s['n']:>5} {s['with_duration']:>5} {_ms(s['p50_ms']):>6} {_ms(s['p95_ms']):>6} "
                 f"{_ms(s['max_ms']):>6}  {decs}{rate}")
    fa = m["false_ask"]
    L.append("")
    L.append(f"false ask/deny approx: {fa['n']}" + (f" ({fa['rate']:.0%} of ask+deny)" if fa["rate"] is not None else "")
             + (" e.g. " + "; ".join(f"{e['script']} {e['prev']}->allow {e['target']}" for e in fa["examples"]) if fa["examples"] else ""))
    L.append(f"ask+deny rate where allow is logged: "
             + (f"{m['ask_deny']['rate_where_allow_logged']:.0%} (denominator {m['ask_deny']['denominator']})"
                if m["ask_deny"]["rate_where_allow_logged"] is not None else "n/a"))
    L.append("")
    L += [f"WARN: {w}" for w in warns] or ["SLO: ok"]
    L.append("note: duration excludes process startup; host and false-ask are approximations (see docstring).")
    return "\n".join(L)


def run(root, logs, session=None, since=None, include_legacy=False):
    mod = load_hooklog(root)
    if mod is None:
        return None, None, None, "読み手 .github/hooks/scripts/_log.py が無い(ハーネスのフックが配布されていない)"
    slo = load_slo(root)
    recs = collect(mod, logs, session=session, since=since, include_legacy=include_legacy)
    m = compute(recs, mod)
    return m, slo_check(m, slo), slo, None


# ---------------------------------------------------------------- selftest

def selftest():
    ok = True
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    mod = load_hooklog(ROOT)
    check("_log.py(読み手・percentile)を読み込める", mod is not None and hasattr(mod, "percentile"))
    if mod is None:
        print("SELFTEST FAIL")
        return 1
    tmp = tempfile.mkdtemp(prefix="hook-metrics-selftest-")
    try:
        logs = os.path.join(tmp, "logs")
        os.makedirs(logs)
        lines = []

        def rec(ts, sid, script, dec, target, dur, host):
            return json.dumps({"ts": ts, "session_id": sid, "hook_event": "PreToolUse", "tool_name": "Bash",
                               "script": script, "decision": dec, "target": target, "tool_use_id": None,
                               "duration_ms": dur, "host": host}, ensure_ascii=False)

        for i in range(1, 21):
            lines.append(rec(f"2026-09-17T10:{i:02d}:00+09:00", "s1", "guard-a.sh", "ask", f"t{i}", 100 * i, "claude-code"))
        seq = [("ask", "x"), ("allow", "x"), ("deny", "y"), ("allow", "z"), ("allow", "x")]
        for i, (dec, tgt) in enumerate(seq, start=1):
            lines.append(rec(f"2026-09-17T11:{i:02d}:00+09:00", "s2", "guard-b.ps1", dec, tgt, 50, "copilot"))
        for i in range(3):  # 旧 8 欄の行(duration / host 無し)
            lines.append(json.dumps({"ts": f"2026-09-14T10:0{i}:00+09:00", "session_id": None, "hook_event": None, "tool_name": None,
                                     "script": "guard-c.sh", "decision": "deny", "target": "old", "tool_use_id": None}))
        with open(os.path.join(logs, "hook-decisions.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        m, warns, slo, err = run(tmp, logs)
        check("集計: 28 件・所要つき 25 件・セッション 2", err is None and m["records"] == 28 and m["with_duration"] == 25
              and m["sessions"] == 2, str(err or m))
        check("集計: 全体 P50 800 / P95 1900 / max 2000(最近傍順位法。25 件の 13 番目 / 24 番目)", m["p50_ms"] == 800 and m["p95_ms"] == 1900 and m["max_ms"] == 2000,
              str((m["p50_ms"], m["p95_ms"], m["max_ms"])))
        check("集計: host 別発火数(旧行は null)", m["hosts"] == {"claude-code": 20, "copilot": 5, "null": 3}, str(m["hosts"]))
        check("集計: decision 分布", m["decisions"] == {"allow": 3, "ask": 21, "deny": 4}, str(m["decisions"]))
        a, b, c = m["by_script"]["guard-a.sh"], m["by_script"]["guard-b.ps1"], m["by_script"]["guard-c.sh"]
        check("script 別: guard-a P95 1900・allow 未記録なので ask 率 n/a", a["p95_ms"] == 1900 and a["ask_deny_rate"] is None
              and a["logs_allow"] is False, str(a))
        check("script 別: guard-b は allow を記録するので ask 率 2/5=40%", b["ask_deny_rate"] == 0.4 and b["logs_allow"] is True
              and b["p95_ms"] == 50, str(b))
        check("script 別: 旧行だけの guard-c は所要 0 件・decision は数える", c["with_duration"] == 0 and c["p95_ms"] is None
              and c["decisions"] == {"deny": 3}, str(c))
        check("false ask 近似: ask x → allow x の直後連続 1 件(deny y → allow z は数えない)", m["false_ask"]["n"] == 1
              and m["false_ask"]["examples"][0]["script"] == "guard-b.ps1", str(m["false_ask"]))
        check("SLO: 既定(P95 1000 ms・min_records 20)で全体と guard-a が WARN、guard-b(n=5)は鳴らない",
              len(warns) == 2 and all("1900" in w for w in warns) and not any("guard-b" in w for w in warns), str(warns))
        check("SLO: min_records 100 なら少数で鳴らさない", slo_check(m, dict(slo, min_records=100)) == [])
        check("SLO: ask 率上限 0.3 で allow 記録スクリプトの 40% が WARN(分母 5 は min_records 5 で判定)",
              any("ask+deny" in w for w in slo_check(m, dict(slo, ask_deny_rate_max=0.3, min_records=5))))
        check("SLO: false ask 率上限 0(分母 ask+deny 25 件)で WARN", any("false ask" in w for w in slo_check(m, dict(slo, false_ask_rate_max=0.0))))
        m2, _w2, _s, _e = run(tmp, logs, session="s2")
        check("--session で絞る", m2["records"] == 5 and m2["hosts"] == {"copilot": 5}, str(m2["records"]))
        m3, _w3, _s, _e = run(tmp, logs, since="2026-09-17T11:00:00")
        check("--since で絞る(文字列比較・19 桁)", m3["records"] == 5, str(m3["records"]))
        line = kpi_line(m, warns, slo)
        check("--kpi-line: P50/P95・host・ask+deny・false ask・SLO 判定を 1 行に", "P50 800 ms / P95 1900 ms" in line
              and "claude-code 20" in line and "ask+deny 25 件" in line and "false ask 近似 1 件" in line and "WARN" in line, line)
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        m4, w4, s4, _e = run(tmp, empty)
        check("判定ログ無し: 0 件・未計測の 1 行・WARN なし", m4["records"] == 0 and w4 == [] and "未計測" in kpi_line(m4, w4, s4), kpi_line(m4, w4, s4))
        check("既定 SLO(usage-config 無し root)", load_slo(tmp) == DEFAULT_SLO, str(load_slo(tmp)))
        real = load_slo(ROOT)
        check("本体 usage-config.json の hook_slo を読む(p95_duration_ms は数値)", isinstance(real.get("p95_duration_ms"), (int, float)), str(real))
        table = render(m, warns, slo, logs)
        check("表: script 行と WARN 行", "guard-a.sh" in table and "WARN:" in table and "false ask/deny approx: 1" in table)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):  # main の表出力は selftest の結果行に混ぜない
            rc_json = main(["--root", tmp, "--logs", logs, "--json"])
            rc_strict = main(["--root", tmp, "--logs", logs, "--strict"])
            rc_ok = main(["--root", tmp, "--logs", logs, "--strict", "--session", "s2"])
        check("main --json は exit 0", rc_json == 0)
        check("main --strict は超過で exit 1、超過なし(--session s2)で exit 0", rc_strict == 1 and rc_ok == 0)
        with open(os.path.join(logs, "hook-decisions.log"), "w", encoding="utf-8") as f:
            f.write("2026-09-01T10:00:00\tguard-old.sh\twarn\tprogress.md\n")
        m5, _w5, _s, _e = run(tmp, logs, include_legacy=True)
        check("--include-legacy で旧 TSV を decision 分布に数える(所要は無し)", m5["records"] == 29 and m5["with_duration"] == 25
              and m5["decisions"].get("warn") == 1, str(m5["decisions"]))
        _m6, _w6, _s6, err6 = run(os.path.join(tmp, "nohooks"), logs)
        check("読み手 _log.py が本体にもあれば配布先 root でも動く(fallback)", err6 is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="フック計測の集計(hook-decisions.jsonl → P50/P95・decision 分布・host 別・false ask 近似・SLO 照合)")
    ap.add_argument("--root", default=ROOT, help="プロジェクトルート(既定: このファイルの親の親)")
    ap.add_argument("--logs", default=None, help="判定ログの置き場(既定: <root>/.github/hooks/logs)")
    ap.add_argument("--session", default=None, help="session_id で絞る")
    ap.add_argument("--since", default=None, help="この ISO 時刻以降の行だけ(文字列比較)")
    ap.add_argument("--include-legacy", action="store_true", help="旧 TSV(hook-decisions.log)も decision 分布に数える")
    ap.add_argument("--json", action="store_true", help="機械可読で出す")
    ap.add_argument("--kpi-line", action="store_true", help="1 行要約だけ出す")
    ap.add_argument("--strict", action="store_true", help="SLO 超過があれば exit 1")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    if args.selftest:
        return selftest()
    root = os.path.abspath(args.root)
    logs = os.path.abspath(args.logs) if args.logs else os.path.join(root, LOGS_REL)
    m, warns, slo, err = run(root, logs, session=args.session, since=args.since, include_legacy=args.include_legacy)
    if err:
        print("ERROR:", err)
        return 2
    if args.json:
        print(json.dumps({"logs": logs, "metrics": m, "warnings": warns, "slo": slo}, ensure_ascii=False, indent=1))
    elif args.kpi_line:
        print(kpi_line(m, warns, slo))
    else:
        print(render(m, warns, slo, logs))
    return 1 if (args.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())

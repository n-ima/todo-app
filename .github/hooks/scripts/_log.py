#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判定ログ(hook_log)の共通実装(python 系。sh は _log.sh、ps1 は _log.ps1 が同じ契約を持つ)。

2026-09-09 再監査 RC-5(hook-decisions.log は TSV 破損 5,368 行中 41 行・session_id 無し・hook_log が
18〜20 スクリプトに重複)/ RC-11(共通ライブラリの置き場未定義)/ A6-20 の実装。

書式(1 行 JSONL。欄の順序も固定):
    {"ts": ISO8601(ローカル時刻+オフセット), "session_id": "…"|null, "hook_event": "PreToolUse"|null,
     "tool_name": "Write"|null, "script": "guard-x.sh", "decision": "deny|ask|warn|allow|block|hold|skip|inject",
     "target": "…(redaction 済み・先頭 120 字・改行は JSON エスケープ)", "tool_use_id": "toolu_…"|null,
     "duration_ms": 412|null, "host": "claude-code|copilot|unknown"}
    duration_ms は本モジュール読込(=フックスクリプト開始の近似。プロセス起動のオーバーヘッドは含まない)からの
    経過ミリ秒、host はペイロードの欄名(snake_case=Claude Code / camelCase=Copilot)→ 環境変数 CLAUDE_PROJECT_DIR の
    順の近似判定(公式にホスト製品を識別する欄・環境変数は無い。R-09 / A2-7。欄は末尾に足し旧行は欠落=null で読める)。
置き場: .github/hooks/logs/hook-decisions.jsonl(gitignore 済。HARNESS_HOOK_LOG_DIR で差し替え可=selftest 用)。
512KB 超は書き込み前に後半 256KB(行境界)だけ残す(肥大化対策)。

redaction / 欄分類の正は同ディレクトリの privacy-patterns.json(.github/harness)(1 ファイルを 3 系統が読む)。
読み手(tools/session-receipt.py の hooks 欄・tools/hook-metrics.py)は iter_decisions() を使い、旧 TSV(hook-decisions.log:
ts \\t script \\t decision \\t target)も同じ dict 形で読める(後方互換。session_id / duration_ms / host は null)。
percentile() は P50/P95 の唯一の定義(最近傍順位法)。事故的停止の記録(logs/abnormal-stop-<sid>.json =
mark-abnormal-stop.py(StopFailure)、logs/watchdog-stop-<sid>.json = watchdog-continue.py(上限到達・トークン枯渇))は
iter_stop_records() / stop_records_for_sessions() が共通 dict で読む(読み手: tools/golden-eval.py・tools/e2e-run.py。A2-1)。

使い方(各 python フックから):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _log as hooklog
    hooklog.hook_log("deny", target, payload=payload, script=os.path.basename(__file__), log_dir=...)
自己テスト: python .github/hooks/scripts/_log.py --selftest
"""
from __future__ import annotations

import datetime
import glob
import json
import math
import os
import re
import sys
import time

_T0 = time.perf_counter()  # duration_ms の起点(モジュール読込時刻=フックスクリプト開始の近似)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_NAME = "hook-decisions.jsonl"
LEGACY_LOG_NAME = "hook-decisions.log"
PATTERNS_REL = os.path.join("..", "..", "harness", "privacy-patterns.json")  # .github/harness/privacy-patterns.json(2026-09-21 移動。Copilot CLI が .github/hooks/**/*.json をフック設定として読むため)
TARGET_MAX_CHARS = 120
TRIM_AT = 524288
KEEP_BYTES = 262144
FIELD_ORDER = ("ts", "session_id", "hook_event", "tool_name", "script", "decision", "target", "tool_use_id",
               "duration_ms", "host")
DECISIONS = ("deny", "ask", "warn", "block", "allow", "inject", "hold", "skip")
HOSTS = ("claude-code", "copilot", "unknown")
# ホスト判定の欄名(既存の判定の集約): Claude Code は snake_case、Copilot(CLI / VS Code Agent Hooks)は camelCase
# (log-effort / log-subagent / session-baseline が「Copilot の camelCase(sessionId)は無視」で使う規則と同じ)
_SNAKE_KEYS = ("hook_event_name", "session_id", "tool_use_id", "transcript_path")
_CAMEL_KEYS = ("hookEventName", "sessionId", "toolName", "transcriptPath")
# 事故的停止の記録(1 セッション 1 ファイル。書き手は各フック、読み手は本モジュールの iter_stop_records)
ABNORMAL_STOP_PREFIX = "abnormal-stop-"   # mark-abnormal-stop.py(StopFailure: error_type / error_message)
WATCHDOG_STOP_PREFIX = "watchdog-stop-"   # watchdog-continue.py(kind: watchdog_limit | max_tokens)

_PATTERNS = None
_LINE_PATTERN_RE = re.compile(r'"pattern"\s*:\s*"((?:[^"\\]|\\.)*)"')
_LINE_REPLACE_RE = re.compile(r'"replace"\s*:\s*"((?:[^"\\]|\\.)*)"')
_LINE_IC_RE = re.compile(r'"ignore_case"\s*:\s*true')


def log_dir():
    override = os.environ.get("HARNESS_HOOK_LOG_DIR")
    if override:
        return override
    return os.path.join(SCRIPT_DIR, "..", "logs")


def _unescape_json_string(s):
    return s.replace('\\"', '"').replace("\\\\", "\\")


def load_patterns(path=None):
    """privacy-patterns.json(.github/harness) を読む。JSON として読めなければ行単位の正規表現で pattern/replace を拾う
    (sh 版と同じフォールバック)。どちらも失敗したら空(=redaction なし。ログ出力自体は止めない)。"""
    global _PATTERNS
    if path is None and _PATTERNS is not None:
        return _PATTERNS
    path = path or os.path.normpath(os.path.join(SCRIPT_DIR, PATTERNS_REL))
    entries = []
    try:
        data = json.load(open(path, encoding="utf-8-sig"))
        for e in data.get("redact") or []:
            if isinstance(e, dict) and isinstance(e.get("pattern"), str):
                entries.append((e["pattern"], str(e.get("replace", "[REDACTED]")), bool(e.get("ignore_case"))))
    except Exception:
        try:
            for line in open(path, encoding="utf-8-sig", errors="replace"):
                m = _LINE_PATTERN_RE.search(line)
                if not m:
                    continue
                rep = _LINE_REPLACE_RE.search(line)
                entries.append((_unescape_json_string(m.group(1)),
                                _unescape_json_string(rep.group(1)) if rep else "[REDACTED]",
                                bool(_LINE_IC_RE.search(line))))
        except Exception:
            entries = []
    compiled = []
    for pat, rep, ic in entries:
        try:
            compiled.append((re.compile(pat, re.I if ic else 0), rep))
        except re.error:
            continue
    if path == os.path.normpath(os.path.join(SCRIPT_DIR, PATTERNS_REL)):
        _PATTERNS = compiled
    return compiled


def redact(text, patterns=None):
    """秘密値らしき文字列を [REDACTED] に置換する(順序は privacy-patterns.json(.github/harness) のとおり)。"""
    s = "" if text is None else str(text)
    for rx, rep in (patterns if patterns is not None else load_patterns()):
        try:
            s = rx.sub(lambda _m, r=rep: r, s)
        except Exception:
            continue
    return s


def sanitize_target(text, patterns=None):
    """redaction → 先頭 TARGET_MAX_CHARS 字(切り詰めは redaction の後: 境界で切れた秘密値の前半が
    パターンに掛からず残るのを防ぐ)。改行は値に残し JSON エスケープで書く(素の改行は出さない)。"""
    return redact(text, patterns)[:TARGET_MAX_CHARS]


def detect_host(payload=None, env=None):
    """ホストの近似判定(claude-code / copilot / unknown)。ペイロードの欄名(snake_case = Claude Code、camelCase =
    Copilot)→ 環境変数 CLAUDE_PROJECT_DIR(公式 hooks リファレンス: フックに常に渡る。Claude Code 側だけ)の順。
    公式にホスト製品(CLI / VS Code / SDK)を識別する欄・環境変数は無い(2026-09-17 確認)ため近似に留める。
    env は selftest 用(None なら os.environ)。"""
    p = payload if isinstance(payload, dict) else {}
    e = os.environ if env is None else env
    if e.get("COPILOT_CLI"):
        return "copilot"  # Copilot CLI 1.0.86 は snake_case ペイロードと CLAUDE_PROJECT_DIR を渡す(Claude 互換)ため環境変数を最優先(2026-09-21 実測)
    if any(k in p for k in _SNAKE_KEYS):
        return "claude-code"
    if any(k in p for k in _CAMEL_KEYS):
        return "copilot"
    if e.get("CLAUDE_PROJECT_DIR"):
        return "claude-code"
    return "unknown"


def elapsed_ms():
    """本モジュール読込からの経過ミリ秒(int)。フックスクリプト開始からの経過の近似(プロセス起動分は含まない)。"""
    try:
        return max(0, int(round((time.perf_counter() - _T0) * 1000)))
    except Exception:
        return None


def build_record(decision, target, payload=None, script=None, now=None, duration_ms=None, host=None):
    p = payload if isinstance(payload, dict) else {}

    def s(v):
        return str(v) if isinstance(v, (str, int, float)) and str(v) != "" else None

    ts = (now or datetime.datetime.now().astimezone()).isoformat(timespec="seconds")
    return {
        "ts": ts,
        "session_id": s(p.get("session_id")),
        "hook_event": s(p.get("hook_event_name")),
        "tool_name": s(p.get("tool_name") or p.get("toolName")),
        "script": script or os.path.basename(sys.argv[0] or "") or None,
        "decision": str(decision),
        "target": sanitize_target(target),
        "tool_use_id": s(p.get("tool_use_id")),
        "duration_ms": int(duration_ms) if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool)
        else elapsed_ms(),
        "host": host if host in HOSTS else detect_host(p),
    }


def _trim(path):
    try:
        if os.path.getsize(path) <= TRIM_AT:
            return
        with open(path, "rb") as fh:
            fh.seek(-KEEP_BYTES, os.SEEK_END)
            keep = fh.read()
        nl = keep.find(b"\n")
        if 0 <= nl < len(keep) - 1:
            keep = keep[nl + 1:]
        with open(path, "wb") as fh:
            fh.write(keep)
    except Exception:
        pass


def hook_log(decision, target, payload=None, script=None, log_dir_override=None, duration_ms=None, host=None):
    """判定を 1 行追記する。いかなる失敗でも例外を出さない(フック判定に影響させない=fail-open)。
    duration_ms / host は省略時に自動判定(elapsed_ms / detect_host)。"""
    try:
        d = log_dir_override or log_dir()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, LOG_NAME)
        _trim(path)
        rec = build_record(decision, target, payload, script, duration_ms=duration_ms, host=host)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 読み手(両形式)

def parse_line(line, legacy=False):
    """JSONL 1 行、または旧 TSV 1 行を共通の dict にする。読めなければ None。"""
    line = line.rstrip("\r\n")
    if not line.strip():
        return None
    if not legacy and line.lstrip().startswith("{"):
        try:
            d = json.loads(line)
        except Exception:
            return None
        if not isinstance(d, dict) or "decision" not in d:
            return None
        rec = {k: d.get(k) for k in FIELD_ORDER}
        rec["legacy"] = False
        return rec
    parts = line.split("\t")
    if len(parts) < 3:
        return None
    return {"ts": parts[0], "session_id": None, "hook_event": None, "tool_name": None,
            "script": parts[1], "decision": parts[2], "target": "\t".join(parts[3:]) if len(parts) > 3 else "",
            "tool_use_id": None, "duration_ms": None, "host": None, "legacy": True}


def percentile(values, p):
    """最近傍順位法(nearest-rank)の百分位。values の数値だけを昇順に並べ、ceil(p/100*n) 番目を返す(空なら None)。
    P50 / P95 の唯一の定義(hook-metrics.py・session-receipt.py・effort-report.py はこれを使う)。"""
    vals = sorted(v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool))
    if not vals:
        return None
    k = max(1, min(len(vals), int(math.ceil(float(p) / 100.0 * len(vals)))))
    return vals[k - 1]


def iter_decisions(logs=None, include_legacy=True):
    """hook-decisions.jsonl(と旧 hook-decisions.log)の記録を時系列ファイル順に返す。"""
    d = logs or log_dir()
    files = []
    if include_legacy:
        files.append((os.path.join(d, LEGACY_LOG_NAME), True))
    files.append((os.path.join(d, LOG_NAME), False))
    for path, legacy in files:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    rec = parse_line(line, legacy=legacy)
                    if rec is not None:
                        yield rec
        except Exception:
            continue


def summarize(records, session_id=None, since=None, until=None):
    """decision 別・script 別の件数。session_id を指定すると JSONL はその値で、旧 TSV(session_id 無し)は
    since/until(ISO 文字列。文字列比較)の時間窓で絞る。戻り値の source に何で絞ったかを残す。"""
    counts = {k: 0 for k in DECISIONS}
    by_script = {}
    by_host = {}
    durations = []
    n_jsonl = n_legacy = 0
    for r in records:
        if session_id is not None:
            if r.get("legacy"):
                # 旧 TSV は session_id を持たないため、時間窓が与えられたときだけ数える
                if not since and not until:
                    continue
                ts = str(r.get("ts") or "")
                if (since and ts < str(since)[:19]) or (until and ts > str(until)[:19]):
                    continue
            elif r.get("session_id") != session_id:
                continue
        dec = str(r.get("decision") or "")
        if dec in counts:
            counts[dec] += 1
        else:
            counts[dec] = counts.get(dec, 0) + 1
        sc = str(r.get("script") or "?")
        by_script.setdefault(sc, {})
        by_script[sc][dec] = by_script[sc].get(dec, 0) + 1
        dur = r.get("duration_ms")
        if isinstance(dur, (int, float)) and not isinstance(dur, bool):
            durations.append(dur)
        h = r.get("host")
        if h:
            by_host[str(h)] = by_host.get(str(h), 0) + 1
        if r.get("legacy"):
            n_legacy += 1
        else:
            n_jsonl += 1
    source = "jsonl" if n_jsonl and not n_legacy else ("tsv(time-window)" if n_legacy and not n_jsonl
                                                       else ("jsonl+tsv" if n_jsonl and n_legacy else "none"))
    return {"counts": counts, "by_script": by_script, "records": n_jsonl + n_legacy, "source": source,
            "duration_n": len(durations), "duration_p50_ms": percentile(durations, 50),
            "duration_p95_ms": percentile(durations, 95), "duration_max_ms": max(durations) if durations else None,
            "by_host": by_host}


def count_for_session(session_id, logs=None, since=None, until=None):
    return summarize(iter_decisions(logs), session_id=session_id, since=since, until=until)


# ---------------------------------------------------------------- ゲート遷移ログ / session.lock(状態機械の堅牢化)
# 仕様の正は .github/harness/STATE-MACHINE.md(再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15)。
# 書式(1 行 JSONL。欄の順序固定。_log.sh / _log.ps1 と同一):
#   {"ts","rev","session_id","hook_event","tool_name","script","source","before","after","changed","loops"}
#   rev は同一ファイル内の通番(最終行の rev + 1)、before / after は {phase: 値}(before は初回 null)、
#   changed は "phase:前->後" のカンマ連結、loops は GATE_COUNTERS の implement_test_loops(無ければ null)、
#   source は hook(warn-gate-tamper)/ session-start(inject-progress の突合)/ cli / reconcile / recover:<元>。
# session.lock は {"session_id","pid","ts","epoch","cwd"} の 1 行 JSON。stale(既定 120 分。tools/usage-config.json の
# session_lock_stale_minutes、HARNESS_SESSION_LOCK_STALE_MIN で上書き)を過ぎた lock は無視される。

GATE_LOG_NAME = "gate-transitions.jsonl"
SESSION_LOCK_NAME = "session.lock"
GATE_FIELD_ORDER = ("ts", "rev", "session_id", "hook_event", "tool_name", "script", "source", "before", "after", "changed", "loops")
GATE_PHASES = ("requirements", "design", "implementation", "test", "release")
SESSION_LOCK_STALE_MIN_DEFAULT = 120


def _gate_state_dict(v):
    if isinstance(v, dict):
        return {str(k): str(x) for k, x in v.items() if str(x) != ""}
    if isinstance(v, str) and v:
        out = {}
        for pair in v.split(","):
            if "=" in pair:
                k, x = pair.split("=", 1)
                out[k.strip()] = x.strip()
        return out
    return None


def gate_diff(before, after):
    """"phase:前->後" のカンマ連結(after の正準順。before に無い phase は none)。"""
    b = _gate_state_dict(before) or {}
    a = _gate_state_dict(after) or {}
    keys = [k for k in GATE_PHASES if k in a] + [k for k in a if k not in GATE_PHASES]
    return ",".join(f"{k}:{b.get(k, 'none')}->{a[k]}" for k in keys if b.get(k) != a[k])


def iter_gate_transitions(logs=None):
    path = os.path.join(logs or log_dir(), GATE_LOG_NAME)
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if isinstance(d, dict) and "after" in d:
                    yield d
    except Exception:
        return


def gate_last(logs=None):
    """最終記録(dict)。無ければ None。"""
    last = None
    for rec in iter_gate_transitions(logs):
        last = rec
    return last


def gate_log(before, after, loops=None, source="hook", payload=None, script=None, log_dir_override=None):
    """遷移を 1 行追記する。いかなる失敗でも例外を出さない(fail-open)。"""
    try:
        d = log_dir_override or log_dir()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, GATE_LOG_NAME)
        _trim(path)
        last = gate_last(d)
        rev = int(last.get("rev") or 0) + 1 if isinstance(last, dict) else 1
        base = build_record("gate", "", payload, script)
        a = _gate_state_dict(after) or {}
        b = _gate_state_dict(before)
        try:
            n = int(loops) if loops is not None and str(loops) != "" else None
        except (TypeError, ValueError):
            n = None
        rec = {"ts": base["ts"], "rev": rev, "session_id": base["session_id"], "hook_event": base["hook_event"],
               "tool_name": base["tool_name"], "script": base["script"], "source": str(source or "hook"),
               "before": b, "after": a, "changed": gate_diff(b, a), "loops": n}
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def session_lock_path(logs=None):
    return os.path.join(logs or log_dir(), SESSION_LOCK_NAME)


def session_lock_stale_min(root=None):
    """stale 閾値(分): HARNESS_SESSION_LOCK_STALE_MIN > tools/usage-config.json > 120。"""
    env = os.environ.get("HARNESS_SESSION_LOCK_STALE_MIN", "")
    if re.fullmatch(r"\d+", env):
        return int(env)
    try:
        cfg_path = os.path.join(root or os.path.join(SCRIPT_DIR, "..", "..", ".."), "tools", "usage-config.json")
        v = int(json.load(open(cfg_path, encoding="utf-8-sig")).get("session_lock_stale_minutes"))
        return v if v > 0 else SESSION_LOCK_STALE_MIN_DEFAULT
    except Exception:
        return SESSION_LOCK_STALE_MIN_DEFAULT


def session_lock_read(logs=None):
    """{"session_id", "pid", "ts", "epoch", "cwd", "age_min"} か None(無い・読めない)。"""
    try:
        import time
        with open(session_lock_path(logs), encoding="utf-8", errors="replace") as fh:
            d = json.loads(fh.read(4096))
        if not isinstance(d, dict) or not d.get("session_id") or not isinstance(d.get("epoch"), (int, float)):
            return None
        d["age_min"] = max(0, int((time.time() - float(d["epoch"])) // 60))
        return d
    except Exception:
        return None


def session_lock_other(session_id, logs=None, root=None):
    """別セッションの新しい(stale でない)lock があればその dict、無ければ None。"""
    info = session_lock_read(logs)
    if info is None or not session_id or info.get("session_id") == session_id:
        return None
    if info["age_min"] >= session_lock_stale_min(root):
        return None
    return info


def session_lock_write(session_id, logs=None, cwd=None):
    """自セッションの lock を書く(一時ファイル→rename)。失敗は False。"""
    try:
        import time
        d = logs or log_dir()
        os.makedirs(d, exist_ok=True)
        now = datetime.datetime.now().astimezone()
        rec = {"session_id": re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id)), "pid": os.getpid(),
               "ts": now.isoformat(timespec="seconds"), "epoch": int(time.time()), "cwd": cwd or os.getcwd()}
        path = session_lock_path(d)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 事故的停止の記録(A2-1)

def parse_gate_status(text):
    """GATE_STATUS ブロック(スナップショット文字列)からフェーズ→状態の dict を返す(読めなければ {})。"""
    if not isinstance(text, str):
        return {}
    m = re.search(r"<!--\s*GATE_STATUS(.*?)(?:-->|\Z)", text, re.S)
    body = m.group(1) if m else text
    out = {}
    for line in body.splitlines():
        mm = re.match(r"^\s*(requirements|design|implementation|test|release)\s*:\s*([A-Za-z_]+)", line)
        if mm:
            out[mm.group(1)] = mm.group(2)
    return out


def iter_stop_records(logs=None):
    """事故的停止の記録を共通 dict で時系列順に返す。
    - logs/abnormal-stop-<sid>.json(mark-abnormal-stop.py / StopFailure)→ kind "abnormal_stop"、reason = error_type
    - logs/watchdog-stop-<sid>.json(watchdog-continue.py / Stop)→ kind = "watchdog_limit" | "max_tokens"、reason = 記録の reason
    欄: kind / reason / session_id / recorded_at / gate_status(スナップショット文字列)/ head_sha / cwd / path。
    読めないファイルは捨てる(fail-soft)。"""
    d = logs or log_dir()
    out = []
    for prefix in (ABNORMAL_STOP_PREFIX, WATCHDOG_STOP_PREFIX):
        for path in sorted(glob.glob(os.path.join(d, prefix + "*.json"))):
            try:
                with open(path, encoding="utf-8-sig") as fh:
                    rec = json.load(fh)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            if prefix == ABNORMAL_STOP_PREFIX:
                kind, reason = "abnormal_stop", str(rec.get("error_type") or "unknown")
            else:
                kind, reason = str(rec.get("kind") or "watchdog_limit"), str(rec.get("reason") or "")
            out.append({"kind": kind, "reason": reason, "session_id": rec.get("session_id"),
                        "recorded_at": rec.get("recorded_at"), "gate_status": rec.get("gate_status"),
                        "head_sha": rec.get("head_sha"), "cwd": rec.get("cwd"), "path": path,
                        "hook_event_name": rec.get("hook_event_name")})
    out.sort(key=lambda r: str(r.get("recorded_at") or ""))
    return out


def stop_records_for_sessions(session_ids, logs=None):
    """指定 session_id(記録側の欄。ファイル名の無害化前の値)に一致する事故的停止の記録だけを返す。"""
    wanted = {str(s) for s in (session_ids or []) if s}
    return [r for r in iter_stop_records(logs) if str(r.get("session_id") or "") in wanted]


# ---------------------------------------------------------------- selftest

def selftest():
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

    tmp = tempfile.mkdtemp(prefix="hooklog-selftest-")
    old_env = os.environ.get("HARNESS_HOOK_LOG_DIR")
    try:
        logs = os.path.join(tmp, "logs")
        os.environ["HARNESS_HOOK_LOG_DIR"] = logs
        pats = load_patterns()
        check("patterns: privacy-patterns.json(.github/harness) を読める(1 件以上)", len(pats) >= 10, str(len(pats)))
        canary_ant = "sk-ant-api03-CANARYCANARYCANARY0123456789abcdef"
        canary_gh = "github_pat_CANARY0123456789abcdefghijk"
        canary_url = "https://alice:hunter2-canary@example.com/repo.git"
        raw = f"git push {canary_url} ; export X={canary_ant} ; token: {canary_gh} ; password=Pass1234567890"
        red = redact(raw)
        check("redact: URL 埋め込み認証", "hunter2-canary" not in red and "://[REDACTED]@" in red, red)
        check("redact: anthropic / github トークン", canary_ant not in red and canary_gh not in red, red)
        check("redact: 汎用 password= 代入", "Pass1234567890" not in red, red)
        check("redact: 非秘密は残る", "git push" in red and "export X=" in red, red)
        long = "a" * 200 + canary_ant
        san = sanitize_target(long)
        check("target: redaction の後に 120 字へ切り詰め(境界の秘密値を残さない)", len(san) == 120 and "sk-ant" not in san, san)
        san2 = sanitize_target("x" * 110 + canary_ant)
        check("target: 境界を跨ぐ秘密値も先に伏せる", "sk-ant-api03" not in san2 and "[REDACTED]" in san2, san2)

        payload = {"session_id": "sess-1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                   "tool_use_id": "toolu_01", "tool_input": {"command": raw}}
        check("hook_log: 書ける", hook_log("ask", raw, payload=payload, script="guard-x.sh"))
        path = os.path.join(logs, LOG_NAME)
        line = open(path, encoding="utf-8").read().splitlines()[-1]
        rec = json.loads(line)
        check("record: 欄の順序と集合が固定", list(rec.keys()) == list(FIELD_ORDER), str(list(rec.keys())))
        check("record: session_id / hook_event / tool_name / tool_use_id / script", rec["session_id"] == "sess-1"
              and rec["hook_event"] == "PreToolUse" and rec["tool_name"] == "Bash" and rec["tool_use_id"] == "toolu_01"
              and rec["script"] == "guard-x.sh" and rec["decision"] == "ask", str(rec))
        check("record: duration_ms は 0 以上の整数、host は snake_case ペイロードで claude-code(R-09 / A2-7)",
              isinstance(rec["duration_ms"], int) and rec["duration_ms"] >= 0 and rec["host"] == "claude-code", str(rec))
        hook_log("ask", "git push", payload={"sessionId": "c1", "toolName": "Bash", "tool_input": {}}, script="g.sh")
        rec_c = json.loads(open(path, encoding="utf-8").read().splitlines()[-1])
        check("record: camelCase ペイロード(Copilot)は host copilot・session_id null", rec_c["host"] == "copilot"
              and rec_c["session_id"] is None and rec_c["tool_name"] == "Bash", str(rec_c))
        check("detect_host: COPILOT_CLI があれば snake_case でも copilot(Copilot CLI 1.0.86 は Claude 互換ペイロード)",
              detect_host({"hook_event_name": "PreToolUse"}, env={"COPILOT_CLI": "1", "CLAUDE_PROJECT_DIR": "/p"}) == "copilot")
        check("detect_host: 欄が無ければ CLAUDE_PROJECT_DIR → claude-code、無ければ unknown",
              detect_host({}, env={}) == "unknown" and detect_host({}, env={"CLAUDE_PROJECT_DIR": "/p"}) == "claude-code"
              and detect_host(None, env={}) == "unknown")
        check("hook_log: duration_ms / host の明示指定が優先", hook_log("allow", "x", payload={}, script="s.py", duration_ms=77, host="copilot")
              and json.loads(open(path, encoding="utf-8").read().splitlines()[-1])["duration_ms"] == 77
              and json.loads(open(path, encoding="utf-8").read().splitlines()[-1])["host"] == "copilot")
        old8 = parse_line('{"ts": "2026-09-14T10:00:00+09:00", "session_id": "s0", "hook_event": null, "tool_name": null, '
                          '"script": "guard-old.sh", "decision": "deny", "target": "x", "tool_use_id": null}')
        check("parse_line: 旧 8 欄の行は duration_ms / host が null で読める(後方互換)", old8 is not None
              and old8["duration_ms"] is None and old8["host"] is None and old8["decision"] == "deny", str(old8))
        check("percentile: 最近傍順位法(P50 of [5,1,3]=3・P95 of 1..100=95・空は None)", percentile([5, 1, 3], 50) == 3
              and percentile(list(range(1, 101)), 95) == 95 and percentile([], 50) is None and percentile([7], 95) == 7)
        check("record: canary secret が平文で残らない", "hunter2-canary" not in line and canary_ant not in line
              and canary_gh not in line and "Pass1234567890" not in line, line)
        check("record: ts はオフセット付き ISO8601", re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$", rec["ts"]) is not None, rec["ts"])
        hook_log("deny", "line1\nline2\tTAB \"quoted\"", payload={}, script="s.py")
        line2 = open(path, encoding="utf-8").read().splitlines()[-1]
        rec2 = json.loads(line2)
        check("record: 改行/タブ/引用符は JSON エスケープ(1 行のまま)", rec2["target"] == "line1\nline2\tTAB \"quoted\""
              and "\n" not in line2, line2)
        check("record: 欄が無ければ null", rec2["session_id"] is None and rec2["tool_use_id"] is None, str(rec2))

        # 両形式の読み手
        with open(os.path.join(logs, LEGACY_LOG_NAME), "w", encoding="utf-8") as fh:
            fh.write("2026-09-01T10:00:00\tguard-old.sh\tdeny\tAGENTS.md\n")
            fh.write("broken line\n")
            fh.write("2026-09-01T10:05:00\tremind-record.py\tblock\tapp=1 docs=0\n")
        rows = list(iter_decisions(logs))
        check("iter_decisions: TSV 2 行 + JSONL 4 行(壊れた行は捨てる)", len(rows) == 6
              and rows[0]["legacy"] and rows[0]["decision"] == "deny" and rows[0]["session_id"] is None
              and rows[-1]["legacy"] is False, str([r["decision"] for r in rows]))
        summ = count_for_session("sess-1", logs, since="2026-09-01T10:00:00", until="2026-09-01T10:01:00")
        check("summarize: session_id で JSONL を、時間窓で TSV を絞る", summ["counts"]["ask"] == 1
              and summ["counts"]["deny"] == 1 and summ["counts"]["block"] == 0 and summ["source"] == "jsonl+tsv", str(summ))
        summ2 = count_for_session("other", logs)
        check("summarize: 該当なしは 0 件・source none", summ2["records"] == 0 and summ2["source"] == "none", str(summ2))
        summ3 = summarize(iter_decisions(logs, include_legacy=False), session_id="sess-1")
        check("summarize: JSONL のみ", summ3["source"] == "jsonl" and summ3["by_script"].get("guard-x.sh", {}).get("ask") == 1, str(summ3))
        with open(path, "a", encoding="utf-8") as fh:
            for dur, h in ((100, "claude-code"), (300, "claude-code"), (500, "copilot")):
                fh.write(json.dumps(build_record("allow", "t", {"session_id": "dur"}, "g.sh", duration_ms=dur, host=h)) + "\n")
        summ4 = count_for_session("dur", logs)
        check("summarize: duration の P50 / P95 / max と host 別件数(旧行は duration null で数えない)",
              summ4["duration_n"] == 3 and summ4["duration_p50_ms"] == 300 and summ4["duration_p95_ms"] == 500
              and summ4["duration_max_ms"] == 500 and summ4["by_host"] == {"claude-code": 2, "copilot": 1}, str(summ4))
        # 事故的停止の記録(A2-1): StopFailure(abnormal-stop-*)と watchdog(watchdog-stop-*)を共通 dict で読む
        with open(os.path.join(logs, "abnormal-stop-a.json"), "w", encoding="utf-8") as fh:
            json.dump({"recorded_at": "2026-09-17T10:00:00+09:00", "session_id": "a", "error_type": "rate_limit",
                       "gate_status": "<!-- GATE_STATUS\nimplementation: done\n-->", "head_sha": "abc"}, fh)
        with open(os.path.join(logs, "watchdog-stop-b.json"), "w", encoding="utf-8") as fh:
            json.dump({"recorded_at": "2026-09-17T09:00:00+09:00", "session_id": "b", "kind": "watchdog_limit",
                       "reason": "limit-reached 3/3", "gate_status": None}, fh)
        with open(os.path.join(logs, "abnormal-stop-broken.json"), "w", encoding="utf-8") as fh:
            fh.write("{broken")
        stops = iter_stop_records(logs)
        check("iter_stop_records: 2 種類の記録を時系列順に読み、壊れたファイルは捨てる", len(stops) == 2
              and stops[0]["kind"] == "watchdog_limit" and stops[0]["reason"] == "limit-reached 3/3"
              and stops[1]["kind"] == "abnormal_stop" and stops[1]["reason"] == "rate_limit", str(stops))
        check("stop_records_for_sessions: session_id で絞る", [r["session_id"] for r in stop_records_for_sessions(["a", "zz"], logs)] == ["a"]
              and stop_records_for_sessions([], logs) == [])
        check("parse_gate_status: スナップショット文字列からフェーズ状態", parse_gate_status(stops[1]["gate_status"]) == {"implementation": "done"}
              and parse_gate_status(None) == {})

        # 512KB 超の切り詰め(行境界)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "x", "decision": "allow", "target": "y" * 1000}) + "\n")
            for _ in range(600):
                fh.write(json.dumps({"ts": "x", "decision": "allow", "target": "z" * 1000}) + "\n")
        hook_log("allow", "after-trim", payload={}, script="s.py")
        size = os.path.getsize(path)
        first = open(path, encoding="utf-8").read().splitlines()[0]
        check("trim: 512KB 超で後半 256KB だけ残し、先頭行は完全な JSON", size < TRIM_AT and json.loads(first) is not None, str(size))
        # 壊れた patterns ファイルでも行フォールバックで読める
        pf = os.path.join(tmp, "p.json")
        with open(pf, "w", encoding="utf-8") as fh:
            fh.write('{"redact": [\n {"name": "a", "pattern": "sk-ant-[A-Za-z0-9_-]{20,}", "replace": "[REDACTED]"},\n BROKEN\n')
        fb = load_patterns(pf)
        check("patterns: JSON 破損時は行フォールバック", len(fb) == 1 and fb[0][0].search(canary_ant) is not None, str(fb))
        # ゲート遷移ログ(状態機械の堅牢化): rev の通番・before null・changed の差分・loops・source
        glogs = os.path.join(tmp, "glogs")
        check("gate_last: 記録が無ければ None", gate_last(glogs) is None)
        st1 = {"requirements": "done", "design": "in_progress", "implementation": "not_started", "test": "not_started", "release": "not_started"}
        check("gate_log: 初回(before null)", gate_log(None, st1, loops=None, source="session-start",
                                                   payload={"session_id": "s-g", "hook_event_name": "SessionStart"}, script="inject-progress.sh", log_dir_override=glogs))
        g1 = gate_last(glogs)
        check("gate record: 欄の順序固定・rev 1・before null・changed は全キー none->値", g1 and list(g1.keys()) == list(GATE_FIELD_ORDER)
              and g1["rev"] == 1 and g1["before"] is None and g1["after"] == st1 and g1["loops"] is None and g1["source"] == "session-start"
              and g1["session_id"] == "s-g" and g1["hook_event"] == "SessionStart" and g1["script"] == "inject-progress.sh"
              and g1["changed"].startswith("requirements:none->done,design:none->in_progress"), str(g1))
        st2 = dict(st1, design="done", implementation="in_progress")
        gate_log("requirements=done,design=in_progress,implementation=not_started,test=not_started,release=not_started", "requirements=done,design=done,implementation=in_progress,test=not_started,release=not_started",
                 loops="2", source="hook", payload={"session_id": "s-g", "tool_name": "Edit"}, script="warn-gate-tamper.sh", log_dir_override=glogs)
        g2 = gate_last(glogs)
        check("gate record: compact 文字列でも書け rev 2・changed は差分だけ・loops は int", g2 and g2["rev"] == 2 and g2["before"] == st1 and g2["after"] == st2
              and g2["changed"] == "design:in_progress->done,implementation:not_started->in_progress" and g2["loops"] == 2 and g2["tool_name"] == "Edit", str(g2))
        check("iter_gate_transitions: 2 行", len(list(iter_gate_transitions(glogs))) == 2)
        check("gate_diff: 変化なしは空", gate_diff(st2, st2) == "")
        # session.lock: 書く → 自分は other でない → 別 sid は other → stale なら無視
        os.environ["HARNESS_SESSION_LOCK_STALE_MIN"] = "10"
        check("session_lock_write", session_lock_write("s-A", glogs, cwd="/x") and os.path.exists(session_lock_path(glogs)))
        lk = session_lock_read(glogs)
        check("session_lock_read: session_id / pid / epoch / age_min", lk and lk["session_id"] == "s-A" and isinstance(lk["pid"], int) and lk["age_min"] == 0 and lk["cwd"] == "/x", str(lk))
        check("session_lock_other: 自分なら None", session_lock_other("s-A", glogs) is None)
        check("session_lock_other: 別セッションの新しい lock → dict", (session_lock_other("s-B", glogs) or {}).get("session_id") == "s-A")
        old = json.load(open(session_lock_path(glogs), encoding="utf-8"))
        old["epoch"] = old["epoch"] - 11 * 60
        with open(session_lock_path(glogs), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(old) + "\n")
        check("session_lock_other: stale(閾値超)は無視", session_lock_other("s-B", glogs) is None)
        check("session_lock_stale_min: 環境変数", session_lock_stale_min() == 10)
        os.environ.pop("HARNESS_SESSION_LOCK_STALE_MIN", None)
        check("session_lock_read: 壊れた lock は None", (open(session_lock_path(glogs), "w").write("{broken") or True) and session_lock_read(glogs) is None)
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
    sys.exit(0)

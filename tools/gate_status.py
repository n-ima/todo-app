#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GATE_STATUS(docs/00-overview/progress.md の機械可読ブロック)の完全性検査・順序矛盾検査・往復カウンタ・復旧 CLI。

状態機械の仕様(語彙・許される遷移・誰がいつ書くか・往復上限・復旧手順)の正は
`.github/harness/STATE-MACHINE.md`。本モジュールはその機械検査と復旧操作の正で、
`tools/validate-harness.py` (r) が import して本体のテンプレ(progress_template.md)と配布先の実物(progress.md)を
検査する。フック(warn-stale-gate / warn-gate-tamper / inject-progress の sh/ps1)は同じ規則を bash / PowerShell で
持つ鏡で、selftest 両系がケースを固定する(再監査 2026-08-31 §6 A7-M-3 / codex R-04 / IA-20260831-15)。

検査:
- 完全性(ERROR): `<!-- GATE_STATUS … -->` ブロックの有無、5 キー(requirements / design / implementation / test /
  release)の欠落・重複、未知のキー、読めない行、語彙外の値(値は行の最初のトークン。後ろの注記は許容)
- 順序矛盾(WARN): 後続フェーズが着手済みなのに先行が not_started(規則 A)、後続が done なのに先行が done でない(規則 B)。
  `progress.md` 本文に「状態: 運用中」の注記があれば規則 B を免除する(brownfield 取り込み残余の test=in_progress、
  /12 改修サイクルで該当フェーズだけを戻す運用は仕様どおり)
- 往復カウンタ(`<!-- GATE_COUNTERS … -->` の implement_test_loops。無ければ 0 扱い=後方互換): 整数でなければ ERROR、
  上限(tools/usage-config.json の implement_test_loop_max。既定 3)超は WARN

使い方(リポジトリルートで):
    python tools/gate_status.py check [--strict]          # ERROR があれば exit 1(--strict は WARN も)
    python tools/gate_status.py show                      # 'requirements=done,design=in_progress,…' の 1 行
    python tools/gate_status.py set <phase> <value> [--note '…']   # 一時ファイル→rename の原子的書換 + 遷移ログ 1 行
    python tools/gate_status.py bump-loop | reset-loop    # implement↔test 往復カウンタ
    python tools/gate_status.py recover --from log|git|table|baseline   # 壊れた GATE_STATUS の復旧(STATE-MACHINE.md「復旧手順」)
    python tools/gate_status.py reconcile                 # 遷移ログの最終記録と実物がずれていれば 1 行追記
    python tools/gate_status.py unlock [--force]          # session.lock を消す(stale でなければ --force が必要)
    python tools/gate_status.py --selftest
`--root <dir>` でプロジェクトルートを指定できる(既定は tools/ の親)。終了コードは 0 / 1。標準ライブラリのみ。
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

PHASES = ("requirements", "design", "implementation", "test", "release")
VALUES = ("not_started", "in_progress", "pending_approval", "done")
COUNTER_KEYS = ("implement_test_loops",)
GATE_REL = os.path.join("docs", "00-overview", "progress.md")
TEMPLATE_REL = os.path.join("docs", "00-overview", "progress_template.md")
SPEC_REL = os.path.join(".github", "harness", "STATE-MACHINE.md")
USAGE_CONFIG_REL = os.path.join("tools", "usage-config.json")
HOOK_SCRIPTS_REL = os.path.join(".github", "hooks", "scripts")
OPERATING_NOTE = "状態: 運用中"
DEFAULT_LOOP_MAX = 3
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 人間向けフェーズ表(progress_template.md)からの復旧に使う対応(表の語 → 機械語彙)
HUMAN_PHASE = {"要件定義": "requirements", "設計": "design", "実装": "implementation", "テスト": "test", "リリース": "release"}
HUMAN_VALUE = {"未着手": "not_started", "進行中": "in_progress", "ゲート承認待ち": "pending_approval",
               "承認待ち": "pending_approval", "完了": "done"}

# GATE_STATUS を直接パースする鏡(validate (r-2) が 5 キーの語が揃っているかを見る。フック側の正規表現は
# `(requirements|design|implementation|test|release)` の alternation、python 側は PHASES 相当のタプル)
MIRRORS = tuple(os.path.join(HOOK_SCRIPTS_REL, n) for n in (
    "guard-phase-scope.sh", "guard-phase-scope.ps1", "warn-gate-tamper.sh", "warn-gate-tamper.ps1",
    "warn-stale-gate.sh", "warn-stale-gate.ps1", "inject-progress.sh", "inject-progress.ps1",
    "route-request.sh", "route-request.ps1", "session-baseline.py", "log-effort.py", "statusline.py",
)) + (os.path.join("tools", "session-receipt.py"), os.path.join("tools", "golden-eval.py"))
# 語彙(4 値)を検査する側(完全性検査の鏡)
VALUE_MIRRORS = tuple(os.path.join(HOOK_SCRIPTS_REL, n) for n in ("warn-stale-gate.sh", "warn-stale-gate.ps1"))

BLOCK_RE = re.compile(r"<!--[ \t]*GATE_STATUS[ \t]*(?:-->|\r?\n)(.*?)-->", re.S)
COUNTERS_RE = re.compile(r"<!--[ \t]*GATE_COUNTERS[ \t]*(?:-->|\r?\n)(.*?)-->", re.S)
LINE_RE = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*:[ \t]*([^ \t\r\n]*)(?:[ \t]+(.*?))?[ \t]*$")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 解析

def _split_lines(body):
    return [ln.rstrip("\r") for ln in body.split("\n")]


def parse(text):
    """GATE_STATUS / GATE_COUNTERS の解析結果を dict で返す。
    block: ブロック本文(無ければ None) / entries: [(key, value, note, 行番号)] / junk: 読めない行 /
    counters: {key: 生の値}(ブロックが無ければ None) / counters_junk / operating_note: 本文に「状態: 運用中」があるか"""
    res = {"block": None, "entries": [], "junk": [], "counters": None, "counters_junk": [],
           "operating_note": OPERATING_NOTE in (text or "")}
    m = BLOCK_RE.search(text or "")
    if not m:
        return res
    res["block"] = m.group(1)
    for i, ln in enumerate(_split_lines(m.group(1)), 1):
        if not ln.strip():
            continue
        lm = LINE_RE.match(ln)
        if lm:
            res["entries"].append((lm.group(1), lm.group(2), (lm.group(3) or "").strip(), i))
        else:
            res["junk"].append(ln.strip())
    cm = COUNTERS_RE.search(text or "")
    if cm:
        res["counters"] = {}
        for ln in _split_lines(cm.group(1)):
            if not ln.strip():
                continue
            lm = LINE_RE.match(ln)
            if lm:
                res["counters"][lm.group(1)] = lm.group(2)
            else:
                res["counters_junk"].append(ln.strip())
    return res


def state(text):
    """{phase: 値(最初のトークン)} を返す(既知のキーのみ・初出優先)。ブロックが無ければ None。"""
    p = parse(text)
    if p["block"] is None:
        return None
    st = {}
    for k, v, _n, _i in p["entries"]:
        if k in PHASES and k not in st:
            st[k] = v
    return st


def loops(text):
    """implement_test_loops の値(int)。GATE_COUNTERS が無い・キーが無い・読めないときは 0(後方互換)。"""
    p = parse(text)
    v = (p["counters"] or {}).get("implement_test_loops")
    try:
        n = int(v)
        return n if n >= 0 else 0
    except (TypeError, ValueError):
        return 0


def compact(st):
    """{phase: value} → 'requirements=done,design=…'(正準順。フックの gate_file_state_into と同じ形)。"""
    if not st:
        return ""
    return ",".join(f"{k}={st[k]}" for k in PHASES if k in st)


def from_compact(s):
    out = {}
    for pair in (s or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------- 検査

def order_contradictions(st, operating_note=False):
    """順序矛盾の一覧(文字列)。規則 A: 後続が着手済みなのに先行が not_started。規則 B: 後続が done なのに先行が done
    でない(運用中注記があれば免除=改修サイクルで該当フェーズだけを戻す運用・brownfield の test 残余)。"""
    out = []
    known = [k for k in PHASES if k in st]
    for j, later in enumerate(known):
        for earlier in known[:j]:
            ve, vl = st[earlier], st[later]
            if vl != "not_started" and ve == "not_started":
                out.append(f"順序矛盾: {later}: {vl} なのに先行の {earlier} が not_started(規則 A)")
            elif vl == "done" and ve != "done" and not operating_note:
                out.append(f"順序矛盾: {later}: done なのに先行の {earlier} が {ve}(規則 B。改修サイクルなら progress.md に"
                           f"「{OPERATING_NOTE}」の注記を置く)")
    return out


def check(text, loop_max=None, mirror_note=True):
    """(errors, warnings)。errors は完全性(構造)の破れ、warnings は順序矛盾と往復上限超。"""
    errors, warnings = [], []
    p = parse(text)
    if p["block"] is None:
        errors.append("<!-- GATE_STATUS … --> ブロックが無い(docs/00-overview/progress_template.md の形式に戻す)")
        return errors, warnings
    for ln in p["junk"]:
        errors.append(f"読めない行: '{ln[:40]}'(書式は 'キー: 値')")
    seen = {}
    for k, v, _n, i in p["entries"]:
        if k not in PHASES:
            errors.append(f"未知のキー: {k}(5 キーは {'/'.join(PHASES)})")
            continue
        if k in seen:
            errors.append(f"キーの重複: {k}({seen[k]} 行目と {i} 行目)")
            continue
        seen[k] = i
        if v not in VALUES:
            errors.append(f"語彙外の値: {k}: {v or '(空)'}(値は {'/'.join(VALUES)} のいずれか。注記は値の後ろに空白区切りで置く)")
    for k in PHASES:
        if k not in seen:
            errors.append(f"キー欠落: {k}")
    st = {k: v for k, v, _n, _i in p["entries"] if k in PHASES and v in VALUES}
    if not errors:
        warnings.extend(order_contradictions(st, p["operating_note"]))
    if p["counters"] is not None:
        for ln in p["counters_junk"]:
            errors.append(f"GATE_COUNTERS の読めない行: '{ln[:40]}'")
        for k, v in p["counters"].items():
            if k not in COUNTER_KEYS:
                errors.append(f"GATE_COUNTERS の未知のキー: {k}({'/'.join(COUNTER_KEYS)})")
                continue
            if not re.fullmatch(r"\d+", v or ""):
                errors.append(f"GATE_COUNTERS の {k} が非負整数でない: {v or '(空)'}")
    n = loops(text)
    mx = DEFAULT_LOOP_MAX if loop_max is None else loop_max
    if n > mx:
        warnings.append(f"implement↔test の往復が {n} 回で上限 {mx} を超えている(自動の差し戻しを止め、/13-converge か人の判断へ。"
                        "上限の正は tools/usage-config.json の implement_test_loop_max)")
    return errors, warnings


def loop_max(root=None):
    """往復上限: HARNESS_LOOP_MAX > tools/usage-config.json の implement_test_loop_max > 既定 3。"""
    env = os.environ.get("HARNESS_LOOP_MAX", "")
    if re.fullmatch(r"\d+", env):
        return int(env)
    try:
        cfg = json.load(open(os.path.join(root or ROOT, USAGE_CONFIG_REL), encoding="utf-8-sig"))
        v = int(cfg.get("implement_test_loop_max"))
        return v if v >= 0 else DEFAULT_LOOP_MAX
    except Exception:  # noqa: BLE001
        return DEFAULT_LOOP_MAX


# ---------------------------------------------------------------- 書換(原子的)

def render_block(st, notes=None):
    lines = ["<!-- GATE_STATUS"]
    for k in PHASES:
        v = st.get(k, "not_started")
        note = (notes or {}).get(k)
        lines.append(f"{k}: {v}" + (f" {note}" if note else ""))
    lines.append("-->")
    return "\n".join(lines)


def render_counters(n):
    return f"<!-- GATE_COUNTERS\nimplement_test_loops: {int(n)}\n-->"


def _nl(text):
    """本文の改行様式(CRLF のファイルには CRLF で書き、混在させない)。"""
    return "\r\n" if "\r\n" in (text or "") else "\n"


def replace_block(text, new_block):
    """GATE_STATUS ブロックを差し替える(無ければ先頭に置く)。ブロック以外の本文は変えない。"""
    nl = _nl(text)
    new_block = new_block.replace("\n", nl)
    m = BLOCK_RE.search(text or "")
    if m:
        return (text[:m.start()] + new_block + text[m.end():])
    return new_block + nl + nl + (text or "")


def replace_counters(text, new_counters):
    """GATE_COUNTERS を差し替える(無ければ GATE_STATUS 直下に置く)。"""
    nl = _nl(text)
    new_counters = new_counters.replace("\n", nl)
    cm = COUNTERS_RE.search(text or "")
    if cm:
        return text[:cm.start()] + new_counters + text[cm.end():]
    m = BLOCK_RE.search(text or "")
    if m:
        return text[:m.end()] + nl + new_counters + text[m.end():]
    return new_counters + nl + nl + (text or "")


def atomic_write(path, text):
    """一時ファイルに書いてから rename(os.replace)する。途中失敗で元ファイルが半端になるのを防ぐ(推奨手順の実装)。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".gate-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_text(path):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        return f.read()


# ---------------------------------------------------------------- 遷移ログ / lock(_log.py を読む。無ければ無記録)

def _hooklog(root):
    path = os.path.join(root, HOOK_SCRIPTS_REL, "_log.py")
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("_harness_hooklog", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod if hasattr(mod, "gate_log") else None
    except Exception:  # noqa: BLE001
        return None


def _log_dir(root):
    return os.environ.get("HARNESS_HOOK_LOG_DIR") or os.path.join(root, ".github", "hooks", "logs")


def log_transition(root, before, after, n_loops, source):
    """遷移ログに 1 行追記する(_log.py の gate_log。無い環境では False)。"""
    hl = _hooklog(root)
    if hl is None:
        return False
    return bool(hl.gate_log(before, after, loops=n_loops, source=source, payload={}, script="gate_status.py",
                            log_dir_override=_log_dir(root)))


def last_logged_state(root):
    hl = _hooklog(root)
    if hl is None:
        return None
    rec = hl.gate_last(_log_dir(root))
    return rec.get("after") if isinstance(rec, dict) else None


# ---------------------------------------------------------------- 復旧

def state_from_table(text):
    """人間向けフェーズ表(| 要件定義 | 未着手 | …)から状態を復元する。行が 5 つ揃わなければ None。"""
    st = {}
    for ln in (text or "").splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        ph = HUMAN_PHASE.get(cells[0])
        if ph and ph not in st:
            word = re.sub(r"[（(].*$", "", cells[1]).strip()
            if word in HUMAN_VALUE:
                st[ph] = HUMAN_VALUE[word]
    return st if len(st) == len(PHASES) else None


def state_from_git(root):
    try:
        proc = subprocess.run(["git", "-C", root, "show", "HEAD:" + GATE_REL.replace(os.sep, "/")],
                              capture_output=True, timeout=10)
        if proc.returncode != 0:
            return None
        return state(proc.stdout.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def state_from_baseline(root):
    files = glob.glob(os.path.join(_log_dir(root), "usage", "*.baseline.json"))
    for p in sorted(files, key=os.path.getmtime, reverse=True):
        try:
            g = json.load(open(p, encoding="utf-8")).get("gate")
        except Exception:  # noqa: BLE001
            continue
        if isinstance(g, dict) and g:
            return {k: str(v).split()[0] for k, v in g.items() if k in PHASES and str(v).strip()}
    return None


def recover(root, source, path=None):
    """復旧元 source(log / git / table / baseline)から GATE_STATUS を再構成して原子的に書く。(新状態, 説明) を返す。"""
    path = path or os.path.join(root, GATE_REL)
    text = read_text(path) if os.path.exists(path) else ""
    if source == "log":
        st = last_logged_state(root)
        desc = "遷移ログ gate-transitions.jsonl の最終記録"
    elif source == "git":
        st = state_from_git(root)
        desc = "git HEAD の progress.md"
    elif source == "table":
        st = state_from_table(text)
        desc = "progress.md の人間向けフェーズ表"
    elif source == "baseline":
        st = state_from_baseline(root)
        desc = "最新の logs/usage/<sid>.baseline.json"
    else:
        raise ValueError(f"unknown source: {source}")
    if not st or any(k not in st for k in PHASES) or any(st[k] not in VALUES for k in PHASES):
        raise RuntimeError(f"{desc} から 5 キーの完全な状態を復元できない: {st}")
    before = state(text) or None
    new_text = replace_block(text, render_block(st))
    atomic_write(path, new_text)
    log_transition(root, before, st, loops(new_text), f"recover:{source}")
    return st, desc


# ---------------------------------------------------------------- CLI

def _print_result(rel, errors, warnings):
    for e in errors:
        print(f"ERROR: {rel}: GATE_STATUS {e}")
    for w in warnings:
        print(f"WARN: {rel}: GATE_STATUS {w}")


def cmd_check(args):
    root = args.root
    path = args.path or os.path.join(root, GATE_REL)
    if not os.path.exists(path):
        print(f"INFO: {os.path.relpath(path, root)} が無い(未初期化。検査対象なし)")
        return 0
    errors, warnings = check(read_text(path), loop_max=loop_max(root))
    _print_result(os.path.relpath(path, root), errors, warnings)
    if not errors and not warnings:
        print(f"OK: {os.path.relpath(path, root)}: GATE_STATUS は完全({compact(state(read_text(path)))} / loops {loops(read_text(path))})")
    return 1 if errors or (args.strict and warnings) else 0


def cmd_show(args):
    path = args.path or os.path.join(args.root, GATE_REL)
    if not os.path.exists(path):
        print("")
        return 1
    text = read_text(path)
    print(compact(state(text) or {}) + f" loops={loops(text)}")
    return 0


# 証拠 3 点セット(gate-check スキル / guard-done-evidence.sh と同じ規則。A2-4b): 再実行可能なコマンド(`…` または
# 「コマンド:」ラベル)・出力の要約(→ / 結果 / passed / OK 等)・実行日時(YYYY-MM-DD HH:MM)。`set <phase> done` は
# Bash 経由の書込で PreToolUse の guard-done-evidence を通らないため、CLI 側で同じ検査を行い、証拠を値の注記に残す。
EVIDENCE_TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}")
EVIDENCE_CMD_RE = re.compile(r"`[^`]+`|(コマンド|command|cmd|検証)[ \t]*[:：]")
EVIDENCE_OUT_RE = re.compile(r"(→|->|=>|出力|結果|output|result|passed|failed|pass|fail|OK|NG|成功|失敗|"
                             r"exit[ \t]*(code)?[ \t]*[0-9]|[0-9]+[ \t]*(件|tests?|passed|errors?|failures?))", re.I)


def evidence_missing(text):
    """証拠 3 点セットのうち欠けているものの一覧(空なら揃っている)。"""
    missing = []
    if not EVIDENCE_TS_RE.search(text or ""):
        missing.append("実行日時(YYYY-MM-DD HH:MM)")
    if not EVIDENCE_CMD_RE.search(text or ""):
        missing.append("再実行可能なコマンド(`...` またはコマンド: ラベル)")
    if not EVIDENCE_OUT_RE.search(text or ""):
        missing.append("出力の要約(→ / 結果: / passed / OK 等)")
    return missing


def cmd_set(args):
    root = args.root
    path = args.path or os.path.join(root, GATE_REL)
    if args.phase not in PHASES or args.value not in VALUES:
        print(f"ERROR: phase は {'/'.join(PHASES)}、value は {'/'.join(VALUES)}")
        return 1
    if args.value == "done":
        ev = args.evidence or ""
        missing = evidence_missing(ev)
        if missing:
            print("ERROR: done への遷移には --evidence '証拠: `<再実行可能なコマンド>` → <出力の要約> (YYYY-MM-DD HH:MM)' が必要"
                  f"(不足: {'・'.join(missing)}。guard-done-evidence と同じ規則=A2-4b。人の承認発言を得てから実行する)")
            return 1
    text = read_text(path) if os.path.exists(path) else ""
    before = state(text) or {}
    st = dict(before)
    st[args.phase] = args.value
    notes = {}
    p = parse(text)
    for k, _v, note, _i in p["entries"]:
        if k in PHASES and note and k not in notes:
            notes[k] = note
    if args.value == "done":
        ev = args.evidence.strip()
        notes[args.phase] = (ev if ev.startswith("証拠") else "証拠: " + ev) + (" " + args.note if args.note else "")
    elif args.note is not None:
        notes[args.phase] = args.note
    new_text = replace_block(text, render_block(st, notes))
    if args.phase == "test" and args.value == "done" and p["counters"] is not None:
        new_text = replace_counters(new_text, render_counters(0))  # サイクル完了で往復カウンタを 0 に戻す
    errors, warnings = check(new_text, loop_max=loop_max(root))
    if errors:
        _print_result(os.path.relpath(path, root), errors, [])
        print("ERROR: 書換後の GATE_STATUS が完全でないため書き込まない")
        return 1
    atomic_write(path, new_text)
    logged = False if args.no_log else log_transition(root, before or None, st, loops(new_text), "cli")
    print(f"OK: {args.phase}: {before.get(args.phase, '(なし)')} -> {args.value}"
          + (" (遷移ログ 1 行)" if logged else " (遷移ログなし)"))
    _print_result(os.path.relpath(path, root), [], warnings)
    return 0


def cmd_loop(args, delta):
    root = args.root
    path = args.path or os.path.join(root, GATE_REL)
    text = read_text(path)
    n = 0 if delta is None else loops(text) + delta
    new_text = replace_counters(text, render_counters(n))
    atomic_write(path, new_text)
    mx = loop_max(root)
    print(f"OK: implement_test_loops = {n}" + (f" (上限 {mx} 超。/13-converge か人の判断へ)" if n > mx else ""))
    return 0


def cmd_recover(args):
    try:
        st, desc = recover(args.root, args.source, args.path)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: 復旧失敗: {e}")
        return 1
    print(f"OK: {desc} から復旧: {compact(st)}")
    return 0


def cmd_reconcile(args):
    root = args.root
    path = args.path or os.path.join(root, GATE_REL)
    if not os.path.exists(path):
        print("INFO: progress.md が無い")
        return 0
    text = read_text(path)
    cur = state(text)
    if not cur:
        print("ERROR: GATE_STATUS を読めない(python tools/gate_status.py check / recover)")
        return 1
    last = last_logged_state(root)
    if last == cur:
        print("OK: 遷移ログの最終記録と一致")
        return 0
    ok = log_transition(root, last, cur, loops(text), "reconcile")
    print(f"OK: 遷移ログに 1 行追記({compact(last or {}) or 'なし'} -> {compact(cur)})" if ok else "WARN: 遷移ログを書けない(_log.py 不在)")
    return 0


def cmd_unlock(args):
    hl = _hooklog(args.root)
    if hl is None:
        print("ERROR: _log.py を読めない")
        return 1
    d = _log_dir(args.root)
    info = hl.session_lock_read(d)
    if info is None:
        print("OK: session.lock は無い")
        return 0
    stale = info["age_min"] >= hl.session_lock_stale_min(args.root)
    if not stale and not args.force:
        print(f"WARN: lock は {info['session_id']} が {info['age_min']} 分前に更新(まだ stale でない)。別セッションが動いていないことを確認して --force")
        return 1
    try:
        os.remove(os.path.join(d, hl.SESSION_LOCK_NAME))
    except OSError as e:
        print(f"ERROR: 削除失敗: {e}")
        return 1
    print(f"OK: session.lock を削除({info['session_id']} / {info['age_min']} 分前)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--path", default=None, help="progress.md のパス(既定: <root>/docs/00-overview/progress.md)")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check")
    c.add_argument("--strict", action="store_true")
    sub.add_parser("show")
    s = sub.add_parser("set")
    s.add_argument("phase")
    s.add_argument("value")
    s.add_argument("--note", default=None)
    s.add_argument("--evidence", default=None, help="done のとき必須: 証拠 3 点セット(`コマンド` → 出力の要約 (YYYY-MM-DD HH:MM))")
    s.add_argument("--no-log", action="store_true")
    sub.add_parser("bump-loop")
    sub.add_parser("reset-loop")
    r = sub.add_parser("recover")
    r.add_argument("--from", dest="source", required=True, choices=("log", "git", "table", "baseline"))
    sub.add_parser("reconcile")
    u = sub.add_parser("unlock")
    u.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    args.root = os.path.abspath(args.root)
    if args.selftest:
        return selftest()
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "show":
        return cmd_show(args)
    if args.cmd == "set":
        return cmd_set(args)
    if args.cmd == "bump-loop":
        return cmd_loop(args, 1)
    if args.cmd == "reset-loop":
        return cmd_loop(args, None)
    if args.cmd == "recover":
        return cmd_recover(args)
    if args.cmd == "reconcile":
        return cmd_reconcile(args)
    if args.cmd == "unlock":
        return cmd_unlock(args)
    ap.print_help()
    return 2


# ---------------------------------------------------------------- selftest

TEMPLATE_BLOCK = ("<!-- GATE_STATUS\nrequirements: not_started\ndesign: not_started\nimplementation: not_started\n"
                  "test: not_started\nrelease: not_started\n-->\n")


def selftest():
    import shutil
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    def full(r="done", d="done", i="in_progress", t="not_started", rel="not_started", extra=""):
        return f"<!-- GATE_STATUS\nrequirements: {r}\ndesign: {d}\nimplementation: {i}\ntest: {t}\nrelease: {rel}\n-->\n{extra}"

    e, w = check(TEMPLATE_BLOCK)
    chk("テンプレ(全 not_started)は ERROR 0 / WARN 0", not e and not w, str(e + w))
    e, w = check(full())
    chk("正常な進行中状態は ERROR 0 / WARN 0", not e and not w, str(e + w))
    e, _ = check(full().replace("test: not_started\n", ""))
    chk("キー欠落 → ERROR", any("キー欠落: test" in x for x in e), str(e))
    e, _ = check(full(i="dne"))
    chk("語彙外の値 → ERROR", any("語彙外の値: implementation: dne" in x for x in e), str(e))
    e, _ = check(full(extra="").replace("release: not_started", "release: not_started\ndeploy: done"))
    chk("未知のキー → ERROR", any("未知のキー: deploy" in x for x in e), str(e))
    e, _ = check(full().replace("design: done", "design: done\ndesign: in_progress"))
    chk("キー重複 → ERROR", any("キーの重複: design" in x for x in e), str(e))
    e, _ = check("<!-- GATE_STATUS\n(書式が壊れていてキーを読めない)\n-->\n")
    chk("読めない行 → ERROR(golden-eval の fixture E と同じ入力)", any("読めない行" in x for x in e) and any("キー欠落" in x for x in e), str(e))
    e, _ = check("# 進捗\n本文だけ\n")
    chk("ブロック無し → ERROR", e and "ブロックが無い" in e[0], str(e))
    e, w = check(full(i="done", t="done 2026-08-01", rel="in_progress (rc1)"))
    chk("値の後ろの注記は許容(最初のトークンが値)", not e and not w, str(e + w))
    chk("state() は最初のトークン", state(full(t="done 2026-08-01"))["test"] == "done")
    e, w = check(full(i="in_progress", t="done"))
    chk("順序矛盾(規則 B: test done なのに implementation in_progress) → WARN", not e and any("規則 B" in x for x in w), str(e + w))
    e, w = check(full(i="in_progress", t="done", extra="\n状態: 運用中（改修サイクル）\n"))
    chk("運用中注記があれば規則 B は免除(改修サイクル・brownfield 残余)", not e and not w, str(e + w))
    e, w = check(full(r="not_started", d="in_progress", extra="\n状態: 運用中\n"))
    chk("規則 A(先行が not_started なのに後続が着手済み)は注記でも WARN", not e and any("規則 A" in x for x in w), str(e + w))
    e, w = check(full(r="done", d="done", i="done", t="in_progress", rel="done", extra="\n状態: 運用中（改修サイクル）\n"))
    chk("brownfield 取り込み直後(test 残余 in_progress + release done + 注記)は WARN 0", not e and not w, str(e + w))
    chk("GATE_COUNTERS 無し → loops 0(後方互換)", loops(full()) == 0)
    text_c = full() + "<!-- GATE_COUNTERS\nimplement_test_loops: 4\n-->\n"
    e, w = check(text_c, loop_max=3)
    chk("往復 4 > 上限 3 → WARN", not e and any("上限 3" in x for x in w), str(e + w))
    e, _ = check(full() + "<!-- GATE_COUNTERS\nimplement_test_loops: x\n-->\n")
    chk("往復カウンタが整数でない → ERROR", any("非負整数でない" in x for x in e), str(e))
    e, _ = check(full() + "<!-- GATE_COUNTERS\nfoo: 1\n-->\n")
    chk("GATE_COUNTERS の未知のキー → ERROR", any("未知のキー: foo" in x for x in e), str(e))
    chk("compact / from_compact 往復", from_compact(compact(state(full()))) == state(full()))
    chk("render_block は正準 5 行", render_block({"design": "done"}).count("\n") == 6 and "design: done" in render_block({"design": "done"}))
    body = "# 進捗\n\n| 要件定義 | 完了 | - | |\n| 設計 | 進行中 | | |\n| 実装 | 未着手 | | |\n| テスト | ゲート承認待ち | | |\n| リリース | 未着手 | | |\n"
    chk("人間向け表から復元", state_from_table(body) == {"requirements": "done", "design": "in_progress", "implementation": "not_started",
                                                      "test": "pending_approval", "release": "not_started"}, str(state_from_table(body)))
    chk("表が 5 行揃わなければ None", state_from_table("| 要件定義 | 完了 |\n") is None)

    tmp = tempfile.mkdtemp(prefix="gate-selftest-")
    old_env = os.environ.get("HARNESS_HOOK_LOG_DIR")
    try:
        root = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(root, "docs", "00-overview"))
        os.makedirs(os.path.join(root, "tools"))
        scripts = os.path.join(root, HOOK_SCRIPTS_REL)
        os.makedirs(scripts)
        for n in ("_log.py",):
            src = os.path.join(ROOT, HOOK_SCRIPTS_REL, n)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(scripts, n))
        _pp_src = os.path.join(ROOT, ".github", "harness", "privacy-patterns.json")
        if os.path.exists(_pp_src):
            os.makedirs(os.path.join(root, ".github", "harness"), exist_ok=True)
            shutil.copy(_pp_src, os.path.join(root, ".github", "harness", "privacy-patterns.json"))
        with open(os.path.join(root, USAGE_CONFIG_REL), "w", encoding="utf-8") as f:
            json.dump({"implement_test_loop_max": 3, "session_lock_stale_minutes": 120}, f)
        logs = os.path.join(tmp, "logs")
        os.environ["HARNESS_HOOK_LOG_DIR"] = logs
        path = os.path.join(root, GATE_REL)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(TEMPLATE_BLOCK + "\n# 進捗ダッシュボード\n\n| 要件定義 | 未着手 | - | |\n| 設計 | 未着手 | - | |\n| 実装 | 未着手 | - | |\n| テスト | 未着手 | - | |\n| リリース | 未着手 | - | |\n\n## 申し送り\n- メモ\n")
        import contextlib
        import io

        def run(*argv):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["--root", root, *argv])
            return rc

        rc = run("check")
        chk("CLI check: テンプレ状態は exit 0", rc == 0)
        rc = run("set", "requirements", "in_progress")
        t1 = read_text(path)
        chk("CLI set: 原子的書換で値が変わり本文は残る", rc == 0 and state(t1)["requirements"] == "in_progress" and "## 申し送り\n- メモ" in t1, t1[:120])
        chk("set: 一時ファイルが残らない", not [p for p in os.listdir(os.path.dirname(path)) if p.endswith(".tmp")])
        hl = _hooklog(root)
        rec = hl.gate_last(logs) if hl else None
        chk("set: 遷移ログ 1 行(rev 1・before は書換前のファイル状態・changed は差分だけ)", rec and rec["rev"] == 1
            and rec["before"]["requirements"] == "not_started" and rec["after"]["requirements"] == "in_progress"
            and rec["changed"] == "requirements:not_started->in_progress" and rec["source"] == "cli", str(rec))
        rc = run("set", "requirements", "done")
        chk("set done: 証拠 3 点セット(--evidence)が無ければ拒否(guard-done-evidence と同じ規則。A2-4b)", rc == 1 and state(read_text(path))["requirements"] == "in_progress")
        rc = run("set", "requirements", "done", "--evidence", "`python tools/golden-eval.py .` → OK")
        chk("set done: 実行日時の無い証拠は拒否", rc == 1 and state(read_text(path))["requirements"] == "in_progress")
        ev = "`python tools/golden-eval.py .` → OK 3/3 (2026-09-17 10:00)"
        run("set", "requirements", "done", "--evidence", ev, "--note", "承認: 山田")
        rec2 = hl.gate_last(logs)
        chk("set: 2 回目は rev 2・before は前回の after・changed は差分だけ", rec2 and rec2["rev"] == 2 and rec2["before"]["requirements"] == "in_progress"
            and rec2["changed"] == "requirements:in_progress->done", str(rec2))
        chk("set done --evidence --note: 証拠と注記が値の後ろに残る", "requirements: done 証拠: " + ev + " 承認: 山田" in read_text(path), read_text(path)[:200])
        rc = run("set", "release", "done", "--evidence", ev)
        chk("set: 順序矛盾(規則 B)は WARN のみで書ける(exit 0)", rc == 0 and state(read_text(path))["release"] == "done")
        run("set", "release", "not_started")
        rc = run("set", "design", "bogus")
        chk("set: 語彙外の値は拒否", rc == 1 and state(read_text(path))["design"] == "not_started")
        rc = run("bump-loop")
        rc2 = run("bump-loop")
        t2 = read_text(path)
        chk("bump-loop: GATE_COUNTERS が GATE_STATUS 直下にでき 2 になる", rc == 0 and rc2 == 0 and loops(t2) == 2
            and t2.index("GATE_COUNTERS") > t2.index("GATE_STATUS") and t2.index("GATE_COUNTERS") < t2.index("# 進捗"), t2[:200])
        for _ in range(2):
            run("bump-loop")
        rc = run("check")
        chk("check: 往復 4 > 3 は WARN のみ(exit 0)、--strict なら exit 1", rc == 0 and run("check", "--strict") == 1)
        run("set", "test", "done", "--evidence", ev)
        chk("test: done でカウンタは 0 に戻る", loops(read_text(path)) == 0)
        run("set", "test", "not_started")
        # 復旧ドリル: 壊す → 検出 → 復旧 → 検査が通る
        good = state(read_text(path))
        broken = read_text(path).replace("implementation: not_started", "implementaton: nto_started")
        atomic_write(path, broken)
        chk("復旧ドリル: 壊れた GATE_STATUS を check が検出(exit 1)", run("check") == 1)
        chk("復旧ドリル: recover --from log で復旧", run("recover", "--from", "log") == 0 and state(read_text(path)) == good, compact(state(read_text(path)) or {}))
        chk("復旧ドリル: 復旧後の check は exit 0", run("check") == 0)
        rec3 = hl.gate_last(logs)
        chk("復旧も遷移ログに残る(source recover:log)", rec3 and rec3["source"] == "recover:log", str(rec3))
        atomic_write(path, read_text(path).replace("<!-- GATE_STATUS", "<!-- GATE_STATUX"))
        chk("復旧ドリル: ブロック喪失も検出", run("check") == 1)
        chk("復旧ドリル: recover --from table(人間向け表)でブロックを再生成", run("recover", "--from", "table") == 0
            and state(read_text(path)) == {k: "not_started" for k in PHASES}, compact(state(read_text(path)) or {}))
        # reconcile: ログ外の書換(sed 等)を検知して 1 行追記
        atomic_write(path, read_text(path).replace("design: not_started", "design: in_progress"))
        rc = run("reconcile")
        rec4 = hl.gate_last(logs)
        chk("reconcile: ログ外の書換を 1 行追記(source reconcile)", rc == 0 and rec4["source"] == "reconcile" and rec4["changed"] == "design:not_started->in_progress", str(rec4))
        chk("reconcile: 一致していれば追記しない", run("reconcile") == 0 and hl.gate_last(logs)["rev"] == rec4["rev"])
        # git からの復旧(git があれば)
        try:
            gi = subprocess.run(["git", "-C", root, "init", "-q"], capture_output=True, timeout=10).returncode == 0
        except Exception:  # noqa: BLE001
            gi = False
        if gi:
            subprocess.run(["git", "-C", root, "-c", "user.email=t@x", "-c", "user.name=t", "add", "-A"], capture_output=True)
            subprocess.run(["git", "-C", root, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-q", "-m", "init"], capture_output=True)
            committed = state(read_text(path))
            atomic_write(path, read_text(path).replace("<!-- GATE_STATUS", "<!-- GATE_STATUX"))
            chk("復旧ドリル: recover --from git", run("recover", "--from", "git") == 0 and state(read_text(path)) == committed)
        else:
            print("SKIP git not available")
        # baseline からの復旧
        os.makedirs(os.path.join(logs, "usage"), exist_ok=True)
        with open(os.path.join(logs, "usage", "s1.baseline.json"), "w", encoding="utf-8") as f:
            json.dump({"gate": {"requirements": "done", "design": "done", "implementation": "in_progress", "test": "not_started", "release": "not_started"}}, f)
        chk("復旧ドリル: recover --from baseline", run("recover", "--from", "baseline") == 0 and state(read_text(path))["implementation"] == "in_progress")
        # lock
        if hl:
            hl.session_lock_write("sess-A", logs, cwd=root)
            chk("unlock: fresh な lock は --force 無しで消さない", run("unlock") == 1 and os.path.exists(os.path.join(logs, hl.SESSION_LOCK_NAME)))
            chk("unlock --force: 消す", run("unlock", "--force") == 0 and not os.path.exists(os.path.join(logs, hl.SESSION_LOCK_NAME)))
        chk("loop_max: usage-config.json を読む", loop_max(root) == 3)
        os.environ["HARNESS_LOOP_MAX"] = "5"
        chk("loop_max: 環境変数が優先", loop_max(root) == 5)
        os.environ.pop("HARNESS_LOOP_MAX", None)
        chk("show: compact 1 行", run("show") == 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--root", tmp, "check"])
        chk("check: progress.md が無ければ INFO で exit 0", rc == 0 and "INFO" in buf.getvalue(), buf.getvalue())
    finally:
        if old_env is None:
            os.environ.pop("HARNESS_HOOK_LOG_DIR", None)
        else:
            os.environ["HARNESS_HOOK_LOG_DIR"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

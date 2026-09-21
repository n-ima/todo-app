#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""受領書(logs/usage。tools/session-receipt.py が書く)を集計し、トークン/費用レポートと基準線を生成する。

usage:
  python tools/effort-report.py                        # logs/usage → docs/06-retrospective/effort-report.md + baselines.json
  python tools/effort-report.py --migrate PATH.csv     # 旧 effort-log.csv(D040 形式)を受領書(migrated)へ取り込む(既存 sid はスキップ)
  python tools/effort-report.py --log PATH.csv         # 旧 CSV を移行せずその場で合算して集計する
  python tools/effort-report.py --snapshot             # 月次スナップショット(effort-snapshots.json)を更新し、ローテーションで
                                                       #   消えたセッションは前月分から合算する
  python tools/effort-report.py --rotate               # rotate_days(tools/usage-config.json)超の受領書を削除(--snapshot と併用)
  python tools/effort-report.py --no-anonymize         # session_id/日付を出す(既定は匿名化: 連番+ISO週)
  python tools/effort-report.py --summary              # 標準出力に要約のみ(/99-status の費用欄用)
  python tools/effort-report.py --selftest

- 入力(一次データ): `.github/hooks/logs/usage/<sid>.receipt.json`(確定)/`.receipt.draft.json`(暫定)。
  D040 の docs/00-overview/effort-log.csv は廃止(2026-09-09 再監査 RC-4/A6-7)。--migrate で取り込む。
- 出力(committed・集計のみ): docs/06-retrospective/effort-report.md、baselines.json(工程別 p50/p90、n<5 は暫定)、
  effort-snapshots.json(月次。匿名化済み)。個人の session_id・日付(日単位)・プロンプト本文は載せない。
- 単価: tools/prices.json(list 価格・請求額ではない)。受領書の推定値は採取時の単価表なので、ここで再計算して基準を揃える。
- 用途: /10-retrospective の「トークン効率」検証(モデル×effort の適否・無駄の所在・逸脱セッション)。
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLS = ["input", "output", "cache_read", "cache_w5m", "cache_w1h"]
LOGS_REL = os.path.join(".github", "hooks", "logs", "usage")
OUT_REL = os.path.join("docs", "06-retrospective", "effort-report.md")
BASELINES_REL = os.path.join("docs", "06-retrospective", "baselines.json")
SNAPSHOT_REL = os.path.join("docs", "06-retrospective", "effort-snapshots.json")
REVIEW_ROLES = {"reviewer", "spec-critic"}
DRAFT_SETTLED_HOURS = 6  # draft のまま 6 時間更新が無ければ終了済みとみなす(SessionEnd 未発火に耐える)

_PRICES = None


def _load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def load_prices(root=None):
    """tools/prices.json を最長前方一致順に整えて返す(受領書側と同じ規則)。"""
    global _PRICES
    data = _load_json(os.path.join(root or ROOT, "tools", "prices.json"), {}) or {}
    models = [m for m in (data.get("models") or []) if isinstance(m, dict) and m.get("id")]
    models.sort(key=lambda m: -len(m["id"]))
    _PRICES = {"as_of": data.get("as_of") or "?", "models": models,
               "sources": data.get("sources") or []}
    return _PRICES


def price_of(model):
    """単価行(dict)を返す。未登録は None。family 行は fallback キー付き。"""
    prices = _PRICES or load_prices()
    for m in prices["models"]:
        if (model or "").startswith(m["id"]):
            return m
    return None


def row_cost(r):
    """(推定コストUSD, 単価既知か) を返す。r は COLS を持つ dict。"""
    p = price_of(r.get("model", ""))
    if p is None:
        return 0.0, False
    cost = (r["input"] * p.get("input", 0) + r["output"] * p.get("output", 0)
            + r["cache_read"] * p.get("cache_read", 0) + r["cache_w5m"] * p.get("cache_w5m", 0)
            + r["cache_w1h"] * p.get("cache_w1h", 0)) / 1_000_000
    return cost, True


def load_rows(path):
    """旧 effort-log.csv(1行 = セッション×エージェント×モデル)を読む。"""
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            try:
                for c in COLS:
                    r[c] = int(r.get(c) or 0)
            except (TypeError, ValueError):
                continue
            rows.append(r)
    return rows


def group(rows, key):
    g = defaultdict(lambda: {"sessions": set(), "cost": 0.0, **{c: 0 for c in COLS}})
    for r in rows:
        k = key(r)
        acc = g[k]
        acc["sessions"].add(r["session_id"])
        for c in COLS:
            acc[c] += r[c]
        cost, _ = row_cost(r)
        acc["cost"] += cost
    return g


def fmt(n):
    return f"{int(n):,}"


def table(title, g, sort_key=None, label="区分"):
    lines = [f"## {title}", "",
             f"| {label} | セッション数 | input | output | cache_read | cache_write (5m / 1h) | 推定コスト (USD) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    keys = sorted(g.keys(), key=sort_key) if sort_key else \
        sorted(g.keys(), key=lambda k: -g[k]["cost"])
    for k in keys:
        a = g[k]
        lines.append(
            f"| {k} | {len(a['sessions'])} | {fmt(a['input'])} | {fmt(a['output'])} "
            f"| {fmt(a['cache_read'])} | {fmt(a['cache_w5m'])} / {fmt(a['cache_w1h'])} "
            f"| {a['cost']:,.2f} |")
    lines.append("")
    return lines


# ---------------------------------------------------------------- 受領書の読み込み

def percentile(values, p):
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 2)
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 2)


def iso_week(date_str):
    try:
        d = datetime.strptime((date_str or "")[:10], "%Y-%m-%d")
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    except Exception:
        return "?"


def month_of(date_str):
    return (date_str or "")[:7] or "?"


def session_from_receipt(sid, r, settled):
    """受領書 → 集計用のセッション dict(本文は持たない)。"""
    meta, cost, oc, oh = r.get("meta") or {}, r.get("cost") or {}, r.get("outcome_common") or {}, r.get("outcome_harness") or {}
    host = meta.get("host") or {}
    tokens = []
    for t in r.get("tokens") or []:
        row = {"session_id": sid, "agent": t.get("agent") or "?", "model": t.get("model") or "?",
               "phase": meta.get("phase") or "other", "effort": host.get("effort") or "?"}
        for c in COLS:
            try:
                row[c] = int(t.get(c) or 0)
            except (TypeError, ValueError):
                row[c] = 0
        tokens.append(row)
    est = sum(row_cost(t)[0] for t in tokens)
    spawns = oh.get("spawns") or {}
    started = meta.get("started") or meta.get("captured_at") or ""
    return {
        "sid": sid, "phase": meta.get("phase") or "other", "date": started[:10], "week": iso_week(started),
        "month": month_of(started), "platform": meta.get("platform") or "?", "capture": meta.get("capture"),
        "settled": settled, "tokens": tokens, "cost": est, "host_cost": cost.get("host_amount"),
        "turns": oc.get("num_turns"), "user_prompts": oc.get("user_prompts"),
        "transitions": len(oh.get("gate_transitions") or []),
        "tasks_delta": oh.get("tasks_done_delta") if isinstance(oh.get("tasks_done_delta"), int) else 0,
        "review_runs": sum(int(v or 0) for k, v in spawns.items() if k in REVIEW_ROLES),
        "spawns": sum(int(v or 0) for v in spawns.values()),
        "review_less_done": bool(oh.get("review_less_done")),
        "used_pct": (r.get("context") or {}).get("used_percentage"),
        "cache_read_cost_share": cost.get("cache_read_cost_share"),
        "coverage": (meta.get("coverage") or {}).get("ratio"),
        "model_main": host.get("model_main"), "effort": host.get("effort"),
        "tests": (oc.get("tests") or {}).get("result"),
        "flags": list(r.get("flags") or []), "source": "migrated" if "migrated" in (r.get("flags") or []) else "receipt",
        "total_tokens": sum(sum(t[c] for c in COLS) for t in tokens),
    }


def load_sessions(logs):
    """logs/usage の受領書を読む(確定を優先。draft は 6 時間更新が無ければ settled)。"""
    sessions = {}
    now = datetime.now(timezone.utc).timestamp()
    for path in glob.glob(os.path.join(logs, "*.receipt.json")):
        sid = os.path.basename(path)[:-len(".receipt.json")]
        data = _load_json(path)
        if isinstance(data, dict) and data.get("meta"):
            sessions[sid] = session_from_receipt(sid, data, True)
    for path in glob.glob(os.path.join(logs, "*.receipt.draft.json")):
        sid = os.path.basename(path)[:-len(".receipt.draft.json")]
        if sid in sessions:
            continue
        data = _load_json(path)
        if isinstance(data, dict) and data.get("meta"):
            try:
                settled = (now - os.path.getmtime(path)) > DRAFT_SETTLED_HOURS * 3600
            except OSError:
                settled = False
            sessions[sid] = session_from_receipt(sid, data, settled)
    return sessions


def sessions_from_csv(rows):
    """旧 CSV 行をセッション dict に(移行せず合算する --log 用)。"""
    by = defaultdict(list)
    for r in rows:
        by[r["session_id"]].append(r)
    out = {}
    for sid, rs in by.items():
        tokens = [{"session_id": sid, "agent": r.get("agent") or "?", "model": r.get("model") or "?",
                   "phase": r.get("phase") or "other", "effort": "?", **{c: r[c] for c in COLS}} for r in rs]
        date = rs[0].get("date") or ""
        out[sid] = {"sid": sid, "phase": rs[0].get("phase") or "other", "date": date, "week": iso_week(date),
                    "month": month_of(date), "platform": rs[0].get("platform") or "?", "capture": "csv",
                    "settled": True, "tokens": tokens, "cost": sum(row_cost(t)[0] for t in tokens),
                    "host_cost": None, "turns": None, "user_prompts": None, "transitions": 0, "tasks_delta": 0,
                    "review_runs": sum(1 for r in rs if r.get("agent") in REVIEW_ROLES), "spawns": sum(1 for r in rs if r.get("agent") != "main"),
                    "review_less_done": False, "used_pct": None, "cache_read_cost_share": None, "coverage": None,
                    "model_main": next((r.get("model") for r in rs if r.get("agent") == "main"), None), "effort": None,
                    "tests": None, "flags": ["csv"], "source": "csv",
                    "total_tokens": sum(sum(t[c] for c in COLS) for t in tokens)}
    return out


# ---------------------------------------------------------------- 移行(旧 CSV → 受領書)

def migrate_csv(csv_path, logs, prices_date):
    """旧 effort-log.csv の各セッションを migrated 受領書として logs/usage に書く。既存 sid はスキップ。"""
    rows = load_rows(csv_path)
    by = defaultdict(list)
    for r in rows:
        by[r["session_id"]].append(r)
    written, skipped = 0, 0
    os.makedirs(logs, exist_ok=True)
    for sid, rs in by.items():
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
        dst = os.path.join(logs, safe + ".receipt.json")
        if os.path.exists(dst) or os.path.exists(os.path.join(logs, safe + ".receipt.draft.json")):
            skipped += 1
            continue
        tokens = []
        for r in rs:
            tokens.append({"agent": r.get("agent") or "?", "model": r.get("model") or "?",
                           "query_source": "main" if r.get("agent") == "main" else "subagent", "spawn_depth": 0 if r.get("agent") == "main" else 1,
                           **{c: r[c] for c in COLS}})
        est = sum(row_cost(t)[0] for t in tokens)
        date = rs[0].get("date") or ""
        spawns = {r["agent"]: 1 for r in rs if r.get("agent") != "main"}
        receipt = {
            "schema": "receipt/1",
            "meta": {"session_id": sid, "cwd": None, "platform": rs[0].get("platform") or "claude-code",
                     "started": (date + "T00:00:00Z") if date else None, "ended": None,
                     "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "capture": "migrated-csv",
                     "host": {"product": "claude-code", "version": None, "model_main": next((r.get("model") for r in rs if r.get("agent") == "main"), None),
                              "effort": None, "entrypoints": [], "harness_commit": None},
                     "phase": rs[0].get("phase") or "other", "phase_source": "csv-first-command", "skills_attributed": {},
                     "commands": [], "coverage": {"tokens": "transcript(csv)", "parsed": None, "total": None, "ratio": None,
                                                  "status_source": "none", "resumed": False}},
            "tokens": tokens,
            "cost": {"amount": round(est, 4), "cost_unit": "usd", "basis": "list", "is_estimate": True,
                     "price_table_date": prices_date, "unknown_models": [], "fallback_models": [],
                     "host_amount": None, "host_basis": None, "divergence_ratio": None,
                     "note": "effort-log.csv からの移行。host 値・成果層は無い"},
            "context": {"used_percentage": None, "context_window_size": None, "exceeds_200k_tokens": None, "prompt_cache": None, "cache_read_ratio": None},
            "outcome_common": {"lines_added": None, "lines_removed": None, "files_changed": None, "commits": None, "num_turns": None,
                               "user_prompts": None, "tests": {"ran": False, "runs": 0, "result": "unknown", "source": "none", "observed": None},
                               "total_tokens": sum(sum(t[c] for c in COLS) for t in tokens)},
            "outcome_harness": {"gate_before": None, "gate_after": None, "gate_transitions": [], "tasks_done_delta": None,
                                "spawns": spawns, "requested_models": [], "resolved_models": {}, "review_runs_phase": 0,
                                "review_ratio": None, "review_less_done": False, "fast_track": False, "rework_cycles": 0},
            "baseline": None, "flags": ["migrated"],
        }
        fd, tmp = tempfile.mkstemp(dir=logs, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(receipt, f, ensure_ascii=False, indent=1)
        os.replace(tmp, dst)
        written += 1
    return written, skipped


# ---------------------------------------------------------------- スナップショット(月次・匿名)

def sid_hash(sid):
    return hashlib.sha1(sid.encode("utf-8")).hexdigest()[:12]


def anonymous_record(s):
    return {"h": sid_hash(s["sid"]), "phase": s["phase"], "week": s["week"], "month": s["month"], "cost": round(s["cost"], 4),
            "tokens": {c: sum(t[c] for t in s["tokens"]) for c in COLS}, "transitions": s["transitions"],
            "tasks_delta": s["tasks_delta"], "review_runs": s["review_runs"], "model_main": s["model_main"],
            "effort": s["effort"], "platform": s["platform"]}


def update_snapshot(path, sessions):
    """月次スナップショットを更新して返す。logs から消えたセッションは前回分を保持(合算)。"""
    snap = _load_json(path, {}) or {}
    records = {r["h"]: r for r in (snap.get("sessions") or []) if isinstance(r, dict) and r.get("h")}
    for s in sessions.values():
        if s["settled"]:
            records[sid_hash(s["sid"])] = anonymous_record(s)
    months = defaultdict(lambda: {"sessions": 0, "cost": 0.0, **{c: 0 for c in COLS}})
    for r in records.values():
        m = months[r["month"]]
        m["sessions"] += 1
        m["cost"] = round(m["cost"] + r["cost"], 4)
        for c in COLS:
            m[c] += int((r.get("tokens") or {}).get(c) or 0)
    data = {"schema": "effort-snapshots/1", "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": "月次スナップショット(集計値のみ・session_id はハッシュ)。ローテーションで logs から消えたセッションもここから合算する",
            "months": dict(sorted(months.items())), "sessions": sorted(records.values(), key=lambda r: (r["month"], r["week"], r["h"]))}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


def merge_snapshot_sessions(sessions, snap):
    """logs に無いスナップショット分をセッション集合に足す(トークン行は 'main+subagents' 1 行に潰す)。"""
    present = {sid_hash(s["sid"]) for s in sessions.values()}
    merged = dict(sessions)
    for r in (snap or {}).get("sessions") or []:
        if not isinstance(r, dict) or r.get("h") in present:
            continue
        tok = {"session_id": r["h"], "agent": "main+subagents", "model": r.get("model_main") or "?", "phase": r.get("phase") or "other",
               "effort": r.get("effort") or "?", **{c: int((r.get("tokens") or {}).get(c) or 0) for c in COLS}}
        merged["snap:" + r["h"]] = {
            "sid": r["h"], "phase": r.get("phase") or "other", "date": "", "week": r.get("week") or "?", "month": r.get("month") or "?",
            "platform": r.get("platform") or "?", "capture": "snapshot", "settled": True, "tokens": [tok], "cost": float(r.get("cost") or 0),
            "host_cost": None, "turns": None, "user_prompts": None, "transitions": int(r.get("transitions") or 0),
            "tasks_delta": int(r.get("tasks_delta") or 0), "review_runs": int(r.get("review_runs") or 0), "spawns": 0,
            "review_less_done": False, "used_pct": None, "cache_read_cost_share": None, "coverage": None,
            "model_main": r.get("model_main"), "effort": r.get("effort"), "tests": None, "flags": ["snapshot"], "source": "snapshot",
            "total_tokens": sum(tok[c] for c in COLS)}
    return merged


def rotate(logs, days):
    """rotate_days 超の受領書と付随ファイルを削除する(呼び出し側で --snapshot 済みを前提)。"""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    removed = 0
    for path in glob.glob(os.path.join(logs, "*.*")):
        try:
            if os.path.getmtime(path) < cutoff and os.path.basename(path) != "sessions.jsonl":
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


# ---------------------------------------------------------------- 集計・レポート

def compute_baselines(sessions, min_n):
    per_phase = defaultdict(list)
    all_costs = []
    for s in sessions.values():
        if not s["settled"] or s["cost"] <= 0:
            continue
        per_phase[s["phase"]].append(s)
        all_costs.append(s["cost"])

    def stats(ss):
        costs = [x["cost"] for x in ss]
        return {"n": len(ss), "cost_p50": percentile(costs, 0.5), "cost_p90": percentile(costs, 0.9),
                "cost_max": round(max(costs), 2) if costs else None,
                "turns_p50": percentile([x["turns"] for x in ss if isinstance(x["turns"], int)], 0.5),
                "cost_per_transition": (round(sum(costs) / sum(x["transitions"] for x in ss), 2) if sum(x["transitions"] for x in ss) else None),
                "cost_per_task_done": (round(sum(costs) / sum(x["tasks_delta"] for x in ss), 2) if sum(x["tasks_delta"] for x in ss) > 0 else None),
                "provisional": len(ss) < min_n}

    return {"schema": "baselines/1", "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "min_n": min_n,
            "basis": "list 価格(tools/prices.json)による推定。請求額ではない",
            "all": {"n": len(all_costs), "cost_p50": percentile(all_costs, 0.5), "cost_p90": percentile(all_costs, 0.9),
                    "cost_max": round(max(all_costs), 2) if all_costs else None, "provisional": len(all_costs) < min_n},
            "phases": {ph: stats(ss) for ph, ss in sorted(per_phase.items())}}


def deviations(sessions, baselines, cfg):
    warn_pct = float(cfg.get("context_warn_pct") or 40)
    cache_warn = float(cfg.get("cache_read_ratio_warn") or 0.95)
    out = []
    for s in sessions.values():
        reasons = []
        bl = (baselines.get("phases") or {}).get(s["phase"]) or {}
        if bl.get("n", 0) >= 2 and isinstance(bl.get("cost_p90"), (int, float)) and s["cost"] > bl["cost_p90"]:
            reasons.append(f"同工程 p90 超({'暫定' if bl.get('provisional') else '確定'})")
        if isinstance(s["used_pct"], (int, float)) and s["used_pct"] >= warn_pct:
            reasons.append(f"文脈 {s['used_pct']:.0f}% ≥ {warn_pct:.0f}%")
        if isinstance(s["cache_read_cost_share"], (int, float)) and s["cache_read_cost_share"] >= cache_warn:
            reasons.append(f"cache_read 費用比 {s['cache_read_cost_share'] * 100:.0f}%(継続セッションの文脈再送)")
        if s["review_less_done"]:
            reasons.append("review_less_done(同工程で reviewer/spec-critic 起動 0 のまま done)")
        if isinstance(s["coverage"], (int, float)) and s["coverage"] < 0.8:
            reasons.append(f"解析率 {s['coverage'] * 100:.0f}%(数値を信用しない)")
        if reasons:
            out.append((s, reasons))
    return out


def label_sessions(sessions, anonymize):
    ordered = sorted(sessions.values(), key=lambda s: (s["date"] or "9999", s["week"], s["sid"]))
    labels = {}
    for i, s in enumerate(ordered, 1):
        labels[s["sid"]] = f"S{i:02d} ({s['week']})" if anonymize else f"{s['sid'][:8]} ({s['date'] or s['week']})"
    return labels


def build_report(sessions, baselines, cfg, prices, anonymize=True, snapshot_note=None, kpi=None, hook_line=None):
    rows = [t for s in sessions.values() for t in s["tokens"]]
    total = group(rows, lambda r: "合計")
    unknown = sorted({r["model"] for r in rows if price_of(r["model"]) is None})
    fallback = sorted({r["model"] for r in rows if (price_of(r["model"]) or {}).get("family")})
    weeks = sorted({s["week"] for s in sessions.values() if s["week"] != "?"})
    labels = label_sessions(sessions, anonymize)
    n_settled = sum(1 for s in sessions.values() if s["settled"])
    n_draft = len(sessions) - n_settled
    sources = defaultdict(int)
    for s in sessions.values():
        sources[s["source"]] += 1

    lines = ["# トークン利用レポート (effort-report)", ""]
    lines.append(f"- 生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- 対象: `.github/hooks/logs/usage/` の受領書 {len(sessions)} セッション"
                 f"（確定/settled {n_settled}・進行中 draft {n_draft}。出所: "
                 + ", ".join(f"{k} {v}" for k, v in sorted(sources.items())) + "）"
                 f"、期間 {weeks[0] if weeks else '-'} 〜 {weeks[-1] if weeks else '-'}（ISO 週）")
    lines.append("- 自動記録の対象は Claude Code セッション（`-p` は result JSON から同スキーマ）。Copilot CLI/VS Code/cloud の"
                 "取得経路と劣化モードは `.github/harness/PLATFORM.md`「コスト計測の詳細」")
    lines.append(f"- 推定コストは `tools/prices.json`（取得日 {prices.get('as_of')}）の list 価格換算。"
                 "**請求額ではない**（サブスクは無関係）。host 側の値（statusline）は受領書にのみ保持し、ここでは自前単価で再計算して基準を揃える")
    lines.append("- 匿名化: " + ("session_id と日付は出さない（連番 + ISO 週）。個人の一次データはローカル logs/usage のみ"
                                if anonymize else "**無効**（session_id を含む。共有リポジトリに commit しないこと）"))
    if snapshot_note:
        lines.append(f"- {snapshot_note}")
    if unknown:
        lines.append(f"- **単価不明のモデル**（コスト0として計上。tools/prices.json への追加が必要）: " + ", ".join(f"`{m}`" for m in unknown))
    if fallback:
        lines.append(f"- family 行で解決したモデル（生 ID 未登録。単価は同族の最新版を仮置き）: " + ", ".join(f"`{m}`" for m in fallback))
    lines.append("")

    lines += table("合計", total, sort_key=lambda k: k)
    lines += table("工程（フェーズ）別", group(rows, lambda r: r.get("phase") or "other"),
                   sort_key=lambda k: (k == "other", k), label="工程")
    lines += table("エージェント別", group(rows, lambda r: r.get("agent") or "?"), label="エージェント")
    lines += table("モデル別", group(rows, lambda r: r.get("model") or "?"), label="モデル")
    lines += table("モデル × effort 別（effort はセッションのメイン会話の値。サブエージェント個別の effort は未取得）",
                   group(rows, lambda r: f"{r.get('model') or '?'} / {r.get('effort') or '?'}"), label="モデル / effort")

    # セッション×工程の分布表
    lines += ["## セッション × 工程の分布（1 セッションあたり推定コスト USD）", "",
              f"n < {baselines['min_n']} の工程は**暫定**。受領書の基準線比較はこの表（docs/06-retrospective/baselines.json）を使う。", "",
              "| 工程 | n | p50 | p90 | max | turns p50 | 1 ゲート遷移あたり | 1 タスク [x] あたり |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for ph, st in baselines["phases"].items():
        lines.append(f"| {ph}{'（暫定）' if st['provisional'] else ''} | {st['n']} | {st['cost_p50']} | {st['cost_p90']} | {st['cost_max']} "
                     f"| {st['turns_p50'] if st['turns_p50'] is not None else '-'} "
                     f"| {st['cost_per_transition'] if st['cost_per_transition'] is not None else '-'} "
                     f"| {st['cost_per_task_done'] if st['cost_per_task_done'] is not None else '-'} |")
    a = baselines["all"]
    lines.append(f"| **全工程**{'（暫定）' if a['provisional'] else ''} | {a['n']} | {a['cost_p50']} | {a['cost_p90']} | {a['cost_max']} | - | - | - |")
    lines.append("")

    # 成果あたり費用(全体)
    settled = [s for s in sessions.values() if s["settled"]]
    tot_cost = sum(s["cost"] for s in settled)
    tot_tr = sum(s["transitions"] for s in settled)
    tot_tasks = sum(s["tasks_delta"] for s in settled)
    tot_rev = sum(s["review_runs"] for s in settled)
    lines += ["## 成果あたり費用（量の代理指標。品質ではない）", "",
              "| 指標 | 値 | 備考 |", "|---|---:|---|",
              f"| 1 ゲート遷移あたり | {round(tot_cost / tot_tr, 2) if tot_tr else '-'} | 遷移 {tot_tr} 件 / ${tot_cost:,.2f} |",
              f"| 1 タスク [x] あたり | {round(tot_cost / tot_tasks, 2) if tot_tasks > 0 else '-'} | tasks [x] 増分 {tot_tasks} / SessionStart 基準値のあるセッションのみ |",
              f"| 1 CR あたり | - | 未計測（change-requests.md の CR-ID 差分は受領書 v2 で追加予定） |",
              f"| reviewer/spec-critic 起動 | {tot_rev} 回 | done タスクに対するレビュー済み率は受領書の review_ratio |", ""]

    # 逸脱一覧
    devs = deviations(sessions, baselines, cfg)
    lines += ["## 逸脱一覧（警告のみ。フックは止めない）", ""]
    if devs:
        lines += ["| セッション | 工程 | 推定 USD | 逸脱 |", "|---|---|---:|---|"]
        for s, reasons in sorted(devs, key=lambda x: -x[0]["cost"]):
            lines.append(f"| {labels[s['sid']]} | {s['phase']} | {s['cost']:,.2f} | {'; '.join(reasons)} |")
    else:
        lines.append("該当なし")
    lines.append("")

    if kpi is not None:
        lines += kpi_lines(kpi, hook_line)

    lines.append("## 振り返りでの見方")
    lines.append("")
    lines.append("- **モデル × effort は適切だったか**: 機械的な作業(task-worker等)に高価なモデル・高 effort を使っていないか、"
                 "逆に設計・レビューを安いモデルで行って手戻りしていないかを見る（役割別方針の正は `.github/harness/model-policy.yml`）。")
    lines.append("- **無駄はないか**: 同一工程のセッション数が多い場合は差し戻し往復やセッション分割の失敗を疑う。"
                 "cache_read の費用比が高いセッションは長すぎる会話(context rot帯域)の兆候。")
    lines.append("- **上流品質との相関**: 差し戻しで消えたトークンと spec-critic 1回のトークンを比較する(独立レビューの費用対効果の実測)。")
    lines.append("- **数値の出所**: 累計トークンは transcript(非公式・解析率付き)、host $ と文脈%は statusline(公式)。"
                 "retrospective_template §3 の数値行はこのレポートから転記する。")
    lines.append("")
    return "\n".join(lines)


def summary_text(sessions, baselines):
    settled = [s for s in sessions.values() if s["settled"]]
    tot = sum(s["cost"] for s in settled)
    parts = [f"effort-report: {len(sessions)} セッション（確定 {len(settled)}）・推定合計 ${tot:,.2f}（list 価格・請求額ではない）"]
    for ph, st in baselines["phases"].items():
        parts.append(f"  {ph}: n={st['n']} p50 ${st['cost_p50']} / p90 ${st['cost_p90']}{'（暫定）' if st['provisional'] else ''}")
    return "\n".join(parts)


# ---------------------------------------------------------------- ハーネス自己改善 KPI(OP-4 / A6-24)

PROPOSALS_REL = os.path.join("audits", "PROPOSALS.md")
PROPOSAL_STATES = ("applied", "partial", "deferred", "rejected", "open")


def _parse_date(s):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def load_proposals(path):
    """audits/PROPOSALS.md の表を [{id, state, registered, updated, source}] で返す。
    列は見出し行(ID / 状態 / 登録日 / 状態更新日 / 出典)から位置を決める(validate-harness.py (e) と同じ流儀)。
    登録日・状態更新日の列が無い旧書式では None(KPI は「列なし」を報告する)。"""
    rows = []
    if not os.path.exists(path):
        return rows
    cols = None
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line.strip().startswith("|"):
                cols = None
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cols is None:
                if any("状態" in c for c in cells):
                    cols = {"id": next((i for i, c in enumerate(cells) if c.upper() == "ID"), 0),
                            "state": next(i for i, c in enumerate(cells) if "状態" in c and "更新" not in c),
                            "reg": next((i for i, c in enumerate(cells) if "登録日" in c), None),
                            "upd": next((i for i, c in enumerate(cells) if "状態更新日" in c), None),
                            "src": next((i for i, c in enumerate(cells) if "出典" in c), None)}
                continue
            if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                continue
            if cols["state"] >= len(cells):
                continue
            token = cells[cols["state"]].lower()
            state = next((s for s in PROPOSAL_STATES if token == s or token.startswith(s)), None)
            rows.append({
                "id": re.sub(r"\*", "", cells[cols["id"]]).strip(),
                "state": state,
                "registered": _parse_date(cells[cols["reg"]]) if cols["reg"] is not None and cols["reg"] < len(cells) else None,
                "updated": _parse_date(cells[cols["upd"]]) if cols["upd"] is not None and cols["upd"] < len(cells) else None,
                "source": cells[cols["src"]] if cols["src"] is not None and cols["src"] < len(cells) else "",
            })
    return rows


def proposal_kpi(rows, today=None):
    """ハーネス自己改善 KPI: 状態別件数・未適用(open/partial)の最古滞留日数・監査→適用の中央リードタイム。
    リードタイム = 状態更新日 − 登録日(applied 行)。日付列が無い行は集計から外し、件数だけ報告する。"""
    today = today or datetime.now().date()
    counts = {s: 0 for s in PROPOSAL_STATES}
    counts["unknown"] = 0
    pending = []
    lead_applied = []
    lead_partial = []
    undated = 0
    for r in rows:
        counts[r["state"] or "unknown"] += 1
        if r["registered"] is None:
            undated += 1
            continue
        if r["state"] in ("open", "partial"):
            pending.append(((today - r["registered"]).days, r["id"], r["state"]))
        if r["state"] == "applied" and r["updated"] is not None:
            lead_applied.append(max(0, (r["updated"] - r["registered"]).days))
        if r["state"] == "partial" and r["updated"] is not None:
            lead_partial.append(max(0, (r["updated"] - r["registered"]).days))
    pending.sort(reverse=True)
    return {
        "total": len(rows), "counts": counts, "undated": undated,
        "oldest_pending": pending[:5],
        "oldest_pending_days": pending[0][0] if pending else None,
        "pending_median_days": percentile([p[0] for p in pending], 0.5) if pending else None,
        "lead_applied_n": len(lead_applied),
        "lead_applied_median": percentile(lead_applied, 0.5) if lead_applied else None,
        "lead_applied_max": max(lead_applied) if lead_applied else None,
        "lead_partial_n": len(lead_partial),
        "lead_partial_median": percentile(lead_partial, 0.5) if lead_partial else None,
        "today": today.isoformat(),
    }


def _days(v):
    """日数表示(整数なら小数を出さない)。"""
    return str(int(v)) if isinstance(v, (int, float)) and float(v).is_integer() else str(v)


def hook_kpi_line(root):
    """フック所要の 1 行(R-09 / A2-7): 集計の正は tools/hook-metrics.py(importlib で読み込み、無ければ None)。"""
    import importlib.util
    path = os.path.join(root, "tools", "hook-metrics.py")
    if not os.path.exists(path):
        path = os.path.join(ROOT, "tools", "hook-metrics.py")
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("harness_hook_metrics", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        m, warns, slo, err = mod.run(root, os.path.join(root, ".github", "hooks", "logs"))
        return None if err else mod.kpi_line(m, warns, slo)
    except Exception:  # noqa: BLE001
        return None


def kpi_lines(k, hook_line=None):
    """KPI の Markdown 節(effort-report.md の末尾と --kpi の標準出力で共用)。hook_line はフック所要の 1 行(hook-metrics.py)。"""
    c = k["counts"]
    lines = ["## ハーネス自己改善 KPI（audits/PROPOSALS.md。監査→適用のリードタイム。OP-4）", "",
             f"- 基準日 {k['today']}・提案 {k['total']} 件（applied {c['applied']} / partial {c['partial']} / deferred {c['deferred']} / "
             f"rejected {c['rejected']} / open {c['open']}"
             + (f" / 状態不明 {c['unknown']}" if c["unknown"] else "") + "）"
             + (f"。登録日の無い行 {k['undated']} 件は滞留・リードタイムの集計外" if k["undated"] else "")]
    if k["oldest_pending_days"] is None:
        lines.append("- 未適用（open / partial）の滞留: 該当なし")
    else:
        lines.append(f"- 未適用（open / partial）の最古滞留: **{_days(k['oldest_pending_days'])} 日**"
                     f"（中央値 {_days(k['pending_median_days'])} 日）。最古 5 件: "
                     + ", ".join(f"{pid}({st} {d}日)" for d, pid, st in k["oldest_pending"]))
    if k["lead_applied_median"] is None:
        lines.append("- 監査→適用（applied）のリードタイム: 日付列のある applied 行なし")
    else:
        lines.append(f"- 監査→適用（applied）のリードタイム: 中央値 **{_days(k['lead_applied_median'])} 日**・最大 {_days(k['lead_applied_max'])} 日"
                     f"（n={k['lead_applied_n']}。登録日→状態更新日）")
    if k["lead_partial_median"] is not None:
        lines.append(f"- 監査→一部適用（partial）のリードタイム: 中央値 {_days(k['lead_partial_median'])} 日（n={k['lead_partial_n']}。CI 緑 run URL 待ちを含む）")
    lines.append("- " + (hook_line or "フック所要: 未計測（判定ログ hook-decisions.jsonl が無いか、読み手 tools/hook-metrics.py が無い）")
                 + "（正は `python tools/hook-metrics.py`。所要はスクリプト内の経過でプロセス起動分を含まない。R-09 / A2-7）")
    lines.append("- 日付の正は台帳の 登録日 / 状態更新日 列（初期値は出典日と git blame で機械補完。以後は /90 が状態変更のたびに更新）。"
                 "logs/usage は不要で、台帳だけから出る。")
    lines.append("")
    return lines


def load_config(root):
    cfg = {"context_warn_pct": 40, "cache_read_ratio_warn": 0.95, "baseline_min_n": 5, "rotate_days": 90}
    data = _load_json(os.path.join(root, "tools", "usage-config.json"), {})
    if isinstance(data, dict):
        cfg.update({k: v for k, v in data.items() if k in cfg})
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=ROOT, help="プロジェクトルート(既定: このファイルの親の親)")
    ap.add_argument("--logs", default=None, help="受領書ディレクトリ(既定: <root>/.github/hooks/logs/usage)")
    ap.add_argument("--log", default=None, help="旧 effort-log.csv を移行せずその場で合算する")
    ap.add_argument("--migrate", metavar="CSV", default=None, help="旧 effort-log.csv を受領書へ取り込む")
    ap.add_argument("--out", default=None, help="レポート出力先(既定: docs/06-retrospective/effort-report.md)")
    ap.add_argument("--snapshot", action="store_true", help="月次スナップショットを更新し、消えたセッションを合算する")
    ap.add_argument("--rotate", action="store_true", help="rotate_days 超の受領書を削除する(--snapshot 推奨)")
    ap.add_argument("--no-anonymize", action="store_true", help="session_id/日付を出す")
    ap.add_argument("--summary", action="store_true", help="標準出力に要約だけ(ファイルは書かない)")
    ap.add_argument("--kpi", action="store_true",
                    help="ハーネス自己改善 KPI(audits/PROPOSALS.md の滞留・リードタイム)だけを標準出力に出す(logs/usage 不要)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.selftest:
        return selftest()

    root = os.path.abspath(args.root)
    proposals_path = os.path.join(root, PROPOSALS_REL)
    kpi = proposal_kpi(load_proposals(proposals_path)) if os.path.exists(proposals_path) else None
    hook_line = hook_kpi_line(root)
    if args.kpi:
        if kpi is None:
            print(f"台帳がありません: {proposals_path}(ハーネス本体リポジトリ専用)")
            return 1
        print("\n".join(kpi_lines(kpi, hook_line)))
        return 0
    logs = args.logs or os.path.join(root, LOGS_REL)
    prices = load_prices(root)
    cfg = load_config(root)
    if args.migrate:
        if not os.path.exists(args.migrate):
            print(f"CSV がありません: {args.migrate}")
            return 1
        written, skipped = migrate_csv(args.migrate, logs, prices["as_of"])
        print(f"migrate: {written} セッションを受領書へ取り込み、{skipped} セッションは既存のためスキップ → {logs}")
    sessions = load_sessions(logs)
    if args.log:
        if not os.path.exists(args.log):
            print(f"CSV がありません: {args.log}")
            return 1
        for sid, s in sessions_from_csv(load_rows(args.log)).items():
            sessions.setdefault(sid, s)
    snapshot_note = None
    if args.snapshot:
        snap = update_snapshot(os.path.join(root, SNAPSHOT_REL), sessions)
        before = len(sessions)
        sessions = merge_snapshot_sessions(sessions, snap)
        snapshot_note = f"月次スナップショット `docs/06-retrospective/effort-snapshots.json` を更新（logs に無い過去分 {len(sessions) - before} セッションを合算）"
    if not sessions:
        print(f"受領書がありません: {logs}")
        print("(Claude Code の Stop/SessionEnd フック log-effort.py が docs/00-overview/progress.md のあるプロジェクトで自動記録します。"
              "旧 effort-log.csv は --migrate で取り込めます)")
        return 1
    baselines = compute_baselines(sessions, int(cfg.get("baseline_min_n") or 5))
    if args.summary:
        print(summary_text(sessions, baselines))
        return 0
    out = args.out or os.path.join(root, OUT_REL)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_report(sessions, baselines, cfg, prices, anonymize=not args.no_anonymize, snapshot_note=snapshot_note, kpi=kpi,
                             hook_line=hook_line))
    bl_path = os.path.join(root, BASELINES_REL)
    with open(bl_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(baselines, f, ensure_ascii=False, indent=1)
    if args.rotate:
        removed = rotate(logs, int(cfg.get("rotate_days") or 90))
        print(f"rotate: {removed} ファイルを削除({cfg.get('rotate_days')} 日超)")
    print(f"生成: {out}")
    print(f"基準線: {bl_path}")
    print(summary_text(sessions, baselines))
    return 0


# ---------------------------------------------------------------- selftest

def selftest():
    import shutil
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    tmp = tempfile.mkdtemp(prefix="effortreport-selftest-")
    try:
        root = os.path.join(tmp, "root")
        os.makedirs(os.path.join(root, "tools"))
        for name in ("prices.json", "usage-config.json"):
            shutil.copyfile(os.path.join(ROOT, "tools", name), os.path.join(root, "tools", name))
        prices = load_prices(root)
        # 全行の単価解決(生 ID・日付サフィックス・[1m]・family fallback)
        ids = ["claude-fable-5-1", "claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5",
               "claude-sonnet-4-6", "claude-opus-4-8", "claude-sonnet-5-20261001", "claude-opus-5[1m]", "claude-fable-5-1[1m]"]
        check("prices: 代表 ID が全て解決", all(price_of(i) is not None for i in ids), str([i for i in ids if price_of(i) is None]))
        check("prices: fable-5-1 の cache_read は input の 0.025x、fable-5 は 0.1x",
              price_of("claude-fable-5-1")["cache_read"] == 0.25 and price_of("claude-fable-5")["cache_read"] == 1.0)
        check("prices: 5-1 を 5 より先に解決(最長前方一致)", price_of("claude-fable-5-1[1m]")["id"] == "claude-fable-5-1")
        check("prices: sonnet-5 は 2/10、legacy sonnet-4-6 は 3/15", price_of("claude-sonnet-5")["input"] == 2.0
              and price_of("claude-sonnet-4-6")["output"] == 15.0)
        check("prices: family fallback(claude-opus-9)と未登録(gpt)の区別", (price_of("claude-opus-9") or {}).get("family") is True
              and price_of("gpt-5") is None)
        # 監査 RD-1 の再計算: 同一トークン量で fable-5 → fable-5-1 は cache read のみ 0.25x
        r = {"model": "claude-fable-5", "input": 1046, "output": 595686, "cache_read": 62168531, "cache_w5m": 1707843, "cache_w1h": 0}
        c5, _ = row_cost(r)
        c51, _ = row_cost(dict(r, model="claude-fable-5-1"))
        check("row_cost: ChronoLines task-worker 行 fable-5 ≈ $113.31、5-1 なら cache read 分が 1/4",
              abs(c5 - 113.31) < 0.05 and abs((c5 - c51) - 62168531 * 0.75 / 1e6) < 0.01, f"{c5:.2f} {c51:.2f}")

        # --migrate: 旧 CSV(D040 形式)を一時ディレクトリで取り込む
        logs = os.path.join(root, LOGS_REL)
        csv_path = os.path.join(tmp, "effort-log.csv")
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            f.write("session_id,date,platform,phase,agent,model,input,output,cache_read,cache_w5m,cache_w1h,updated_at\n"
                    "aaaa1111-0000,2026-08-12,claude-code,01-requirements-intake,main,claude-fable-5,60,55624,3104030,0,111865,2026-08-12T11:27:45Z\n"
                    "aaaa1111-0000,2026-08-12,claude-code,01-requirements-intake,spec-critic,claude-fable-5,12,15647,158920,46897,0,2026-08-12T11:27:45Z\n"
                    "bbbb2222-0000,2026-08-17,claude-code,06-implement-task,main,claude-fable-5,70,36281,3759010,0,112136,2026-08-18T13:48:52Z\n"
                    "bbbb2222-0000,2026-08-17,claude-code,06-implement-task,task-worker,claude-fable-5,1046,595686,62168531,1707843,0,2026-08-18T13:48:52Z\n"
                    "cccc3333-0000,2026-08-18,claude-code,06-implement-task,main,claude-fable-5,48,24624,2294326,0,92875,2026-08-18T16:23:51Z\n")
        written, skipped = migrate_csv(csv_path, logs, prices["as_of"])
        check("migrate: 3 セッションを受領書へ", written == 3 and skipped == 0 and len(glob.glob(os.path.join(logs, "*.receipt.json"))) == 3)
        written2, skipped2 = migrate_csv(csv_path, logs, prices["as_of"])
        check("migrate: 再実行は既存をスキップ(冪等)", written2 == 0 and skipped2 == 3)
        mig = _load_json(os.path.join(logs, "bbbb2222-0000.receipt.json"))
        check("migrate: 受領書スキーマ・migrated フラグ・spawns", mig["schema"] == "receipt/1" and "migrated" in mig["flags"]
              and mig["outcome_harness"]["spawns"] == {"task-worker": 1} and abs(mig["cost"]["amount"] - 121.13) < 0.05, str(mig["cost"]))

        # 受領書(確定 2 本 + 新しい draft 1 本)を追加
        def receipt(sid, phase, cost_tokens, started, extra_flags=(), transitions=(), tasks_delta=None, used=None, review=0):
            return {"schema": "receipt/1",
                    "meta": {"session_id": sid, "cwd": "/p", "platform": "claude-code", "started": started, "captured_at": started,
                             "capture": "SessionEnd(確定)", "host": {"product": "claude-code", "version": "2.1.234", "model_main": "claude-fable-5",
                                                                     "effort": "high"}, "phase": phase, "coverage": {"ratio": 1.0}},
                    "tokens": [{"agent": "main", "model": "claude-fable-5", "query_source": "main", "spawn_depth": 0,
                                "input": 100, "output": 1000, "cache_read": cost_tokens, "cache_w5m": 0, "cache_w1h": 0}],
                    "cost": {"amount": 0, "host_amount": None, "cache_read_cost_share": None},
                    "context": {"used_percentage": used},
                    "outcome_common": {"num_turns": 20, "user_prompts": 3, "tests": {"result": "unknown"}},
                    "outcome_harness": {"gate_transitions": list(transitions), "tasks_done_delta": tasks_delta,
                                        "spawns": {"reviewer": review}, "review_less_done": review == 0 and bool(transitions)},
                    "flags": list(extra_flags)}
        for i, (sid, tok, started, tr, td, used, rev) in enumerate([
            ("dddd4444", 20_000_000, "2026-09-01T01:00:00Z", ["implementation:in_progress->done"], 4, 30, 1),
            ("eeee5555", 90_000_000, "2026-09-03T01:00:00Z", ["test:in_progress->done"], 2, 65, 0),
        ]):
            with open(os.path.join(logs, sid + ".receipt.json"), "w", encoding="utf-8") as f:
                json.dump(receipt(sid, "06-implement-task", tok, started, transitions=tr, tasks_delta=td, used=used, review=rev), f)
        with open(os.path.join(logs, "ffff6666.receipt.draft.json"), "w", encoding="utf-8") as f:
            json.dump(receipt("ffff6666", "03-design-architecture", 5_000_000, "2026-09-09T01:00:00Z"), f)
        sessions = load_sessions(logs)
        check("load_sessions: 確定 5 + 新しい draft 1(未 settled)", len(sessions) == 6 and sessions["ffff6666"]["settled"] is False
              and sum(1 for s in sessions.values() if s["settled"]) == 5, str({k: v["settled"] for k, v in sessions.items()}))
        cfg = load_config(root)
        bl = compute_baselines(sessions, cfg["baseline_min_n"])
        check("baselines: 06 は n=4(暫定)、draft は基準線に含めない", bl["phases"]["06-implement-task"]["n"] == 4
              and bl["phases"]["06-implement-task"]["provisional"] is True and "03-design-architecture" not in bl["phases"], str(bl["phases"].keys()))
        check("baselines: 成果あたり費用(タスク [x] 6 件で割る)", bl["phases"]["06-implement-task"]["cost_per_task_done"] is not None
              and bl["phases"]["06-implement-task"]["cost_per_transition"] is not None, str(bl["phases"]["06-implement-task"]))
        report = build_report(sessions, bl, cfg, prices, anonymize=True)
        check("report: 匿名化(session_id が出ない・ISO 週)", "bbbb2222" not in report and "dddd4444" not in report and "2026-W" in report)
        check("report: 逸脱一覧に文脈超過と review_less_done", "文脈 65%" in report and "review_less_done" in report, report[-1500:])
        check("report: モデル×effort 表と分布表", "モデル × effort" in report and "セッション × 工程の分布" in report)
        report2 = build_report(sessions, bl, cfg, prices, anonymize=False)
        check("report: --no-anonymize で session_id を出す", "bbbb2222" in report2)
        # スナップショット: 1 本消しても前回分から合算される
        snap_path = os.path.join(root, SNAPSHOT_REL)
        snap = update_snapshot(snap_path, sessions)
        check("snapshot: 月次集計と匿名レコード(ハッシュのみ)", "2026-08" in snap["months"] and snap["months"]["2026-08"]["sessions"] == 3
              and all(len(r["h"]) == 12 and "sid" not in r for r in snap["sessions"]))
        os.remove(os.path.join(logs, "aaaa1111-0000.receipt.json"))
        merged = merge_snapshot_sessions(load_sessions(logs), _load_json(snap_path))
        check("snapshot: logs から消えたセッションを合算", len(merged) == 6 and any(s["source"] == "snapshot" for s in merged.values()))
        # main() 経由: --summary と出力ファイル
        out_md = os.path.join(root, OUT_REL)
        rc = main(["--root", root])
        check("main: レポートと baselines.json を生成", rc == 0 and os.path.exists(out_md) and os.path.exists(os.path.join(root, BASELINES_REL)))
        rc2 = main(["--root", root, "--log", csv_path, "--summary"])
        check("main: --log で旧 CSV をその場で合算(--summary)", rc2 == 0)
        empty_root = os.path.join(tmp, "empty")
        os.makedirs(os.path.join(empty_root, "tools"))
        for name in ("prices.json", "usage-config.json"):
            shutil.copyfile(os.path.join(ROOT, "tools", name), os.path.join(empty_root, "tools", name))
        check("main: 受領書ゼロは 1 を返す", main(["--root", empty_root]) == 1)
        check("legacy: load_rows/group/table の出力形式維持", "| 合計 |" in "\n".join(table("合計", group(load_rows(csv_path), lambda r: "合計"))))
        # ハーネス自己改善 KPI(OP-4): 台帳フィクスチャ(登録日 / 状態更新日 列あり)
        os.makedirs(os.path.join(root, "audits"), exist_ok=True)
        with open(os.path.join(root, PROPOSALS_REL), "w", encoding="utf-8") as f:
            f.write("# PROPOSALS\n\n| ID | 出典 | 提案（1行） | 状態 | 登録日 | 状態更新日 | 対応D番号/理由 |\n|---|---|---|---|---|---|---|\n"
                    "| A1-1 | 監査 2026-08-01 | a | applied | 2026-08-01 | 2026-08-11 | D001 |\n"
                    "| A1-2 | 監査 2026-08-01 | b | applied | 2026-08-01 | 2026-08-05 | D002 |\n"
                    "| A1-3 | 監査 2026-08-01 | c | open | 2026-08-01 | 2026-08-01 | — |\n"
                    "| A1-4 | 監査 2026-08-20 | d | partial | 2026-08-20 | 2026-09-01 | D003 |\n"
                    "| A1-5 | 監査 2026-08-25 | e | rejected | 2026-08-25 | 2026-08-26 | 理由 |\n"
                    "| A1-6 | 監査 2026-08-30 | f | open | | | — |\n")
        prows = load_proposals(os.path.join(root, PROPOSALS_REL))
        k = proposal_kpi(prows, today=datetime(2026, 9, 10).date())
        check("kpi: 状態別件数と登録日なし行の除外", k["total"] == 6 and k["counts"]["applied"] == 2 and k["counts"]["open"] == 2
              and k["undated"] == 1, str(k["counts"]))
        check("kpi: 未適用の最古滞留 40 日(A1-3)・partial 21 日", k["oldest_pending_days"] == 40 and k["oldest_pending"][0][1] == "A1-3"
              and k["oldest_pending"][1] == (21, "A1-4", "partial"), str(k["oldest_pending"]))
        check("kpi: applied リードタイム中央値 7 日・最大 10 日、partial 12 日",
              k["lead_applied_median"] == 7 and k["lead_applied_max"] == 10 and k["lead_partial_median"] == 12, str(k))
        kl = "\n".join(kpi_lines(k))
        check("kpi: Markdown 節に最古滞留と中央値", "最古滞留: **40 日**" in kl and "中央値 **7 日**" in kl, kl)
        kl_h = "\n".join(kpi_lines(k, "フック所要: P50 1 ms / P95 2 ms（n=3、host claude-code 3）"))
        check("kpi: フック所要の 1 行(hook-metrics.py の kpi_line)を KPI 節に載せ、無ければ未計測と書く",
              "- フック所要: P50 1 ms / P95 2 ms" in kl_h and "フック所要: 未計測" in kl, kl_h)
        hl = hook_kpi_line(root)
        check("kpi: hook_kpi_line は判定ログの無い root でも未計測の 1 行を返す(読み手は本体の hook-metrics.py)",
              isinstance(hl, str) and "フック所要" in hl and "未計測" in hl, str(hl))
        with open(os.path.join(root, PROPOSALS_REL), "w", encoding="utf-8") as f:
            f.write("| ID | 出典 | 提案（1行） | 状態 | 対応D番号/理由 |\n|---|---|---|---|---|\n| X-1 | 監査 2026-08-01 | a | applied | D001 |\n")
        k2 = proposal_kpi(load_proposals(os.path.join(root, PROPOSALS_REL)), today=datetime(2026, 9, 10).date())
        check("kpi: 旧書式(日付列なし)は件数のみで滞留・リードタイムを出さない", k2["total"] == 1 and k2["undated"] == 1
              and k2["oldest_pending_days"] is None and k2["lead_applied_median"] is None)
        check("main: --kpi は台帳だけで動く(受領書不要)", main(["--root", root, "--kpi"]) == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

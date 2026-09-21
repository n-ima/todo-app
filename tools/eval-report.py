#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""評価結果の backfill・妥当性検査・REPORT.md 生成(評価装置 v2。監査 2026-09-09 §6 / A6-16)。

evaluation/results/*.json(装置 v1 = schema_version 1、装置 v2 = schema_version 2)を読み、

  --scan-trajectories --zip-dir DIR
      退避済み transcript zip(evaluation/results/trajectories/*.zip。gitignore)を走査して、
      MANIFEST.json の各エントリに models_observed(transcript 中の "model" 値の件数)・
      sessions・result_json(encoded cwd で結合)を書き足す。zip が手元にある機械でだけ実行する。
  --backfill [--dry-run]
      schema_version 1 の結果に verdict / valid_for_comparison / invalid_reasons /
      recovered_model(MANIFEST の models_observed から) / trajectory(zip 名と sha256)/
      conditions(v1 の budgets から導出)/ totals.n_truncated_calls(推定)を付与する。
      既存キーは書き換えない(追記のみ)。冪等(再実行しても同じ内容)。
  --validate [--strict]
      系列ごとに条件の単一性(condition_hash)・アーム間の予算同額・事前登録ファイルの
      sha256 と登録日時(系列開始より前)・auth_mode の一貫性を検査し、走行ごとの
      verdict / valid / invalid_reasons を表にする。--strict は INVALID があれば exit 1。
  --report [--check] [--out PATH]
      evaluation/REPORT.md を生成する(生成物・手編集禁止)。Fisher 両側と Wilson 95% は
      自前実装(依存ゼロ)。費用は範囲(min / median / max)で書き、比は作らない。
      --check は再生成して差分が無いことを exit code で返す(CI 用)。
      results/manual/*.json(手動 E2E=Copilot 経路。schema manual-e2e/1。テンプレは evaluation/manual/)も読み、
      「手動 E2E」節に集計する(A/B 比較・統計には使わない。A5-8)。
  --selftest
      統計(既知値)・backfill 規則・妥当性検査・レポート生成の自己テスト。

終了コード: 0 = 正常  1 = --validate --strict で INVALID あり / --report --check で差分 / selftest 失敗
            2 = 実行エラー
依存: Python 3.x 標準ライブラリのみ(CI に PyYAML を要求しない)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = HARNESS_ROOT / "evaluation"
RESULTS_DIR = EVAL_DIR / "results"
TRAJ_DIR = RESULTS_DIR / "trajectories"
MANIFEST = TRAJ_DIR / "MANIFEST.json"
EXPERIMENTS_DIR = EVAL_DIR / "experiments"
REPORT_PATH = EVAL_DIR / "REPORT.md"
# 手動 E2E(Copilot 経路。.github/harness/COPILOT-E2E.md のチェックリスト結果)の機械可読記録(A5-8)。
# 記入テンプレは evaluation/manual/copilot-e2e_template.json、記録は results/manual/*.json。A/B 比較・統計には使わない
MANUAL_DIR = RESULTS_DIR / "manual"
MANUAL_SCHEMA = "manual-e2e/1"
MANUAL_ITEM_RESULTS = ("OK", "NG", "PARTIAL", "NA")
MANUAL_VERDICTS = ("PASS", "PARTIAL", "FAIL", "INCOMPLETE")

BACKFILL_RULE_VERSION = 1
LEGACY_SERIES_ID = "legacy-v1-2026-08"
LEGACY_CLI_MIN_BUDGET = "2.1.217"
LEGACY_CLI_MIN_MODEL = "2.1.251"
QUOTA_MARKERS = ("out of usage credits", "usage limit", "session limit", "rate limit",
                 "credit balance", "insufficient credits")
TRUNCATION_COST_RATIO = 0.98   # v1 の call は subtype を持たないため cost ≥ 0.98×call 上限で打切りと推定
CAPABILITY_BUDGET_RATIO_MAX = 0.7
Z95 = 1.959963984540054


def say(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 2) -> "None":
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def encode_cwd(path: str) -> str:
    """Claude Code の projects/ ディレクトリ名(英数字以外を '-' に置換。実測: '_' も '-')。"""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


# ---------------------------------------------------------------------------
# 統計(自前実装)
# ---------------------------------------------------------------------------

def hypergeom_pmf(k: int, r1: int, r2: int, c1: int) -> float:
    """行和 r1, r2・列和 c1 の 2x2 表で左上が k になる確率。"""
    n = r1 + r2
    if k < 0 or k > r1 or c1 - k < 0 or c1 - k > r2:
        return 0.0
    return math.comb(r1, k) * math.comb(r2, c1 - k) / math.comb(n, c1)


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2x2 表 [[a, b], [c, d]] の Fisher 正確検定(両側。観測確率以下の表の確率和)。"""
    r1, r2, c1 = a + b, c + d, a + c
    p_obs = hypergeom_pmf(a, r1, r2, c1)
    total = 0.0
    for k in range(0, min(r1, c1) + 1):
        p = hypergeom_pmf(k, r1, r2, c1)
        if p <= p_obs * (1 + 1e-9):
            total += p
    return min(1.0, total)


def wilson_interval(k: int, n: int, z: float = Z95) -> tuple:
    """Wilson スコア区間(95%)。n=0 は (0, 1)。"""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def cost_range(values: list) -> str:
    """費用の範囲表記(min / median / max)。比は作らない。"""
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    if not vals:
        return "—"
    if len(vals) == 1:
        return f"${vals[0]:.2f} (n=1)"
    return f"${min(vals):.2f} / ${statistics.median(vals):.2f} / ${max(vals):.2f}"


def median_or_dash(values: list, fmt: str = "{:.0f}") -> str:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    return fmt.format(statistics.median(vals)) if vals else "—"


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------

def load_results(results_dir: Path = RESULTS_DIR) -> list:
    out = []
    for p in sorted(results_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            say(f"WARN: {p.name}: JSON として読めない({e})")
            continue
        d["_path"] = p
        out.append(d)
    return out


def load_manifest(path: Path = MANIFEST) -> dict:
    if not path.is_file():
        return {"entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(data: dict, path: Path = MANIFEST) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_manual_results(manual_dir: Path = MANUAL_DIR) -> list:
    """results/manual/*.json(schema manual-e2e/1)を読む。壊れた JSON・schema 違いは _problems に理由を持たせて
    レポートに「読めない記録」として出す(黙って落とさない)。"""
    out = []
    if not manual_dir.is_dir():
        return out
    for p in sorted(manual_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:  # noqa: BLE001
            out.append({"_path": p, "_problems": [f"JSON として読めない({e})"], "items": {}})
            continue
        if not isinstance(d, dict):
            d = {"_problems": ["トップレベルがオブジェクトではない"], "items": {}}
        d["_path"] = p
        d["_problems"] = manual_problems(d)
        out.append(d)
    return out


def manual_problems(d: dict) -> list:
    problems = []
    if d.get("schema") != MANUAL_SCHEMA:
        problems.append(f"schema は {MANUAL_SCHEMA}(実際: {d.get('schema')!r})")
    for key in ("route", "date", "items", "verdict"):
        if not d.get(key):
            problems.append(f"{key} が無い")
    if d.get("verdict") not in (None, "") and d.get("verdict") not in MANUAL_VERDICTS:
        problems.append(f"verdict は {MANUAL_VERDICTS}")
    items = d.get("items") or {}
    if not isinstance(items, dict):
        problems.append("items はオブジェクト({項目ID: {result, note}})")
    else:
        for k, v in items.items():
            r = (v or {}).get("result") if isinstance(v, dict) else v
            if r not in MANUAL_ITEM_RESULTS:
                problems.append(f"items.{k}.result は {MANUAL_ITEM_RESULTS}(実際: {r!r})")
    counts = manual_counts(d)
    if d.get("verdict") == "PASS" and (counts["NG"] or counts["PARTIAL"]):
        problems.append("verdict PASS なのに NG / PARTIAL の項目がある")
    return problems


def manual_counts(d: dict) -> dict:
    counts = {k: 0 for k in MANUAL_ITEM_RESULTS}
    items = d.get("items") or {}
    if isinstance(items, dict):
        for v in items.values():
            r = (v or {}).get("result") if isinstance(v, dict) else v
            if r in counts:
                counts[r] += 1
    return counts


def manual_section(manual: list) -> list:
    L = ["## 手動 E2E(Copilot 経路。.github/harness/COPILOT-E2E.md のチェックリスト結果。A/B 比較・統計には使わない。A5-8)", ""]
    if not manual:
        L.append("(記録なし。記入テンプレは evaluation/manual/copilot-e2e_template.json、置き場は evaluation/results/manual/)")
        L.append("")
        return L
    L.append("| 記録 | 日付 | 経路 | ハーネス種別 | ホスト(VS Code / Copilot Chat) | commit | OK | NG | PARTIAL | NA | verdict | 備考 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for d in manual:
        c = manual_counts(d)
        host = d.get("host") or {}
        note = "; ".join(d.get("_problems") or []) or (d.get("notes") or "—")
        L.append(f"| {d['_path'].name} | {_cell(d.get('date') or '—')} | {_cell(d.get('route') or '—')} | "
                 f"{_cell(host.get('harness_kind') or '—')} | {_cell((host.get('vscode') or '—') + ' / ' + (host.get('copilot_chat') or '—'))} | "
                 f"{_cell(d.get('harness_commit') or '—')} | {c['OK']} | {c['NG']} | {c['PARTIAL']} | {c['NA']} | "
                 f"{_cell(d.get('verdict') or '(読めない記録)')} | {_cell(note[:160])} |")
    L.append("")
    for d in manual:
        if d.get("_problems"):
            L.append(f"- {d['_path'].name}: 記録の不備 — " + "; ".join(d["_problems"]))
    if any(d.get("_problems") for d in manual):
        L.append("")
    return L


def schema_of(d: dict) -> int:
    return int(d.get("schema_version") or 1)


def series_of(d: dict) -> str:
    return (d.get("experiment") or {}).get("series_id") or LEGACY_SERIES_ID


def arm_name(d: dict) -> str:
    arm = d.get("arm")
    return arm.get("name", "?") if isinstance(arm, dict) else str(arm)


def task_name(d: dict) -> str:
    task = d.get("task")
    return task.get("name", "?") if isinstance(task, dict) else str(task)


# ---------------------------------------------------------------------------
# --scan-trajectories: zip から model 名を数えて MANIFEST に写す
# ---------------------------------------------------------------------------

def scan_zip(zip_path: Path) -> dict:
    models: dict = {}
    sessions = set()
    files = 0
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            files += 1
            if not name.endswith(".jsonl"):
                continue
            data = zf.read(name).decode("utf-8", "replace")
            for m in re.finditer(r'"model":\s*"([^"]+)"', data):
                models[m.group(1)] = models.get(m.group(1), 0) + 1
            for m in re.finditer(r'"sessionId":\s*"([0-9a-f-]{36})"', data):
                sessions.add(m.group(1))
    return {"models_observed": dict(sorted(models.items(), key=lambda kv: -kv[1])),
            "sessions": sorted(sessions), "files": files}


def scan_trajectories(zip_dir: Path, results: list, manifest: dict) -> int:
    by_encoded = {}
    for d in results:
        wd = d.get("workdir")
        if wd:
            by_encoded[encode_cwd(str(wd))] = d["_path"].name
    updated = 0
    for e in manifest.get("entries", []):
        zp = zip_dir / e["zip"]
        if not zp.is_file():
            say(f"SKIP: zip が無い: {zp}")
            continue
        info = scan_zip(zp)
        e["models_observed"] = info["models_observed"]
        e["sessions"] = info["sessions"]
        if e.get("sha256") and e["sha256"] != sha256_file(zp):
            say(f"WARN: {e['zip']}: sha256 が MANIFEST と一致しない(zip が差し替わっている)")
        e["result_json"] = by_encoded.get(e["source_dir"], e.get("result_json"))
        updated += 1
        say(f"scanned: {e['zip']} models={info['models_observed']} sessions={len(info['sessions'])} "
            f"result={e['result_json']}")
    manifest["scan"] = {"tool": "tools/eval-report.py --scan-trajectories",
                        "rule": 'count of "model" values in *.jsonl (main + subagents)',
                        "scanned_entries": updated}
    return updated


# ---------------------------------------------------------------------------
# --backfill: schema_version 1 の結果に verdict 等を付与
# ---------------------------------------------------------------------------

def infer_call_class_v1(call: dict, call_budget: float) -> str:
    text = str(call.get("result_tail") or "").lower()
    cost = call.get("total_cost_usd")
    if call.get("timeout"):
        return "timeout"
    if call.get("parse_error"):
        return "parse_error"
    if call.get("is_error") and any(m in text for m in QUOTA_MARKERS):
        return "quota"
    if (call.get("is_error") and isinstance(cost, (int, float)) and call_budget
            and cost >= TRUNCATION_COST_RATIO * call_budget and not text.strip()):
        return "budget_call"
    if call.get("is_error"):
        return "execution_error"
    return "none"


def legacy_verdict(d: dict, classes: list) -> str:
    totals = d.get("totals") or {}
    budgets = d.get("budgets") or {}
    total_budget = float(budgets.get("total_budget_usd") or 0)
    cost = float(totals.get("cost_usd") or 0)
    if totals.get("aborted_by_quota") or "quota" in classes:
        return "DNF_QUOTA"
    if "timeout" in classes:
        return "DNF_TIMEOUT"
    if any(k in ("execution_error", "parse_error") for k in classes):
        return "DNF_ERROR"
    if totals.get("aborted_by_budget") or (total_budget and cost > total_budget):
        return "DNF_BUDGET_TOTAL"
    return "PASS" if (d.get("check") or {}).get("pass") else "FAIL"


def budget_pair(d: dict) -> tuple:
    b = d.get("budgets") or {}
    return (float(b.get("session_budget_usd") or 0), float(b.get("total_budget_usd") or 0))


def backfill_legacy(d: dict, manifest: dict, peers: list, today: str) -> dict:
    """schema_version 1 の結果 dict に追記する(既存キーは触らない)。peers = 同一タスクの v1 結果。"""
    if schema_of(d) >= 2:
        return d
    budgets = d.get("budgets") or {}
    call_budget = float(budgets.get("session_budget_usd") or 0)
    total_budget = float(budgets.get("total_budget_usd") or 0)
    classes = []
    for c in d.get("calls", []):
        k = infer_call_class_v1(c, call_budget)
        c["error_class_inferred"] = k
        c["truncated_by_call_budget_inferred"] = (k == "budget_call")
        classes.append(k)
    totals = d.setdefault("totals", {})
    cost = float(totals.get("cost_usd") or 0)
    max_call = max([float(c.get("total_cost_usd") or 0) for c in d.get("calls", [])] or [0.0])
    totals["n_truncated_calls"] = sum(1 for k in classes if k == "budget_call")
    totals["n_truncated_calls_basis"] = (f"inferred: is_error and cost >= {TRUNCATION_COST_RATIO}x call budget "
                                         f"and empty result (v1 has no subtype)")
    totals["n_error_calls"] = sum(1 for k in classes if k in ("execution_error", "parse_error"))
    totals["budget_used_ratio"] = round(cost / total_budget, 4) if total_budget else None
    totals["max_call_cost_usd"] = round(max_call, 4)
    totals["max_call_cost_ratio"] = round(max_call / call_budget, 4) if call_budget else None
    verdict = legacy_verdict(d, classes)
    d["verdict"] = verdict
    # 無効理由(装置 v1 の構造的欠陥。監査 2026-09-09 EV-1〜EV-3・RG-9・RD-7)
    reasons = [f"cli<{LEGACY_CLI_MIN_BUDGET}", f"cli<{LEGACY_CLI_MIN_MODEL}",
               "user_scope_unisolated", "budget_enforcement=main-only",
               "workdir_lost(temp auto-cleanup 2026-09-04/06; check.py cannot be re-run)",
               "model_unrecorded(recovered from transcript)"]
    my_pair = budget_pair(d)
    other_pairs = {budget_pair(p) for p in peers if arm_name(p) != arm_name(d)}
    if other_pairs and my_pair not in other_pairs:
        others = ", ".join(f"{a:g}/{b:g}" for a, b in sorted(other_pairs))
        reasons.append(f"budget_asymmetric_across_arms(this {my_pair[0]:g}/{my_pair[1]:g} vs other arm {others})")
    if verdict.startswith("DNF") or verdict == "INVALID_APPARATUS":
        reasons.append(f"verdict={verdict}")
    if totals["n_truncated_calls"]:
        reasons.append(f"n_truncated_calls={totals['n_truncated_calls']}(inferred)")
    d["valid_for_comparison"] = False
    d["invalid_reasons"] = reasons
    # transcript との結合(MANIFEST の encoded cwd)
    encoded = encode_cwd(str(d.get("workdir") or ""))
    entry = next((e for e in manifest.get("entries", []) if e.get("source_dir") == encoded), None)
    if entry:
        d["trajectory"] = {"zip": entry.get("zip"), "sha256": entry.get("sha256"),
                           "source_dir": entry.get("source_dir"), "files": entry.get("files"),
                           "bytes": entry.get("bytes"), "zip_bytes": entry.get("zip_bytes"),
                           "latest_mtime": entry.get("latest_mtime"),
                           "manifest": "evaluation/results/trajectories/MANIFEST.json"}
        observed = entry.get("models_observed") or {}
        real = {k: v for k, v in observed.items() if not k.startswith("<")}
        d["recovered_models_observed"] = observed
        d["recovered_model"] = max(real, key=real.get) if real else None
        d["recovered_model_source"] = (f"transcript zip {entry.get('zip')} "
                                       f"(count of \"model\" values; via --scan-trajectories)"
                                       if observed else "transcript zip present but not scanned")
    else:
        d["trajectory"] = {"zip": None, "sha256": None, "source_dir": encoded,
                           "note": "MANIFEST.json に対応エントリ無し"}
        d["recovered_model"] = None
        d["recovered_model_source"] = "transcript not archived"
    # v2 互換の conditions / experiment(集計器が同じ経路で読めるように)
    ver = d.get("claude_version") or "unknown"
    m = re.search(r"\d+\.\d+\.\d+", ver)
    d["conditions"] = {
        "model_requested": None, "effort_requested": None,
        "claude_version": m.group(0) if m else "unknown",
        "min_cli_ok": False, "budget_enforcement": "main-only", "subagent_model_forced": False,
        "setting_sources": "user,project,local (CLI default; user scope unisolated)",
        "config_dir_isolated": False, "permission_mode": "acceptEdits",
        "allowed_tools": ["Bash(git:*)", "Bash(py:*)", "Bash(python:*)"],
        "max_turns_per_call": budgets.get("max_turns_per_call"),
        "call_budget_usd": call_budget, "total_budget_usd": total_budget,
        "fresh_policy": "continue", "auth_mode": "oauth(inferred: usage-credits quota text in same batch)",
        "task_md_sha256": None, "check_py_sha256": None,
    }
    d["experiment"] = {"kind": "legacy", "series_id": LEGACY_SERIES_ID, "run_index": None,
                       "condition_hash": sha256_text(json.dumps(d["conditions"], sort_keys=True))[:12],
                       "plan_path": None, "plan_sha256": None}
    d["schema_version"] = 1
    bf = d.setdefault("backfill", {})
    bf.setdefault("first_applied_on", today)
    bf["rule_version"] = BACKFILL_RULE_VERSION
    bf["tool"] = "tools/eval-report.py --backfill"
    bf["basis"] = ("audits/external-reaudit-2026-09-09.md §6 (EV-1/EV-2/EV-3/RG-9); verdict by "
                   "cost > total_budget (abort-flag independent); truncation inferred from call cost")
    return d


def run_backfill(results_dir: Path = RESULTS_DIR, manifest_path: Path = MANIFEST,
                 dry_run: bool = False, today: "str | None" = None) -> int:
    today = today or time.strftime("%Y-%m-%d")
    manifest = load_manifest(manifest_path)
    results = load_results(results_dir)
    legacy = [d for d in results if schema_of(d) < 2]
    changed = 0
    for d in legacy:
        path = d.pop("_path")
        before = json.dumps({k: v for k, v in d.items()}, ensure_ascii=False, sort_keys=True)
        peers = [p for p in legacy if task_name(p) == task_name(d) and p is not d]
        backfill_legacy(d, manifest, peers, today)
        after = json.dumps(d, ensure_ascii=False, sort_keys=True)
        status = "unchanged" if before == after else "updated"
        say(f"{status}: {path.name} verdict={d['verdict']} n_truncated={d['totals'].get('n_truncated_calls')} "
            f"recovered_model={d.get('recovered_model')}")
        if before != after:
            changed += 1
            if not dry_run:
                path.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    say(f"backfill: {len(legacy)} legacy results, {changed} {'would be ' if dry_run else ''}updated")
    return 0


# ---------------------------------------------------------------------------
# --validate: 系列単位の検査
# ---------------------------------------------------------------------------

def plan_registered_at(plan_path: Path) -> "str | None":
    """事前登録ファイルの登録日時(最終コミット日時。未コミットなら mtime)。"""
    try:
        cp = subprocess.run(["git", "-C", str(HARNESS_ROOT), "log", "-1", "--format=%cI", "--",
                             str(plan_path)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
        if cp.returncode == 0 and cp.stdout.strip():
            return cp.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    if plan_path.is_file():
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(plan_path.stat().st_mtime)) + "(mtime)"
    return None


def validate_series(results: list) -> dict:
    """系列ごとの検査結果 {series_id: {...}}。結果 dict には _series_reasons を書き足す。"""
    groups: dict = {}
    for d in results:
        groups.setdefault(series_of(d), []).append(d)
    report = {}
    for sid, items in sorted(groups.items()):
        reasons = []
        hashes = {(d.get("experiment") or {}).get("condition_hash") for d in items}
        if len(hashes) > 1:
            reasons.append(f"condition_hash が複数({len(hashes)})")
        budgets_by_arm: dict = {}
        for d in items:
            budgets_by_arm.setdefault(arm_name(d), set()).add(
                float((d.get("conditions") or {}).get("total_budget_usd") or 0))
        if len(budgets_by_arm) > 1 and len({tuple(sorted(v)) for v in budgets_by_arm.values()}) > 1:
            reasons.append("アーム間で total_budget_usd が異なる: " + ", ".join(
                f"{a}={sorted(v)}" for a, v in sorted(budgets_by_arm.items())))
        auths = {(d.get("conditions") or {}).get("auth_mode") for d in items}
        if len(auths) > 1:
            reasons.append(f"auth_mode が混在: {sorted(str(a) for a in auths)}")
        plan_paths = {(d.get("experiment") or {}).get("plan_path") for d in items} - {None}
        for pp in sorted(plan_paths):
            path = Path(pp)
            if not path.is_absolute():
                path = HARNESS_ROOT / path
            if not path.is_file():
                reasons.append(f"事前登録ファイルが無い: {pp}")
                continue
            cur = sha256_file(path)
            for d in items:
                rec = (d.get("experiment") or {}).get("plan_sha256")
                if rec and rec != cur:
                    reasons.append(f"plan_hash_mismatch: {d['_path'].name} は {rec[:12]}、現在 {cur[:12]}")
            reg = plan_registered_at(path)
            first = min((d.get("timestamp") or "") for d in items)
            if reg and first and reg[:19] > first[:19]:
                reasons.append(f"plan_modified_after_series_start: 登録 {reg} > 初回 {first}")
        n_valid = sum(1 for d in items if d.get("valid_for_comparison"))
        for d in items:
            d["_series_reasons"] = reasons
        report[sid] = {"n": len(items), "n_valid": n_valid, "reasons": reasons,
                       "arms": sorted({arm_name(d) for d in items}),
                       "tasks": sorted({task_name(d) for d in items}),
                       "kind": (items[0].get("experiment") or {}).get("kind")}
    return report


def print_validation(results: list, report: dict) -> int:
    n_invalid = 0
    for sid, info in report.items():
        say(f"== series {sid} ({info['kind']}; tasks={','.join(info['tasks'])}; arms={','.join(info['arms'])}; "
            f"n={info['n']}, valid={info['n_valid']})")
        for r in info["reasons"]:
            say(f"  SERIES-INVALID: {r}")
        for d in sorted((x for x in results if series_of(x) == sid), key=lambda x: x["_path"].name):
            v = d.get("verdict") or "(no verdict)"
            ok = d.get("valid_for_comparison")
            reasons = list(d.get("invalid_reasons") or []) + list(d.get("_series_reasons") or [])
            if not ok or info["reasons"]:
                n_invalid += 1
            say(f"  {d['_path'].name}: {arm_name(d):8s} verdict={v:17s} valid={ok} "
                f"cost=${float((d.get('totals') or {}).get('cost_usd') or 0):.2f}"
                + (f" reasons={'; '.join(reasons)}" if reasons else ""))
    say(f"validate: {len(results)} results, {n_invalid} not comparable")
    return n_invalid


# ---------------------------------------------------------------------------
# --report: REPORT.md 生成
# ---------------------------------------------------------------------------

def _cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def summarize_cell(items: list) -> dict:
    """1 セル(task × experiment × B × arm)の集計。"""
    n = len(items)
    verdicts = [d.get("verdict") for d in items]
    n_pass = sum(1 for v in verdicts if v == "PASS")
    n_fail = sum(1 for v in verdicts if v == "FAIL")
    dnf = {}
    for v in verdicts:
        if v and v.startswith("DNF"):
            dnf[v] = dnf.get(v, 0) + 1
    lo, hi = wilson_interval(n_pass, n) if n else (0.0, 1.0)
    costs_pass = [(d.get("totals") or {}).get("cost_usd") for d in items if d.get("verdict") == "PASS"]
    costs_other = [(d.get("totals") or {}).get("cost_usd") for d in items if d.get("verdict") != "PASS"]
    total_spend = sum(float((d.get("totals") or {}).get("cost_usd") or 0) for d in items)
    tok = {"input": [], "output": [], "cache_read": [], "cache_creation": []}
    for d in items:
        t = (d.get("totals") or {}).get("tokens") or {}
        for k in tok:
            if isinstance(t.get(k), (int, float)):
                tok[k].append(t[k])
    return {"n": n, "pass": n_pass, "fail": n_fail, "dnf": dnf,
            "pass_rate": (n_pass / n) if n else None, "wilson": (lo, hi),
            "cost_pass": cost_range(costs_pass), "cost_other": cost_range(costs_other),
            "cost_per_pass": (total_spend / n_pass) if n_pass else None,
            "tokens": {k: median_or_dash(v) for k, v in tok.items()},
            "turns": median_or_dash([(d.get("totals") or {}).get("num_turns") for d in items]),
            "duration": median_or_dash([(d.get("totals") or {}).get("duration_s") for d in items]),
            "ratio_max": max([float((d.get("totals") or {}).get("budget_used_ratio") or 0) for d in items] or [0]),
            "n_trunc": sum(int((d.get("totals") or {}).get("n_truncated_calls") or 0) for d in items),
            "artifacts": {
                "files": median_or_dash([(d.get("artifact_stats") or {}).get("files") for d in items]),
                "tests": median_or_dash([(d.get("artifact_stats") or {}).get("test_functions") for d in items]),
                "lines_added": median_or_dash([(d.get("artifact_stats") or {}).get("lines_added") for d in items]),
            }}


def build_report(results: list, series_report: dict, generated_note: str = "", manual: "list | None" = None) -> str:
    v2 = [d for d in results if schema_of(d) >= 2]
    legacy = [d for d in results if schema_of(d) < 2]
    valid = [d for d in v2 if d.get("valid_for_comparison") and not (series_report.get(series_of(d)) or {}).get("reasons")]
    last_ts = max((d.get("timestamp") or "" for d in results), default="")
    L = []
    L.append("# evaluation/REPORT.md — A/B 実測レポート(生成物)")
    L.append("")
    L.append("<!-- GENERATED by tools/eval-report.py --report. 手編集禁止。再生成: python tools/eval-report.py --report -->")
    L.append("")
    L.append(f"- 結果 JSON: {len(results)} 本(装置 v2: {len(v2)} / 装置 v1(参考値): {len(legacy)})、"
             f"最終結果 {last_ts or '—'}。比較可能(valid_for_comparison=true かつ系列条件充足): {len(valid)} 本。")
    if generated_note:
        L.append(f"- {generated_note}")
    L.append("")
    L.append("## 読み方(事前登録の統計規則。evaluation/README.md が正)")
    L.append("")
    L.append("- 主指標は pass@1 率と Wilson 95% 区間、アーム間の差は Fisher 正確検定(両側)。n=5/アームで "
             f"5/5 vs 0/5 は p={fisher_exact_two_sided(5, 0, 0, 5):.4f}、4/5 vs 0/5 は p={fisher_exact_two_sided(4, 1, 0, 5):.4f}、"
             f"3/5 vs 0/5 は p={fisher_exact_two_sided(3, 2, 0, 5):.3f}(差を主張しない)。pass^5 合格の Wilson 下限は "
             f"{wilson_interval(5, 5)[0]:.2f}。分離しなければ n=8 まで延長(8/8 vs 0/8: p={fisher_exact_two_sided(8, 0, 0, 8):.5f})。")
    L.append("- 費用は **範囲(min / median / max)** で書く。アーム間の費用比・PASS 費用÷FAIL 費用は作らない"
             "(cost-per-PASS は系列総支出÷PASS 数のアーム内絶対値のみ)。")
    L.append("- 同一表に載るのは condition_hash が一致する走行だけ。DNF・INVALID_APPARATUS の走行も results/ に残し、"
             "除外理由は invalid_reasons に機械記録する(削除しない)。")
    L.append("")
    L.append("## 比較可能な系列(装置 v2・valid_for_comparison=true)")
    L.append("")
    if not valid:
        L.append("比較可能な走行はまだ無い(0 本)。系列 S1〜S5 の実施順序と前提は evaluation/README.md を参照。")
    else:
        cells: dict = {}
        for d in valid:
            exp = d.get("experiment") or {}
            key = (task_name(d), exp.get("kind"), float((d.get("conditions") or {}).get("total_budget_usd") or 0),
                   series_of(d))
            cells.setdefault(key, {}).setdefault(arm_name(d), []).append(d)
        L.append("| 課題 | 実験 | B(USD) | 系列 | アーム | n | PASS | FAIL | DNF | pass@1 [Wilson95] | Fisher p(vs 対照) | "
                 "cost PASS min/med/max | cost 非PASS min/med/max | cost-per-PASS | tokens med in/out/cr/cc | "
                 "turns med | duration med(s) | ratio max | 打切り | artifacts med files/tests/+lines |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for key in sorted(cells):
            task, kind, budget, sid = key
            arms = cells[key]
            control = arms.get("bare")
            for arm, items in sorted(arms.items()):
                s = summarize_cell(items)
                p_txt = "—"
                if control is not None and arm != "bare":
                    sc = summarize_cell(control)
                    p_txt = f"{fisher_exact_two_sided(s['pass'], s['n'] - s['pass'], sc['pass'], sc['n'] - sc['pass']):.4f}"
                dnf_txt = ", ".join(f"{k}:{v}" for k, v in sorted(s["dnf"].items())) or "0"
                rate = f"{s['pass_rate']:.2f} [{s['wilson'][0]:.2f}, {s['wilson'][1]:.2f}]" if s["pass_rate"] is not None else "—"
                cpp = f"${s['cost_per_pass']:.2f}" if s["cost_per_pass"] is not None else "—"
                tk = s["tokens"]
                L.append(f"| {task} | {kind} | {budget:g} | {_cell(sid)} | {arm} | {s['n']} | {s['pass']} | {s['fail']} | {dnf_txt} | "
                         f"{rate} | {p_txt} | {s['cost_pass']} | {s['cost_other']} | {cpp} | "
                         f"{tk['input']}/{tk['output']}/{tk['cache_read']}/{tk['cache_creation']} | {s['turns']} | "
                         f"{s['duration']} | {s['ratio_max']:.2f} | {s['n_trunc']} | "
                         f"{s['artifacts']['files']}/{s['artifacts']['tests']}/{s['artifacts']['lines_added']} |")
    L.append("")
    L.append("## 系列の妥当性検査(tools/eval-report.py --validate)")
    L.append("")
    if not series_report:
        L.append("(結果なし)")
    else:
        L.append("| 系列 | 種別 | 課題 | アーム | n | valid | 系列レベルの無効理由 |")
        L.append("|---|---|---|---|---|---|---|")
        for sid, info in sorted(series_report.items()):
            L.append(f"| {_cell(sid)} | {info['kind']} | {','.join(info['tasks'])} | {','.join(info['arms'])} | "
                     f"{info['n']} | {info['n_valid']} | {_cell('; '.join(info['reasons']) or '—')} |")
    L.append("")
    L.append("## 装置 v2 の全走行(比較可否を問わず列挙。negative も記録)")
    L.append("")
    if not v2:
        L.append("(まだ無い)")
    else:
        L.append("| 結果 JSON | 系列 | run | 課題 | アーム | verdict | valid | cost | ratio | 打切り | 督促 | 無効理由 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for d in sorted(v2, key=lambda x: x["_path"].name):
            t = d.get("totals") or {}
            exp = d.get("experiment") or {}
            L.append(f"| {d['_path'].name} | {_cell(series_of(d))} | {exp.get('run_index')} | {task_name(d)} | {arm_name(d)} | "
                     f"{d.get('verdict')} | {d.get('valid_for_comparison')} | ${float(t.get('cost_usd') or 0):.2f} | "
                     f"{t.get('budget_used_ratio')} | {t.get('n_truncated_calls')} | {t.get('n_nudges')} | "
                     f"{_cell('; '.join(d.get('invalid_reasons') or []) or '—')} |")
    L.append("")
    L.extend(manual_section(manual or []))
    L.append("## 参考値: 装置 v1(schema_version 1)の走行 — 比較には使えない")
    L.append("")
    L.append("2026-08-22〜30 の 10 走行は CLI 2.1.201(`--max-budget-usd` がサブエージェント分を合算しない "
             "main-only、Fable 5.1 非対応)・ユーザースコープ未隔離・model 未記録(transcript から復元)・"
             "アーム間の予算非対等・workdir 消失(再検証不能)のため、能力・効率いずれの比較にも載せない。"
             "verdict は `cost > total_budget` で判定し直した(abort フラグ非依存)。打切り件数は call 費用からの推定。")
    L.append("")
    if legacy:
        L.append("| 結果 JSON | 課題 | アーム | verdict(再判定) | check.pass | cost | 上限(call/total) | ratio | "
                 "打切り(推定) | 復元 model | 主な無効理由 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for d in sorted(legacy, key=lambda x: x["_path"].name):
            t = d.get("totals") or {}
            b = d.get("budgets") or {}
            reasons = [r for r in (d.get("invalid_reasons") or [])
                       if r.startswith(("verdict=", "budget_asymmetric", "n_truncated"))]
            L.append(f"| {d['_path'].name} | {task_name(d)} | {arm_name(d)} | {d.get('verdict') or '(未 backfill)'} | "
                     f"{(d.get('check') or {}).get('pass')} | ${float(t.get('cost_usd') or 0):.2f} | "
                     f"{b.get('session_budget_usd')}/{b.get('total_budget_usd')} | {t.get('budget_used_ratio')} | "
                     f"{t.get('n_truncated_calls')} | {d.get('recovered_model') or '—'} | "
                     f"{_cell('; '.join(reasons) or '装置 v1 共通(下記)')} |")
        L.append("")
        common = None
        for d in legacy:
            rs = set(d.get("invalid_reasons") or [])
            common = rs if common is None else (common & rs)
        if common:
            L.append("装置 v1 共通の無効理由: " + "; ".join(sorted(common)))
        L.append("")
        # 種別ごとの件数(比を作らない。件数のみ)
        by = {}
        for d in legacy:
            by.setdefault((task_name(d), arm_name(d)), []).append(d.get("verdict"))
        L.append("| 課題 | アーム | n | PASS | FAIL | DNF(種別) |")
        L.append("|---|---|---|---|---|---|")
        for (task, arm), vs in sorted(by.items()):
            dnf = {}
            for v in vs:
                if v and v.startswith("DNF"):
                    dnf[v] = dnf.get(v, 0) + 1
            L.append(f"| {task} | {arm} | {len(vs)} | {vs.count('PASS')} | {vs.count('FAIL')} | "
                     f"{', '.join(f'{k}:{n}' for k, n in sorted(dnf.items())) or '0'} |")
    else:
        L.append("(装置 v1 の結果なし)")
    L.append("")
    L.append("## 無効理由の集計(全走行)")
    L.append("")
    counts: dict = {}
    for d in results:
        for r in d.get("invalid_reasons") or []:
            key = r.split("(")[0].split(":")[0]
            counts[key] = counts.get(key, 0) + 1
    if counts:
        L.append("| 理由 | 件数 |")
        L.append("|---|---|")
        for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            L.append(f"| {_cell(k)} | {n} |")
    else:
        L.append("(なし)")
    L.append("")
    return "\n".join(L) + "\n"


def run_report(results_dir: Path = RESULTS_DIR, out: Path = REPORT_PATH, check: bool = False) -> int:
    results = load_results(results_dir)
    series_report = validate_series(results)
    text = build_report(results, series_report, manual=load_manual_results(results_dir / "manual"))
    if check:
        cur = out.read_text(encoding="utf-8") if out.is_file() else ""
        if cur.replace("\r\n", "\n") != text:
            say(f"STALE: {out} (再生成すると差分が出る。python tools/eval-report.py --report で更新)")
            return 1
        say(f"eval-report --check: OK ({out})")
        return 0
    out.write_text(text, encoding="utf-8", newline="\n")
    say(f"WROTE: {out} ({len(results)} results)")
    return 0


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _legacy_fixture(name: str, arm: str, task: str, cost: float, call_b: float, total_b: float,
                    check_pass: bool, calls: "list | None" = None, aborted: bool = False,
                    workdir: str = "C:\\Temp\\e2e-x_y") -> dict:
    return {"task": task, "arm": arm, "timestamp": "2026-08-22T18:17:02", "harness_commit": "abc",
            "claude_version": "2.1.201 (Claude Code)", "workdir": workdir,
            "budgets": {"max_turns_per_call": 60, "session_budget_usd": call_b,
                        "total_budget_usd": total_b, "max_budget_usd_flag_used": True},
            "calls": calls or [{"index": 1, "prompt": "x", "exit_code": 0, "num_turns": 3,
                                "total_cost_usd": cost, "is_error": False, "result_tail": "ok", "duration_s": 10}],
            "totals": {"duration_s": 10, "num_turns": 3, "cost_usd": cost, "aborted_by_budget": aborted},
            "check": {"pass": check_pass, "exit_code": 0 if check_pass else 1},
            "artifact_stats": {"files": 1}}


def _v2_fixture(name: str, arm: str, verdict: str, cost: float, series: str = "S", idx: int = 1,
                valid: bool = True, chash: str = "h1", budget: float = 120.0) -> dict:
    return {"schema_version": 2, "task": {"name": "expense-webapp"}, "arm": {"name": arm, "kind": "builtin"},
            "experiment": {"kind": "capability", "series_id": series, "run_index": idx, "condition_hash": chash,
                           "plan_path": None, "plan_sha256": None},
            "conditions": {"total_budget_usd": budget, "auth_mode": "api_key", "claude_version": "2.1.257"},
            "totals": {"cost_usd": cost, "num_turns": 50, "duration_s": 1000, "budget_used_ratio": cost / budget,
                       "n_truncated_calls": 0, "n_nudges": 0,
                       "tokens": {"input": 10, "output": 5, "cache_read": 100, "cache_creation": 20}},
            "verdict": verdict, "valid_for_comparison": valid, "invalid_reasons": [] if valid else ["x"],
            "artifact_stats": {"files": 3, "test_functions": 4, "lines_added": 100},
            "timestamp": f"2026-10-0{idx}T00:00:00"}


def selftest() -> int:
    failures = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            say(f"PASS: {name}")
        else:
            failures.append(name)
            say(f"FAIL: {name} {detail}")

    # 1. 統計(既知値。監査 §6 / 設計レビューの再計算値と一致すること)
    p = fisher_exact_two_sided(5, 0, 0, 5)
    check("Fisher 両側 5/5 vs 0/5 = 0.0079", abs(p - 2 / 252) < 1e-9, f"{p}")
    p = fisher_exact_two_sided(4, 1, 0, 5)
    check("Fisher 両側 4/5 vs 0/5 = 0.0476", abs(p - 10 / 210) < 1e-9, f"{p}")
    p = fisher_exact_two_sided(3, 2, 0, 5)
    check("Fisher 両側 3/5 vs 0/5 = 0.167(差を主張しない)", abs(p - 20 / 120) < 1e-9, f"{p}")
    p = fisher_exact_two_sided(8, 0, 0, 8)
    check("Fisher 両側 8/8 vs 0/8 = 0.00016", abs(p - 2 / 12870) < 1e-9, f"{p}")
    check("Fisher 両側 2/2 vs 0/2 = 0.33(n=2 は主張不可)", abs(fisher_exact_two_sided(2, 0, 0, 2) - 2 / 6) < 1e-9)
    check("Fisher 対称(表の入替で同じ p)", abs(fisher_exact_two_sided(4, 1, 0, 5) - fisher_exact_two_sided(0, 5, 4, 1)) < 1e-12)
    lo, hi = wilson_interval(5, 5)
    check("Wilson 95% 5/5 の下限 0.57", abs(lo - 0.5655) < 0.001 and hi == 1.0, f"{lo},{hi}")
    lo, hi = wilson_interval(0, 5)
    check("Wilson 95% 0/5 の上限 0.43", lo == 0.0 and abs(hi - 0.4345) < 0.001, f"{lo},{hi}")
    check("Wilson n=0 は (0,1)", wilson_interval(0, 0) == (0.0, 1.0))
    check("費用の範囲表記(比を作らない)", cost_range([38.0, 52.0, 45.0]) == "$38.00 / $45.00 / $52.00"
          and cost_range([]) == "—" and "n=1" in cost_range([3.2]))
    check("encoded cwd: '_' も '-' に", encode_cwd(r"C:\Users\n\AppData\Local\Temp\e2e-todo-cli-harness-x3w_jas_")
          == "C--Users-n-AppData-Local-Temp-e2e-todo-cli-harness-x3w-jas-")

    with tempfile.TemporaryDirectory(prefix="eval-report-selftest-") as tmp:
        rd = Path(tmp) / "results"
        rd.mkdir()
        (rd / "trajectories").mkdir()
        # 2. backfill 規則(合成 v1 フィクスチャ)
        trunc_call = {"index": 2, "prompt": "y", "exit_code": 1, "num_turns": 27, "total_cost_usd": 4.169,
                      "is_error": True, "result_tail": "", "duration_s": 400}
        quota_call = {"index": 3, "prompt": "z", "exit_code": 1, "num_turns": 1, "total_cost_usd": 0,
                      "is_error": True, "result_tail": "You're out of usage credits. Run /usage-credits", "duration_s": 5}
        ok_call = {"index": 1, "prompt": "x", "exit_code": 0, "num_turns": 3, "total_cost_usd": 2.0,
                   "is_error": False, "result_tail": "ok", "duration_s": 10}
        fixtures = {
            "todo-cli-bare-1.json": _legacy_fixture("b", "bare", "todo-cli", 3.22, 4, 12, True),
            "todo-cli-harness-1.json": _legacy_fixture("h1", "harness", "todo-cli", 13.75, 4, 12, False,
                                                       calls=[ok_call, trunc_call], aborted=True),
            "todo-cli-harness-2.json": _legacy_fixture("h2", "harness", "todo-cli", 45.47, 8, 40, True, aborted=True),
            "todo-cli-harness-3.json": _legacy_fixture("h3", "harness", "todo-cli", 24.72, 8, 40, True),
            "expense-webapp-harness-1.json": _legacy_fixture("e1", "harness", "expense-webapp", 69.06, 10, 60, True,
                                                             workdir="C:\\Temp\\e2e-expense-webapp-harness-42c7je4t"),
            "expense-webapp-harness-2.json": _legacy_fixture("e2", "harness", "expense-webapp", 33.66, 10, 60, False,
                                                             calls=[ok_call, quota_call]),
            "expense-webapp-bare-1.json": _legacy_fixture("eb", "bare", "expense-webapp", 14.14, 8, 40, False),
        }
        for name, d in fixtures.items():
            (rd / name).write_text(json.dumps(d), encoding="utf-8")
        manifest = {"entries": [{"source_dir": "C--Temp-e2e-expense-webapp-harness-42c7je4t",
                                 "zip": "x.zip", "sha256": "deadbeef", "files": 23,
                                 "models_observed": {"claude-fable-5": 550, "<synthetic>": 1}}]}
        (rd / "trajectories" / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
        run_backfill(rd, rd / "trajectories" / "MANIFEST.json", today="2026-09-10")
        got = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in rd.glob("*.json")}
        check("backfill: PASS 走行(上限内)は PASS", got["todo-cli-bare-1.json"]["verdict"] == "PASS")
        check("backfill: aborted + cost>total は DNF_BUDGET_TOTAL(打切り 1 件を推定)",
              got["todo-cli-harness-1.json"]["verdict"] == "DNF_BUDGET_TOTAL"
              and got["todo-cli-harness-1.json"]["totals"]["n_truncated_calls"] == 1)
        check("backfill: check PASS でも cost 45.47 > 40 は DNF_BUDGET_TOTAL",
              got["todo-cli-harness-2.json"]["verdict"] == "DNF_BUDGET_TOTAL")
        check("backfill: check PASS・上限内は PASS(ただし valid=false)",
              got["todo-cli-harness-3.json"]["verdict"] == "PASS"
              and got["todo-cli-harness-3.json"]["valid_for_comparison"] is False)
        check("backfill: abort フラグ false でも cost 69.06 > 60 は DNF_BUDGET_TOTAL",
              got["expense-webapp-harness-1.json"]["verdict"] == "DNF_BUDGET_TOTAL")
        check("backfill: 利用上限文言は DNF_QUOTA", got["expense-webapp-harness-2.json"]["verdict"] == "DNF_QUOTA")
        check("backfill: check FAIL は FAIL", got["expense-webapp-bare-1.json"]["verdict"] == "FAIL")
        rs = got["todo-cli-harness-2.json"]["invalid_reasons"]
        check("backfill: 共通の無効理由(cli<2.1.217・user_scope_unisolated・budget_enforcement=main-only)",
              {"cli<2.1.217", "user_scope_unisolated", "budget_enforcement=main-only"} <= set(rs), str(rs))
        check("backfill: アーム間の予算非対等を検出(8/40 vs bare 4/12)",
              any(r.startswith("budget_asymmetric_across_arms") for r in rs)
              and not any(r.startswith("budget_asymmetric") for r in got["todo-cli-harness-1.json"]["invalid_reasons"]))
        e1 = got["expense-webapp-harness-1.json"]
        check("backfill: MANIFEST から transcript zip と recovered_model を結合",
              e1["trajectory"]["zip"] == "x.zip" and e1["recovered_model"] == "claude-fable-5"
              and e1["recovered_models_observed"]["<synthetic>"] == 1)
        check("backfill: MANIFEST 不在は recovered_model None", got["todo-cli-bare-1.json"]["recovered_model"] is None)
        check("backfill: 既存キー(budgets/calls/check)を保持", got["todo-cli-harness-2.json"]["budgets"]["total_budget_usd"] == 40
              and got["todo-cli-harness-2.json"]["check"]["pass"] is True)
        check("backfill: v2 互換 conditions/experiment(legacy 系列)", e1["experiment"]["series_id"] == LEGACY_SERIES_ID
              and e1["conditions"]["budget_enforcement"] == "main-only" and e1["schema_version"] == 1)
        snapshot = {p.name: p.read_text(encoding="utf-8") for p in rd.glob("*.json")}
        run_backfill(rd, rd / "trajectories" / "MANIFEST.json", today="2099-01-01")
        check("backfill は冪等(再実行で差分なし・first_applied_on 維持)",
              snapshot == {p.name: p.read_text(encoding="utf-8") for p in rd.glob("*.json")})

        # 3. validate(系列条件)と report
        v2 = {
            "expense-webapp-harness-v2-1.json": _v2_fixture("a", "harness", "PASS", 40.0, idx=1),
            "expense-webapp-bare-v2-2.json": _v2_fixture("b", "bare", "FAIL", 12.0, idx=2),
            "expense-webapp-harness-v2-3.json": _v2_fixture("c", "harness", "PASS", 52.0, idx=3),
            "expense-webapp-bare-v2-4.json": _v2_fixture("d", "bare", "FAIL", 15.0, idx=4),
            "expense-webapp-harness-v2-5.json": _v2_fixture("e", "harness", "DNF_BUDGET_TOTAL", 130.0, idx=5, valid=False),
            "expense-webapp-harness-mixed-1.json": _v2_fixture("f", "harness", "PASS", 40.0, series="M", idx=1, chash="h1"),
            "expense-webapp-bare-mixed-2.json": _v2_fixture("g", "bare", "PASS", 10.0, series="M", idx=2, chash="h2", budget=60.0),
        }
        for name, d in v2.items():
            (rd / name).write_text(json.dumps(d), encoding="utf-8")
        results = load_results(rd)
        rep = validate_series(results)
        check("validate: 条件単一・予算同額の系列は系列レベル理由なし", rep["S"]["reasons"] == [] and rep["S"]["n_valid"] == 4)
        check("validate: hash 混在と予算非対等を検出", len(rep["M"]["reasons"]) == 2, str(rep["M"]["reasons"]))
        check("validate: legacy 系列は全件 valid=false", rep[LEGACY_SERIES_ID]["n_valid"] == 0)
        text = build_report(results, rep)
        check("report: 比較表に S 系列の harness/bare 行", "| expense-webapp | capability | 120 | S | harness | 2 | 2 | 0 |" in text
              and "| expense-webapp | capability | 120 | S | bare | 2 | 0 | 2 |" in text, text[:2000])
        check("report: Fisher p(2/2 vs 0/2)=0.3333 を表示", "0.3333" in text)
        check("report: PASS 費用は範囲(min/median/max)で、比を作らない",
              "$40.00 / $46.00 / $52.00" in text and "倍" not in text and "x cheaper" not in text)
        check("report: 混在系列 M は比較表に載らない", "| M | harness |" not in text)
        check("report: 装置 v1 は参考値の別表に verdict 再判定つきで載る",
              "todo-cli-harness-2.json | todo-cli | harness | DNF_BUDGET_TOTAL" in text)
        check("report: 無効理由の集計表", "| cli<2.1.217 | 7 |" in text)
        out = Path(tmp) / "REPORT.md"
        check("report --check: 生成直後は差分なし", run_report(rd, out) == 0 and run_report(rd, out, check=True) == 0)
        out.write_text(out.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        check("report --check: 手編集は差分として検出", run_report(rd, out, check=True) == 1)

        # 3b. 手動 E2E(Copilot 経路)の記録: 集計表に載り、読めない記録は理由つきで残り、verdict と項目の矛盾を指摘する
        check("manual: 記録が無ければ「記録なし」", "(記録なし" in "\n".join(manual_section([])))
        md = rd / "manual"
        md.mkdir()
        good = {"schema": MANUAL_SCHEMA, "route": "copilot-vscode", "date": "2026-08-30", "harness_commit": "abc1234",
                "host": {"vscode": "1.13x", "copilot_chat": "0.3x", "harness_kind": "Local"},
                "items": {"1-1": {"result": "OK"}, "2-1": {"result": "OK"}, "2-2": {"result": "PARTIAL", "note": "指示層"},
                          "2-5": {"result": "NA"}, "3-5": {"result": "NG"}},
                "verdict": "PARTIAL", "notes": "転記"}
        (md / "copilot-e2e-20260830.json").write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        (md / "broken.json").write_text("{not json", encoding="utf-8")
        bad = dict(good, verdict="PASS", items={"1-1": {"result": "ok"}})
        (md / "bad.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        manual = load_manual_results(md)
        check("manual: 3 記録を読む(壊れた JSON も落とさない)", len(manual) == 3)
        g = next(m for m in manual if m["_path"].name == "copilot-e2e-20260830.json")
        check("manual: 正常な記録は不備なし・件数 OK 2 / NG 1 / PARTIAL 1 / NA 1",
              g["_problems"] == [] and manual_counts(g) == {"OK": 2, "NG": 1, "PARTIAL": 1, "NA": 1}, str(g["_problems"]))
        b = next(m for m in manual if m["_path"].name == "bad.json")
        check("manual: result の語彙外を検出", any("items.1-1.result" in p for p in b["_problems"]), str(b["_problems"]))
        br = next(m for m in manual if m["_path"].name == "broken.json")
        check("manual: 壊れた JSON は理由つき", any("JSON として読めない" in p for p in br["_problems"]))
        sec = "\n".join(manual_section(manual))
        check("manual: 集計表に日付・種別・件数・verdict が載り、不備は箇条書きで残る",
              "| copilot-e2e-20260830.json | 2026-08-30 | copilot-vscode | Local | 1.13x / 0.3x | abc1234 | 2 | 1 | 1 | 1 | PARTIAL |" in sec
              and "- bad.json: 記録の不備" in sec and "- broken.json: 記録の不備" in sec, sec)
        text = build_report(results, rep, manual=manual)
        check("report: 手動 E2E 節が装置 v2 の節と参考値の節の間に入る",
              text.index("## 手動 E2E") > text.index("## 装置 v2 の全走行") and text.index("## 手動 E2E") < text.index("## 参考値"))
        check("report --check: 手動記録を足すと差分として検出(再生成が要る)", run_report(rd, out, check=True) == 1
              and run_report(rd, out) == 0 and run_report(rd, out, check=True) == 0)

        # 4. scan-trajectories(合成 zip)
        zd = Path(tmp) / "zips"
        zd.mkdir()
        with zipfile.ZipFile(zd / "x.zip", "w") as zf:
            zf.writestr("s1.jsonl", '{"sessionId":"11111111-1111-4111-8111-111111111111","message":{"model":"claude-opus-5"}}\n'
                                     '{"sessionId":"11111111-1111-4111-8111-111111111111","message":{"model":"claude-opus-5"}}\n')
            zf.writestr("s1/subagents/agent-a.jsonl", '{"message":{"model":"<synthetic>"}}\n')
            zf.writestr("s1/subagents/agent-a.meta.json", "{}")
        man = {"entries": [{"source_dir": "C--Temp-e2e-expense-webapp-harness-42c7je4t", "zip": "x.zip",
                            "sha256": sha256_file(zd / "x.zip")}]}
        n = scan_trajectories(zd, results, man)
        e = man["entries"][0]
        check("scan: model 件数・session・result_json を MANIFEST に写す",
              n == 1 and e["models_observed"] == {"claude-opus-5": 2, "<synthetic>": 1} and len(e["sessions"]) == 1
              and e["result_json"] == "expense-webapp-harness-1.json", json.dumps(e))

    say(f"selftest: {'OK' if not failures else 'NG'} (FAIL {len(failures)} 件)")
    return 0 if not failures else 1


# ---------------------------------------------------------------------------

def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="評価結果の backfill / 妥当性検査 / REPORT.md 生成(詳細は evaluation/README.md)")
    ap.add_argument("--backfill", action="store_true", help="schema_version 1 の結果に verdict 等を付与(追記のみ・冪等)")
    ap.add_argument("--scan-trajectories", action="store_true", help="transcript zip を走査して MANIFEST に model 件数を書く")
    ap.add_argument("--zip-dir", default=None, help="--scan-trajectories の zip ディレクトリ(既定: evaluation/results/trajectories)")
    ap.add_argument("--validate", action="store_true", help="系列ごとの妥当性検査")
    ap.add_argument("--strict", action="store_true", help="--validate で比較不能があれば exit 1")
    ap.add_argument("--report", action="store_true", help="evaluation/REPORT.md を生成")
    ap.add_argument("--check", action="store_true", help="--report と併用: 再生成しても差分が無いか(0/1)")
    ap.add_argument("--out", default=None, help="--report の出力先(既定: evaluation/REPORT.md)")
    ap.add_argument("--dry-run", action="store_true", help="--backfill で書き込まない")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    if args.selftest:
        return selftest()
    rc = 0
    if args.scan_trajectories:
        zip_dir = Path(args.zip_dir) if args.zip_dir else TRAJ_DIR
        manifest = load_manifest()
        scan_trajectories(zip_dir, load_results(), manifest)
        save_manifest(manifest)
        say(f"WROTE: {MANIFEST}")
    if args.backfill:
        rc = run_backfill(dry_run=args.dry_run) or rc
    if args.validate:
        results = load_results()
        n_bad = print_validation(results, validate_series(results))
        if args.strict and n_bad:
            rc = 1
    if args.report:
        rc = run_report(out=Path(args.out) if args.out else REPORT_PATH, check=args.check) or rc
    if not (args.backfill or args.scan_trajectories or args.validate or args.report):
        ap.print_help()
        return 2
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

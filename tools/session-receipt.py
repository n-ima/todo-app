#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会話(セッション)ごとの「受領書」(session receipt): 集計とレンダリングの唯一の正。

2026-09-09 再監査 §5(TM-3/RC-6/RC-4/TM-4/TM-5/TM-10/TM-11/RC-12)の実装。
呼び出し側は3経路とも同じ関数を使う(正を1か所にする):
  - .github/hooks/scripts/log-effort.py   : Stop で draft を書き、区切り条件を満たすときだけ
                                            systemMessage(3行以内)を返す。SessionEnd で確定。
  - .github/skills/harness-stats/SKILL.md  : `python tools/session-receipt.py --session current`
  - .github/prompts/99-status.prompt.md    : `--session latest --summary`

出所タグ(モデルの自己申告は使わない):
  [公式]        statusline の stdin JSON(cost.total_cost_usd / context_window.used_percentage /
                cost.total_lines_*)。対話モードで費用・文脈%・行数が取れる唯一の公式経路。
                statusline.py が logs/usage/<sid>.status.json に永続化したものを読む。
                `total_*`/`current_usage` は累計ではない(直近呼び出し/現在の文脈窓)ため使わない。
  [transcript]  累計トークン(main/サブエージェント別・解析率 parsed/total 付き)。非公式形式。
                input_tokens は「最後のキャッシュ境界より後のトークン」で正しい値(RC-1 反証)。
  [推定]        自前単価表(tools/prices.json、list 価格)による推定 USD。請求額ではない。
  [git]         コミット数(SessionStart の HEAD からの rev-list)。
  tests の pass/fail を主張できるのは golden-eval / check.py の決定論検証のみ。
  PostToolUse(Bash) 抽出は ran/unknown 止まり(main と subagents/*.jsonl の両方を走査)。

一次データの置き場(RC-4): `.github/hooks/logs/usage/`(gitignore 済・90日ローテーション)。
  <sid>.status.json / <sid>.baseline.json / <sid>.subagents.jsonl /
  <sid>.receipt.draft.json(Stop) → <sid>.receipt.json(SessionEnd) / sessions.jsonl
committed 側は docs/06-retrospective/effort-report.md・baselines.json(effort-report.py が集計のみ生成)。
取らないもの: プロンプト本文・tool 出力本文・ファイル内容・commit 本文・秘密値・開発者識別子。

閾値の正は tools/usage-config.json(1か所)。フックは決して block しない(fail-open)。
自己テスト: python tools/session-receipt.py --selftest
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_USAGE_REL = os.path.join(".github", "hooks", "logs", "usage")
GATE_REL = os.path.join("docs", "00-overview", "progress.md")
TASKS_REL = os.path.join("docs", "03-implementation", "tasks.md")
BASELINES_REL = os.path.join("docs", "06-retrospective", "baselines.json")
SCHEMA = "receipt/1"
HOOK_SCRIPTS_REL = os.path.join(".github", "hooks", "scripts")  # 判定ログ JSONL の読み手 _log.py の置き場(A6-20)
REVIEW_LOG_REL = os.path.join("docs", "04-test", "review-log.md")  # 独立レビューの一次記録(D073)
REVIEW_LOG_ENTRY_RE = re.compile(r"^###\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}\s*/")  # 見出し「### YYYY-MM-DD HH:MM / …」

PHASE_KEY_RE = re.compile(r"^\d{2}-[a-z0-9][a-z0-9\-]*$")
CMD_RE = re.compile(r"<command-name>/?([A-Za-z0-9][A-Za-z0-9_\-]*)</command-name>")
AGENT_ID_RE = re.compile(r"agentId:\s*([0-9a-fA-F]+)")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
AGENT_TOOLS = {"Agent", "Task"}
SHELL_TOOLS = {"Bash", "PowerShell"}
REVIEW_ROLES = {"reviewer", "spec-critic"}
GATE_PHASES = ("requirements", "design", "implementation", "test", "release")
TEST_CMD_RE = re.compile(
    r"(?:^|[\s;&|(])(?:pytest|py\.test|npm\s+(?:run\s+)?test|npx\s+(?:vitest|jest|mocha|playwright\s+test)"
    r"|vitest|jest|pnpm\s+test|yarn\s+test|dotnet\s+test|go\s+test|cargo\s+test"
    r"|python3?\s+-m\s+(?:pytest|unittest)|mvn\s+test|gradle\s+test|rspec|phpunit|ctest|make\s+test)\b")
GOLDEN_RE = re.compile(r"完了宣言\s*(\d+)\s*件中、機械的検証を通過\s*(\d+)\s*件")
# vitest/jest は "Test Files N passed" の後に "Tests N passed" が出るため、Tests 行を優先する
OBS_PASSED_RES = (re.compile(r"\bTests:?\s+(\d+)\s+passed"), re.compile(r"(\d+)\s+passed"))
OBS_FAILED_RES = (re.compile(r"\bTests:?\s+(\d+)\s+failed"), re.compile(r"(\d+)\s+(?:failed|failing)"))
TOKEN_COLS = ("input", "output", "cache_read", "cache_w5m", "cache_w1h")
DEFAULT_CONFIG = {
    "context_warn_pct": 40, "cost_delta_usd": 2.0, "token_delta": 500000, "turns_every": 10,
    "cache_read_ratio_warn": 0.95, "price_divergence_warn": 0.10, "baseline_min_n": 5,
    "baseline_switch_n": 20, "rotate_days": 90, "receipt_max_lines": 3,
    "host_min_version": "2.1.217", "provisional_baselines": {},
}


# ---------------------------------------------------------------- 基本ユーティリティ

def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_path(p):
    return (p or "").replace("\\", "/").rstrip("/").lower()


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def write_json_atomic(path, data):
    """temp→rename で原子的に書く(途中で読まれても壊れた JSON を見せない)。"""
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


def logs_dir(root=None):
    return os.path.join(root or ROOT, LOGS_USAGE_REL)


def safe_sid(sid):
    return re.sub(r"[^A-Za-z0-9_-]", "_", sid or "")


def load_config(root=None):
    cfg = dict(DEFAULT_CONFIG)
    data = load_json(os.path.join(root or ROOT, "tools", "usage-config.json"), {})
    if isinstance(data, dict):
        cfg.update({k: v for k, v in data.items() if not k.startswith("_")})
    return cfg


def load_prices(root=None):
    data = load_json(os.path.join(root or ROOT, "tools", "prices.json"), {}) or {}
    models = data.get("models") if isinstance(data, dict) else None
    table = []
    for m in models or []:
        if isinstance(m, dict) and m.get("id"):
            table.append(m)
    # 最長前方一致: claude-fable-5-1 を claude-fable-5 より先に解決する
    table.sort(key=lambda m: -len(m["id"]))
    return {"as_of": (data.get("as_of") if isinstance(data, dict) else None) or "?", "models": table}


def price_of(model, prices):
    """(単価行 or None, fallback か) を返す。前方一致(最長優先)。family 行は fallback 扱い。"""
    for m in prices.get("models", []):
        if model.startswith(m["id"]):
            return m, bool(m.get("family"))
    return None, False


def row_cost(vals, price):
    """5列トークン [input,output,cache_read,w5m,w1h] × 単価行 → USD。"""
    return (vals[0] * price.get("input", 0) + vals[1] * price.get("output", 0)
            + vals[2] * price.get("cache_read", 0) + vals[3] * price.get("cache_w5m", 0)
            + vals[4] * price.get("cache_w1h", 0)) / 1_000_000


def fmt_tokens(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def fmt_usd(v):
    return "n/a" if v is None else f"${v:,.2f}"


def version_tuple(v):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])
    except Exception:
        return ()


# ---------------------------------------------------------------- transcript 走査

def iter_json_lines(path):
    """(parsed, total) を数えながら JSON 行を返す。壊れた行はスキップ。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield line
    except OSError:
        return


def usage_of(msg, entry):
    """message.usage から (message_id, model, [input,output,cache_read,w5m,w1h])。対象外は None。"""
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None
    model = msg.get("model") or ""
    if not model or model == "<synthetic>":
        return None

    def n(x):
        try:
            return int(x or 0)
        except (TypeError, ValueError):
            return 0

    cc = u.get("cache_creation")
    if isinstance(cc, dict):
        w5 = n(cc.get("ephemeral_5m_input_tokens"))
        w1 = n(cc.get("ephemeral_1h_input_tokens"))
    else:
        w5 = n(u.get("cache_creation_input_tokens"))
        w1 = 0
    mid = msg.get("id") or entry.get("uuid") or ""
    return (mid, model, [n(u.get("input_tokens")), n(u.get("output_tokens")),
                         n(u.get("cache_read_input_tokens")), w5, w1])


def result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def observe_tests(text):
    """テスト出力の保守的な観測(ヒューリスティック。pass の主張には使わない)。"""
    obs = {}
    for key, pats in (("passed", OBS_PASSED_RES), ("failed", OBS_FAILED_RES)):
        for pat in pats:
            m = pat.search(text)
            if m:
                obs[key] = int(m.group(1))
                break
    return obs or None


def scan_transcript(path):
    """1つの JSONL を1回走査して受領書に要る事実をまとめて返す(本文は保持しない)。"""
    f = {
        "usage": {}, "assistant_ids": set(), "user_prompts": 0, "commands": [],
        "attribution": Counter(), "attribution_agent": Counter(), "effort": Counter(),
        "versions": set(), "entrypoints": set(), "first_ts": "", "last_ts": "",
        "parsed": 0, "total": 0, "edit_paths": set(), "test_runs": [],
        "agent_use": {}, "agent_ids": {}, "agents": {}, "golden": [], "check_py": [],
    }
    test_ids, golden_ids, check_ids = {}, set(), set()
    for line in iter_json_lines(path):
        f["total"] += 1
        try:
            e = json.loads(line)
        except Exception:
            continue
        if not isinstance(e, dict):
            continue
        f["parsed"] += 1
        ts = e.get("timestamp")
        if isinstance(ts, str) and len(ts) >= 10:
            if not f["first_ts"]:
                f["first_ts"] = ts
            f["last_ts"] = ts
        if isinstance(e.get("version"), str):
            f["versions"].add(e["version"])
        if isinstance(e.get("entrypoint"), str):
            f["entrypoints"].add(e["entrypoint"])
        etype = e.get("type")
        msg = e.get("message") if isinstance(e.get("message"), dict) else None
        if etype == "assistant" and msg:
            got = usage_of(msg, e)
            if got:
                mid, model, vals = got
                f["usage"][mid] = (model, vals)
                f["assistant_ids"].add(mid)
            a = e.get("attributionSkill")
            if isinstance(a, str) and a:
                f["attribution"][a] += 1
            ag = e.get("attributionAgent")
            if isinstance(ag, str) and ag:
                f["attribution_agent"][ag] += 1
            eff = e.get("effort")
            if isinstance(eff, dict):
                eff = eff.get("level")
            if isinstance(eff, str) and eff:
                f["effort"][eff] += 1
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    name = b.get("name") or ""
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    tid = b.get("id") or ""
                    if name in AGENT_TOOLS:
                        f["agent_use"][tid] = {"type": inp.get("subagent_type") or "",
                                               "model": inp.get("model")}
                    elif name in EDIT_TOOLS:
                        p = inp.get("file_path") or inp.get("notebook_path") or ""
                        if isinstance(p, str) and p:
                            f["edit_paths"].add(norm_path(p))
                    elif name in SHELL_TOOLS:
                        cmd = inp.get("command") or ""
                        if not isinstance(cmd, str):
                            continue
                        if "golden-eval.py" in cmd:
                            golden_ids.add(tid)
                        elif re.search(r"\bcheck\.py\b", cmd):
                            check_ids.add(tid)
                        elif TEST_CMD_RE.search(cmd):
                            f["test_runs"].append({"tool_use_id": tid, "observed": None})
                            test_ids[tid] = len(f["test_runs"]) - 1
        elif etype == "user" and msg:
            content = msg.get("content")
            texts, has_result = [], False
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        has_result = True
                        tid = b.get("tool_use_id") or ""
                        text = result_text(b.get("content"))
                        if tid in test_ids:
                            f["test_runs"][test_ids[tid]]["observed"] = observe_tests(text)
                        elif tid in golden_ids:
                            m = GOLDEN_RE.search(text)
                            if m:
                                f["golden"].append({"declared": int(m.group(1)), "passed": int(m.group(2))})
                        elif tid in check_ids:
                            m = re.search(r'"pass"\s*:\s*(true|false)', text)
                            if m:
                                f["check_py"].append(m.group(1) == "true")
                        elif tid in f["agent_use"]:
                            m = AGENT_ID_RE.search(text)
                            if m:
                                f["agent_ids"].setdefault(m.group(1), f["agent_use"][tid]["type"])
                    elif b.get("type") == "text":
                        texts.append(b.get("text", ""))
            if not has_result:
                t = "".join(texts).lstrip()
                if t.startswith("<command-"):
                    m = CMD_RE.search(t)
                    if m:
                        f["commands"].append(m.group(1))
                elif t and not e.get("isMeta"):
                    f["user_prompts"] += 1
            tur = e.get("toolUseResult")
            if isinstance(tur, dict) and isinstance(tur.get("agentId"), str) and tur.get("agentId"):
                info = f["agents"].setdefault(tur["agentId"], {})
                if isinstance(tur.get("agentType"), str) and tur.get("agentType"):
                    info["type"] = tur["agentType"]
                for k in ("resolvedModel", "status"):
                    if tur.get(k) is not None:
                        info[k] = tur.get(k)
                if isinstance(tur.get("modelsUsed"), list):
                    info["modelsUsed"] = tur["modelsUsed"]
    return f


def subagent_files(transcript_path):
    base = os.path.splitext(transcript_path)[0]
    return sorted(glob.glob(os.path.join(base, "subagents", "agent-*.jsonl")))


def label_subagent(path, main_facts, sub_facts, sub_log_types):
    """サブエージェント種別の解決順: meta.json(agentType) → main の toolUseResult(agentType) →
    SubagentStop/PostToolUse ログ → tool_result 文面の agentId 正規表現 → attributionAgent → subagent。"""
    m = re.match(r"agent-([0-9a-fA-F]+)\.jsonl$", os.path.basename(path))
    aid = m.group(1) if m else ""
    meta = load_json(path[:-len(".jsonl")] + ".meta.json", {}) or {}
    label = meta.get("agentType") if isinstance(meta, dict) else None
    depth = meta.get("spawnDepth") if isinstance(meta, dict) else None
    source = "meta.json" if label else None
    if not label and aid in main_facts["agents"] and main_facts["agents"][aid].get("type"):
        label, source = main_facts["agents"][aid]["type"], "toolUseResult"
    if not label and aid in sub_log_types:
        label, source = sub_log_types[aid], "subagents.jsonl"
    if not label and aid in main_facts["agent_ids"]:
        label, source = main_facts["agent_ids"][aid], "tool_result-regex"
    if not label and sub_facts["attribution_agent"]:
        label, source = sub_facts["attribution_agent"].most_common(1)[0][0], "attributionAgent"
    if not label:
        label, source = "subagent", "unknown"
    return aid, label, (depth if isinstance(depth, int) else 1), source


# ---------------------------------------------------------------- 周辺一次データ

def read_status(logs, sid):
    path = os.path.join(logs, safe_sid(sid) + ".status.json")
    data = load_json(path)
    if not isinstance(data, dict):
        return None
    try:
        data["_captured_at"] = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        pass
    return data


def read_baseline(logs, sid):
    data = load_json(os.path.join(logs, safe_sid(sid) + ".baseline.json"))
    return data if isinstance(data, dict) else None


def read_subagent_log(logs, sid):
    rows = []
    for line in iter_json_lines(os.path.join(logs, safe_sid(sid) + ".subagents.jsonl")):
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return [r for r in rows if isinstance(r, dict)]


def parse_gate(progress_path):
    """progress.md の GATE_STATUS ブロックを {phase: state} で返す(無ければ None)。"""
    try:
        text = open(progress_path, encoding="utf-8", errors="replace").read()
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


def gate_state(value):
    """注記付きの値('done (PM approved)')から状態語だけを取り出す。"""
    m = re.match(r"\s*([a-z_]+)", value or "")
    return m.group(1) if m else (value or "")


def tasks_done_count(tasks_path):
    try:
        text = open(tasks_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    return len(re.findall(r"^\s*[-*]\s+\[[xX]\]", text, re.M))


def fast_track_active(progress_path):
    try:
        return "ライトパス使用中" in open(progress_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False


def git_count(cwd, since_sha):
    if not since_sha:
        return None
    try:
        proc = subprocess.run(["git", "-C", cwd, "rev-list", "--count", f"{since_sha}..HEAD"],
                              capture_output=True, text=True, timeout=3)
        if proc.returncode == 0:
            return int(proc.stdout.strip() or 0)
    except Exception:
        pass
    return None


def harness_commit(cwd):
    try:
        text = open(os.path.join(cwd, "docs", "00-overview", "harness-origin.md"),
                    encoding="utf-8", errors="replace").read(4000)
    except OSError:
        return None
    m = re.search(r"\b([0-9a-f]{7,40})\b", text)
    return m.group(1) if m else None


def list_receipts(logs):
    """logs/usage の受領書(確定を優先、無ければ draft)を sid → dict で返す。"""
    out = {}
    for path in glob.glob(os.path.join(logs, "*.receipt.json")):
        data = load_json(path)
        if isinstance(data, dict):
            out[os.path.basename(path)[:-len(".receipt.json")]] = data
    for path in glob.glob(os.path.join(logs, "*.receipt.draft.json")):
        sid = os.path.basename(path)[:-len(".receipt.draft.json")]
        if sid not in out:
            data = load_json(path)
            if isinstance(data, dict):
                out[sid] = data
    return out


def phase_review_runs(logs, phase, cwd, exclude_sid):
    """同フェーズ(同プロジェクト)の他セッションで reviewer/spec-critic が起動した回数の累計。"""
    runs, sessions = 0, 0
    for sid, r in list_receipts(logs).items():
        if sid == safe_sid(exclude_sid):
            continue
        meta = r.get("meta") or {}
        if meta.get("phase") != phase or norm_path(meta.get("cwd")) != norm_path(cwd):
            continue
        sessions += 1
        spawns = (r.get("outcome_harness") or {}).get("spawns") or {}
        runs += sum(int(v or 0) for k, v in spawns.items() if k in REVIEW_ROLES)
    return runs, sessions


# ---------------------------------------------------------------- フック判定 JSONL と review-log(第6波 A6-20 / D073 open)

def _hook_log_module(root=None):
    """判定ログ JSONL(+旧 TSV)の読み手 .github/hooks/scripts/_log.py を読み込む。渡された root に無ければ
    本ツール自身のハーネス(ROOT)のものを使う(受領書の一次データだけ別ルートに置く selftest 用)。どちらにも
    無い配布先では None(hooks 欄は source=unavailable)。"""
    import importlib.util
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
        except Exception:
            return None
    return None


def _local_naive(ts):
    """ISO8601(Z / オフセット付き)をローカル時刻の naive 文字列(YYYY-MM-DDTHH:MM:SS)にする。旧 TSV 判定ログの
    時刻はローカル naive で書かれていたため、時間窓の比較はこの形に揃える(近似。JSONL は session_id で絞る)。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def hook_decisions_for_session(logs, session_id, since=None, until=None, root=None):
    """フック判定(deny / ask / warn / block / allow)の件数。logs(= logs/usage)の親にある hook-decisions.jsonl を
    session_id で絞り、旧 TSV(hook-decisions.log。session_id 無し)は since〜until のローカル時刻窓で数える
    (後方互換の近似)。source は jsonl / tsv(time-window) / jsonl+tsv / none / unavailable(読み手 _log.py が無い)。
    件数 0 と「記録経路が無い」を区別するため source を必ず残す。"""
    base = {"deny": 0, "ask": 0, "warn": 0, "block": 0, "allow": 0, "records": 0, "source": "none", "by_script": {},
            "duration_n": 0, "duration_p50_ms": None, "duration_p95_ms": None, "by_host": {}}
    mod = _hook_log_module(root)
    if mod is None:
        base["source"] = "unavailable"
        return base
    try:
        summ = mod.count_for_session(session_id, os.path.dirname(os.path.abspath(logs)), since=since, until=until)
    except Exception:
        return base
    counts = summ.get("counts") or {}
    for k in ("deny", "ask", "warn", "block", "allow"):
        base[k] = int(counts.get(k) or 0)
    base["records"] = int(summ.get("records") or 0)
    base["source"] = summ.get("source") or "none"
    # 所要(duration_ms。R-09 / A2-7): P50 / P95 は _log.py の percentile(最近傍順位法)。旧行(欄なし)は数えない
    base["duration_n"] = int(summ.get("duration_n") or 0)
    base["duration_p50_ms"] = summ.get("duration_p50_ms")
    base["duration_p95_ms"] = summ.get("duration_p95_ms")
    base["by_host"] = dict(summ.get("by_host") or {})
    shown = ("deny", "ask", "warn", "block")
    for script, per in (summ.get("by_script") or {}).items():
        sub = {d: int(n) for d, n in per.items() if d in shown and n}
        if sub:
            base["by_script"][script] = sub
    return base


def review_log_entries(cwd, phase=None):
    """docs/04-test/review-log.md の日時付きエントリ数(見出し「### YYYY-MM-DD HH:MM / フェーズ / …」。D073 の
    出力契約)。phase(implementation / test)を与えると見出し 2 欄目が一致するものだけ数える。ファイルが無ければ 0。
    Copilot 経路や resume 後は reviewer の spawn が transcript に残らないため、工程累計は spawn 記録とこの数の
    大きい方を採る(D073 open)。"""
    try:
        text = open(os.path.join(cwd, REVIEW_LOG_REL), encoding="utf-8", errors="replace").read()
    except OSError:
        return 0
    n = 0
    for line in text.splitlines():
        if not REVIEW_LOG_ENTRY_RE.match(line):
            continue
        if phase:
            parts = [p.strip() for p in line.split("/")]
            if len(parts) < 2 or parts[1] != phase:
                continue
        n += 1
    return n


# ---------------------------------------------------------------- 受領書の組み立て

def aggregate_tokens(transcript_path, logs=None, sid=None):
    """main + subagents を集計し (tokens rows, facts, coverage, sub summaries) を返す。"""
    main = scan_transcript(transcript_path)
    sub_log = read_subagent_log(logs, sid) if (logs and sid) else []
    sub_log_types = {r.get("agent_id"): r.get("agent_type") for r in sub_log
                     if r.get("agent_id") and r.get("agent_type")}
    totals = {}
    subs = []

    def add(usage, agent, source, depth):
        for model, vals in usage.values():
            acc = totals.setdefault((agent, model, source, depth), [0, 0, 0, 0, 0])
            for i, v in enumerate(vals):
                acc[i] += v

    add(main["usage"], "main", "main", 0)
    parsed, total = main["parsed"], main["total"]
    for p in subagent_files(transcript_path):
        sf = scan_transcript(p)
        aid, label, depth, src = label_subagent(p, main, sf, sub_log_types)
        add(sf["usage"], label, "subagent", depth)
        parsed += sf["parsed"]
        total += sf["total"]
        subs.append({"agent_id": aid, "type": label, "depth": depth, "label_source": src, "facts": sf})
    rows = []
    for (agent, model, source, depth), v in sorted(totals.items()):
        rows.append({"agent": agent, "model": model, "query_source": source, "spawn_depth": depth,
                     "input": v[0], "output": v[1], "cache_read": v[2], "cache_w5m": v[3], "cache_w1h": v[4]})
    coverage = {"tokens": "transcript" if rows else "none", "parsed": parsed, "total": total,
                "ratio": round(parsed / total, 3) if total else None}
    return rows, main, coverage, subs, sub_log


def estimate_cost(rows, prices):
    amount, unknown, fallback = 0.0, set(), set()
    for r in rows:
        p, fb = price_of(r["model"], prices)
        if p is None:
            unknown.add(r["model"])
            continue
        if fb:
            fallback.add(r["model"])
        amount += row_cost([r[c] for c in TOKEN_COLS], p)
    return amount, sorted(unknown), sorted(fallback)


def cost_breakdown(rows, prices):
    """種別(input/output/cache_read/cache_w5m/cache_w1h)ごとの推定 USD。"""
    out = {c: 0.0 for c in TOKEN_COLS}
    for r in rows:
        p, _ = price_of(r["model"], prices)
        if p is None:
            continue
        for i, c in enumerate(TOKEN_COLS):
            vals = [0, 0, 0, 0, 0]
            vals[i] = r[c]
            out[c] += row_cost(vals, p)
    return out


def detect_phase(main):
    attr = [(k, v) for k, v in main["attribution"].items() if PHASE_KEY_RE.match(k)]
    total = sum(main["attribution"].values())
    skills = {k: round(v / total, 2) for k, v in main["attribution"].most_common()} if total else {}
    if attr:
        attr.sort(key=lambda kv: -kv[1])
        return attr[0][0], "attributionSkill", skills
    for c in main["commands"]:
        if PHASE_KEY_RE.match(c):
            return c, "first_command", skills
    return "other", "none", skills


def pick_baseline(phase, cwd, cfg):
    """docs/06-retrospective/baselines.json(effort-report が生成)を優先し、無ければ config の暫定値。"""
    data = load_json(os.path.join(cwd, BASELINES_REL))
    min_n = int(cfg.get("baseline_min_n") or 5)
    if isinstance(data, dict):
        entry = (data.get("phases") or {}).get(phase) or data.get("all")
        if isinstance(entry, dict) and entry.get("n"):
            return {"phase": phase if (data.get("phases") or {}).get(phase) else "all",
                    "n": entry["n"], "cost_p50": entry.get("cost_p50"), "cost_p90": entry.get("cost_p90"),
                    "cost_max": entry.get("cost_max"), "provisional": int(entry["n"]) < min_n,
                    "source": "baselines.json"}
    prov = cfg.get("provisional_baselines") or {}
    entry = (prov.get("phases") or {}).get(phase) or prov.get("all")
    if isinstance(entry, dict) and entry.get("n"):
        return {"phase": phase if (prov.get("phases") or {}).get(phase) else "all",
                "n": entry["n"], "cost_p50": entry.get("cost_p50"), "cost_p90": entry.get("cost_p90"),
                "cost_max": entry.get("cost_max"), "provisional": True, "source": "usage-config.json(暫定)"}
    return None


def build_receipt(session_id, transcript_path, cwd, root=None, payload=None, capture="Stop(暫定)"):
    """受領書 dict を組み立てる(書き込みはしない)。全経路がこの関数を使う。"""
    root = root or ROOT
    cfg = load_config(root)
    prices = load_prices(root)
    logs = logs_dir(root)
    payload = payload or {}
    rows, main, coverage, subs, sub_log = aggregate_tokens(transcript_path, logs, session_id)
    status = read_status(logs, session_id)
    baseline = read_baseline(logs, session_id)
    phase, phase_source, skills = detect_phase(main)
    est, unknown, fallback = estimate_cost(rows, prices)

    # --- host(公式・statusline 由来のみ)
    host_cost = host_pct = host_lines = None
    host_version = host_model = host_effort = None
    ctx_size = exceeds_200k = prompt_cache = None
    if status:
        cost = status.get("cost") if isinstance(status.get("cost"), dict) else {}
        cw = status.get("context_window") if isinstance(status.get("context_window"), dict) else {}
        host_cost = cost.get("total_cost_usd")
        host_lines = (cost.get("total_lines_added"), cost.get("total_lines_removed"))
        host_pct = cw.get("used_percentage")
        ctx_size = cw.get("context_window_size")
        exceeds_200k = status.get("exceeds_200k_tokens")
        host_version = status.get("version")
        mdl = status.get("model")
        host_model = mdl.get("id") if isinstance(mdl, dict) else mdl
        eff = status.get("effort")
        host_effort = eff.get("level") if isinstance(eff, dict) else eff
        pc = status.get("prompt_cache")
        if isinstance(pc, dict):
            prompt_cache = {k: pc.get(k) for k in ("hit_ratio", "requests", "misses", "warm") if k in pc}
    versions = sorted(main["versions"])
    version = host_version or (versions[-1] if versions else None)
    eff_payload = payload.get("effort")
    if isinstance(eff_payload, dict):
        eff_payload = eff_payload.get("level")
    effort = host_effort or eff_payload or (main["effort"].most_common(1)[0][0] if main["effort"] else None)
    model_main = host_model or next((r["model"] for r in rows if r["agent"] == "main"), None)
    entry = sorted(main["entrypoints"])
    product = {"claude-vscode": "claude-vscode", "sdk-cli": "claude-p"}.get(entry[0], "claude-code") if entry else "claude-code"

    # --- 成果(量)
    gate_after = parse_gate(os.path.join(cwd, GATE_REL))
    gate_before = baseline.get("gate") if baseline else None
    transitions, rework = [], 0
    if isinstance(gate_before, dict) and isinstance(gate_after, dict):
        for k in GATE_PHASES:
            b, a = gate_state(gate_before.get(k)), gate_state(gate_after.get(k))
            if b != a and (b or a):
                transitions.append(f"{k}:{b or '-'}->{a or '-'}")
                if b == "done" and a in ("in_progress", "pending_approval"):
                    rework += 1
    tasks_after = tasks_done_count(os.path.join(cwd, TASKS_REL))
    tasks_before = baseline.get("tasks_done") if baseline else None
    tasks_delta = (tasks_after - tasks_before) if (tasks_after is not None and isinstance(tasks_before, int)) else None
    commits = git_count(cwd, baseline.get("head") if baseline else None)
    edit_paths = set(main["edit_paths"])
    for s in subs:
        edit_paths |= s["facts"]["edit_paths"]

    # --- 成果(整合)
    test_runs = list(main["test_runs"])
    golden = list(main["golden"])
    check_py = list(main["check_py"])
    for s in subs:
        test_runs += s["facts"]["test_runs"]
        golden += s["facts"]["golden"]
        check_py += s["facts"]["check_py"]
    observed = next((t["observed"] for t in reversed(test_runs) if t.get("observed")), None)
    if golden:
        g = golden[-1]
        tests = {"ran": True, "runs": len(test_runs), "result": "pass" if (g["declared"] > 0 and g["passed"] == g["declared"]) else "fail",
                 "source": "golden-eval", "declared": g["declared"], "passed": g["passed"], "observed": observed}
    elif check_py:
        tests = {"ran": True, "runs": len(test_runs), "result": "pass" if all(check_py) else "fail",
                 "source": "check.py", "observed": observed}
    else:
        tests = {"ran": bool(test_runs), "runs": len(test_runs), "result": "unknown",
                 "source": "post-tool-bash" if test_runs else "none", "observed": observed}
    spawns = Counter(v["type"] or "unknown" for v in main["agent_use"].values())
    resolved = {}
    for aid, info in main["agents"].items():
        t = info.get("type") or "?"
        if info.get("resolvedModel"):
            resolved.setdefault(t, set()).add(info["resolvedModel"])
    for r in sub_log:
        if r.get("agent_type") and r.get("resolved_model"):
            resolved.setdefault(r["agent_type"], set()).add(r["resolved_model"])
    requested = sorted({(v.get("model") or "inherit") for v in main["agent_use"].values()})
    other_runs, other_sessions = phase_review_runs(logs, phase, cwd, session_id)
    review_runs_here = sum(v for k, v in spawns.items() if k in REVIEW_ROLES)
    # 工程累計は spawn 記録(受領書 + 当セッション)と review-log.md の日時付きエントリ数の大きい方(D073 open。
    # spawn が transcript に残らない経路でも独立レビューの一次記録があれば review_less_done にしない)
    review_log_n = review_log_entries(cwd)
    review_runs_phase = max(other_runs + review_runs_here, review_log_n)
    # フック判定(deny/ask/warn/block)。JSONL は session_id、旧 TSV はセッション開始以降の時間窓(A6-20)
    hooks = hook_decisions_for_session(logs, session_id, root=root,
                                       since=_local_naive(main["first_ts"] or (baseline or {}).get("ts")))
    fast_track = fast_track_active(os.path.join(cwd, GATE_REL))
    done_here = any(t.endswith("->done") for t in transitions)
    review_less_done = bool(done_here and review_runs_phase == 0 and not fast_track)
    review_ratio = (round(review_runs_phase / tasks_after, 2) if tasks_after else None)

    # --- 比較・フラグ
    cache_total = sum(r["cache_read"] + r["cache_w5m"] + r["cache_w1h"] for r in rows)
    cache_read_ratio = (round(sum(r["cache_read"] for r in rows) / cache_total, 3) if cache_total else None)
    breakdown = cost_breakdown(rows, prices)
    # 判定は「課金相当」の比率(Sonar 実測: 請求の 98% がキャッシュ再読)。トークン比は情報表示のみ
    cache_read_cost_share = (round(breakdown["cache_read"] / est, 3) if est > 0 else None)
    total_tokens = sum(sum(r[c] for c in TOKEN_COLS) for r in rows)
    resumed = bool(baseline and baseline.get("source") in ("resume", "fork"))
    divergence = None
    if isinstance(host_cost, (int, float)) and host_cost > 0 and not resumed:
        divergence = round(abs(est - host_cost) / host_cost, 3)
    bl = pick_baseline(phase, cwd, cfg)
    position = "n/a"
    if bl and isinstance(bl.get("cost_p90"), (int, float)):
        position = "above_p90" if est > bl["cost_p90"] else ("above_p50" if isinstance(bl.get("cost_p50"), (int, float)) and est > bl["cost_p50"] else "within")
    warn_pct = float(cfg.get("context_warn_pct") or 40)
    flags = []
    if review_less_done:
        flags.append("review_less_done")
    if divergence is not None and divergence > float(cfg.get("price_divergence_warn") or 0.1):
        flags.append("price_table_stale")
    if version and version_tuple(version) and version_tuple(version) < version_tuple(cfg.get("host_min_version") or "0"):
        flags.append("host_below_min_version")
    if resumed:
        flags.append("resumed")
    if coverage["ratio"] is not None and coverage["ratio"] < 0.8:
        flags.append("low_parse_coverage")
    if isinstance(host_pct, (int, float)) and host_pct >= warn_pct:
        flags.append("context_high")
    if cache_read_cost_share is not None and cache_read_cost_share >= float(cfg.get("cache_read_ratio_warn") or 0.95):
        flags.append("cache_read_high")
    if position == "above_p90":
        flags.append("above_p90")
    if not status:
        flags.append("no_statusline")
    if unknown:
        flags.append("price_unknown_model")
    if fallback:
        flags.append("price_family_fallback")
    if rework:
        flags.append("rework")

    return {
        "schema": SCHEMA,
        "meta": {
            "session_id": session_id, "cwd": cwd, "platform": "claude-code",
            "started": main["first_ts"] or (baseline or {}).get("ts"), "ended": None,
            "captured_at": utc_now(), "capture": capture,
            "host": {"product": product, "version": version, "model_main": model_main, "effort": effort,
                     "entrypoints": entry, "harness_commit": harness_commit(cwd)},
            "phase": phase, "phase_source": phase_source, "skills_attributed": skills,
            "commands": main["commands"],
            "coverage": dict(coverage, status_source="statusline" if status else "none",
                             status_captured_at=(status or {}).get("_captured_at"), resumed=resumed),
        },
        "tokens": rows,
        "cost": {"amount": round(est, 4), "cost_unit": "usd", "basis": "list", "is_estimate": True,
                 "price_table_date": prices.get("as_of"), "unknown_models": unknown, "fallback_models": fallback,
                 "breakdown": {k: round(v, 4) for k, v in breakdown.items()},
                 "cache_read_cost_share": cache_read_cost_share,
                 "host_amount": host_cost, "host_basis": f"list(host v{version})" if host_cost is not None else None,
                 "divergence_ratio": divergence,
                 "note": "list 価格の推定。請求額ではない(サブスクは無関係)。host は /clear でリセット、resume 後は再開以降のみ"},
        "context": {"used_percentage": host_pct, "context_window_size": ctx_size,
                    "exceeds_200k_tokens": exceeds_200k, "prompt_cache": prompt_cache,
                    "cache_read_ratio": cache_read_ratio},
        "outcome_common": {
            "lines_added": host_lines[0] if host_lines else None,
            "lines_removed": host_lines[1] if host_lines else None,
            "files_changed": len(edit_paths), "commits": commits,
            "num_turns": len(main["assistant_ids"]), "user_prompts": main["user_prompts"],
            "tests": tests, "total_tokens": total_tokens,
        },
        "outcome_harness": {
            "gate_before": gate_before, "gate_after": gate_after, "gate_transitions": transitions,
            "tasks_done_before": tasks_before, "tasks_done_after": tasks_after, "tasks_done_delta": tasks_delta,
            "spawns": dict(spawns), "requested_models": requested,
            "resolved_models": {k: sorted(v) for k, v in resolved.items()},
            "subagent_label_sources": Counter(s["label_source"] for s in subs),
            "review_runs_session": review_runs_here, "review_runs_phase": review_runs_phase,
            "review_sessions_phase": other_sessions + 1, "review_ratio": review_ratio,
            "review_less_done": review_less_done, "fast_track": fast_track, "rework_cycles": rework,
            "review_log_entries": review_log_n, "hooks": hooks,
        },
        "baseline": bl and dict(bl, position=position),
        "flags": flags,
    }


def receipt_from_result(result, session_id=None, root=None):
    """`claude -p --output-format json` の result から同スキーマの受領書を作る(-p / e2e 経路)。"""
    root = root or ROOT
    prices = load_prices(root)
    rows = []
    mu = result.get("modelUsage") if isinstance(result.get("modelUsage"), dict) else {}
    basis = "unknown"
    for model, u in mu.items():
        if not isinstance(u, dict):
            continue
        rows.append({"agent": "main+subagents", "model": model, "query_source": "result", "spawn_depth": 0,
                     "input": int(u.get("inputTokens") or 0), "output": int(u.get("outputTokens") or 0),
                     "cache_read": int(u.get("cacheReadInputTokens") or 0),
                     "cache_w5m": int(u.get("cacheCreationInputTokens") or 0), "cache_w1h": 0})
        if u.get("costBasis"):
            basis = u["costBasis"]
    est, unknown, fallback = estimate_cost(rows, prices)
    host_cost = result.get("total_cost_usd")
    sid = session_id or result.get("session_id") or "unknown"
    denials = result.get("permission_denials")
    return {
        "schema": SCHEMA,
        "meta": {"session_id": sid, "cwd": None, "platform": "claude-p", "started": None, "ended": None,
                 "captured_at": utc_now(), "capture": "result-json",
                 "host": {"product": "claude-p", "version": None, "model_main": rows[0]["model"] if rows else None,
                          "effort": None, "entrypoints": ["sdk-cli"], "harness_commit": None},
                 "phase": "other", "phase_source": "none", "skills_attributed": {}, "commands": [],
                 "coverage": {"tokens": "result" if rows else "none", "parsed": None, "total": None,
                              "ratio": None, "status_source": "result", "resumed": False}},
        "tokens": rows,
        "cost": {"amount": round(est, 4), "cost_unit": "usd", "basis": "list", "is_estimate": True,
                 "price_table_date": prices.get("as_of"), "unknown_models": unknown, "fallback_models": fallback,
                 "host_amount": host_cost, "host_basis": basis,
                 "divergence_ratio": (round(abs(est - host_cost) / host_cost, 3) if isinstance(host_cost, (int, float)) and host_cost > 0 else None),
                 "note": "-p の result。usage 単独はサブエージェント分を含まないため modelUsage を使う"},
        "context": {"used_percentage": None, "context_window_size": None, "exceeds_200k_tokens": None,
                    "prompt_cache": None, "cache_read_ratio": None},
        "outcome_common": {"lines_added": None, "lines_removed": None, "files_changed": None, "commits": None,
                           "num_turns": result.get("num_turns"), "user_prompts": None,
                           "tests": {"ran": False, "runs": 0, "result": "unknown", "source": "none", "observed": None},
                           "total_tokens": sum(sum(r[c] for c in TOKEN_COLS) for r in rows),
                           "subtype": result.get("subtype"), "is_error": result.get("is_error"),
                           "permission_denials": len(denials) if isinstance(denials, list) else None},
        "outcome_harness": {"gate_before": None, "gate_after": None, "gate_transitions": [], "tasks_done_delta": None,
                            "spawns": {}, "requested_models": [], "resolved_models": {}, "review_runs_phase": 0,
                            "review_ratio": None, "review_less_done": False, "fast_track": False, "rework_cycles": 0},
        "baseline": None,
        "flags": (["price_unknown_model"] if unknown else []) + (["price_family_fallback"] if fallback else []),
    }


# ---------------------------------------------------------------- 表示

def _agent_summary(rows):
    per = {}
    for r in rows:
        acc = per.setdefault(r["agent"], [0, 0])
        acc[0] += sum(r[c] for c in TOKEN_COLS)
    counts = Counter(r["agent"] for r in rows)
    parts = []
    for agent, (tok, _) in sorted(per.items(), key=lambda kv: (kv[0] != "main", -kv[1][0])):
        n = counts[agent]
        parts.append(f"{agent}{'x' + str(n) if agent != 'main' and n > 1 else ''} {fmt_tokens(tok)}")
    return " / ".join(parts)


def render_short(receipt, cfg=None):
    """Stop の systemMessage 用: 3 行以内・絵文字なし。"""
    cfg = cfg or {}
    m, c, ctx = receipt["meta"], receipt["cost"], receipt["context"]
    oc, oh, bl = receipt["outcome_common"], receipt["outcome_harness"], receipt.get("baseline")
    host = m.get("host") or {}
    sid = (m.get("session_id") or "")[:8]
    l1 = (f"受領書 [{m.get('phase')}] sess {sid} · {host.get('model_main') or '?'} · "
          f"v{host.get('version') or '?'} · 採取: {m.get('capture')}")
    tot = {k: sum(r[k] for r in receipt["tokens"]) for k in TOKEN_COLS}
    cov = m.get("coverage") or {}
    ratio = cov.get("ratio")
    host_cost = c.get("host_amount")
    cost_part = (f"費用 host {fmt_usd(host_cost)} [公式・list価格・請求額ではない]" if host_cost is not None
                 else "費用 host n/a [statusline 未取得]")
    cost_part += f" / 推定 {fmt_usd(c.get('amount'))} [prices.json {c.get('price_table_date')}]"
    if "price_table_stale" in receipt.get("flags", []):
        cost_part += " (乖離>閾値: price_table_stale)"
    pct = ctx.get("used_percentage")
    ctx_part = f"文脈 {pct:.0f}% [公式]" if isinstance(pct, (int, float)) else "文脈 n/a"
    tok_part = (f"トークン [transcript 解析率 {ratio * 100:.0f}%] in {fmt_tokens(tot['input'])} / out {fmt_tokens(tot['output'])}"
                f" / cache read {fmt_tokens(tot['cache_read'])} / write {fmt_tokens(tot['cache_w5m'] + tot['cache_w1h'])}"
                f" ({_agent_summary(receipt['tokens'])})" if ratio is not None and receipt["tokens"]
                else "トークン n/a")
    l2 = f"{cost_part} · {ctx_part} · {tok_part}"
    trans = ", ".join(oh.get("gate_transitions") or []) or "GATE 変化なし"
    td = oh.get("tasks_done_delta")
    la, lr = oc.get("lines_added"), oc.get("lines_removed")
    lines_part = f"+{la}/-{lr} 行 [公式]" if la is not None else "行数 n/a"
    tests = oc.get("tests") or {}
    tests_part = (f"tests {tests.get('runs', 0)}回 {tests.get('result')} [{tests.get('source')}]" if tests.get("ran")
                  else "tests 未検出")
    spawns = oh.get("spawns") or {}
    rev = f"reviewer×{spawns.get('reviewer', 0)} (工程累計 {oh.get('review_runs_phase', 0)})"
    if oh.get("review_less_done"):
        rev += " review_less_done"
    hk = oh.get("hooks") or {}
    hk_part = f"hooks deny {hk.get('deny', 0)}/ask {hk.get('ask', 0)}/warn {hk.get('warn', 0)}"
    if bl:
        bl_part = (f"基準線 {bl.get('phase')} p50 {fmt_usd(bl.get('cost_p50'))} / p90 {fmt_usd(bl.get('cost_p90'))}"
                   f" (n={bl.get('n')}{'・暫定' if bl.get('provisional') else ''}) → "
                   + {"within": "範囲内", "above_p50": "中央値超", "above_p90": "p90 超"}.get(bl.get("position"), "n/a"))
    else:
        bl_part = "基準線 n/a(effort-report.py で生成)"
    l3 = (f"成果 {trans} · tasks [x] {'+' + str(td) if isinstance(td, int) else 'n/a'} · {lines_part}"
          f" · commit {oc.get('commits') if oc.get('commits') is not None else 'n/a'} · {tests_part} · {rev} · {hk_part} · {bl_part}")
    warn_pct = float(cfg.get("context_warn_pct") or 40)
    if (isinstance(pct, (int, float)) and pct >= warn_pct) or (bl and bl.get("position") == "above_p90"):
        l3 += " → 次のゲートで新セッションを推奨(D057: 分割で -29% の実測)"
    return "\n".join([l1, l2, l3])


def render_full(receipt, cfg=None):
    m, c, ctx = receipt["meta"], receipt["cost"], receipt["context"]
    oc, oh, bl = receipt["outcome_common"], receipt["outcome_harness"], receipt.get("baseline")
    host = m.get("host") or {}
    cov = m.get("coverage") or {}
    out = [render_short(receipt, cfg).splitlines()[0]]
    out.append(f"ホスト: {host.get('product')} v{host.get('version') or '?'} · model {host.get('model_main') or '?'}"
               f" · effort {host.get('effort') or '?'} · 工程判定 {m.get('phase_source')} · コマンド {', '.join(m.get('commands') or []) or '-'}")
    div = c.get("divergence_ratio")
    out.append(f"費用: host {fmt_usd(c.get('host_amount'))} [公式・list価格・請求額ではない・{c.get('host_basis') or 'n/a'}]"
               f" · 自前推定 {fmt_usd(c.get('amount'))} [prices.json {c.get('price_table_date')}]"
               f" · 乖離 {f'{div * 100:.0f}%' if div is not None else 'n/a'}"
               + (f" · 単価未登録 {', '.join(c.get('unknown_models'))}" if c.get("unknown_models") else "")
               + (f" · family fallback {', '.join(c.get('fallback_models'))}" if c.get("fallback_models") else ""))
    pct = ctx.get("used_percentage")
    pc = ctx.get("prompt_cache") or {}
    share = c.get("cache_read_cost_share")
    # Python 3.11 互換のため f-string の入れ子(同一引用符)を避け、値を先に組み立てる(CI は 3.11)
    pct_s = f"{pct:.0f}%" if isinstance(pct, (int, float)) else "n/a"
    win_s = fmt_tokens(ctx.get("context_window_size")) if ctx.get("context_window_size") else "n/a"
    hit = pc.get("hit_ratio")
    hit_s = f"{hit * 100:.0f}%" if isinstance(hit, (int, float)) else "n/a(2.1.251+)"
    crr = ctx.get("cache_read_ratio")
    crr_s = crr if crr is not None else "n/a"
    share_s = f"{share * 100:.0f}%" if isinstance(share, (int, float)) else "n/a"
    out.append(f"文脈: {pct_s} [公式] · 窓 {win_s}"
               f" · cache命中 {hit_s}"
               f" · cache_read 比 トークン {crr_s}"
               f" / 費用 {share_s}")
    ratio = cov.get("ratio")
    out.append(f"トークン [{cov.get('tokens')}・解析率 {f'{ratio * 100:.0f}%' if ratio is not None else 'n/a'}"
               f" ({cov.get('parsed')}/{cov.get('total')})]:")
    prices = load_prices()
    for r in receipt["tokens"]:
        p, fb = price_of(r["model"], prices)
        cost = row_cost([r[k] for k in TOKEN_COLS], p) if p else None
        out.append(f"  {r['agent']:<16} {r['model']:<20} in {fmt_tokens(r['input'])} / out {fmt_tokens(r['output'])}"
                   f" / cache read {fmt_tokens(r['cache_read'])} / w5m {fmt_tokens(r['cache_w5m'])} / w1h {fmt_tokens(r['cache_w1h'])}"
                   f"  {fmt_usd(cost)}{' (fallback)' if fb else ''}")
    spawns = oh.get("spawns") or {}
    resolved = oh.get("resolved_models") or {}
    out.append("委譲: " + (" · ".join(f"{k}×{v}" for k, v in sorted(spawns.items())) or "なし")
               + f" · 要求 {', '.join(oh.get('requested_models') or []) or '-'} → 実行 "
               + (", ".join(f"{k}:{'/'.join(v)}" for k, v in sorted(resolved.items())) or "n/a") + " [toolUseResult/PostToolUse]")
    trans = ", ".join(oh.get("gate_transitions") or []) or "GATE 変化なし"
    na = lambda v: "n/a" if v is None else v  # noqa: E731
    lines_part = (f"+{oc.get('lines_added')}/-{oc.get('lines_removed')} 行 [公式]" if oc.get("lines_added") is not None
                  else "行数 n/a [statusline 未取得]")
    out.append(f"成果(量・代理指標): {trans} · tasks [x] {na(oh.get('tasks_done_before'))}→{na(oh.get('tasks_done_after'))}"
               f" ({'+' + str(oh.get('tasks_done_delta')) if isinstance(oh.get('tasks_done_delta'), int) else 'n/a'})"
               f" · {lines_part} · 変更ファイル {na(oc.get('files_changed'))} [transcript]"
               f" · commit {na(oc.get('commits'))} [git] · turns {na(oc.get('num_turns'))} / 発話 {na(oc.get('user_prompts'))}")
    t = oc.get("tests") or {}
    obs = t.get("observed") or {}
    _rr = oh.get("review_ratio")
    _rr_s = f"{_rr * 100:.0f}%" if isinstance(_rr, (int, float)) else "n/a"
    out.append(f"成果(整合): tests ran {t.get('runs', 0)} · result {t.get('result')} [{t.get('source')}]"
               + (f" (最終観測: {obs.get('passed', '?')} passed / {obs.get('failed', 0)} failed・ヒューリスティック)" if obs else "")
               + (f" · golden-eval {t.get('passed')}/{t.get('declared')}" if t.get("source") == "golden-eval" else "")
               + f" · reviewer 工程累計 {oh.get('review_runs_phase', 0)} / done タスク {na(oh.get('tasks_done_after'))}"
               + f" → レビュー済み率 {_rr_s}"
               + (" ⚠ review_less_done" if oh.get("review_less_done") else "")
               + f" · 差し戻し {oh.get('rework_cycles', 0)}" + (" · ライトパス" if oh.get("fast_track") else ""))
    hk = oh.get("hooks") or {}
    by_s = ", ".join(sc + ":" + "/".join(f"{d}{n}" for d, n in sorted(v.items()))
                     for sc, v in sorted((hk.get("by_script") or {}).items()))
    out.append(f"フック判定: deny {hk.get('deny', 0)} · ask {hk.get('ask', 0)} · warn {hk.get('warn', 0)} · block {hk.get('block', 0)}"
               f" [hook-decisions.jsonl・{hk.get('source', 'n/a')}]" + (f" · {by_s}" if by_s else "")
               + f" · review-log エントリ {oh.get('review_log_entries', 0)}"
               + (f" · 所要 P50 {hk.get('duration_p50_ms')} / P95 {hk.get('duration_p95_ms')} ms (n={hk.get('duration_n')})"
                  if hk.get("duration_n") else " · 所要 未計測"))
    if bl:
        out.append(f"基準線: {bl.get('phase')} p50 {fmt_usd(bl.get('cost_p50'))} / p90 {fmt_usd(bl.get('cost_p90'))}"
                   f" (n={bl.get('n')}{'・暫定' if bl.get('provisional') else ''}・{bl.get('source')}) → {bl.get('position')}")
    else:
        out.append("基準線: n/a(python tools/effort-report.py で docs/06-retrospective/baselines.json を生成)")
    out.append("フラグ: " + (", ".join(receipt.get("flags") or []) or "なし"))
    return "\n".join(out)


# ---------------------------------------------------------------- トリガと永続化(Stop / SessionEnd)

def draft_path(logs, sid):
    return os.path.join(logs, safe_sid(sid) + ".receipt.draft.json")


def final_path(logs, sid):
    return os.path.join(logs, safe_sid(sid) + ".receipt.json")


def initial_state(receipt):
    oh = receipt["outcome_harness"]
    return {"gate": oh.get("gate_before"), "phase_commands": 0, "cost": 0.0, "tokens": 0,
            "user_prompts": 0, "context_warned": False, "pending_show": False, "shown_count": 0}


def decide_show(receipt, prev_state, cfg, payload, block_turn=False):
    """(表示するか, 理由, 新しい状態)。区切り条件を満たすときだけ表示する。block は決してしない。"""
    payload = payload or {}
    prev = dict(prev_state or initial_state(receipt))
    reasons = []
    oh, oc = receipt["outcome_harness"], receipt["outcome_common"]
    phase_cmds = sum(1 for c in (receipt["meta"].get("commands") or []) if PHASE_KEY_RE.match(c))
    host_cost = receipt["cost"].get("host_amount")
    cost_now = float(host_cost) if isinstance(host_cost, (int, float)) else float(receipt["cost"].get("amount") or 0)
    tokens_now = int(oc.get("total_tokens") or 0)
    prompts_now = int(oc.get("user_prompts") or 0)
    pct = receipt["context"].get("used_percentage")
    warn_pct = float(cfg.get("context_warn_pct") or 40)

    # 基準(前回表示時 or SessionStart の baseline)が無いときは GATE 変化を主張しない(初回 Stop の偽陽性防止)
    if prev.get("gate") is not None and oh.get("gate_after") != prev.get("gate"):
        reasons.append("gate_changed")
    if phase_cmds > int(prev.get("phase_commands") or 0):
        reasons.append("phase_command")
    if cost_now - float(prev.get("cost") or 0) >= float(cfg.get("cost_delta_usd") or 2):
        reasons.append("cost_delta")
    if tokens_now - int(prev.get("tokens") or 0) >= int(cfg.get("token_delta") or 500000):
        reasons.append("token_delta")
    if prompts_now - int(prev.get("user_prompts") or 0) >= int(cfg.get("turns_every") or 10):
        reasons.append("turns")
    if isinstance(pct, (int, float)) and pct >= warn_pct and not prev.get("context_warned"):
        reasons.append("context")
    if prev.get("pending_show"):
        reasons.append("carried_over")

    hold = None
    if payload.get("stop_hook_active"):
        hold = "stop_hook_active"
    elif payload.get("background_tasks"):
        hold = "background_tasks"
    elif "last_assistant_message" in payload and not str(payload.get("last_assistant_message") or "").strip():
        hold = "empty_last_assistant_message"
    elif block_turn:
        hold = "block_turn"
    if hold:
        new = dict(prev, pending_show=bool(reasons) or bool(prev.get("pending_show")))
        return False, [hold] + reasons, new
    if not reasons:
        return False, [], prev
    new = {"gate": oh.get("gate_after"), "phase_commands": phase_cmds, "cost": cost_now, "tokens": tokens_now,
           "user_prompts": prompts_now, "context_warned": bool(prev.get("context_warned")) or
           (isinstance(pct, (int, float)) and pct >= warn_pct),
           "pending_show": False, "shown_count": int(prev.get("shown_count") or 0) + 1, "last_shown_at": utc_now(),
           "last_reasons": reasons}
    return True, reasons, new


def write_draft(receipt, state, logs):
    data = dict(receipt)
    data["_state"] = state
    return write_json_atomic(draft_path(logs, receipt["meta"]["session_id"]), data)


def finalize(sid, logs, reason=None):
    """SessionEnd: draft→確定 rename と sessions.jsonl への 1 行追記だけ(重い集計はしない)。"""
    src, dst = draft_path(logs, sid), final_path(logs, sid)
    data = load_json(src)
    if not isinstance(data, dict):
        return False
    data.pop("_state", None)
    data["meta"]["ended"] = utc_now()
    data["meta"]["capture"] = "SessionEnd(確定)"
    if reason:
        data["meta"]["end_reason"] = reason
    if not write_json_atomic(dst, data):
        return False
    try:
        os.remove(src)
    except OSError:
        pass
    try:
        line = {"session_id": sid, "phase": data["meta"].get("phase"), "ended": data["meta"]["ended"],
                "reason": reason, "cost_est": data["cost"].get("amount"), "host_cost": data["cost"].get("host_amount"),
                "total_tokens": data["outcome_common"].get("total_tokens"),
                "gate_transitions": data["outcome_harness"].get("gate_transitions")}
        with open(os.path.join(logs, "sessions.jsonl"), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return True


def latest_session(logs):
    """最終更新の status / draft / receipt から現在(直近)セッションの sid を推定する。"""
    best, best_t = None, -1
    for pat in ("*.status.json", "*.receipt.draft.json", "*.receipt.json"):
        for p in glob.glob(os.path.join(logs, pat)):
            try:
                t = os.path.getmtime(p)
            except OSError:
                continue
            if t > best_t:
                best_t = t
                base = os.path.basename(p)
                best = base.split(".")[0]
    return best


def load_or_build(sid, logs, root=None):
    """CLI 用: transcript が読めれば最新を再計算、無ければ保存済み受領書を返す。"""
    status = read_status(logs, sid)
    tp = (status or {}).get("transcript_path")
    ws = (status or {}).get("workspace") or {}
    cwd = ws.get("project_dir") or (status or {}).get("cwd")
    if tp and os.path.exists(tp) and cwd:
        return build_receipt(sid, tp, cwd, root=root, capture="cli(現時点)")
    for path in (final_path(logs, sid), draft_path(logs, sid)):
        data = load_json(path)
        if isinstance(data, dict):
            data.pop("_state", None)
            return data
    return None


# ---------------------------------------------------------------- CLI

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="会話ごとの受領書を表示する(正は logs/usage)")
    ap.add_argument("--session", default="current", help="current|latest|<session_id>")
    ap.add_argument("--summary", action="store_true", help="3行の短縮形(Stop の systemMessage と同じ)")
    ap.add_argument("--json", action="store_true", help="受領書 JSON を出力")
    ap.add_argument("--list", action="store_true", help="logs/usage にある受領書を一覧")
    ap.add_argument("--from-result", metavar="PATH", help="claude -p --output-format json の result から作る")
    ap.add_argument("--root", default=None, help="ハーネスのルート(既定: このファイルの親の親)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.selftest:
        return selftest()
    root = args.root or ROOT
    logs = logs_dir(root)
    cfg = load_config(root)
    if args.from_result:
        result = load_json(args.from_result)
        if not isinstance(result, dict):
            print(f"result JSON を読めません: {args.from_result}")
            return 2
        receipt = receipt_from_result(result, root=root)
        print(json.dumps(receipt, ensure_ascii=False, indent=1) if args.json else render_full(receipt, cfg))
        return 0
    if args.list:
        for sid, r in sorted(list_receipts(logs).items(), key=lambda kv: kv[1]["meta"].get("captured_at") or ""):
            m = r["meta"]
            print(f"{sid[:8]}  {m.get('phase'):<24} {m.get('capture'):<14} {fmt_usd(r['cost'].get('amount'))}  {m.get('captured_at')}")
        return 0
    sid = args.session
    if sid in ("current", "latest"):
        sid = latest_session(logs)
        if not sid:
            print("受領書がまだありません(logs/usage が空)。statusline / Stop フックの配線と、"
                  "docs/00-overview/progress.md の有無を確認してください。")
            return 1
    receipt = load_or_build(sid, logs, root)
    if not receipt:
        print(f"セッション {sid} の受領書が見つかりません。")
        return 1
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=1))
    elif args.summary:
        print(render_short(receipt, cfg))
    else:
        print(render_full(receipt, cfg))
    return 0


# ---------------------------------------------------------------- selftest(フィクスチャ 6 種)

def _entry(**kw):
    return json.dumps(kw, ensure_ascii=False)


def _usage_msg(mid, model, inp, out, cr=0, w5=0, w1=0, content=None, extra=None):
    msg = {"id": mid, "model": model, "role": "assistant",
           "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
                     "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}}}
    if content is not None:
        msg["content"] = content
    e = {"type": "assistant", "timestamp": "2026-09-09T01:00:00.000Z", "version": "2.1.234",
         "entrypoint": "claude-vscode", "message": msg}
    e.update(extra or {})
    return _entry(**e)


def _cmd(name):
    return _entry(type="user", timestamp="2026-09-09T00:59:00.000Z",
                  message={"role": "user", "content": f"<command-message>{name}</command-message>\n<command-name>/{name}</command-name>\n<command-args></command-args>"})


def _prompt(text):
    return _entry(type="user", timestamp="2026-09-09T01:00:00.000Z", message={"role": "user", "content": text})


def _tool_result(tid, text, extra=None):
    e = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                                  "content": [{"type": "text", "text": text}]}]}}
    e.update(extra or {})
    return _entry(**e)


def _mkproject(tmp, gate="implementation: in_progress", tasks_done=2):
    proj = os.path.join(tmp, "proj")
    os.makedirs(os.path.join(proj, "docs", "00-overview"))
    os.makedirs(os.path.join(proj, "docs", "03-implementation"))
    with open(os.path.join(proj, GATE_REL), "w", encoding="utf-8") as f:
        f.write(f"<!-- GATE_STATUS\nrequirements: done\ndesign: done\n{gate}\ntest: not_started\nrelease: not_started\n-->\n# progress\n")
    with open(os.path.join(proj, TASKS_REL), "w", encoding="utf-8") as f:
        f.write("# tasks\n" + "".join(f"- [x] TASK-{i}\n" for i in range(tasks_done)) + "- [ ] TASK-9\n")
    return proj


def _mkroot(tmp):
    """tools/prices.json・usage-config.json を持つ疑似ルート(本物の設定ファイルをコピー)。"""
    import shutil
    root = os.path.join(tmp, "root")
    os.makedirs(os.path.join(root, "tools"))
    for name in ("prices.json", "usage-config.json"):
        shutil.copyfile(os.path.join(ROOT, "tools", name), os.path.join(root, "tools", name))
    os.makedirs(logs_dir(root))
    return root


def selftest():
    import shutil
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    tmp = tempfile.mkdtemp(prefix="receipt-selftest-")
    try:
        root = _mkroot(tmp)
        logs = logs_dir(root)
        cfg = load_config(root)
        prices = load_prices(root)
        proj = _mkproject(tmp)
        tdir = os.path.join(tmp, "t")
        os.makedirs(os.path.join(tdir, "s1", "subagents"))

        # フィクスチャ1: 2フェーズ跨ぎ + サブエージェント(meta.json あり/なし) + テスト実行はサブエージェント内のみ
        main_path = os.path.join(tdir, "s1.jsonl")
        lines = [
            _cmd("05-implementation-plan"),
            _usage_msg("m1", "claude-fable-5", 100, 5, cr=50, w5=10, w1=20, extra={"attributionSkill": "05-implementation-plan", "effort": "xhigh"}),
            _usage_msg("m1", "claude-fable-5", 100, 50, cr=50, w5=10, w1=20, extra={"attributionSkill": "05-implementation-plan", "effort": "xhigh"}),  # 後勝ち
            _cmd("06-implement-task"),
            _prompt("TASK-3 を進めて"),
            _usage_msg("m2", "claude-fable-5", 1, 1, content=[{"type": "tool_use", "id": "t1", "name": "Agent",
                                                              "input": {"subagent_type": "task-worker", "prompt": "x"}}],
                       extra={"attributionSkill": "06-implement-task"}),
            _tool_result("t1", "done", extra={"toolUseResult": {"status": "completed", "agentId": "aaa111", "agentType": "task-worker",
                                                                "resolvedModel": "claude-fable-5", "usage": {"input_tokens": 1}}}),
            _usage_msg("m3", "claude-fable-5", 1, 1, content=[{"type": "tool_use", "id": "t2", "name": "Task",
                                                              "input": {"subagent_type": "reviewer"}}],
                       extra={"attributionSkill": "06-implement-task"}),
            _tool_result("t2", "launched. agentId: bbb222 (internal)"),
            _usage_msg("m4", "<synthetic>", 999, 999),
            _usage_msg("m5", "claude-fable-5", 1, 1, content=[{"type": "tool_use", "id": "t3", "name": "Edit",
                                                              "input": {"file_path": "D:/p/src/app.ts"}}],
                       extra={"attributionSkill": "06-implement-task"}),
            "{broken json line",
        ]
        with open(main_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        subdir = os.path.join(tdir, "s1", "subagents")
        with open(os.path.join(subdir, "agent-aaa111.jsonl"), "w", encoding="utf-8") as f:
            f.write(_usage_msg("s1", "claude-sonnet-5", 10, 20, content=[{"type": "tool_use", "id": "b1", "name": "Bash",
                                                                          "input": {"command": "cd x && npm test 2>&1 | tail -30"}}]) + "\n")
            f.write(_tool_result("b1", " Test Files  12 passed (12)\n      Tests  344 passed (344)\n") + "\n")
        with open(os.path.join(subdir, "agent-aaa111.meta.json"), "w", encoding="utf-8") as f:
            json.dump({"agentType": "task-worker", "spawnDepth": 1, "toolUseId": "t1"}, f)
        with open(os.path.join(subdir, "agent-bbb222.jsonl"), "w", encoding="utf-8") as f:  # meta.json なし → regex
            f.write(_usage_msg("s2", "claude-fable-5", 3, 4) + "\n")
        with open(os.path.join(subdir, "agent-ccc333.jsonl"), "w", encoding="utf-8") as f:  # attributionAgent 経路
            f.write(_usage_msg("s3", "claude-fable-5", 5, 6, extra={"attributionAgent": "spec-critic"}) + "\n")

        # baseline(startup) + statusline 永続化 JSON
        write_json_atomic(os.path.join(logs, "s1.baseline.json"),
                          {"session_id": "s1", "source": "startup", "ts": "2026-09-09T00:58:00Z",
                           "gate": {"requirements": "done", "design": "done", "implementation": "in_progress",
                                    "test": "not_started", "release": "not_started"}, "tasks_done": 0, "head": None})
        write_json_atomic(os.path.join(logs, "s1.status.json"),
                          {"session_id": "s1", "transcript_path": main_path, "cwd": proj,
                           "workspace": {"project_dir": proj}, "version": "2.1.234",
                           "model": {"id": "claude-fable-5", "display_name": "Fable"},
                           "cost": {"total_cost_usd": 3.5, "total_lines_added": 40, "total_lines_removed": 3},
                           "context_window": {"used_percentage": 62, "context_window_size": 200000},
                           "effort": {"level": "high"}})
        r = build_receipt("s1", main_path, proj, root=root, payload={"effort": {"level": "high"}})
        by = {(t["agent"], t["model"]): t for t in r["tokens"]}
        check("tokens: main 後勝ち重複排除 (in=103,out=53)", by.get(("main", "claude-fable-5"), {}).get("input") == 103
              and by[("main", "claude-fable-5")]["output"] == 53, str(by.get(("main", "claude-fable-5"))))
        check("tokens: synthetic を無視", all(k[1] != "<synthetic>" for k in by))
        check("subagent: meta.json の agentType 優先", ("task-worker", "claude-sonnet-5") in by, str(list(by)))
        check("subagent: meta.json 無し → tool_result 正規表現", ("reviewer", "claude-fable-5") in by, str(list(by)))
        check("subagent: attributionAgent フォールバック", ("spec-critic", "claude-fable-5") in by, str(list(by)))
        check("phase: 2フェーズ跨ぎは attributionSkill 多数決で 06", r["meta"]["phase"] == "06-implement-task"
              and r["meta"]["phase_source"] == "attributionSkill", str(r["meta"]))
        check("phase: コマンド列を保持", r["meta"]["commands"] == ["05-implementation-plan", "06-implement-task"], str(r["meta"]["commands"]))
        check("coverage: 壊れた行を数える(parsed<total)", r["meta"]["coverage"]["total"] == r["meta"]["coverage"]["parsed"] + 1,
              str(r["meta"]["coverage"]))
        check("host: statusline 由来の費用・文脈・行数", r["cost"]["host_amount"] == 3.5 and r["context"]["used_percentage"] == 62
              and r["outcome_common"]["lines_added"] == 40, str(r["cost"]))
        check("tests: サブエージェント内のみ → ran/unknown(pass を主張しない)", r["outcome_common"]["tests"]["ran"]
              and r["outcome_common"]["tests"]["result"] == "unknown" and r["outcome_common"]["tests"]["runs"] == 1
              and r["outcome_common"]["tests"]["observed"] == {"passed": 344}, str(r["outcome_common"]["tests"]))
        check("成果: tasks [x] 差分 +2", r["outcome_harness"]["tasks_done_delta"] == 2, str(r["outcome_harness"]))
        check("成果: GATE 変化なし(baseline と同じ)", r["outcome_harness"]["gate_transitions"] == [])
        check("委譲: spawns と実行モデル", r["outcome_harness"]["spawns"] == {"task-worker": 1, "reviewer": 1}
              and r["outcome_harness"]["resolved_models"].get("task-worker") == ["claude-fable-5"], str(r["outcome_harness"]))
        check("文脈: 閾値超フラグ(config 40%)", "context_high" in r["flags"], str(r["flags"]))
        check("基準線: 06 の暫定値が付く", r["baseline"] and r["baseline"]["provisional"] and r["baseline"]["phase"] == "06-implement-task",
              str(r["baseline"]))
        short = render_short(r, cfg)
        check("短縮表示: 3行以内・絵文字なし・出所タグ", len(short.splitlines()) == 3 and "[公式" in short
              and "transcript" in short and not re.search(r"[\U0001F300-\U0001FAFF]", short), short)
        check("短縮表示: 文脈超過で新セッション推奨の1行", "新セッションを推奨" in short)
        full = render_full(r, cfg)
        check("詳細表示: 単価行と委譲行を含む", "task-worker" in full and "委譲:" in full and "解析率" in full)

        # 第6波(A6-20 / D073 open): フック判定 JSONL(+旧 TSV)の hooks 欄と、review-log.md の日時付きエントリ数
        hook_logs = os.path.dirname(logs)
        with open(os.path.join(hook_logs, "hook-decisions.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-09-09T10:00:00+09:00", "session_id": "s1", "hook_event": "PreToolUse", "tool_name": "Bash",
                                "script": "guard-dangerous-git.sh", "decision": "ask", "target": "git push", "tool_use_id": "t9",
                                "duration_ms": 120, "host": "claude-code"}) + "\n")
            f.write(json.dumps({"ts": "2026-09-09T10:01:00+09:00", "session_id": "s1", "hook_event": "PreToolUse", "tool_name": "Write",
                                "script": "guard-template-edit.sh", "decision": "deny", "target": "x_template.md", "tool_use_id": None,
                                "duration_ms": 480, "host": "claude-code"}) + "\n")
            f.write(json.dumps({"ts": "2026-09-09T10:02:00+09:00", "session_id": "other", "hook_event": "PreToolUse", "tool_name": "Write",
                                "script": "guard-template-edit.sh", "decision": "deny", "target": "y", "tool_use_id": None}) + "\n")
        with open(os.path.join(hook_logs, "hook-decisions.log"), "w", encoding="utf-8") as f:
            f.write("2026-09-10T10:00:00\twarn-gate-tamper.sh\twarn\tprogress.md gate-done\n")
            f.write("2020-01-01T00:00:00\twarn-gate-tamper.sh\twarn\told\n")
        os.makedirs(os.path.join(proj, "docs", "04-test"), exist_ok=True)
        with open(os.path.join(proj, REVIEW_LOG_REL), "w", encoding="utf-8") as f:
            f.write("# 独立レビュー記録\n\n### 2026-09-10 10:30 / implementation / 対象: TASK-1 / verdict: 承認\n\n"
                    "### 2026-09-11 09:00 / implementation / 対象: TASK-2 / verdict: MINOR\n\n見出しでない行 2026-09-12 10:00 / implementation\n")
        rh = build_receipt("s1", main_path, proj, root=root)
        hk = rh["outcome_harness"]["hooks"]
        check("hooks: session_id で JSONL を絞る(deny 1 / ask 1)、他セッションは数えない", hk["deny"] == 1 and hk["ask"] == 1
              and hk["block"] == 0, str(hk))
        check("hooks: 旧 TSV はセッション開始以降の時間窓で数える(warn 1・2020 年の行は除外)、source jsonl+tsv",
              hk["warn"] == 1 and hk["source"] == "jsonl+tsv", str(hk))
        check("hooks: by_script は判定のあるスクリプトだけ", hk["by_script"].get("guard-dangerous-git.sh") == {"ask": 1}, str(hk["by_script"]))
        check("hooks: 所要 P50 / P95(最近傍順位法。JSONL 2 行 120 / 480 ms)と host 別件数", hk["duration_n"] == 2
              and hk["duration_p50_ms"] == 120 and hk["duration_p95_ms"] == 480 and hk["by_host"] == {"claude-code": 2}, str(hk))
        check("受領書: 詳細表示に所要 P50/P95 の 1 行分が載る", "所要 P50 120 / P95 480 ms (n=2)" in render_full(rh, cfg), render_full(rh, cfg))
        check("review-log: 日時付き見出し 2 件を数え、spawn 1 回との大きい方が工程累計", rh["outcome_harness"]["review_log_entries"] == 2
              and rh["outcome_harness"]["review_runs_phase"] == 2 and review_log_entries(proj, "test") == 0, str(rh["outcome_harness"]))
        check("受領書: hooks 欄が短縮/詳細表示に載る", "hooks deny 1/ask 1/warn 1" in render_short(rh, cfg)
              and "フック判定: deny 1 · ask 1 · warn 1" in render_full(rh, cfg), render_full(rh, cfg))
        check("hooks: 読み手 _log.py は root に無ければ本体(ROOT)のものを使う",
              _hook_log_module(os.path.join(tmp, "nowhere")) is not None and hk["source"] != "unavailable")
        os.remove(os.path.join(proj, REVIEW_LOG_REL))
        os.remove(os.path.join(hook_logs, "hook-decisions.jsonl"))
        os.remove(os.path.join(hook_logs, "hook-decisions.log"))

        # トリガ: 初回 Stop(フェーズコマンド起動) → 表示。直後の Stop(変化なし) → 非表示。
        show, why, st = decide_show(r, None, cfg, {"last_assistant_message": "done", "background_tasks": []})
        check("trigger: フェーズコマンド起動で表示", show and "phase_command" in why, str(why))
        show2, why2, st2 = decide_show(r, st, cfg, {"last_assistant_message": "ok", "background_tasks": []})
        check("trigger: 変化なしの次 Stop は非表示", not show2 and why2 == [], str(why2))
        show3, why3, st3 = decide_show(r, st, cfg, {"stop_hook_active": True})
        check("trigger: stop_hook_active は表示しない", not show3 and why3[0] == "stop_hook_active")
        show4, why4, st4 = decide_show(r, None, cfg, {"last_assistant_message": "x", "background_tasks": []}, block_turn=True)
        check("trigger: block ターンは持ち越し(pending_show)", not show4 and st4["pending_show"] and "block_turn" in why4)
        show5, why5, _ = decide_show(r, st4, cfg, {"last_assistant_message": "x", "background_tasks": []})
        check("trigger: 持ち越し分は次 Stop で表示", show5 and "carried_over" in why5, str(why5))
        show6, why6, _ = decide_show(r, st, cfg, {"last_assistant_message": "x", "background_tasks": [{"task_id": "1"}]})
        check("trigger: background_tasks ありは区切りでない", not show6 and why6[0] == "background_tasks")
        # GATE 変化 → 表示
        with open(os.path.join(proj, GATE_REL), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\ndesign: done\nimplementation: done\ntest: not_started\nrelease: not_started\n-->\n")
        r2 = build_receipt("s1", main_path, proj, root=root)
        show7, why7, _ = decide_show(r2, st, cfg, {"last_assistant_message": "x", "background_tasks": []})
        check("trigger: GATE 遷移で表示", show7 and "gate_changed" in why7 and r2["outcome_harness"]["gate_transitions"] == ["implementation:in_progress->done"],
              str(why7) + str(r2["outcome_harness"]["gate_transitions"]))
        check("review_less_done: 同フェーズに reviewer 起動あり → false", r2["outcome_harness"]["review_less_done"] is False)

        # draft → finalize(SessionEnd) の原子性・sessions.jsonl 1行
        check("draft: 書ける", write_draft(r2, st, logs) and os.path.exists(draft_path(logs, "s1")))
        check("finalize: rename と 1 行追記", finalize("s1", logs, reason="clear") and os.path.exists(final_path(logs, "s1"))
              and not os.path.exists(draft_path(logs, "s1"))
              and sum(1 for _ in iter_json_lines(os.path.join(logs, "sessions.jsonl"))) == 1)
        check("finalize: tmp ファイルが残らない", not glob.glob(os.path.join(logs, "*.tmp")))
        fin = load_json(final_path(logs, "s1"))
        check("finalize: 採取タグが確定に変わる", fin["meta"]["capture"] == "SessionEnd(確定)" and "_state" not in fin
              and fin["meta"]["end_reason"] == "clear")

        # フィクスチャ2: review_less_done(同フェーズ累計 0・done 遷移) と resume
        os.makedirs(os.path.join(tdir, "s2", "subagents"))
        p2 = os.path.join(tdir, "s2.jsonl")
        with open(p2, "w", encoding="utf-8") as f:
            f.write("\n".join([_cmd("08-test-execute"),
                               _usage_msg("m1", "claude-fable-5-1", 10, 10, cr=1000, extra={"attributionSkill": "08-test-execute"})]) + "\n")
        proj2 = os.path.join(tmp, "proj2")
        shutil.copytree(proj, proj2)
        with open(os.path.join(proj2, GATE_REL), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\ndesign: done\nimplementation: done\ntest: done\nrelease: not_started\n-->\n")
        write_json_atomic(os.path.join(logs, "s2.baseline.json"),
                          {"session_id": "s2", "source": "resume", "gate": {"requirements": "done", "design": "done",
                                                                            "implementation": "done", "test": "in_progress",
                                                                            "release": "not_started"}, "tasks_done": 2})
        r3 = build_receipt("s2", p2, proj2, root=root)
        check("review_less_done: test done 遷移・工程累計 0 → true", r3["outcome_harness"]["review_less_done"] is True
              and "review_less_done" in r3["flags"], str(r3["outcome_harness"]))
        check("resume: resumed フラグ・乖離判定を抑止", "resumed" in r3["flags"] and r3["cost"]["divergence_ratio"] is None)
        check("statusline 無し: host n/a と no_statusline", r3["cost"]["host_amount"] is None and "no_statusline" in r3["flags"])
        check("prices: fable-5-1 の cache_read は input の 0.025x", price_of("claude-fable-5-1", prices)[0]["cache_read"]
              == price_of("claude-fable-5-1", prices)[0]["input"] * 0.025)
        check("prices: 最長前方一致(5-1 が 5 より先)", price_of("claude-fable-5-1-20261001", prices)[0]["id"] == "claude-fable-5-1"
              and price_of("claude-fable-5", prices)[0]["id"] == "claude-fable-5")
        check("prices: family fallback は fallback 扱い", price_of("claude-opus-9", prices)[1] is True)
        # ライトパス注記があれば review_less_done は false
        with open(os.path.join(proj2, GATE_REL), "a", encoding="utf-8") as f:
            f.write("\nライトパス使用中（fast-track。承認は2回）\n")
        r3b = build_receipt("s2", p2, proj2, root=root)
        check("review_less_done: ライトパス注記で正常扱い", r3b["outcome_harness"]["review_less_done"] is False and r3b["outcome_harness"]["fast_track"])

        # フィクスチャ3: golden-eval の決定論検証だけが pass を主張できる
        p3 = os.path.join(tdir, "s3.jsonl")
        with open(p3, "w", encoding="utf-8") as f:
            f.write("\n".join([
                _usage_msg("m1", "claude-opus-5", 1, 1, content=[{"type": "tool_use", "id": "g1", "name": "Bash",
                                                                 "input": {"command": "python tools/golden-eval.py ."}}]),
                _tool_result("g1", "OK\tprogress\t...\n\n完了宣言 3 件中、機械的検証を通過 3 件(100%)。目標は90%以上。"),
                _usage_msg("m2", "claude-opus-5", 1, 1, content=[{"type": "tool_use", "id": "b2", "name": "Bash",
                                                                 "input": {"command": "pytest -q"}}]),
                _tool_result("b2", "28 passed in 1.2s"),
            ]) + "\n")
        r4 = build_receipt("s3", p3, proj, root=root)
        check("tests: golden-eval 3/3 → pass [golden-eval]", r4["outcome_common"]["tests"]["result"] == "pass"
              and r4["outcome_common"]["tests"]["source"] == "golden-eval" and r4["outcome_common"]["tests"]["runs"] == 1,
              str(r4["outcome_common"]["tests"]))

        # フィクスチャ4: -p の result JSON
        res = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 7, "session_id": "p1",
               "total_cost_usd": 0.242,
               "modelUsage": {"claude-sonnet-5": {"inputTokens": 4, "outputTokens": 120, "cacheReadInputTokens": 0,
                                                  "cacheCreationInputTokens": 40314, "costUSD": 0.242, "costBasis": "list"}},
               "permission_denials": []}
        rp = receipt_from_result(res, root=root)
        check("-p: modelUsage から tokens・host_amount・basis", rp["tokens"][0]["cache_w5m"] == 40314 and rp["cost"]["host_amount"] == 0.242
              and rp["cost"]["host_basis"] == "list" and rp["meta"]["capture"] == "result-json", str(rp["cost"]))
        check("-p: 自前推定は sonnet-5 定価(2/10・書込1.25x)", abs(rp["cost"]["amount"] - (4 * 2 + 120 * 10 + 40314 * 2.5) / 1e6) < 1e-4,
              str(rp["cost"]["amount"]))
        check("-p: 短縮表示が3行", len(render_short(rp, cfg).splitlines()) == 3)

        # 受領書一覧と最新セッション推定
        check("latest_session: 最終更新のセッションを返す", latest_session(logs) in ("s1", "s2"))
        check("list_receipts: 確定を優先", "s1" in list_receipts(logs) and list_receipts(logs)["s1"]["meta"]["capture"] == "SessionEnd(確定)")
        # parse_gate: 注記付き値と終端欠落
        with open(os.path.join(tmp, "g.md"), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done (PM approved)\ndesign: in_progress\n")
        g = parse_gate(os.path.join(tmp, "g.md"))
        check("parse_gate: 注記付き値と --> 欠落に耐える", gate_state(g.get("requirements")) == "done" and g.get("design") == "in_progress", str(g))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

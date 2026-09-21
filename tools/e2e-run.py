#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2E A/B 測定ランナー v2(評価装置 v2。audits/external-reaudit-2026-09-09.md §6、A6-16)。

D049 で人力実施した「git archive で使い捨てプロジェクトを作り、ヘッドレス `claude -p` で
フェーズを通し実行する」手順を、**条件固定・判定・退避まで機械化した**測定装置にする。
v1(A3-2)からの主な変更(監査 §6・設計レビューの反映):

  - 結果 JSON は schema_version 2。verdict ∈ {PASS, FAIL, DNF_BUDGET_TOTAL, DNF_BUDGET_CALL,
    DNF_QUOTA, DNF_TIMEOUT, DNF_ERROR, DNF_TURNS, DNF_ABNORMAL_STOP, INVALID_APPARATUS} と valid_for_comparison /
    invalid_reasons[] を必須で持つ。判定は subtype 単独でなく is_error + api_error_status +
    errors[] + result 文言の複合(2.1.201 で Fable 5.1 が API 400 でも subtype は success)。
    合計超過は「cost > total_budget」で判定する(abort フラグ非依存)。
  - 固定条件 C: --model(フル ID 必須)・--effort(必須)・CLI ≥ 2.1.251(Fable 5.1 対応。
    推奨 2.1.257 = CLAUDE_CODE_SUBAGENT_MODEL_FORCE)・ユーザースコープ隔離
    (系列専用 CLAUDE_CONFIG_DIR に資格情報のみ複製)+ --strict-mcp-config +
    --setting-sources project,local + --settings <series>/settings.json(switchModelsOnFlag:false)。
    各 call を stream-json で起動し system/init(version・model・skills・agents・mcp_servers・
    plugins)を保存、プロジェクト由来以外の要素を condition_hash に含める。
  - 予算: 各 call の --max-budget-usd は min(call_budget, total_budget − 消費済み) を動的付与。
    督促(wait_for_state の nudge)は kind=nudge として calls[] に記録し予算に算入、bare にも
    同数の継続発話を与える(同一 series/run_index の harness 結果から自動で揃える)。
  - 装置: workdir は Temp 外の evaluation/runs/<series>/(gitignore)。終了時に git bundle +
    check.py 出力 + GATE_STATUS 最終値を退避し、transcript(サブエージェント含む)を
    session_id 探索で zip 退避して sha256 を残す。--continue は廃止(--session-id/--resume)。
  - アーム仕様は evaluation/arms/<name>.json(依存ゼロ)。external アーム(Spec Kit 等)は
    setup を実行してから memo 配置 → bootstrap commit。allowed_tools_extra は同一系列の
    全アームに適用する(条件 C を保つ)。
  - 事前登録: evaluation/experiments/<series>.json を --plan で読み、arms の ABAB 順・
    予算・model/effort を計画から取る(CLI 引数で上書きしない。sha256 を結果に記録)。

使い方(ハーネス本体ルートで実行):
  python tools/e2e-run.py --selftest
  python tools/e2e-run.py --arm harness --task todo-cli --experiment capability \
      --series-id pilot --model claude-opus-5 --effort high --dry-run
  python tools/e2e-run.py --plan evaluation/experiments/S1-expense-capability.json --run-index 1
  python tools/e2e-run.py --plan evaluation/experiments/S1-expense-capability.json --next

注意:
- 本ツールは claude を起動する統合テストそのものなので、--selftest は claude を起動しない
  (dry-run 計画・分類器・判定器・妥当性条件・アーム仕様・退避関数の検証に限る)。
- claude CLI が見つからない/版数未達の場合、実走行は明示エラーで停止する(--allow-old-cli で
  続行できるが valid_for_comparison=false になる)。
- 作業ディレクトリは実行後も削除しない(evaluation/runs/ は gitignore)。

終了コード: 0 = 正常(実走行では verdict PASS)  1 = 実走行で PASS 以外 / selftest 失敗
            2 = 実行エラー(引数不正・タスク定義不備・claude 不在・版数未達等)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = HARNESS_ROOT / "evaluation"
RESULTS_DIR = EVAL_DIR / "results"
TRAJ_DIR = RESULTS_DIR / "trajectories"
ARMS_DIR = EVAL_DIR / "arms"
EXPERIMENTS_DIR = EVAL_DIR / "experiments"
RUNS_DIR = EVAL_DIR / "runs"

SCHEMA_VERSION = 2
# CLI 版数の門(監査 §6 / 設計レビュー): Fable 5.1 は 2.1.251 未満で API 400、
# サブエージェント合算の予算強制は 2.1.217 以降、FORCE は 2.1.257 以降、
# 1.1x データ常駐プレミアムの費用推定加算は 2.1.239 以降、CLAUDE_CODE_PROJECT_DIR_NAME は 2.1.234 以降。
# 数値の正は .github/harness/platform-requirements.json(A6-8。ここにハードコードしない)。
sys.path.insert(0, str(HARNESS_ROOT / "tools"))
import platform_requirements as _preq  # noqa: E402

_REQ_CC = _preq.load(str(HARNESS_ROOT))["claude_code"]
MIN_CLI = _preq.vtuple(_REQ_CC["e2e"]["min_cli"])
RECOMMENDED_CLI = _preq.vtuple(_REQ_CC["required"])
CLI_BUDGET_SUBAGENT_INCLUSIVE = _preq.vtuple(_REQ_CC["features"]["budget_subagent_inclusive"]["version"])
CLI_RESIDENCY_PREMIUM = _preq.vtuple(_REQ_CC["features"]["residency_premium_1_1x"]["version"])
CLI_PROJECT_DIR_NAME = _preq.vtuple(_REQ_CC["features"]["project_dir_name_env"]["version"])

PERMISSION_MODE = "acceptEdits"
ALLOWED_TOOLS = ["Bash(python:*)", "Bash(py:*)", "Bash(git:*)"]
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
EXPERIMENTS = ("capability", "efficiency")
FRESH_POLICIES = ("continue", "fresh-on-command")
VERDICTS = ("PASS", "FAIL", "DNF_BUDGET_TOTAL", "DNF_BUDGET_CALL", "DNF_QUOTA",
            "DNF_TIMEOUT", "DNF_ERROR", "DNF_TURNS", "DNF_ABNORMAL_STOP", "INVALID_APPARATUS")
GATE_PHASES = ("requirements", "design", "implementation", "test", "release")
GATE_STATES = ("not_started", "in_progress", "pending_approval", "done")
SERIES_SETTINGS = {"switchModelsOnFlag": False}
DEFAULT_MAX_NUDGES = 2
CAPABILITY_BUDGET_RATIO_MAX = 0.7
AUX_MODEL_COST_RATIO_MAX = 0.01

# 実行環境から掃除する変数(ユーザースコープの model/effort 上書きを遮断)
ENV_SCRUB = ("CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SUBAGENT_MODEL",
             "CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
             "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
             "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
             "MAX_THINKING_TOKENS", "CLAUDE_CODE_SAFE_MODE", "CLAUDE_CONFIG_DIR",
             "CLAUDE_CODE_PROJECT_DIR_NAME")

QUOTA_MARKERS = ("out of usage credits", "usage limit", "session limit", "rate limit",
                 "credit balance", "insufficient credits", "quota")
BUDGET_MARKERS = ("reached maximum budget", "max budget", "maximum budget", "max_budget")
TURNS_MARKERS = ("reached max turns", "reached maximum turns", "max_turns", "maximum turns")
MODEL_REJECT_MARKERS = ("does not support this model", "model not found", "not_found_error")

# bare アームの既定初回発話(タスク定義に scripted-replies-bare が無い場合)
DEFAULT_BARE_PROMPT = (
    "requirements/memo.md に作りたいアプリのメモがあります。"
    "このメモのアプリを実装し、テストも書いてすべて通してください。"
    "完了したら成果物のファイルと実行方法を報告してください。"
)
# bare アームの発話数を harness アームに揃えるための継続発話(予算同等化)
DEFAULT_BARE_NUDGE = (
    "続きをお願いします。未完了の作業があれば進め、"
    "すべて完了済みなら現状のまとめを報告してください。"
)
# 督促(wait_for_state)。両アームで同一文言(kind=nudge として記録)
NUDGE_PROMPT = "続きをお願いします。未完了の作業があれば進めてください。"


def say(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 2) -> "None":
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# タスク定義の読み込み(`# expect:` 注釈つき)
# ---------------------------------------------------------------------------

def _fenced_blocks(text: str) -> dict:
    """task.md からフェンス付きコードブロックを {info文字列: 本文} で取り出す。"""
    blocks: dict = {}
    for m in re.finditer(r"^```([A-Za-z0-9_-]+)[ \t]*\n(.*?)^```[ \t]*$",
                         text, re.MULTILINE | re.DOTALL):
        info, body = m.group(1), m.group(2)
        blocks.setdefault(info, body)
    return blocks


RE_EXPECT = re.compile(r"^\s*#\s*expect\s*:\s*(.+?)\s*$")


def _split_replies_ex(block: str) -> list:
    """scripted-replies ブロックを [(発話, expect注釈 or None)] に分解する。

    発話は `---` だけの行で区切る。`#` で始まる行はコメントで発話本文から除くが、
    `# expect: <条件>` はその発話を送る**前に**満たされているべき GATE_STATUS 条件
    (EV-8。到達状態のみを書く。書式は expect_met() を参照)として保持する。
    """
    out = []
    for chunk in re.split(r"^---[ \t]*$", block, flags=re.MULTILINE):
        expect = None
        lines = []
        for ln in chunk.splitlines():
            if ln.lstrip().startswith("#"):
                m = RE_EXPECT.match(ln)
                if m:
                    expect = m.group(1)
                continue
            lines.append(ln)
        reply = "\n".join(lines).strip()
        if reply:
            out.append((reply, expect))
    return out


def _split_replies(block: str) -> list:
    """scripted-replies ブロックを発話(文字列)のリストに分解する(v1 互換)。"""
    return [r for r, _e in _split_replies_ex(block)]


def _split_stages_ex(block: str) -> list:
    """`===` だけの行でステージに分割する(各要素は [(発話, expect)] のリスト)。"""
    stages = []
    for part in re.split(r"^===[ \t]*$", block, flags=re.MULTILINE):
        replies = _split_replies_ex(part)
        if replies:
            stages.append(replies)
    return stages


def _split_stages(block: str) -> list:
    """`===` 区切りのステージ分割(v1 互換: 各要素は発話文字列のリスト)。"""
    return [[r for r, _e in st] for st in _split_stages_ex(block)]


class Task:
    def __init__(self, name: str, path: Path, memo: str, blocks: dict):
        self.name = name
        self.path = path
        self.memo = memo
        self.blocks = blocks  # replies_block 名 → [[(reply, expect)]]
        self.md_sha256 = sha256_file(path)
        self.check_sha256 = sha256_file(path.parent / "check.py")

    def stages_for(self, block_name: str) -> list:
        if block_name not in self.blocks:
            die(f"task.md に ```{block_name} ブロックがありません(アームの台本が未定義): {self.path}")
        return self.blocks[block_name]

    # v1 互換プロパティ
    @property
    def stages_harness(self) -> list:
        return [[r for r, _e in st] for st in self.blocks["scripted-replies"]]

    @property
    def stages_bare(self) -> list:
        return [[r for r, _e in st] for st in self.blocks["scripted-replies-bare"]]

    @property
    def replies_harness(self) -> list:
        return [r for st in self.stages_harness for r in st]

    @property
    def replies_bare(self) -> list:
        return [r for st in self.stages_bare for r in st]


def load_task(name: str) -> Task:
    task_dir = EVAL_DIR / "tasks" / name
    task_md = task_dir / "task.md"
    check_py = task_dir / "check.py"
    if not task_md.is_file():
        die(f"タスク定義がありません: {task_md}")
    if not check_py.is_file():
        die(f"チェッカーがありません: {check_py}")
    raw = _fenced_blocks(task_md.read_text(encoding="utf-8"))
    memo = raw.get("memo", "").strip()
    if not memo:
        die(f"task.md に ```memo ブロックがありません: {task_md}")
    stages_h = _split_stages_ex(raw.get("scripted-replies", ""))
    if not stages_h:
        die(f"task.md に ```scripted-replies ブロックがありません: {task_md}")
    stages_b = _split_stages_ex(raw.get("scripted-replies-bare", ""))
    if not stages_b:
        stages_b = [[(DEFAULT_BARE_PROMPT, None)]]
    if len(stages_b) != len(stages_h):
        die(f"scripted-replies-bare のステージ数({len(stages_b)})が harness "
            f"({len(stages_h)})と一致しません(`===` 区切りを確認): {task_md}")
    # 予算同等化: 各ステージで bare の発話数を harness に揃える(不足は継続発話)
    for st_h, st_b in zip(stages_h, stages_b):
        while len(st_b) < len(st_h):
            st_b.append((DEFAULT_BARE_NUDGE, None))
    blocks = {"scripted-replies": stages_h, "scripted-replies-bare": stages_b}
    # 外部アーム台本(scripted-replies-<name>)。harness と同じステージ数を要求する
    for info, body in raw.items():
        if info.startswith("scripted-replies-") and info not in blocks:
            st = _split_stages_ex(body)
            if st and len(st) == len(stages_h):
                blocks[info] = st
            elif st:
                die(f"{info} のステージ数({len(st)})が harness({len(stages_h)})と一致しません: {task_md}")
    return Task(name, task_md, memo, blocks)


# ---------------------------------------------------------------------------
# アーム仕様(evaluation/arms/<name>.json。依存ゼロの JSON)
# ---------------------------------------------------------------------------

class ArmSpec:
    def __init__(self, data: dict, path: "Path | None" = None):
        self.name = data.get("name", "")
        self.kind = data.get("kind", "builtin")
        self.replies_block = data.get("replies_block", "scripted-replies")
        self.workdir_source = data.get("workdir_source", "empty")  # git-archive-HEAD | empty
        self.setup = data.get("setup", [])
        self.pin = data.get("pin", {})
        self.allowed_tools_extra = list(data.get("allowed_tools_extra", []))
        self.expect_files = list(data.get("expect_files", []))
        self.smoke = data.get("smoke")
        self.gate_status = bool(data.get("gate_status", self.kind == "builtin"
                                         and self.workdir_source == "git-archive-HEAD"))
        self.status = data.get("status", "ready")
        self.notes = data.get("notes", "")
        self.path = path
        self.sha256 = sha256_file(path) if path else sha256_text(json.dumps(data, sort_keys=True))
        self.raw = data

    def validate(self) -> list:
        problems = []
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.name or ""):
            problems.append(f"name が不正: {self.name!r}")
        if self.kind not in ("builtin", "external"):
            problems.append(f"kind が不正: {self.kind!r}(builtin|external)")
        if self.workdir_source not in ("git-archive-HEAD", "empty"):
            problems.append(f"workdir_source が不正: {self.workdir_source!r}")
        for i, cmd in enumerate(self.setup):
            if not isinstance(cmd, list) or not all(isinstance(a, str) for a in cmd):
                problems.append(f"setup[{i}] は文字列の配列(argv)で書く")
                continue
            if "--ai" in cmd:
                problems.append(f"setup[{i}]: Spec Kit の --ai は v0.10.0 で削除済み(--integration を使う)")
        if self.kind == "external" and not self.setup:
            problems.append("external アームは setup が必須")
        if self.kind == "external" and not self.pin:
            problems.append("external アームは pin(repo/ref)が必須(pin 無しの結果は INVALID)")
        for t in self.allowed_tools_extra:
            if not re.fullmatch(r"[A-Za-z]+(\(.+\))?", t):
                problems.append(f"allowed_tools_extra の書式が不正: {t!r}")
        return problems


BUILTIN_ARMS = {
    "harness": {"name": "harness", "kind": "builtin", "replies_block": "scripted-replies",
                "workdir_source": "git-archive-HEAD", "gate_status": True},
    "bare": {"name": "bare", "kind": "builtin", "replies_block": "scripted-replies-bare",
             "workdir_source": "empty", "gate_status": False},
}


def load_arm_spec(arm: str, spec_path: "Path | None" = None) -> ArmSpec:
    """--arm harness|bare|external:<name> または --arm-spec <path> からアーム仕様を読む。"""
    if spec_path is not None:
        path = Path(spec_path)
        if not path.is_file():
            die(f"アーム仕様がありません: {path}")
        spec = ArmSpec(json.loads(path.read_text(encoding="utf-8")), path)
    else:
        name = arm.split(":", 1)[1] if arm.startswith("external:") else arm
        path = ARMS_DIR / f"{name}.json"
        if path.is_file():
            spec = ArmSpec(json.loads(path.read_text(encoding="utf-8")), path)
        elif name in BUILTIN_ARMS:
            spec = ArmSpec(dict(BUILTIN_ARMS[name]))
        else:
            die(f"未知のアーム: {arm}(evaluation/arms/{name}.json が無い)")
        if arm.startswith("external:") and spec.kind != "external":
            die(f"{path}: kind が external ではありません")
    problems = spec.validate()
    if problems:
        die(f"アーム仕様 {spec.path or spec.name} が不正: " + " / ".join(problems))
    return spec


# ---------------------------------------------------------------------------
# 事前登録(evaluation/experiments/<series>.json)
# ---------------------------------------------------------------------------

def load_plan(path: Path) -> dict:
    if not path.is_file():
        die(f"事前登録ファイルがありません: {path}")
    plan = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for key in ("series_id", "experiment", "task", "arms", "model", "effort"):
        if key not in plan:
            problems.append(f"{key} が無い")
    if plan.get("experiment") not in EXPERIMENTS:
        problems.append(f"experiment が不正: {plan.get('experiment')!r}")
    if plan.get("effort") not in EFFORT_LEVELS:
        problems.append(f"effort が不正: {plan.get('effort')!r}")
    if not is_full_model_id(str(plan.get("model", ""))):
        problems.append(f"model はフル ID で書く: {plan.get('model')!r}")
    if not isinstance(plan.get("arms"), list) or not plan.get("arms"):
        problems.append("arms は 1 件以上の配列")
    if plan.get("experiment") == "efficiency" and not plan.get("budget_ladder_usd") \
            and not plan.get("budgets"):
        problems.append("efficiency は budget_ladder_usd か budgets が必要")
    if plan.get("experiment") == "capability" and not plan.get("budgets"):
        problems.append("capability は budgets{total_usd, call_usd} が必要")
    if problems:
        die(f"事前登録 {path} が不正: " + " / ".join(problems))
    plan["_path"] = str(path)
    plan["_sha256"] = sha256_file(path)
    return plan


def plan_arm_for_index(plan: dict, run_index: int) -> str:
    """ABAB(交互)順で run_index 番目のアームを決める。"""
    arms = plan["arms"]
    return arms[(run_index - 1) % len(arms)]


def plan_series_id(plan: dict, rung: "int | None") -> str:
    sid = plan["series_id"]
    if plan.get("budget_ladder_usd"):
        if rung is None:
            die("efficiency ラダーの事前登録では --budget-rung <index> が必要")
        ladder = plan["budget_ladder_usd"]
        if not (0 <= rung < len(ladder)):
            die(f"--budget-rung は 0..{len(ladder) - 1}")
        b = ladder[rung]
        sid = f"{sid}/B{b:g}"
    return sid


def plan_budgets(plan: dict, rung: "int | None") -> tuple:
    if plan.get("budget_ladder_usd"):
        b = float(plan["budget_ladder_usd"][rung])
        n_stages = int(plan.get("stages") or 1)
        call = float(plan.get("call_budget_usd") or (b / max(1, n_stages)))
        return call, b
    b = plan["budgets"]
    return float(b.get("call_usd", b["total_usd"])), float(b["total_usd"])


def next_run_index(series_id: str) -> int:
    used = []
    for p in RESULTS_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        exp = d.get("experiment") or {}
        if exp.get("series_id") == series_id and isinstance(exp.get("run_index"), int):
            used.append(exp["run_index"])
    return (max(used) + 1) if used else 1


# ---------------------------------------------------------------------------
# claude CLI(版数・起動)
# ---------------------------------------------------------------------------

def find_claude() -> "str | None":
    return shutil.which("claude")


def _claude_argv(claude: str) -> list:
    """Windows の npm 配布(claude.cmd)は cmd.exe 経由でしか起動できない。"""
    if claude.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", claude]
    return [claude]


def claude_supports_max_budget(claude: str) -> bool:
    """--help 文字列での存在確認(情報用。版数の門は require_cli が担う。EV-7)。"""
    try:
        cp = subprocess.run(_claude_argv(claude) + ["--help"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=60)
        return "max-budget-usd" in (cp.stdout or "") + (cp.stderr or "")
    except Exception:
        return False


def claude_version(claude: str) -> str:
    try:
        cp = subprocess.run(_claude_argv(claude) + ["--version"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=60)
        return (cp.stdout or cp.stderr or "").strip()
    except Exception:
        return "unknown"


def parse_claude_version(s: str) -> "tuple | None":
    """'2.1.201 (Claude Code)' → (2, 1, 201)。解釈不能なら None。"""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", s or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def version_str(ver: "tuple | None") -> str:
    return ".".join(str(x) for x in ver) if ver else "unknown"


def budget_enforcement(ver: "tuple | None") -> str:
    """--max-budget-usd の強制範囲。2.1.217 未満は main-only(サブエージェント分を合算しない)。"""
    if ver is None:
        return "unknown"
    return "subagent-inclusive" if ver >= CLI_BUDGET_SUBAGENT_INCLUSIVE else "main-only"


def cli_gate(ver: "tuple | None") -> dict:
    """版数の門の判定(exit するかは require_cli が決める)。"""
    reasons = []
    if ver is None:
        reasons.append("cli_version_unknown")
    else:
        if ver < CLI_BUDGET_SUBAGENT_INCLUSIVE:
            reasons.append(f"cli<{version_str(CLI_BUDGET_SUBAGENT_INCLUSIVE)}")
        if ver < MIN_CLI:
            reasons.append(f"cli<{version_str(MIN_CLI)}")
    return {"min_cli_ok": ver is not None and ver >= MIN_CLI,
            "recommended_cli_ok": ver is not None and ver >= RECOMMENDED_CLI,
            "budget_enforcement": budget_enforcement(ver),
            "subagent_model_forced": ver is not None and ver >= RECOMMENDED_CLI,
            "residency_premium_in_estimate": ver is not None and ver >= CLI_RESIDENCY_PREMIUM,
            "invalid_reasons": reasons}


def require_cli(claude: str, args) -> tuple:
    """CLI 版数の門: MIN_CLI 未満は exit 2(--allow-old-cli なら続行し valid=false)。"""
    ver_s = claude_version(claude)
    ver = parse_claude_version(ver_s)
    gate = cli_gate(ver)
    if not gate["min_cli_ok"]:
        msg = (f"claude {ver_s!r} は最低版 {version_str(MIN_CLI)} 未満"
               f"(Fable 5.1 は API 400・予算強制は {gate['budget_enforcement']})。")
        if not getattr(args, "allow_old_cli", False):
            die(msg + " 開発機を更新するか --allow-old-cli(結果は valid_for_comparison=false)を付ける。")
        say("WARN: " + msg + " --allow-old-cli により続行(比較には使えない)。")
    elif not gate["recommended_cli_ok"]:
        say(f"WARN: claude {ver_s!r} は推奨版 {version_str(RECOMMENDED_CLI)} 未満"
            "(CLAUDE_CODE_SUBAGENT_MODEL_FORCE が効かない。subagent_model_forced=false を記録)。")
    return ver_s, ver, gate


def is_full_model_id(model: str) -> bool:
    """エイリアス(opus/fable/sonnet/opusplan/opus[1m])を拒否し、フル ID だけ通す。"""
    return bool(re.fullmatch(r"claude-[a-z]+(-[a-z0-9]+)*-\d+(-\d+)*(-\d{8})?", model or ""))


def normalize_model_key(key: str) -> str:
    """modelUsage のキー正規化: '[1m]' 接尾辞を除去。'<synthetic>' はそのまま(集計側で除外)。"""
    return re.sub(r"\[1m\]$", "", key.strip())


def build_env(base_env: dict, config_dir: Path, model: str, ver: "tuple | None",
              project_dir_name: "str | None") -> tuple:
    """実行環境: ユーザースコープの model/effort 上書きを掃除し、隔離 CONFIG_DIR と
    サブエージェント固定(≥2.1.257 で FORCE)を注入する。(env, scrubbed[], injected{})"""
    env = dict(base_env)
    scrubbed = [k for k in ENV_SCRUB if k in env]
    for k in scrubbed:
        env.pop(k, None)
    injected = {"CLAUDE_CONFIG_DIR": str(config_dir), "CLAUDE_CODE_SUBAGENT_MODEL": model}
    if ver is not None and ver >= RECOMMENDED_CLI:
        injected["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"] = "1"
    if project_dir_name and ver is not None and ver >= CLI_PROJECT_DIR_NAME:
        injected["CLAUDE_CODE_PROJECT_DIR_NAME"] = project_dir_name
    env.update(injected)
    return env, scrubbed, injected


CONFIG_SEED_KEYS = ("oauthAccount", "hasCompletedOnboarding", "theme", "installMethod", "userID")


def prepare_config_dir(config_dir: Path, home_config: "Path | None" = None) -> dict:
    """系列専用の CLAUDE_CONFIG_DIR を用意する(資格情報のみ複製。skills/plugins/settings は持たない)。

    - <home>/.claude/.credentials.json(OAuth 資格情報)があれば複製
    - <home>/.claude.json のうち CONFIG_SEED_KEYS だけを <config_dir>/.claude.json に写す
      (オンボーディング画面で -p が止まらないようにする最小集合。値は記録しない)
    """
    home_config = home_config or (Path.home() / ".claude")
    config_dir.mkdir(parents=True, exist_ok=True)
    info = {"config_dir": str(config_dir), "credentials_copied": False, "seeded_keys": [],
            "isolated": True}
    cred = home_config / ".credentials.json"
    if cred.is_file():
        shutil.copyfile(cred, config_dir / ".credentials.json")
        try:
            os.chmod(config_dir / ".credentials.json", 0o600)
        except OSError:
            pass
        info["credentials_copied"] = True
    src_json = home_config.parent / ".claude.json"
    seed = {"hasCompletedOnboarding": True, "autoUpdates": False}
    if src_json.is_file():
        try:
            data = json.loads(src_json.read_text(encoding="utf-8"))
            for k in CONFIG_SEED_KEYS:
                if k in data:
                    seed[k] = data[k]
        except Exception:
            pass
    (config_dir / ".claude.json").write_text(json.dumps(seed, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8")
    info["seeded_keys"] = sorted(seed.keys())
    for sub in ("skills", "plugins", "agents", "commands"):
        if (config_dir / sub).exists() and any((config_dir / sub).iterdir()):
            info["isolated"] = False
    return info


def write_series_settings(series_dir: Path) -> Path:
    series_dir.mkdir(parents=True, exist_ok=True)
    p = series_dir / "settings.json"
    p.write_text(json.dumps(SERIES_SETTINGS, indent=2) + "\n", encoding="utf-8")
    return p


def build_call(prompt: str, session_id: str, resume_id: "str | None", args,
               call_budget: float, settings_path: "str | None" = None,
               allowed_tools: "list | None" = None) -> list:
    """1発話分の claude コマンド(実行ファイル名は 'claude' プレースホルダ)を組み立てる。

    --continue は使わない: 新規セッションは --session-id <uuid4>、継続は --resume <id>。
    """
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--max-turns", str(args.max_turns),
           "--permission-mode", PERMISSION_MODE,
           "--allowedTools"] + list(allowed_tools or ALLOWED_TOOLS)
    cmd += ["--max-budget-usd", f"{call_budget:.2f}"]
    cmd += ["--model", args.model, "--effort", args.effort,
            "--setting-sources", args.setting_sources, "--strict-mcp-config"]
    if settings_path:
        cmd += ["--settings", str(settings_path)]
    if resume_id:
        cmd += ["--resume", resume_id]
    else:
        cmd += ["--session-id", session_id]
    return cmd


def effective_call_budget(call_budget: float, total_budget: float, spent: float) -> float:
    """各 call の上限 = min(call_budget, total_budget − 消費済み)。0 以下なら起動しない。"""
    return round(min(float(call_budget), float(total_budget) - float(spent)), 2)


def want_fresh_session(idx: int, prompt: str, fresh_policy: str) -> bool:
    if idx == 0:
        return True
    return fresh_policy == "fresh-on-command" and prompt.lstrip().startswith("/")


def run_claude(argv: list, env: dict, cwd: Path, timeout: int) -> dict:
    """claude を起動し stdout/stderr を返す(stdin は閉じる。fail-open にしない)。"""
    t0 = time.monotonic()
    try:
        cp = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            stdin=subprocess.DEVNULL, timeout=timeout)
        return {"returncode": cp.returncode, "stdout": cp.stdout or "",
                "stderr": cp.stderr or "", "timeout": False,
                "duration_s": round(time.monotonic() - t0, 1)}
    except subprocess.TimeoutExpired as e:
        return {"returncode": None, "stdout": (e.stdout or b"").decode("utf-8", "replace")
                if isinstance(e.stdout, bytes) else (e.stdout or ""),
                "stderr": (e.stderr or b"").decode("utf-8", "replace")
                if isinstance(e.stderr, bytes) else (e.stderr or ""),
                "timeout": True, "duration_s": round(time.monotonic() - t0, 1)}


def parse_stream(stdout: str) -> dict:
    """stream-json(1行1 JSON)から system/init と result を取り出す。"""
    init = None
    result = None
    events = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        events += 1
        if d.get("type") == "system" and d.get("subtype") == "init" and init is None:
            init = d
        elif d.get("type") == "result":
            result = d
    if result is None and stdout.strip().startswith("{"):
        # --output-format json 相当の単一 JSON にも対応(互換)
        try:
            d = json.loads(stdout)
            if d.get("type") == "result" or "total_cost_usd" in d:
                result = d
        except (json.JSONDecodeError, ValueError):
            pass
    return {"init": init, "result": result, "events": events}


def summarize_init(init: "dict | None", project_items: "dict | None" = None,
                   user_items: "dict | None" = None) -> "dict | None":
    """system/init の要約と、プロジェクト由来でない要素(ユーザースコープ漏れの疑い)。

    project_items: workdir 由来の {skills, agents, commands} 名集合(除外用)。
    user_items: 実ユーザー設定ディレクトリ由来の {skills, agents, plugins, mcp_servers} 名集合。
    init の skills/agents にはビルトイン分も含まれるため、user_items との共通部分だけを
    「漏れ」と判定し、それ以外の非プロジェクト要素は指紋(condition_hash)に入れる。
    """
    if not init:
        return None
    project_items = project_items or {}
    user_items = user_items or {}
    skills = sorted(str(s) for s in init.get("skills") or [])
    agents = sorted(str(a) for a in init.get("agents") or [])
    mcp = sorted(str(m.get("name") if isinstance(m, dict) else m)
                 for m in init.get("mcp_servers") or [])
    plugins = sorted(str(p.get("name") if isinstance(p, dict) else p)
                     for p in init.get("plugins") or [])
    nonproject_skills = [s for s in skills if s not in set(project_items.get("skills", ()))]
    nonproject_agents = [a for a in agents if a not in set(project_items.get("agents", ()))]
    leak = {}
    for key, names in (("skills", nonproject_skills), ("agents", nonproject_agents),
                       ("plugins", plugins), ("mcp_servers", mcp)):
        hit = sorted(set(names) & set(user_items.get(key, ())))
        if hit:
            leak[key] = hit
    api_key_source = init.get("apiKeySource")
    return {"claude_code_version": init.get("claude_code_version"),
            "model": init.get("model"),
            "permission_mode": init.get("permissionMode"),
            "api_key_source": api_key_source,
            "auth_mode": "api_key" if api_key_source and api_key_source != "none" else "oauth",
            "n_tools": len(init.get("tools") or []),
            "n_skills": len(skills), "n_agents": len(agents),
            "nonproject_skills": nonproject_skills,
            "nonproject_agents": nonproject_agents,
            "mcp_servers": mcp, "plugins": plugins,
            "user_scope_leak": leak}


def project_items(workdir: Path) -> dict:
    """workdir 由来のスキル/エージェント/コマンド名(init の要素からプロジェクト分を除くため)。"""
    out = {"skills": set(), "agents": set(), "commands": set()}
    for base in (workdir / ".claude" / "skills", workdir / ".github" / "skills"):
        if base.is_dir():
            out["skills"] |= {p.name for p in base.iterdir() if p.is_dir()}
    for base in (workdir / ".claude" / "agents",):
        if base.is_dir():
            out["agents"] |= {p.stem for p in base.glob("*.md")}
    for base in (workdir / ".claude" / "commands",):
        if base.is_dir():
            out["commands"] |= {p.stem for p in base.glob("*.md")}
    return out


def user_scope_items(home_config: "Path | None" = None) -> dict:
    """実ユーザー設定ディレクトリ(~/.claude)にあるスキル/エージェント/プラグイン名。"""
    home_config = home_config or (Path.home() / ".claude")
    out = {"skills": set(), "agents": set(), "plugins": set(), "mcp_servers": set()}
    if (home_config / "skills").is_dir():
        out["skills"] = {p.name for p in (home_config / "skills").iterdir() if p.is_dir()}
    if (home_config / "agents").is_dir():
        out["agents"] = {p.stem for p in (home_config / "agents").glob("*.md")}
    plug = home_config / "plugins" / "installed_plugins.json"
    if plug.is_file():
        try:
            data = json.loads(plug.read_text(encoding="utf-8"))
            names = data.get("plugins", data) if isinstance(data, dict) else {}
            out["plugins"] = {str(k).split("@")[0] for k in (names.keys()
                              if isinstance(names, dict) else [])}
        except Exception:
            pass
    return out


def classify_call(data: "dict | None", call_budget: "float | None" = None,
                  max_turns: "int | None" = None) -> dict:
    """result メッセージを分類する(subtype 単独に依存しない複合判定)。"""
    if not data:
        return {"parse_error": True, "error_class": "parse_error", "subtype": None,
                "is_error": True, "api_error_status": None, "errors": [],
                "truncated_by_call_budget": False, "max_turns_hit": False,
                "permission_denials_n": 0, "permission_denials": [],
                "num_turns": None, "total_cost_usd": None, "usage": None, "modelUsage": None,
                "session_id": None}
    subtype = data.get("subtype")
    is_error = bool(data.get("is_error"))
    status = data.get("api_error_status")
    errors = [str(e) for e in (data.get("errors") or [])]
    text = str(data.get("result") or "")
    low = (text + " \n " + " \n ".join(errors)).lower()
    quota = any(m in low for m in QUOTA_MARKERS) or status in (402, 429)
    budget = subtype == "error_max_budget_usd" or any(m in low for m in BUDGET_MARKERS)
    turns = subtype == "error_max_turns" or any(m in low for m in TURNS_MARKERS)
    model_rejected = (status == 400 and any(m in low for m in MODEL_REJECT_MARKERS))
    api_error = isinstance(status, int) and status >= 400
    if quota:
        klass = "quota"
    elif budget:
        klass = "budget_call"
    elif turns:
        klass = "max_turns"
    elif model_rejected:
        klass = "model_rejected"
    elif api_error:
        klass = "api_error"
    elif is_error or subtype == "error_during_execution":
        klass = "execution_error"
    else:
        klass = "none"
    denials = data.get("permission_denials") or []
    denial_tools = []
    for d in denials:
        if isinstance(d, dict):
            denial_tools.append(str(d.get("tool_name") or d.get("tool") or "?"))
        else:
            denial_tools.append(str(d))
    model_usage = {}
    for k, v in (data.get("modelUsage") or {}).items():
        model_usage[normalize_model_key(str(k))] = v
    return {"parse_error": False, "error_class": klass, "subtype": subtype,
            "is_error": is_error, "api_error_status": status, "errors": errors,
            "truncated_by_call_budget": klass == "budget_call",
            "max_turns_hit": klass == "max_turns",
            "permission_denials_n": len(denials), "permission_denials": denial_tools,
            "num_turns": data.get("num_turns"), "total_cost_usd": data.get("total_cost_usd"),
            "usage": data.get("usage"), "modelUsage": model_usage,
            "cost_basis": data.get("costBasis") or data.get("cost_basis"),
            "session_id": data.get("session_id"), "stop_reason": data.get("stop_reason"),
            "terminal_reason": data.get("terminal_reason"), "result_text": text}


# ---------------------------------------------------------------------------
# GATE_STATUS の読取と期待状態(EV-8)
# ---------------------------------------------------------------------------

def read_gate_status(workdir: Path) -> dict:
    """docs/00-overview/progress.md の GATE_STATUS ブロックを読む。不在は {'exists': False}。"""
    p = workdir / "docs" / "00-overview" / "progress.md"
    if not p.is_file():
        return {"exists": False}
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<!--\s*GATE_STATUS(.*?)-->", text, re.DOTALL)
    out = {"exists": True, "block_found": m is not None,
           "operating": "状態: 運用中" in text}
    if m:
        for ln in m.group(1).splitlines():
            km = re.match(r"^\s*(\w+)\s*:\s*([A-Za-z_]+)", ln)
            if km and km.group(1) in GATE_PHASES:
                out[km.group(1)] = km.group(2)
    return out


def parse_expect(expr: str) -> list:
    """'requirements=in_progress|done, design=not_started' → [(key, {values})]。"""
    out = []
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"expect の書式が不正: {part!r}(key=value|value)")
        key, vals = part.split("=", 1)
        key = key.strip()
        values = {v.strip() for v in vals.split("|") if v.strip()}
        if key not in GATE_PHASES and key not in ("exists", "operating"):
            raise ValueError(f"expect の key が不正: {key!r}")
        if key in GATE_PHASES and not values <= set(GATE_STATES):
            raise ValueError(f"expect の値が不正: {part!r}")
        out.append((key, values))
    return out


def expect_met(gate: dict, expr: "str | None") -> "bool | None":
    """期待状態が満たされているか。expr が無ければ None(検査しない)。"""
    if not expr:
        return None
    for key, values in parse_expect(expr):
        if key in ("exists", "operating"):
            want = "true" in {v.lower() for v in values}
            if bool(gate.get(key)) != want:
                return False
            continue
        if not gate.get("exists"):
            return False
        if str(gate.get(key, "not_started")) not in values:
            return False
    return True


# ---------------------------------------------------------------------------
# 作業ディレクトリ・計測・退避
# ---------------------------------------------------------------------------

GIT_ID = ["-c", "user.name=e2e-run", "-c", "user.email=e2e@example.invalid"]


def run_git(cwd: Path, *argv: str, check: bool = True) -> "subprocess.CompletedProcess":
    cp = subprocess.run(["git", "-C", str(cwd)] + list(argv), capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    if check and cp.returncode != 0:
        die(f"git {' '.join(argv)} が失敗しました: {cp.stderr.strip()}")
    return cp


def harness_archive(dest_zip: Path) -> str:
    cp = subprocess.run(["git", "-C", str(HARNESS_ROOT), "archive", "--format=zip",
                         "-o", str(dest_zip), "HEAD"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        die(f"git archive が失敗しました(本体が git リポジトリか確認): {cp.stderr.strip()}")
    return sha256_file(dest_zip)


def make_workdir(spec: ArmSpec, task: Task, workdir: Path, setup_timeout: int = 600) -> dict:
    """アーム仕様に従って使い捨て workdir を作る。

    builtin/harness: git archive HEAD を展開 → memo → git init → bootstrap commit
    builtin/bare   : 空ディレクトリ → memo → git init → bootstrap commit
    external       : 空ディレクトリ → setup 実行 → (setup が作った .git は除去) → memo →
                     git init → bootstrap commit(ルートコミットが唯一であることを検査)
    戻り値: {archive_sha256, setup_log[], apparatus_failures[], base_commit}
    """
    workdir.mkdir(parents=True, exist_ok=True)
    info = {"archive_sha256": None, "setup_log": [], "apparatus_failures": [],
            "setup_created_git": False}
    if spec.workdir_source == "git-archive-HEAD":
        zip_path = workdir / "_harness.zip"
        info["archive_sha256"] = harness_archive(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(workdir)
        zip_path.unlink()
    for cmd in spec.setup:
        rec = {"argv": cmd, "returncode": None, "stdout_tail": "", "stderr_tail": ""}
        try:
            cp = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=setup_timeout,
                                stdin=subprocess.DEVNULL)
            rec.update(returncode=cp.returncode, stdout_tail=(cp.stdout or "")[-2000:],
                       stderr_tail=(cp.stderr or "")[-2000:])
            if cp.returncode != 0:
                info["apparatus_failures"].append(f"setup_failed:{cmd[0]}")
        except (OSError, subprocess.TimeoutExpired) as e:
            rec["stderr_tail"] = str(e)[-2000:]
            info["apparatus_failures"].append(f"setup_failed:{cmd[0]}:{type(e).__name__}")
        info["setup_log"].append(rec)
    for rel in spec.expect_files:
        if not (workdir / rel).exists():
            info["apparatus_failures"].append(f"expect_file_missing:{rel}")
    if spec.setup and (workdir / ".git").exists():
        shutil.rmtree(workdir / ".git", ignore_errors=True)
        info["setup_created_git"] = True
    memo_path = workdir / "requirements" / "memo.md"
    memo_path.parent.mkdir(parents=True, exist_ok=True)
    memo_path.write_text(task.memo + "\n", encoding="utf-8")
    run_git(workdir, "init", "-q", "-b", "main")
    run_git(workdir, *GIT_ID, "add", "-A")
    run_git(workdir, *GIT_ID, "commit", "-q", "-m", "chore: e2e bootstrap")
    cp = run_git(workdir, "rev-list", "--count", "HEAD")
    if cp.stdout.strip() != "1":
        info["apparatus_failures"].append("bootstrap_not_single_root")
    info["base_commit"] = run_git(workdir, "rev-parse", "HEAD").stdout.strip()
    return info


TEST_NAME_RE = re.compile(r"(^test_|_test\.py$|\.test\.|\.spec\.|^tests?/)", re.IGNORECASE)
TEST_FUNC_RES = {
    ".py": re.compile(r"^\s*(?:async\s+)?def\s+test_", re.MULTILINE),
    ".js": re.compile(r"\b(?:it|test)\s*\(", re.MULTILINE),
    ".ts": re.compile(r"\b(?:it|test)\s*\(", re.MULTILINE),
    ".tsx": re.compile(r"\b(?:it|test)\s*\(", re.MULTILINE),
    ".jsx": re.compile(r"\b(?:it|test)\s*\(", re.MULTILINE),
    ".go": re.compile(r"^func\s+Test", re.MULTILINE),
    ".rs": re.compile(r"#\[test\]", re.MULTILINE),
}


def artifact_stats(workdir: Path, base_commit: "str | None" = None) -> dict:
    """生成物統計。git があれば bootstrap(ルート)コミットとの差分 + 未追跡だけを数える
    (ハーネス同梱ファイルを生成物として数えない。EV-5/TM-6)。全言語対象。"""
    scope_mode = "all-files"
    files: list = []
    lines_added = lines_removed = 0
    if (workdir / ".git").exists() and shutil.which("git"):
        if not base_commit:
            cp = run_git(workdir, "rev-list", "--max-parents=0", "HEAD", check=False)
            roots = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
            base_commit = roots[-1] if roots else None
        if base_commit:
            cp1 = run_git(workdir, "diff", "--numstat", base_commit, check=False)
            cp2 = run_git(workdir, "ls-files", "--others", "--exclude-standard", check=False)
            if cp1.returncode == 0 and cp2.returncode == 0:
                scope_mode = "git-diff"
                seen = set()
                for ln in cp1.stdout.splitlines():
                    parts = ln.split("\t")
                    if len(parts) != 3:
                        continue
                    add, rem, name = parts
                    seen.add(name)
                    lines_added += int(add) if add.isdigit() else 0
                    lines_removed += int(rem) if rem.isdigit() else 0
                for name in cp2.stdout.splitlines():
                    name = name.strip()
                    if name and name not in seen:
                        seen.add(name)
                        p = workdir / name
                        try:
                            lines_added += sum(1 for _ in open(p, encoding="utf-8",
                                                                 errors="replace"))
                        except OSError:
                            pass
                files = sorted(seen)
    if scope_mode == "all-files":
        skip = {".git", "__pycache__", "node_modules", ".venv"}
        for p in workdir.rglob("*"):
            if p.is_file() and not any(part in skip for part in p.parts):
                files.append(p.relative_to(workdir).as_posix())
    by_ext: dict = {}
    py = tests = test_funcs = 0
    for rel in files:
        p = workdir / rel
        if not p.is_file():
            continue
        ext = p.suffix.lower() or "(none)"
        by_ext[ext] = by_ext.get(ext, 0) + 1
        if ext == ".py":
            py += 1
        if TEST_NAME_RE.search(rel) and ext in TEST_FUNC_RES:
            tests += 1
            try:
                test_funcs += len(TEST_FUNC_RES[ext].findall(
                    p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return {"scope": scope_mode, "base_commit": base_commit, "files": len(files),
            "by_extension": dict(sorted(by_ext.items())), "python_files": py,
            "test_files": tests, "test_functions": test_funcs,
            "lines_added": lines_added, "lines_removed": lines_removed}


def run_check(task: Task, workdir: Path, stage: "int | None" = None) -> dict:
    check_py = task.path.parent / "check.py"
    cmd = [sys.executable, str(check_py), str(workdir)]
    if stage is not None:
        cmd += ["--stage", str(stage)]
    cp = subprocess.run(cmd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=1200)
    try:
        data = json.loads(cp.stdout)
    except (json.JSONDecodeError, ValueError):
        data = {"pass": False, "error": "check.py の出力を JSON として解釈できません",
                "raw_stdout": cp.stdout[-2000:], "raw_stderr": cp.stderr[-2000:]}
    data["exit_code"] = cp.returncode
    return data


def snapshot_and_bundle(workdir: Path, run_dir: Path) -> dict:
    """終了時の退避: 最終状態を退避ブランチにコミットして git bundle(--all)を作る。"""
    out = {"bundle": None, "bundle_sha256": None, "snapshot_commit": None, "note": ""}
    try:
        run_git(workdir, "checkout", "-q", "-B", "e2e/final-snapshot")
        run_git(workdir, *GIT_ID, "add", "-A")
        run_git(workdir, *GIT_ID, "commit", "-q", "--allow-empty", "-m", "e2e: final snapshot")
        out["snapshot_commit"] = run_git(workdir, "rev-parse", "HEAD").stdout.strip()
        bundle = run_dir / "repo.bundle"
        run_git(workdir, "bundle", "create", str(bundle), "--all")
        out["bundle"] = str(bundle)
        out["bundle_sha256"] = sha256_file(bundle)
    except SystemExit as e:
        out["note"] = f"bundle failed (exit {e.code})"
    except Exception as e:  # noqa: BLE001 - 退避失敗は記録して続行(結果 JSON は必ず書く)
        out["note"] = f"bundle failed: {e}"
    return out


def find_transcripts(config_dir: Path, session_ids: list) -> list:
    """<config_dir>/projects 配下から session_id に紐づく transcript(サブエージェント含む)を集める。

    encoded cwd の規則(非公開)を再実装せず、ファイル名または親ディレクトリ名が session_id に
    一致するものを探索する(監査 §6 の指示)。
    """
    projects = config_dir / "projects"
    if not projects.is_dir() or not session_ids:
        return []
    wanted = set(session_ids)
    found = []
    for p in projects.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(projects).parts
        if p.stem in wanted or any(part in wanted for part in rel_parts[:-1]):
            found.append(p)
    return sorted(found)


def archive_trajectory(config_dir: Path, session_ids: list, out_zip: Path) -> dict:
    files = find_transcripts(config_dir, session_ids)
    info = {"archived_to": None, "sha256": None, "files": len(files), "bytes": 0,
            "zip_bytes": 0, "subagent_transcripts": 0, "session_ids": list(session_ids)}
    if not files:
        return info
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    projects = config_dir / "projects"
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(projects).as_posix())
            info["bytes"] += p.stat().st_size
            if "subagents" in p.parts:
                info["subagent_transcripts"] += 1
    info["archived_to"] = str(out_zip)
    info["sha256"] = sha256_file(out_zip)
    info["zip_bytes"] = out_zip.stat().st_size
    return info


def append_manifest(entry: dict, manifest_path: Path = TRAJ_DIR / "MANIFEST.json") -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"archived_on": None, "note": "", "entries": []}
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    data.setdefault("entries", []).append(entry)
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")


# ---------------------------------------------------------------------------
# 条件・判定・妥当性
# ---------------------------------------------------------------------------

def build_conditions(args, task: Task, spec: ArmSpec, claude_ver_str: str,
                     plan: "dict | None" = None) -> dict:
    ver = parse_claude_version(claude_ver_str)
    gate = cli_gate(ver)
    allowed = sorted(set(ALLOWED_TOOLS) | set(spec.allowed_tools_extra)
                     | set(getattr(args, "allowed_tools_extra", None) or []))
    return {
        "model_requested": args.model,
        "effort_requested": args.effort,
        "claude_version": version_str(ver),
        "claude_version_raw": claude_ver_str,
        "min_cli_ok": gate["min_cli_ok"],
        "recommended_cli_ok": gate["recommended_cli_ok"],
        "budget_enforcement": gate["budget_enforcement"],
        "subagent_model_forced": gate["subagent_model_forced"],
        "residency_premium_in_estimate": gate["residency_premium_in_estimate"],
        "data_residency_premium_1_1x": bool(getattr(args, "data_residency_premium", False)),
        "setting_sources": args.setting_sources,
        "strict_mcp_config": True,
        "settings_overrides": dict(SERIES_SETTINGS),
        "config_dir_isolated": True,
        "permission_mode": PERMISSION_MODE,
        "allowed_tools": allowed,
        "max_turns_per_call": args.max_turns,
        "call_budget_usd": float(args.call_budget_usd),
        "total_budget_usd": float(args.total_budget_usd),
        "fresh_policy": args.fresh_policy,
        "call_timeout_s": args.call_timeout,
        "max_nudges_per_expect": args.max_nudges,
        "task_md_sha256": task.md_sha256,
        "check_py_sha256": task.check_sha256,
        "auth_mode": getattr(args, "auth_mode", None) or "unknown",
        "plan_sha256": plan["_sha256"] if plan else None,
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "env_scrubbed": list(ENV_SCRUB),
        "init_fingerprint": None,
    }


HASH_EXCLUDE = ("python", "claude_version_raw", "env_scrubbed")


def condition_hash(conditions: dict) -> str:
    payload = {k: v for k, v in conditions.items() if k not in HASH_EXCLUDE}
    return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))[:12]


def init_fingerprint(init_summary: "dict | None") -> "dict | None":
    if not init_summary:
        return None
    return {"claude_code_version": init_summary.get("claude_code_version"),
            "model": init_summary.get("model"),
            "nonproject_skills": init_summary.get("nonproject_skills"),
            "nonproject_agents": init_summary.get("nonproject_agents"),
            "mcp_servers": init_summary.get("mcp_servers"),
            "plugins": init_summary.get("plugins")}


def compute_totals(calls: list, conditions: dict, duration_s: float, flags: dict) -> dict:
    cost = 0.0
    turns = 0
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    cost_by_model: dict = {}
    max_call_cost = 0.0
    n_trunc = n_turns_hit = n_err = n_den = n_nudges = n_scripted = 0
    cost_basis = None
    for c in calls:
        cc = c.get("total_cost_usd")
        if isinstance(cc, (int, float)):
            cost += cc
            max_call_cost = max(max_call_cost, cc)
        if isinstance(c.get("num_turns"), int):
            turns += c["num_turns"]
        for m, u in (c.get("modelUsage") or {}).items():
            if m == "<synthetic>" or not isinstance(u, dict):
                continue
            tokens["input"] += int(u.get("inputTokens") or 0)
            tokens["output"] += int(u.get("outputTokens") or 0)
            tokens["cache_read"] += int(u.get("cacheReadInputTokens") or 0)
            tokens["cache_creation"] += int(u.get("cacheCreationInputTokens") or 0)
            cost_by_model[m] = round(cost_by_model.get(m, 0.0) + float(u.get("costUSD") or 0), 6)
        if c.get("truncated_by_call_budget"):
            n_trunc += 1
        if c.get("max_turns_hit"):
            n_turns_hit += 1
        if c.get("error_class") in ("api_error", "execution_error", "parse_error"):
            n_err += 1
        n_den += int(c.get("permission_denials_n") or 0)
        if c.get("kind") == "nudge":
            n_nudges += 1
        elif c.get("kind", "scripted") == "scripted":
            n_scripted += 1
        if c.get("cost_basis") and not cost_basis:
            cost_basis = c["cost_basis"]
    total_budget = float(conditions.get("total_budget_usd") or 0) or None
    call_budget = float(conditions.get("call_budget_usd") or 0) or None
    primary = max(cost_by_model, key=cost_by_model.get) if cost_by_model else None
    total_model_cost = sum(cost_by_model.values())
    aux_ratio = (round((total_model_cost - cost_by_model[primary]) / total_model_cost, 4)
                 if primary and total_model_cost > 0 else 0.0)
    return {"cost_usd": round(cost, 4), "num_turns": turns, "duration_s": round(duration_s, 1),
            "tokens": tokens, "cost_by_model": cost_by_model,
            "cost_basis": cost_basis or "unknown(list-price estimate)",
            "budget_used_ratio": round(cost / total_budget, 4) if total_budget else None,
            "n_calls": len(calls), "n_scripted_calls": n_scripted, "n_nudges": n_nudges,
            "n_truncated_calls": n_trunc, "n_max_turns_calls": n_turns_hit,
            "n_error_calls": n_err, "n_permission_denials": n_den,
            "max_call_cost_usd": round(max_call_cost, 4),
            "max_call_cost_ratio": round(max_call_cost / call_budget, 4) if call_budget else None,
            "model_primary": primary, "model_aux_cost_ratio": aux_ratio,
            "aborted_by_budget": bool(flags.get("aborted_by_budget")),
            "aborted_by_quota": bool(flags.get("aborted_by_quota")),
            "aborted_by_timeout": bool(flags.get("aborted_by_timeout"))}


def _hook_log_module():
    """判定ログ・事故的停止記録の読み手 .github/hooks/scripts/_log.py(本体のもの)。無ければ None。"""
    import importlib.util
    path = HARNESS_ROOT / ".github" / "hooks" / "scripts" / "_log.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("harness_hook_log", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # noqa: BLE001
        return None


def abnormal_stop_records(workdir: Path, session_ids: list) -> list:
    """走行中に workdir のフックが残した事故的停止の記録(.github/hooks/logs/abnormal-stop-*.json = StopFailure、
    watchdog-stop-*.json = watchdog の上限到達・トークン枯渇)のうち、この走行の session_id に紐づくものを返す
    (A2-1: 事故的停止で終わった走行は達成扱い=PASS にしない。読み手は _log.py の stop_records_for_sessions)。"""
    mod = _hook_log_module()
    logs = workdir / ".github" / "hooks" / "logs"
    if mod is None or not logs.is_dir():
        return []
    try:
        recs = mod.stop_records_for_sessions(list(session_ids or []), str(logs))
    except Exception:  # noqa: BLE001
        return []
    return [{"kind": r.get("kind"), "reason": r.get("reason"), "session_id": r.get("session_id"),
             "recorded_at": r.get("recorded_at"), "file": os.path.basename(str(r.get("path") or ""))} for r in recs]


def decide_verdict(calls: list, check: dict, totals: dict, experiment: str,
                   apparatus_failures: "list | None" = None) -> str:
    """verdict の優先順: INVALID(装置) > QUOTA > TIMEOUT > ERROR > TURNS > ABNORMAL_STOP(フック記録) > BUDGET_TOTAL >
    BUDGET_CALL(能力実験のみ) > check。合計超過は cost > total_budget で判定(abort フラグ非依存)。"""
    if apparatus_failures:
        return "INVALID_APPARATUS"
    classes = [c.get("error_class") for c in calls]
    if totals.get("aborted_by_quota") or "quota" in classes:
        return "DNF_QUOTA"
    if totals.get("aborted_by_timeout") or any(c.get("timeout") for c in calls):
        return "DNF_TIMEOUT"
    if any(k in ("api_error", "execution_error", "parse_error") for k in classes):
        return "DNF_ERROR"
    if "max_turns" in classes:
        return "DNF_TURNS"
    if (totals.get("n_abnormal_stops") or 0) > 0:
        return "DNF_ABNORMAL_STOP"
    if totals.get("aborted_by_budget") or (
            totals.get("budget_used_ratio") is not None and totals["budget_used_ratio"] > 1.0):
        return "DNF_BUDGET_TOTAL"
    if experiment == "capability" and totals.get("n_truncated_calls", 0) > 0:
        return "DNF_BUDGET_CALL"
    return "PASS" if check.get("pass") else "FAIL"


def validity(result: dict, experiment: str) -> tuple:
    """(valid_for_comparison, invalid_reasons[])。事前登録された妥当性条件の機械検査。"""
    reasons = []
    cond = result.get("conditions") or {}
    totals = result.get("totals") or {}
    verdict = result.get("verdict")
    if verdict == "INVALID_APPARATUS":
        reasons.append("invalid_apparatus")
    reasons += list(cli_gate(parse_claude_version(cond.get("claude_version") or ""))["invalid_reasons"])
    if cond.get("budget_enforcement") != "subagent-inclusive":
        reasons.append(f"budget_enforcement={cond.get('budget_enforcement')}")
    if not cond.get("config_dir_isolated"):
        reasons.append("user_scope_unisolated")
    fp = cond.get("init_fingerprint") or {}
    leak = (result.get("trajectory") or {}).get("user_scope_leak") or {}
    if leak:
        reasons.append("user_scope_unisolated:" + ",".join(
            f"{k}={'/'.join(v)}" for k, v in sorted(leak.items())))
    if fp and cond.get("claude_version") and fp.get("claude_code_version") \
            and fp["claude_code_version"] != cond["claude_version"]:
        reasons.append(f"cli_version_drift:{fp['claude_code_version']}")
    if cond.get("auth_mode") in (None, "unknown", "mixed"):
        reasons.append(f"auth_mode={cond.get('auth_mode')}")
    primary = totals.get("model_primary")
    if primary and cond.get("model_requested") and primary != cond["model_requested"]:
        reasons.append(f"model_mismatch:{primary}")
    if (totals.get("model_aux_cost_ratio") or 0) > AUX_MODEL_COST_RATIO_MAX:
        reasons.append(f"model_fallback_cost_ratio={totals.get('model_aux_cost_ratio')}")
    ret = result.get("retention") or {}
    if not ret.get("bundle_sha256"):
        reasons.append("retention_missing(bundle)")
    if not (result.get("trajectory") or {}).get("sha256"):
        reasons.append("retention_missing(transcript)")
    if (totals.get("n_permission_denials") or 0) > 0 and verdict != "PASS":
        reasons.append(f"permission_denials={totals.get('n_permission_denials')}")
    if experiment == "capability":
        if verdict not in ("PASS", "FAIL"):
            reasons.append(f"verdict={verdict}")
        if totals.get("n_truncated_calls", 0) > 0:
            reasons.append(f"n_truncated_calls={totals['n_truncated_calls']}")
        if totals.get("n_max_turns_calls", 0) > 0:
            reasons.append(f"n_max_turns_calls={totals['n_max_turns_calls']}")
        ratio = totals.get("budget_used_ratio")
        if ratio is None or ratio > CAPABILITY_BUDGET_RATIO_MAX:
            reasons.append(f"budget_used_ratio={ratio}>{CAPABILITY_BUDGET_RATIO_MAX}")
    elif experiment == "efficiency":
        pass  # DNF は成果。INVALID だけを除外する(上で付与済み)
    else:
        reasons.append(f"experiment={experiment}")
    # 重複を除いて順序維持
    seen = set()
    uniq = [r for r in reasons if not (r in seen or seen.add(r))]
    return (not uniq), uniq


# ---------------------------------------------------------------------------
# 計画(dry-run)と実走行
# ---------------------------------------------------------------------------

def _show_cmd(cmd: list) -> str:
    return " ".join(a if " " not in a and "\n" not in a else json.dumps(a, ensure_ascii=False)
                    for a in cmd)


def plan_calls(spec: ArmSpec, task: Task, args, settings_path: "str | None",
               allowed: list) -> list:
    """発話列(督促の枠は含まない)をコマンドに展開する(dry-run 表示用)。"""
    stages = task.stages_for(spec.replies_block)
    out = []
    idx = 0
    session = None
    spent = 0.0
    for si, st in enumerate(stages, 1):
        for reply, expect in st:
            fresh = want_fresh_session(idx, reply, args.fresh_policy)
            if fresh:
                session = "<uuid4>"
            budget = effective_call_budget(args.call_budget_usd, args.total_budget_usd, spent)
            cmd = build_call(reply, session, None if fresh else session, args, budget,
                             settings_path, allowed)
            out.append({"index": idx + 1, "stage": si, "expect": expect, "cmd": cmd})
            idx += 1
    return out


def render_plan(spec: ArmSpec, task: Task, args, claude: "str | None", conditions: dict,
                plan: "dict | None" = None, series_dir: "Path | None" = None) -> str:
    lines = ["== e2e-run 計画(schema v2) =="]
    lines.append(f"task: {task.name} ({task.path}) md_sha256={task.md_sha256[:12]} "
                 f"check_sha256={task.check_sha256[:12]}")
    lines.append(f"arm: {spec.name} (kind={spec.kind}, workdir={spec.workdir_source}, "
                 f"replies_block={spec.replies_block}, spec_sha256={spec.sha256[:12]})")
    if spec.setup:
        lines.append("setup: " + " && ".join(_show_cmd(c) for c in spec.setup))
    lines.append(f"experiment: {args.experiment} series_id={args.series_id} "
                 f"run_index={args.run_index}"
                 + (f" plan={plan['_path']} sha256={plan['_sha256'][:12]}" if plan else ""))
    lines.append(f"claude: {claude if claude else '見つかりません(実走行は不可)'} "
                 f"version={conditions['claude_version']} "
                 f"(min {version_str(MIN_CLI)} ok={conditions['min_cli_ok']}, "
                 f"budget_enforcement={conditions['budget_enforcement']}, "
                 f"forced={conditions['subagent_model_forced']})")
    lines.append(f"isolation: CLAUDE_CONFIG_DIR={series_dir / 'claude-config' if series_dir else '<runs>/<series>/claude-config'} "
                 f"--strict-mcp-config --setting-sources {args.setting_sources} "
                 f"--settings <series>/settings.json {json.dumps(SERIES_SETTINGS)}")
    lines.append(f"model: --model {args.model} --effort {args.effort} "
                 f"(env: CLAUDE_CODE_SUBAGENT_MODEL={args.model}"
                 f"{', FORCE=1' if conditions['subagent_model_forced'] else ''}; "
                 f"scrub {len(ENV_SCRUB)} vars)")
    lines.append(f"予算: --max-turns {args.max_turns}/発話, --max-budget-usd "
                 f"min({args.call_budget_usd}, {args.total_budget_usd}−消費済み)/発話, "
                 f"合計上限 {args.total_budget_usd} USD(cost > 上限で DNF_BUDGET_TOTAL)")
    lines.append(f"allowed_tools: {' '.join(conditions['allowed_tools'])}")
    lines.append(f"condition_hash(pre-init): {condition_hash(conditions)}")
    if spec.workdir_source == "git-archive-HEAD":
        lines.append("workdir: evaluation/runs/<series>/<run>/work に git archive HEAD を展開"
                     "(ハーネス適用プロジェクト)")
    else:
        lines.append("workdir: evaluation/runs/<series>/<run>/work(ハーネス無し・空)")
    lines.append(f"memo: requirements/memo.md へ配置 ({len(task.memo.encode('utf-8'))} bytes)")
    lines.append("git: init -b main → bootstrap commit(単一ルート)")
    calls = plan_calls(spec, task, args, "<series>/settings.json", conditions["allowed_tools"])
    stages = task.stages_for(spec.replies_block)
    multi = len(stages) > 1
    lines.append(f"発話 ({len(calls)}件" + (f" / {len(stages)} ステージ" if multi else "")
                 + f"; 督促は kind=nudge で最大 {args.max_nudges}/expect):")
    last_stage = 0
    for c in calls:
        if multi and c["stage"] != last_stage and last_stage:
            lines.append(f"  -- stage {last_stage} 終端 → 中間チェック: check.py --stage {last_stage} --")
        last_stage = c["stage"]
        if c["expect"] and spec.gate_status:
            lines.append(f"      (expect: {c['expect']})")
        lines.append(f"  [{c['index']}] {_show_cmd(c['cmd'])}")
    verify = f"検証: {sys.executable} {task.path.parent / 'check.py'} <workdir>"
    if multi:
        verify += f" --stage {len(stages)}"
    lines.append(verify)
    lines.append("退避: git bundle + check 出力 + GATE_STATUS 最終値 → evaluation/runs/<series>/<run>/, "
                 "transcript zip → evaluation/results/trajectories/<結果名>.zip(sha256 を JSON に)")
    lines.append(f"結果: {RESULTS_DIR / (task.name + '-' + spec.name + '-<timestamp>.json')}")
    return "\n".join(lines)


def matched_nudges(series_id: str, run_index: "int | None", task_name: str,
                   results_dir: Path = RESULTS_DIR) -> "tuple":
    """同一系列で直前(run_index が自分より小さい最大)の harness 系(gate_status あり)結果から
    督促数(ステージ別)を取る。ABAB 順では bare の run_index k は harness の k−1 と対になる。"""
    if run_index is None:
        return None, None
    best = None
    for p in sorted(results_dir.glob(f"{task_name}-*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        exp = d.get("experiment") or {}
        idx = exp.get("run_index")
        if exp.get("series_id") != series_id or not isinstance(idx, int) or idx >= run_index:
            continue
        if not (d.get("arm") or {}).get("gate_status"):
            continue
        per_stage = (d.get("totals") or {}).get("nudges_by_stage")
        if per_stage is None:
            continue
        if best is None or idx > best[0]:
            best = (idx, {int(k): v for k, v in per_stage.items()}, p.name)
    return (best[1], best[2]) if best else (None, None)


class ArmRun:
    """1 走行の状態(消費・calls・セッション)を保持する実行器。"""

    def __init__(self, spec: ArmSpec, task: Task, args, claude: str, ver_s: str,
                 ver: "tuple | None", plan: "dict | None"):
        self.spec, self.task, self.args, self.claude = spec, task, args, claude
        self.ver_s, self.ver, self.plan = ver_s, ver, plan
        self.calls: list = []
        self.state_trace: list = []
        self.spent = 0.0
        self.session_id = None
        self.flags = {"aborted_by_budget": False, "aborted_by_quota": False,
                      "aborted_by_timeout": False}
        self.apparatus_failures: list = []
        self.nudges_by_stage: dict = {}
        self.auth_modes: set = set()
        self.first_init_summary = None
        self.user_scope_leak: dict = {}
        self.init_versions: set = set()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.stamp = stamp
        self.result_stem = f"{task.name}-{spec.name}-{stamp}"
        safe_series = re.sub(r"[^A-Za-z0-9_.-]", "_", args.series_id)
        self.series_dir = RUNS_DIR / safe_series
        self.run_dir = self.series_dir / self.result_stem
        self.workdir = self.run_dir / "work"
        self.stream_dir = self.run_dir / "streams"
        self.traj_dir = TRAJ_DIR / self.result_stem
        self.config_dir = self.series_dir / "claude-config"
        self.settings_path = write_series_settings(self.series_dir)
        self.config_info = prepare_config_dir(self.config_dir)
        self.env, self.env_scrubbed, self.env_injected = build_env(
            os.environ, self.config_dir, args.model, ver, self.result_stem)
        self.conditions = build_conditions(args, task, spec, ver_s, plan)
        self.conditions["config_dir_isolated"] = bool(self.config_info.get("isolated"))
        self.conditions["config_dir_seeded_keys"] = self.config_info.get("seeded_keys")
        self.conditions["env_injected"] = sorted(self.env_injected.keys())
        self.allowed = self.conditions["allowed_tools"]
        self.project_items = None
        self.user_items = user_scope_items()

    # -- 1 call --------------------------------------------------------------
    def execute(self, prompt: str, kind: str, stage: int, expect: "str | None",
                index: int) -> dict:
        budget = effective_call_budget(self.args.call_budget_usd,
                                       self.args.total_budget_usd, self.spent)
        record = {"index": index, "kind": kind, "stage": stage, "prompt": prompt,
                  "expect": expect, "call_budget_usd_effective": budget}
        if budget <= 0:
            record["skipped"] = "budget_exhausted"
            self.flags["aborted_by_budget"] = True
            return record
        fresh = want_fresh_session(len([c for c in self.calls if not c.get("skipped")]),
                                   prompt, self.args.fresh_policy) or self.session_id is None
        if kind == "nudge":
            fresh = self.session_id is None
        resume = None if fresh else self.session_id
        if fresh:
            self.session_id = str(uuid.uuid4())
        cmd = build_call(prompt, self.session_id, resume, self.args, budget,
                         str(self.settings_path), self.allowed)
        argv = _claude_argv(self.claude) + cmd[1:]
        record.update(session_id=self.session_id, resumed_from=resume)
        say(f"[{index}] {kind} claude -p {prompt.splitlines()[0][:60]!r} "
            f"(budget {budget}, {'new' if fresh else 'resume'} {self.session_id[:8]}) ...")
        res = run_claude(argv, self.env, self.workdir, self.args.call_timeout)
        record["exit_code"] = res["returncode"]
        record["duration_s"] = res["duration_s"]
        self.stream_dir.mkdir(parents=True, exist_ok=True)
        (self.stream_dir / f"call-{index:02d}.stream.jsonl").write_text(
            res["stdout"], encoding="utf-8")
        if res["stderr"].strip():
            (self.stream_dir / f"call-{index:02d}.stderr.txt").write_text(
                res["stderr"], encoding="utf-8")
        if res["timeout"]:
            record["timeout"] = True
            record["error_class"] = "timeout"
            self.flags["aborted_by_timeout"] = True
            say(f"  発話 {index} が {self.args.call_timeout}s でタイムアウト。アームを打ち切ります。")
            return record
        parsed = parse_stream(res["stdout"])
        cls = classify_call(parsed["result"], budget, self.args.max_turns)
        text = cls.pop("result_text", "") if not cls.get("parse_error") else ""
        record.update(cls)
        if cls.get("parse_error"):
            record["raw_stdout_tail"] = res["stdout"][-2000:]
            record["raw_stderr_tail"] = res["stderr"][-2000:]
            self.apparatus_failures.append(f"parse_error@call{index}")
        # result 全文は trajectories/<結果名>/call-<i>.txt、JSON には sha256 と末尾 2000 字
        self.traj_dir.mkdir(parents=True, exist_ok=True)
        (self.traj_dir / f"call-{index:02d}.txt").write_text(text, encoding="utf-8")
        record["result_sha256"] = sha256_text(text)
        record["result_tail"] = text[-2000:]
        init = parsed["init"]
        if init is not None:
            (self.traj_dir / f"call-{index:02d}.init.json").write_text(
                json.dumps(init, ensure_ascii=False, indent=1), encoding="utf-8")
            if self.project_items is None:
                self.project_items = project_items(self.workdir)
            summ = summarize_init(init, self.project_items, self.user_items)
            record["init_summary"] = summ
            if self.first_init_summary is None:
                self.first_init_summary = summ
            self.auth_modes.add(summ["auth_mode"])
            if summ.get("claude_code_version"):
                self.init_versions.add(summ["claude_code_version"])
            for k, v in (summ.get("user_scope_leak") or {}).items():
                self.user_scope_leak[k] = sorted(set(self.user_scope_leak.get(k, [])) | set(v))
        cost = record.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            self.spent += cost
        say(f"  turns={record.get('num_turns')} cost={record.get('total_cost_usd')} "
            f"class={record.get('error_class')} ({record['duration_s']}s, 累計 {round(self.spent, 4)} USD)")
        if record.get("error_class") == "quota":
            self.flags["aborted_by_quota"] = True
            say(f"  発話 {index} でクレジット/利用上限を検知。アームを中断します(DNF_QUOTA)。")
        if record.get("error_class") == "model_rejected":
            self.apparatus_failures.append(f"model_rejected@call{index}")
            say(f"  発話 {index} でモデルが拒否されました(装置不備 INVALID_APPARATUS)。")
        return record

    def aborted(self) -> bool:
        return any(self.flags.values()) or bool(self.apparatus_failures)

    # -- expect / nudge --------------------------------------------------------
    def wait_for_state(self, expect: "str | None", before_index: int, stage: int) -> None:
        if not expect or not self.spec.gate_status:
            return
        nudges = 0
        while True:
            gate = read_gate_status(self.workdir)
            met = expect_met(gate, expect)
            self.state_trace.append({"before_call": before_index, "gate_status": gate,
                                     "expect": expect, "expect_met": met,
                                     "nudges_sent": nudges})
            if met or nudges >= self.args.max_nudges or self.aborted():
                if not met:
                    say(f"  expect {expect!r} は未達のまま続行(督促 {nudges} 回。"
                        "verdict は check で決める。state_trace に記録)")
                return
            nudges += 1
            self.nudges_by_stage[stage] = self.nudges_by_stage.get(stage, 0) + 1
            rec = self.execute(NUDGE_PROMPT, "nudge", stage, expect, len(self.calls) + 1)
            self.calls.append(rec)

    # -- run -------------------------------------------------------------------
    def run(self) -> int:
        args, task, spec = self.args, self.task, self.spec
        stages = task.stages_for(spec.replies_block)
        multi = len(stages) > 1
        self.run_dir.mkdir(parents=True, exist_ok=True)
        say(f"run_dir: {self.run_dir} (実行後も削除しません)")
        wd_info = make_workdir(spec, task, self.workdir)
        self.apparatus_failures += wd_info["apparatus_failures"]
        # bare 系: 同一 series/run_index の harness 結果に督促があれば同数の継続発話を各ステージ末尾に足す
        nudge_plan = None
        nudge_src = None
        if not spec.gate_status:
            if args.nudges is not None:
                nudge_plan = {len(stages): args.nudges}
                nudge_src = "--nudges"
            else:
                nudge_plan, nudge_src = matched_nudges(args.series_id, args.run_index, task.name)
        t0 = time.monotonic()
        stage_checks = {}
        idx = 0
        if not self.apparatus_failures:
            for si, st in enumerate(stages, 1):
                for reply, expect in st:
                    if self.aborted():
                        break
                    self.wait_for_state(expect, len(self.calls) + 1, si)
                    if self.aborted():
                        break
                    idx = len(self.calls) + 1
                    rec = self.execute(reply, "scripted", si, expect, idx)
                    self.calls.append(rec)
                    if rec.get("skipped"):
                        break
                if not spec.gate_status and nudge_plan and not self.aborted():
                    for _ in range(int(nudge_plan.get(si, 0) or 0)):
                        if self.aborted():
                            break
                        rec = self.execute(NUDGE_PROMPT, "nudge", si, None, len(self.calls) + 1)
                        self.calls.append(rec)
                        self.nudges_by_stage[si] = self.nudges_by_stage.get(si, 0) + 1
                if self.aborted():
                    break
                if multi and si < len(stages):
                    say(f"stage {si} の発話が完了。中間チェック(check.py --stage {si})を実行中...")
                    stage_checks[f"stage{si}"] = run_check(task, self.workdir, stage=si)
                    say(f"  stage {si} check: {'PASS' if stage_checks[f'stage{si}'].get('pass') else 'FAIL'}")
        duration = time.monotonic() - t0

        say("check.py で受入検証を実行中...")
        check = run_check(task, self.workdir, stage=(len(stages) if multi else None))
        if check.get("exit_code") == 2 or "error" in check:
            self.apparatus_failures.append("check_error")
        stats = artifact_stats(self.workdir, wd_info.get("base_commit"))
        gate_final = read_gate_status(self.workdir) if spec.gate_status else None

        # 退避(再検証可能性)
        self.traj_dir.mkdir(parents=True, exist_ok=True)
        (self.traj_dir / "check-final.json").write_text(
            json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
        if gate_final is not None:
            (self.traj_dir / "gate-status-final.json").write_text(
                json.dumps(gate_final, ensure_ascii=False, indent=1), encoding="utf-8")
        retention = snapshot_and_bundle(self.workdir, self.run_dir)
        retention.update(run_dir=str(self.run_dir), workdir=str(self.workdir),
                         check_output=str(self.traj_dir / "check-final.json"),
                         gate_status_final=gate_final)
        session_ids = sorted({c.get("session_id") for c in self.calls if c.get("session_id")})
        traj = {"config_dir": str(self.config_dir), "session_ids": session_ids,
                "stream_dir": str(self.stream_dir), "user_scope_leak": self.user_scope_leak}
        if args.archive_transcripts:
            traj.update(archive_trajectory(self.config_dir, session_ids,
                                           TRAJ_DIR / f"{self.result_stem}.zip"))
        else:
            traj.update({"archived_to": None, "sha256": None, "files": 0})

        # 条件の確定(init 指紋・auth_mode)と判定
        self.conditions["init_fingerprint"] = init_fingerprint(self.first_init_summary)
        if self.auth_modes:
            self.conditions["auth_mode"] = (next(iter(self.auth_modes))
                                            if len(self.auth_modes) == 1 else "mixed")
        if len(self.init_versions) > 1:
            self.apparatus_failures.append("cli_version_drift:" + "/".join(sorted(self.init_versions)))
        totals = compute_totals(self.calls, self.conditions, duration, self.flags)
        totals["nudges_by_stage"] = {str(k): v for k, v in sorted(self.nudges_by_stage.items())}
        totals["nudge_source"] = nudge_src
        stops = abnormal_stop_records(self.workdir, session_ids)
        totals["n_abnormal_stops"] = len(stops)
        verdict = decide_verdict(self.calls, check, totals, args.experiment, self.apparatus_failures)
        if totals["n_permission_denials"] > 0 and verdict != "PASS":
            verdict = "INVALID_APPARATUS"
            self.apparatus_failures.append("permission_denied:" + ",".join(
                sorted({t for c in self.calls for t in (c.get("permission_denials") or [])})))
        result = {
            "schema_version": SCHEMA_VERSION,
            "task": {"name": task.name, "md_sha256": task.md_sha256,
                     "check_sha256": task.check_sha256},
            "arm": {"name": spec.name, "kind": spec.kind, "spec_path": str(spec.path) if spec.path else None,
                    "spec_sha256": spec.sha256, "replies_block": spec.replies_block,
                    "workdir_source": spec.workdir_source, "gate_status": spec.gate_status,
                    "pin": spec.pin, "harness_commit": _harness_commit(),
                    "harness_archive_sha256": wd_info.get("archive_sha256"),
                    "setup_log": wd_info.get("setup_log"),
                    "setup_created_git": wd_info.get("setup_created_git")},
            "experiment": {"kind": args.experiment, "series_id": args.series_id,
                           "run_index": args.run_index, "condition_hash": condition_hash(self.conditions),
                           "plan_path": self.plan["_path"] if self.plan else None,
                           "plan_sha256": self.plan["_sha256"] if self.plan else None,
                           "budget_rung": getattr(args, "budget_rung", None)},
            "conditions": self.conditions,
            "calls": self.calls,
            "state_trace": self.state_trace,
            "totals": totals,
            "verdict": verdict,
            "valid_for_comparison": None,
            "invalid_reasons": [],
            "apparatus_failures": self.apparatus_failures,
            "abnormal_stops": stops,
            "check": check,
            "stages": {"sizes": [len(st) for st in stages], "intermediate_checks": stage_checks},
            "artifact_stats": stats,
            "trajectory": traj,
            "retention": retention,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "harness_commit": _harness_commit(),
            "claude_version": self.ver_s,
            "workdir": str(self.workdir),
        }
        valid, reasons = validity(result, args.experiment)
        result["valid_for_comparison"] = valid
        result["invalid_reasons"] = reasons
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / f"{self.result_stem}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if traj.get("archived_to"):
            append_manifest({"result_json": out.name, "source_dir": traj.get("config_dir"),
                             "zip": Path(traj["archived_to"]).name, "files": traj.get("files"),
                             "bytes": traj.get("bytes"), "zip_bytes": traj.get("zip_bytes"),
                             "sha256": traj.get("sha256"), "session_ids": session_ids,
                             "archived_on": time.strftime("%Y-%m-%d")})
        say("== サマリ ==")
        say(f"arm={spec.name} task={task.name} verdict={verdict} valid_for_comparison={valid}")
        if reasons:
            say("  invalid_reasons: " + "; ".join(reasons))
        for key in sorted(stage_checks):
            say(f"  中間 {key}: {'PASS' if stage_checks[key].get('pass') else 'FAIL'}")
        say(f"発話 {totals['n_scripted_calls']} 件 + 督促 {totals['n_nudges']} 件, "
            f"合計 {totals['num_turns']} turns, {totals['cost_usd']} USD "
            f"(上限比 {totals['budget_used_ratio']}), {totals['duration_s']}s, "
            f"打切り {totals['n_truncated_calls']} 件")
        say(f"生成物({stats['scope']}): {stats['files']} files / py {stats['python_files']} / "
            f"tests {stats['test_files']} ({stats['test_functions']} test functions)")
        say(f"結果: {out}")
        say(f"run_dir: {self.run_dir}")
        return 0 if verdict == "PASS" else 1


def run_arm(spec: ArmSpec, task: Task, args, plan: "dict | None" = None) -> int:
    claude = find_claude()
    if not claude:
        die("claude CLI が見つかりません。インストールと PATH を確認してください"
            "(https://docs.claude.com/claude-code)。--dry-run は claude 無しでも使えます。")
    ver_s, ver, _gate = require_cli(claude, args)
    if not claude_supports_max_budget(claude):
        die("この claude CLI は --max-budget-usd 未対応です(予算規律を満たせないため実走行しない)。")
    return ArmRun(spec, task, args, claude, ver_s, ver, plan).run()


def _harness_commit() -> str:
    cp = subprocess.run(["git", "-C", str(HARNESS_ROOT), "rev-parse", "--short", "HEAD"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return cp.stdout.strip() if cp.returncode == 0 else "unknown"


# ---------------------------------------------------------------------------
# selftest(claude を起動しない)
# ---------------------------------------------------------------------------

def _args(extra: "list | None" = None, **kw):
    base = ["--arm", "harness", "--experiment", "capability", "--series-id", "selftest",
            "--model", "claude-opus-5", "--effort", "high", "--total-budget-usd", "60",
            "--call-budget-usd", "60", "--dry-run"]
    args = build_parser().parse_args(base + (extra or []))
    resolve_defaults(args)
    for k, v in kw.items():
        setattr(args, k, v)
    return args


def selftest() -> int:
    failures = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            say(f"PASS: {name}")
        else:
            failures.append(name)
            say(f"FAIL: {name} {detail}")

    # 1. タスク定義
    try:
        task = load_task("todo-cli")
    except SystemExit:
        say("FAIL: todo-cli タスク定義の読み込み")
        return 1
    check("memo が非空", len(task.memo) > 50)
    check("harness 発話が複数", len(task.replies_harness) >= 5, f"(件数={len(task.replies_harness)})")
    check("初回発話は /00 起動", task.replies_harness[0].startswith("/00"))
    check("bare 発話数が harness と同数(予算同等)", len(task.replies_bare) == len(task.replies_harness))
    check("task.md / check.py の sha256 を保持", len(task.md_sha256) == 64 and len(task.check_sha256) == 64)

    # 2. 発話パーサ(expect 注釈)
    replies = _split_replies("# コメント\n一行目\n二行目\n---\n次の発話\n---\n")
    check("--- 区切りの発話分解", replies == ["一行目\n二行目", "次の発話"], f"(実際={replies!r})")
    ex = _split_replies_ex("# expect: requirements=done|in_progress\n/05-x\n---\n# memo\nb\n")
    check("# expect: 注釈を発話ごとに保持", ex == [("/05-x", "requirements=done|in_progress"), ("b", None)],
          f"(実際={ex!r})")
    blocks = _fenced_blocks("x\n```memo\nM\n```\ny\n```scripted-replies\nR\n```\n")
    check("フェンスブロック抽出", blocks.get("memo") == "M\n" and blocks.get("scripted-replies") == "R\n")
    stages = _split_stages("a\n---\nb\n===\nc\n")
    check("=== 区切りのステージ分解", stages == [["a", "b"], ["c"]], f"(実際={stages!r})")
    check("todo-cli は単一ステージ", len(task.stages_harness) == 1)
    has_expect = any(e for st in task.blocks["scripted-replies"] for _r, e in st)
    check("todo-cli 台本に expect 注釈がある(EV-8)", has_expect)
    for st in task.blocks["scripted-replies"]:
        for _r, e in st:
            if e:
                try:
                    parse_expect(e)
                except ValueError as err:
                    check(f"expect 注釈が解析できる: {e}", False, str(err))
    task2 = None
    try:
        task2 = load_task("expense-webapp")
    except SystemExit:
        failures.append("expense-webapp 読み込み")
        say("FAIL: expense-webapp タスク定義の読み込み")
    if task2:
        check("expense-webapp は2ステージ(harness)", len(task2.stages_harness) == 2)
        check("expense-webapp は2ステージ(bare)", len(task2.stages_bare) == 2)
        check("各ステージで bare と harness の発話数が同数(予算同等)",
              [len(s) for s in task2.stages_bare] == [len(s) for s in task2.stages_harness])
        check("第2ステージは /12 変更依頼で開始", task2.stages_harness[1][0].startswith("/12"))
        check("speckit 台本(scripted-replies-speckit)が harness と同ステージ数",
              "scripted-replies-speckit" in task2.blocks
              and [len(s) for s in task2.blocks["scripted-replies-speckit"]]
              == [len(s) for s in task2.stages_harness],
              str([len(s) for s in task2.blocks.get("scripted-replies-speckit", [])]))

    # 3. 版数の門
    check("版数解析 '2.1.201 (Claude Code)'", parse_claude_version("2.1.201 (Claude Code)") == (2, 1, 201))
    check("2.1.201 → main-only", budget_enforcement((2, 1, 201)) == "main-only")
    check("2.1.217 → subagent-inclusive", budget_enforcement((2, 1, 217)) == "subagent-inclusive")
    g201 = cli_gate((2, 1, 201))
    check("2.1.201 は min_cli 未達で cli<2.1.217 と cli<2.1.251 を理由に持つ",
          not g201["min_cli_ok"] and g201["invalid_reasons"] == ["cli<2.1.217", "cli<2.1.251"],
          str(g201))
    g251 = cli_gate((2, 1, 251))
    check("2.1.251 は min_cli ok・FORCE なし", g251["min_cli_ok"] and not g251["subagent_model_forced"])
    g257 = cli_gate((2, 1, 257))
    check("2.1.257 は推奨版・FORCE あり・1.1x 加算版", g257["recommended_cli_ok"]
          and g257["subagent_model_forced"] and g257["residency_premium_in_estimate"])
    check("版数不明は理由 cli_version_unknown", cli_gate(None)["invalid_reasons"] == ["cli_version_unknown"])
    check("フル model ID を受理", is_full_model_id("claude-fable-5-1") and is_full_model_id("claude-opus-5")
          and is_full_model_id("claude-haiku-4-5-20251001"))
    check("エイリアスを拒否", not any(is_full_model_id(m) for m in ("fable", "opus", "opusplan", "opus[1m]", "claude-fable")))
    check("modelUsage キーの [1m] 正規化", normalize_model_key("claude-opus-5[1m]") == "claude-opus-5")

    # 4. 環境(掃除と注入)
    base_env = {"PATH": "x", "CLAUDE_CODE_EFFORT_LEVEL": "high", "CLAUDE_CODE_SUBAGENT_MODEL": "haiku",
                "CLAUDE_CONFIG_DIR": "/home/u/.claude"}
    env, scrubbed, injected = build_env(base_env, Path("/tmp/cfg"), "claude-opus-5", (2, 1, 257), "run1")
    check("ユーザースコープの effort/model 変数を掃除",
          set(scrubbed) == {"CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CONFIG_DIR"}
          and "CLAUDE_CODE_EFFORT_LEVEL" not in env)
    check("隔離 CONFIG_DIR・SUBAGENT_MODEL・FORCE(≥2.1.257)・PROJECT_DIR_NAME を注入",
          env["CLAUDE_CONFIG_DIR"] == str(Path("/tmp/cfg")) and env["CLAUDE_CODE_SUBAGENT_MODEL"] == "claude-opus-5"
          and env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"] == "1" and env["CLAUDE_CODE_PROJECT_DIR_NAME"] == "run1")
    env2, _s, inj2 = build_env(base_env, Path("/tmp/cfg"), "claude-opus-5", (2, 1, 251), "run1")
    check("2.1.251 では FORCE を注入しない", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE" not in env2)

    # 5. 予算とコマンド組立
    check("call 上限 = min(call_budget, total−spent)", effective_call_budget(10, 60, 55) == 5.0
          and effective_call_budget(10, 60, 0) == 10.0 and effective_call_budget(10, 60, 60) == 0.0)
    a = _args()
    cmd_new = build_call("hi", "sid-1", None, a, 10, "s.json")
    cmd_res = build_call("hi", "sid-1", "sid-1", a, 3.5, "s.json")
    check("新規セッションは --session-id、--continue は使わない",
          "--session-id" in cmd_new and "--continue" not in cmd_new and "--resume" not in cmd_new)
    check("継続は --resume <id>", "--resume" in cmd_res and "--session-id" not in cmd_res)
    for needle in ("--output-format", "stream-json", "--verbose", "--effort", "--strict-mcp-config",
                   "--setting-sources", "--settings", "--max-budget-usd", "--model", "--permission-mode"):
        check(f"コマンドに {needle}", needle in cmd_new)
    check("動的 call 上限がコマンドに載る", cmd_res[cmd_res.index("--max-budget-usd") + 1] == "3.50")
    check("fresh-on-command: スラッシュ発話で新セッション",
          want_fresh_session(3, "/05-x", "fresh-on-command") and not want_fresh_session(3, "続き", "fresh-on-command")
          and not want_fresh_session(3, "/05-x", "continue") and want_fresh_session(0, "x", "continue"))

    # 6. 分類器(複合判定)
    c = classify_call({"subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": 0.55,
                       "errors": ["Reached maximum budget ($0.5)"], "modelUsage": {"claude-fable-5": {"costUSD": 0.55}}})
    check("subtype=error_max_budget_usd → truncated_by_call_budget", c["truncated_by_call_budget"]
          and c["error_class"] == "budget_call")
    c = classify_call({"subtype": "success", "is_error": True, "api_error_status": 400,
                       "result": "API Error: 400 Claude Code 2.1.201 does not support this model; version 2.1.251 or newer is required."})
    check("subtype=success でも is_error+400 でモデル拒否を検出(装置不備)", c["error_class"] == "model_rejected")
    c = classify_call({"subtype": "success", "is_error": True,
                       "result": "You're out of usage credits. Run /usage-credits to keep using Fable 5"})
    check("利用上限文言 → quota", c["error_class"] == "quota")
    c = classify_call({"subtype": "error_max_turns", "is_error": True, "result": ""})
    check("error_max_turns → max_turns_hit", c["max_turns_hit"] and c["error_class"] == "max_turns")
    c = classify_call({"subtype": "error_during_execution", "is_error": True, "result": "boom"})
    check("error_during_execution → execution_error", c["error_class"] == "execution_error")
    c = classify_call({"subtype": "success", "is_error": False, "api_error_status": None, "result": "OK",
                       "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "npm i"}}],
                       "modelUsage": {"claude-opus-5[1m]": {"costUSD": 1.0}}})
    check("正常 call: class none・denials 件数とツール名・modelUsage 正規化",
          c["error_class"] == "none" and c["permission_denials_n"] == 1 and c["permission_denials"] == ["Bash"]
          and list(c["modelUsage"].keys()) == ["claude-opus-5"])
    check("result 不在 → parse_error", classify_call(None)["error_class"] == "parse_error")
    ps = parse_stream('{"type":"system","subtype":"init","claude_code_version":"2.1.257","skills":["a"]}\n'
                      'garbage\n{"type":"assistant"}\n{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1.5}\n')
    check("stream-json から init と result を抽出", ps["init"]["claude_code_version"] == "2.1.257"
          and ps["result"]["total_cost_usd"] == 1.5 and ps["events"] == 3)
    summ = summarize_init({"claude_code_version": "2.1.257", "model": "claude-opus-5", "apiKeySource": "none",
                           "skills": ["gate-check", "deep-research", "verify"], "agents": ["reviewer", "Explore"],
                           "mcp_servers": [{"name": "claude.ai Gmail", "status": "connected"}], "plugins": []},
                          {"skills": {"gate-check"}, "agents": {"reviewer"}},
                          {"skills": {"deep-research"}, "plugins": set(), "agents": set(),
                           "mcp_servers": {"claude.ai Gmail"}})
    check("init 要約: プロジェクト由来を除き、ユーザースコープ漏れを検出",
          summ["nonproject_skills"] == ["deep-research", "verify"] and summ["auth_mode"] == "oauth"
          and summ["user_scope_leak"] == {"skills": ["deep-research"], "mcp_servers": ["claude.ai Gmail"]},
          json.dumps(summ, ensure_ascii=False))
    check("apiKeySource=ANTHROPIC_API_KEY → auth_mode api_key",
          summarize_init({"apiKeySource": "ANTHROPIC_API_KEY"})["auth_mode"] == "api_key")

    # 7. GATE_STATUS と expect
    with tempfile.TemporaryDirectory(prefix="e2e-selftest-") as tmp:
        wd = Path(tmp) / "wd"
        (wd / "docs" / "00-overview").mkdir(parents=True)
        check("progress.md 不在は exists=False", read_gate_status(wd) == {"exists": False})
        (wd / "docs" / "00-overview" / "progress.md").write_text(
            "<!-- GATE_STATUS\nrequirements: done\ndesign: in_progress\nimplementation: not_started\n"
            "test: not_started\nrelease: not_started\n-->\n# 進捗\n", encoding="utf-8")
        gate = read_gate_status(wd)
        check("GATE_STATUS を読める", gate["exists"] and gate["requirements"] == "done" and gate["design"] == "in_progress")
        check("expect 判定: 到達状態(OR)と未達", expect_met(gate, "requirements=done, design=in_progress|done") is True
              and expect_met(gate, "implementation=in_progress|done") is False
              and expect_met(gate, None) is None and expect_met({"exists": False}, "requirements=done") is False
              and expect_met(gate, "exists=true") is True)
        try:
            parse_expect("bogus=done")
            check("expect の不正 key はエラー", False)
        except ValueError:
            check("expect の不正 key はエラー", True)

        # 8. artifact_stats(git-diff スコープ)と退避
        if shutil.which("git"):
            g = Path(tmp) / "g"
            g.mkdir()
            (g / "noise.py").write_text("def test_noise():\n    pass\n", encoding="utf-8")
            run_git(g, "init", "-q", "-b", "main")
            run_git(g, *GIT_ID, "add", "-A")
            run_git(g, *GIT_ID, "commit", "-q", "-m", "bootstrap")
            base = run_git(g, "rev-parse", "HEAD").stdout.strip()
            (g / "app.py").write_text("print(1)\n", encoding="utf-8")
            (g / "test_app.py").write_text("def test_a():\n    pass\n\ndef test_b():\n    pass\n", encoding="utf-8")
            (g / "noise.py").write_text("def test_noise():\n    pass\n\ndef test_more():\n    pass\n", encoding="utf-8")
            st = artifact_stats(g, base)
            check("artifact_stats は git-diff スコープ(bootstrap 同梱を数えない・変更分は数える)",
                  st["scope"] == "git-diff" and st["files"] == 3 and st["python_files"] == 3
                  and st["test_files"] == 1 and st["test_functions"] == 2 and st["lines_added"] >= 5,
                  json.dumps(st))
            st_all = artifact_stats(g, None)
            check("base_commit 省略時はルートコミットを bootstrap とみなす",
                  st_all["scope"] == "git-diff" and st_all["base_commit"] == base and st_all["files"] == 3)
            rd = Path(tmp) / "run"
            rd.mkdir()
            ret = snapshot_and_bundle(g, rd)
            check("git bundle を退避し sha256 を持つ", ret["bundle_sha256"] and Path(ret["bundle"]).is_file(), ret["note"])
            cfg = Path(tmp) / "cfg"
            sid = "11111111-2222-4333-8444-555555555555"
            (cfg / "projects" / "X--dir").mkdir(parents=True)
            (cfg / "projects" / "X--dir" / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")
            (cfg / "projects" / "X--dir" / sid / "subagents").mkdir(parents=True)
            (cfg / "projects" / "X--dir" / sid / "subagents" / "agent-a1.jsonl").write_text("{}\n", encoding="utf-8")
            (cfg / "projects" / "X--dir" / "other.jsonl").write_text("{}\n", encoding="utf-8")
            found = find_transcripts(cfg, [sid])
            check("transcript を session_id 探索で集める(サブエージェント含む・他セッション除外)",
                  len(found) == 2 and all(sid in p.as_posix() for p in found))
            tj = archive_trajectory(cfg, [sid], Path(tmp) / "t.zip")
            check("transcript を zip 退避し sha256/件数を記録", tj["files"] == 2 and tj["subagent_transcripts"] == 1
                  and tj["sha256"] and Path(tj["archived_to"]).is_file())
            info = prepare_config_dir(Path(tmp) / "isolated", home_config=Path(tmp) / "nohome" / ".claude")
            check("隔離 CONFIG_DIR は .claude.json のみ(skills/plugins 無し)で isolated",
                  info["isolated"] and (Path(tmp) / "isolated" / ".claude.json").is_file()
                  and not info["credentials_copied"])
            # make_workdir(実際の git archive HEAD → 展開 → memo → bootstrap 単一ルート)
            wd_h = Path(tmp) / "wd-harness"
            info_h = make_workdir(load_arm_spec("harness"), task, wd_h)
            check("harness workdir: 配布物を展開し memo と単一ルートの bootstrap を持つ",
                  (wd_h / ".github" / "harness" / "USAGE.md").is_file()
                  and (wd_h / "requirements" / "memo.md").is_file()
                  and info_h["archive_sha256"] and info_h["apparatus_failures"] == []
                  and run_git(wd_h, "rev-list", "--count", "HEAD").stdout.strip() == "1",
                  json.dumps(info_h, ensure_ascii=False)[:300])
            check("harness workdir: 測定装置(e2e-run/eval-report)と DECISIONS.md は配布物に含まれない",
                  not (wd_h / "tools" / "e2e-run.py").exists() and not (wd_h / "tools" / "eval-report.py").exists()
                  and not (wd_h / "DECISIONS.md").exists() and not (wd_h / "evaluation").exists())
            wd_b = Path(tmp) / "wd-bare"
            info_b = make_workdir(load_arm_spec("bare"), task, wd_b)
            check("bare workdir: memo だけを持ち bootstrap 単一ルート",
                  (wd_b / "requirements" / "memo.md").is_file() and not (wd_b / ".github").exists()
                  and info_b["archive_sha256"] is None and info_b["apparatus_failures"] == [])
            check("bare の初期 GATE_STATUS は exists=False(督促は harness 系のみ)",
                  read_gate_status(wd_b) == {"exists": False} and not load_arm_spec("bare").gate_status)
            # 督促の対称化: 直前の harness 結果(run_index 1)から bare(run_index 2)へ同数を写す
            rdir = Path(tmp) / "results"
            rdir.mkdir()
            (rdir / "todo-cli-harness-x.json").write_text(json.dumps(
                {"experiment": {"series_id": "S", "run_index": 1}, "arm": {"name": "harness", "gate_status": True},
                 "totals": {"nudges_by_stage": {"1": 2}}}), encoding="utf-8")
            (rdir / "todo-cli-harness-y.json").write_text(json.dumps(
                {"experiment": {"series_id": "S", "run_index": 3}, "arm": {"name": "harness", "gate_status": True},
                 "totals": {"nudges_by_stage": {"1": 1}}}), encoding="utf-8")
            got, src = matched_nudges("S", 2, "todo-cli", rdir)
            got4, _src4 = matched_nudges("S", 4, "todo-cli", rdir)
            check("bare の督促数は直前の harness 走行(ABAB の対)から取る",
                  got == {1: 2} and src == "todo-cli-harness-x.json" and got4 == {1: 1}
                  and matched_nudges("S", 1, "todo-cli", rdir) == (None, None))
        else:
            say("SKIP: git が無いため artifact_stats/bundle/transcript の検証を省略")

        # 9. アーム仕様(JSON・依存ゼロ)
        for name in ("harness", "bare"):
            spec = load_arm_spec(name)
            check(f"arms/{name}.json を読める", spec.name == name and spec.kind == "builtin")
        sk = load_arm_spec("external:speckit")
        check("speckit は external・pin・setup(--integration)・allowed_tools_extra・expect_files を持つ",
              sk.kind == "external" and sk.pin.get("ref") and sk.setup and "--integration" in sk.setup[0]
              and sk.allowed_tools_extra and sk.expect_files)
        bad = ArmSpec({"name": "x", "kind": "external", "pin": {"repo": "r", "ref": "v1"},
                       "setup": [["uvx", "specify", "init", ".", "--ai", "claude"]]})
        check("--ai を含む setup は拒否", any("--ai" in p for p in bad.validate()))
        (Path(tmp) / "bad.json").write_text('{"name": "Bad Name", "kind": "weird"}', encoding="utf-8")
        try:
            load_arm_spec("x", Path(tmp) / "bad.json")
            check("不正なアーム仕様はエラー", False)
        except SystemExit as e:
            check("不正なアーム仕様はエラー", e.code == 2)

        # 10. 条件と condition_hash(アーム差で変わらず、model 差で変わる)
        a_h = _args()
        cond_h = build_conditions(a_h, task, load_arm_spec("harness"), "2.1.257 (Claude Code)")
        cond_b = build_conditions(a_h, task, load_arm_spec("bare"), "2.1.257 (Claude Code)")
        check("同一条件なら harness と bare の condition_hash が一致", condition_hash(cond_h) == condition_hash(cond_b))
        a_m = _args(model="claude-fable-5-1")
        cond_m = build_conditions(a_m, task, load_arm_spec("harness"), "2.1.257 (Claude Code)")
        check("model が違えば condition_hash が変わる", condition_hash(cond_m) != condition_hash(cond_h))
        cond_sk = build_conditions(a_h, task, sk, "2.1.257 (Claude Code)")
        check("speckit の allowed_tools_extra は conditions.allowed_tools に入る(全アームへ同一適用が必要)",
              set(sk.allowed_tools_extra) <= set(cond_sk["allowed_tools"]) and condition_hash(cond_sk) != condition_hash(cond_h))
        extra_argv = [x for t in sk.allowed_tools_extra for x in ("--allowed-tools-extra", t)]
        a_x = _args(extra_argv)
        cond_hx = build_conditions(a_x, task, load_arm_spec("harness"), "2.1.257 (Claude Code)")
        check("--allowed-tools-extra を harness に与えると speckit と同一 hash になる(全アーム同一適用)",
              condition_hash(cond_hx) == condition_hash(cond_sk))
        check("python 版は hash から除外(記録はする)", "python" in cond_h and "python" in HASH_EXCLUDE)

        # 11. verdict 優先順と妥当性条件
        def mk(calls, cost, total=60.0, check_pass=True, flags=None):
            conds = {"total_budget_usd": total, "call_budget_usd": 10.0}
            totals = compute_totals(calls, conds, 100.0, flags or {})
            return decide_verdict(calls, {"pass": check_pass}, totals, "capability"), totals
        ok_call = {"total_cost_usd": 5.0, "num_turns": 3, "error_class": "none",
                   "modelUsage": {"claude-opus-5": {"costUSD": 5.0, "inputTokens": 10, "outputTokens": 5,
                                                    "cacheReadInputTokens": 100, "cacheCreationInputTokens": 20}}}
        v, t = mk([ok_call, ok_call], 10)
        check("正常 2 call・check PASS → PASS", v == "PASS" and t["cost_usd"] == 10.0 and t["tokens"]["cache_read"] == 200
              and t["model_primary"] == "claude-opus-5", str(t))
        v, _t = mk([ok_call], 5, check_pass=False)
        check("check FAIL → FAIL", v == "FAIL")
        v, _t = mk([ok_call, dict(ok_call, error_class="quota")], 10, flags={"aborted_by_quota": True})
        check("quota は最優先(check PASS でも DNF_QUOTA)", v == "DNF_QUOTA")
        v, _t = mk([ok_call, dict(ok_call, timeout=True, error_class="timeout")], 10, flags={"aborted_by_timeout": True})
        check("timeout → DNF_TIMEOUT", v == "DNF_TIMEOUT")
        v, _t = mk([ok_call, dict(ok_call, error_class="execution_error")], 10)
        check("execution_error → DNF_ERROR(BUDGET より優先)", v == "DNF_ERROR")
        v, _t = mk([ok_call, dict(ok_call, error_class="max_turns", max_turns_hit=True)], 10)
        check("max_turns → DNF_TURNS(新設)", v == "DNF_TURNS")
        t_ab = compute_totals([ok_call], {"total_budget_usd": 60.0, "call_budget_usd": 10.0}, 1.0, {})
        t_ab["n_abnormal_stops"] = 1
        check("事故的停止の記録あり → DNF_ABNORMAL_STOP(TURNS より後・BUDGET より先。A2-1)",
              decide_verdict([ok_call], {"pass": True}, t_ab, "capability") == "DNF_ABNORMAL_STOP"
              and decide_verdict([ok_call, dict(ok_call, error_class="max_turns", max_turns_hit=True)], {"pass": True}, t_ab, "capability") == "DNF_TURNS"
              and decide_verdict([ok_call], {"pass": True}, t_ab, "efficiency") == "DNF_ABNORMAL_STOP")
        with tempfile.TemporaryDirectory() as td_ab:
            wd_ab = Path(td_ab)
            logs_ab = wd_ab / ".github" / "hooks" / "logs"
            logs_ab.mkdir(parents=True)
            (logs_ab / "abnormal-stop-s1.json").write_text(json.dumps({"session_id": "s1", "error_type": "rate_limit",
                                                                       "recorded_at": "2026-09-17T10:00:00+09:00"}), encoding="utf-8")
            (logs_ab / "watchdog-stop-s2.json").write_text(json.dumps({"session_id": "s2", "kind": "watchdog_limit", "reason": "limit-reached 3/3",
                                                                       "recorded_at": "2026-09-17T11:00:00+09:00"}), encoding="utf-8")
            got = abnormal_stop_records(wd_ab, ["s1", "s2", "s3"])
            check("事故的停止の記録を workdir の hooks/logs から session_id で拾う(StopFailure + watchdog。_log.py の読み手)",
                  [g["kind"] for g in got] == ["abnormal_stop", "watchdog_limit"] and got[0]["reason"] == "rate_limit"
                  and got[1]["file"] == "watchdog-stop-s2.json", str(got))
            check("記録が無い session_id・logs 無しは空", abnormal_stop_records(wd_ab, ["s9"]) == []
                  and abnormal_stop_records(wd_ab / "none", ["s1"]) == [])
        big = dict(ok_call, total_cost_usd=35.0)
        v, t = mk([big, big], 70)
        check("cost > total_budget は abort フラグ無しでも DNF_BUDGET_TOTAL", v == "DNF_BUDGET_TOTAL"
              and t["budget_used_ratio"] > 1.0)
        trunc = dict(ok_call, total_cost_usd=10.0, truncated_by_call_budget=True, error_class="budget_call")
        v, t = mk([ok_call, trunc], 15)
        check("能力実験で打切り 1 件 → DNF_BUDGET_CALL", v == "DNF_BUDGET_CALL" and t["n_truncated_calls"] == 1)
        totals_e = compute_totals([ok_call, trunc], {"total_budget_usd": 60.0, "call_budget_usd": 10.0}, 1.0, {})
        check("効率実験では打切りを許容し件数を報告", decide_verdict([ok_call, trunc], {"pass": True}, totals_e, "efficiency") == "PASS")
        check("装置不備は INVALID_APPARATUS", decide_verdict([ok_call], {"pass": True}, t, "capability", ["setup_failed:x"]) == "INVALID_APPARATUS")
        # validity 6 条件(能力実験)
        good = {"verdict": "PASS", "conditions": {"claude_version": "2.1.257", "budget_enforcement": "subagent-inclusive",
                                                  "config_dir_isolated": True, "auth_mode": "api_key",
                                                  "model_requested": "claude-opus-5", "init_fingerprint": {"claude_code_version": "2.1.257"}},
                "totals": {"n_truncated_calls": 0, "n_max_turns_calls": 0, "budget_used_ratio": 0.5,
                           "model_primary": "claude-opus-5", "model_aux_cost_ratio": 0.0, "n_permission_denials": 0},
                "retention": {"bundle_sha256": "x"}, "trajectory": {"sha256": "y", "user_scope_leak": {}}}
        ok, rs = validity(good, "capability")
        check("妥当性: 全条件充足で valid", ok and rs == [], str(rs))
        bad1 = json.loads(json.dumps(good)); bad1["totals"]["budget_used_ratio"] = 0.82
        check("妥当性: budget_used_ratio > 0.7 で invalid", not validity(bad1, "capability")[0])
        bad2 = json.loads(json.dumps(good)); bad2["totals"]["n_truncated_calls"] = 1
        check("妥当性: 打切りありで invalid", "n_truncated_calls=1" in validity(bad2, "capability")[1])
        bad3 = json.loads(json.dumps(good)); bad3["conditions"]["claude_version"] = "2.1.201"; bad3["conditions"]["budget_enforcement"] = "main-only"
        rs3 = validity(bad3, "capability")[1]
        check("妥当性: 2.1.201 は cli<2.1.217・cli<2.1.251・budget_enforcement=main-only",
              {"cli<2.1.217", "cli<2.1.251", "budget_enforcement=main-only"} <= set(rs3), str(rs3))
        bad4 = json.loads(json.dumps(good)); bad4["totals"]["model_primary"] = "claude-fable-5"; bad4["totals"]["model_aux_cost_ratio"] = 0.05
        rs4 = validity(bad4, "capability")[1]
        check("妥当性: 主モデル不一致と補助モデル費用比 >1% を理由に持つ",
              any(r.startswith("model_mismatch") for r in rs4) and any(r.startswith("model_fallback") for r in rs4))
        bad5 = json.loads(json.dumps(good)); bad5["trajectory"]["user_scope_leak"] = {"skills": ["ai-video-publish"]}
        check("妥当性: ユーザースコープ漏れは user_scope_unisolated", any(r.startswith("user_scope_unisolated") for r in validity(bad5, "capability")[1]))
        bad6 = json.loads(json.dumps(good)); bad6["verdict"] = "DNF_BUDGET_TOTAL"
        check("妥当性: 能力実験で DNF は invalid、効率実験では valid",
              not validity(bad6, "capability")[0] and validity(bad6, "efficiency")[0])
        bad7 = json.loads(json.dumps(good)); bad7["retention"] = {}; bad7["trajectory"]["sha256"] = None
        check("妥当性: 退避欠落(bundle/transcript)は invalid(再検証可能性)", len(validity(bad7, "capability")[1]) == 2)
        bad8 = json.loads(json.dumps(good)); bad8["verdict"] = "FAIL"; bad8["totals"]["n_permission_denials"] = 2
        check("妥当性: PASS 未到達 + permission_denials で invalid", not validity(bad8, "capability")[0])
        check("妥当性: PASS なら permission_denials は記録のみ",
              validity(dict(good, totals=dict(good["totals"], n_permission_denials=2)), "capability")[0])

        # 12. dry-run 計画
        a_h = _args()
        cond = build_conditions(a_h, task, load_arm_spec("harness"), "unknown")
        plan_h = render_plan(load_arm_spec("harness"), task, a_h, "claude(検証用)", cond)
        for needle in ["--max-turns", "--permission-mode acceptEdits", "Bash(python:*)", "Bash(git:*)",
                       "/00-start-project", "--output-format stream-json", "--max-budget-usd", "git archive",
                       "requirements/memo.md", "check.py", "--session-id", "--resume", "--effort high",
                       "--strict-mcp-config", "--setting-sources project,local", "condition_hash", "expect:",
                       "git bundle"]:
            check(f"harness 計画に {needle!r}", needle in plan_h)
        check("計画に --continue が無い", "--continue" not in plan_h)
        check("harness 計画の発話数が定義と一致", plan_h.count("claude -p") == len(task.replies_harness))
        a_f = _args(["--fresh-policy", "fresh-on-command"])
        plan_f = render_plan(load_arm_spec("harness"), task, a_f, "claude(検証用)", cond)
        n_cmd = sum(1 for r in task.replies_harness if r.startswith("/"))
        check("fresh-on-command: スラッシュ発話ごとに --session-id(新規)", plan_f.count("--session-id") == n_cmd)
        plan_b = render_plan(load_arm_spec("bare"), task, a_h, None, cond)
        check("bare 計画はハーネス無し", "ハーネス無し" in plan_b)
        check("claude 不在が計画に明示", "見つかりません" in plan_b)
        if task2:
            plan2 = render_plan(load_arm_spec("harness"), task2, a_h, "claude(検証用)",
                                build_conditions(a_h, task2, load_arm_spec("harness"), "unknown"))
            check("多段計画に stage 1 の中間チェック", "--stage 1" in plan2)
            check("多段計画の最終検証は --stage 2", "--stage 2" in plan2)

        # 13. 事前登録(plan)の読み込みと ABAB
        plans = sorted(EXPERIMENTS_DIR.glob("*.json"))
        check("evaluation/experiments/ に事前登録 JSON がある", len(plans) >= 1)
        for pp in plans:
            try:
                pl = load_plan(pp)
                check(f"事前登録 {pp.name} が読める", bool(pl["_sha256"]))
                arms = [plan_arm_for_index(pl, i) for i in range(1, 5)]
                check(f"{pp.name}: ABAB 順", arms == [pl["arms"][0], pl["arms"][1 % len(pl['arms'])]] * 2)
                if pl.get("budget_ladder_usd"):
                    check(f"{pp.name}: ラダーの series_id と予算", plan_series_id(pl, 0).endswith(f"/B{pl['budget_ladder_usd'][0]:g}")
                          and plan_budgets(pl, 0)[1] == float(pl["budget_ladder_usd"][0]))
                else:
                    cb, tb = plan_budgets(pl, None)
                    check(f"{pp.name}: 能力実験は call 上限 = 合計上限", cb == tb)
            except SystemExit as e:
                check(f"事前登録 {pp.name} が読める", False, f"exit {e.code}")
        (Path(tmp) / "badplan.json").write_text('{"series_id": "x", "experiment": "capability", "task": "todo-cli", "arms": ["harness"], "model": "opus", "effort": "high", "budgets": {"total_usd": 1}}', encoding="utf-8")
        try:
            load_plan(Path(tmp) / "badplan.json")
            check("エイリアス model の事前登録は拒否", False)
        except SystemExit as e:
            check("エイリアス model の事前登録は拒否", e.code == 2)

    # 14. 存在しないタスクは明示エラー
    try:
        load_task("no-such-task-xyz")
        check("未知タスクはエラー", False)
    except SystemExit as e:
        check("未知タスクはエラー", e.code == 2)

    say(f"selftest: {'OK' if not failures else 'NG'} (FAIL {len(failures)} 件)")
    return 0 if not failures else 1


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="E2E A/B 測定ランナー v2(harness / bare / external アーム。詳細は evaluation/README.md)")
    ap.add_argument("--task", default=None, help="evaluation/tasks/ 配下のタスク名(既定: todo-cli。--plan では計画が正)")
    ap.add_argument("--arm", help="harness | bare | external:<name>(evaluation/arms/<name>.json)")
    ap.add_argument("--arm-spec", help="アーム仕様 JSON のパス(--arm の代わり)")
    ap.add_argument("--plan", help="事前登録 JSON(evaluation/experiments/<series>.json)。arms/予算/model/effort はここが正")
    ap.add_argument("--run-index", type=int, help="系列内の走行番号(1 始まり。--plan では ABAB 順でアームを決める)")
    ap.add_argument("--next", action="store_true", help="--plan の系列で次の run_index を自動採番する")
    ap.add_argument("--budget-rung", type=int, help="効率実験ラダーの段(0 始まり)")
    ap.add_argument("--experiment", choices=EXPERIMENTS, help="capability(能力) | efficiency(効率)")
    ap.add_argument("--series-id", help="系列 ID(同一条件の走行をまとめる。集計は hash 一致だけを同一表に載せる)")
    ap.add_argument("--model", help="claude に渡す --model(フル ID 必須。例 claude-opus-5 / claude-fable-5-1)")
    ap.add_argument("--effort", choices=EFFORT_LEVELS, help="claude に渡す --effort(必須)")
    ap.add_argument("--dry-run", action="store_true", help="実行せず計画のみ表示(claude 不在でも可)")
    ap.add_argument("--selftest", action="store_true", help="自己検証(claude を起動しない)")
    ap.add_argument("--max-turns", type=int, default=None, help="発話ごとの --max-turns(既定: 60)")
    ap.add_argument("--call-budget-usd", "--session-budget-usd", dest="call_budget_usd", type=float,
                    default=None, help="発話ごとの --max-budget-usd 上限(実際は min(これ, 合計−消費済み))")
    ap.add_argument("--total-budget-usd", type=float, default=None,
                    help="アーム全体のコスト上限 USD(cost > 上限で DNF_BUDGET_TOTAL)")
    ap.add_argument("--call-timeout", type=int, default=3600, help="発話ごとの実時間タイムアウト秒(既定: 3600)")
    ap.add_argument("--fresh-policy", choices=FRESH_POLICIES, default=None,
                    help="continue(全発話を --resume で継続) | fresh-on-command(スラッシュ発話で新セッション)")
    ap.add_argument("--fresh-on-command", action="store_true", help="(互換) --fresh-policy fresh-on-command")
    ap.add_argument("--setting-sources", default="project,local",
                    help="--setting-sources(既定 project,local。ユーザースコープは CLAUDE_CONFIG_DIR 隔離で遮断)")
    ap.add_argument("--allowed-tools-extra", action="append", default=None,
                    help="allowedTools への追加(同一系列の全アームに同じ値を与える。例 'Bash(bash .specify/*)')")
    ap.add_argument("--max-nudges", type=int, default=None,
                    help=f"expect 未達時の督促上限(1 expect あたり。既定 {DEFAULT_MAX_NUDGES}。kind=nudge として記録)")
    ap.add_argument("--nudges", type=int, default=None,
                    help="bare 系アームに与える継続発話数(省略時は同一 series/run_index の harness 結果から自動)")
    ap.add_argument("--auth-mode", choices=["api_key", "oauth"], default=None,
                    help="課金経路の宣言(省略時は init.apiKeySource から判定)")
    ap.add_argument("--data-residency-premium", action="store_true",
                    help="データ常駐ワークスペース(費用推定に 1.1x 加算)であることを記録")
    ap.add_argument("--archive-transcripts", dest="archive_transcripts", action="store_true", default=True)
    ap.add_argument("--no-archive-transcripts", dest="archive_transcripts", action="store_false")
    ap.add_argument("--allow-old-cli", action="store_true",
                    help=f"CLI が {version_str(MIN_CLI)} 未満でも続行する(valid_for_comparison=false)")
    return ap


def resolve_defaults(args) -> None:
    """--plan との衝突検査のため parser 既定値を None にしている項目を、ここで既定に落とす。"""
    if args.task is None:
        args.task = "todo-cli"
    if args.max_turns is None:
        args.max_turns = 60
    if args.max_nudges is None:
        args.max_nudges = DEFAULT_MAX_NUDGES
    if getattr(args, "fresh_on_command", False) and not args.fresh_policy:
        args.fresh_policy = "fresh-on-command"
    if not args.fresh_policy:
        args.fresh_policy = "continue"
    if args.call_budget_usd is None and args.total_budget_usd is not None:
        args.call_budget_usd = args.total_budget_usd


def _apply_plan(args, plan: dict) -> None:
    """事前登録の値を args に写す(CLI で違う値が与えられていたらエラー: 事前登録が正)。"""
    def take(key, value):
        cur = getattr(args, key, None)
        if cur not in (None, False, []) and cur != value:
            die(f"--{key.replace('_', '-')}={cur!r} は事前登録 {plan['_path']} の {value!r} と異なる(事前登録が正)")
        setattr(args, key, value)
    if args.next:
        args.run_index = next_run_index(plan_series_id(plan, args.budget_rung))
    if args.run_index is None:
        die("--plan では --run-index <k> か --next が必要")
    take("task", plan["task"])
    take("experiment", plan["experiment"])
    take("series_id", plan_series_id(plan, args.budget_rung))
    take("model", plan["model"])
    take("effort", plan["effort"])
    cb, tb = plan_budgets(plan, args.budget_rung)
    take("call_budget_usd", cb)
    take("total_budget_usd", tb)
    if plan.get("max_turns_per_call"):
        take("max_turns", int(plan["max_turns_per_call"]))
    if plan.get("fresh_policy"):
        take("fresh_policy", plan["fresh_policy"])
    if plan.get("allowed_tools_extra"):
        take("allowed_tools_extra", list(plan["allowed_tools_extra"]))
    if plan.get("max_nudges") is not None:
        take("max_nudges", int(plan["max_nudges"]))
    if not args.arm and not args.arm_spec:
        args.arm = plan_arm_for_index(plan, args.run_index)


def main(argv: list) -> int:
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    if args.selftest:
        return selftest()
    plan = None
    if args.plan:
        plan = load_plan(Path(args.plan))
        _apply_plan(args, plan)
    resolve_defaults(args)
    if not args.arm and not args.arm_spec:
        die("--arm harness | bare | external:<name>(または --arm-spec / --plan)を指定してください")
    for key in ("experiment", "series_id", "model", "effort"):
        if not getattr(args, key):
            die(f"--{key.replace('_', '-')} は必須です(固定条件 C。--plan で事前登録から与えてもよい)")
    if not is_full_model_id(args.model):
        die(f"--model はフル ID で指定してください(エイリアス不可): {args.model!r}")
    if args.total_budget_usd is None:
        die("--total-budget-usd は必須です(事前登録ラダー/B_cap を明示する)")
    if args.call_budget_usd is None:
        args.call_budget_usd = args.total_budget_usd
    if args.experiment == "capability" and args.call_budget_usd != args.total_budget_usd:
        say("WARN: 能力実験では call 上限=合計上限が事前登録の既定(発話単位の拘束を消す)。")
    spec = load_arm_spec(args.arm or "", Path(args.arm_spec) if args.arm_spec else None)
    task = load_task(args.task)
    if args.dry_run:
        claude = find_claude()
        ver_s = claude_version(claude) if claude else "unknown"
        conditions = build_conditions(args, task, spec, ver_s, plan)
        safe_series = re.sub(r"[^A-Za-z0-9_.-]", "_", args.series_id)
        say(render_plan(spec, task, args, claude, conditions, plan, RUNS_DIR / safe_series))
        return 0
    return run_arm(spec, task, args, plan)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

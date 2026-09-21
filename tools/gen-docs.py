#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鏡の生成物化(第3回監査 A3-4): 説明文書の件数・対応表を一次データから生成する。

「同じ事実を書いた複数の面(N面鏡)」の手編集ドリフトを、検査(対症療法)ではなく
生成(根治)で塞ぐ。一次データは次の3つ:
- .github/prompts/*.prompt.md の frontmatter(ファイル名・agent・description)
- .github/skills/*/SKILL.md の一覧(スキル数。Copilot Agent Host 向け入口スキル <nn>-<name>/ は
  prompts からの生成物=コマンドの鏡なので手順部品の数に含めない。判定の正は tools/copilot_entry_skills.py)
  と .github/agents/*.agent.md の一覧(エージェント数)
- .github/hooks/scripts/selftest.sh / selftest.ps1 の静的ケース数
  (sh: ^check 呼び出し行 + check() を経由しない手動判定ブロック数。
   ps1: ^Check 呼び出し行 + Check() を経由しない手動判定ブロック数。
   tools/validate-harness.py の (c) 検査と同一の数え方)
  **静的計数であり実行結果ではない**: pass/fail の正は各 selftest の実行結果。
  生成物には件数だけを書き、「全PASS」「検証済み」等の品質主張は書かない
  (再監査 2026-09-09 RG-12: CI 未緑・ps1 2 FAIL の状態で「全PASS」を機械生成していた)。

生成先(マーカー <!-- BEGIN GENERATED: <name> --> / <!-- END GENERATED: <name> --> の間):
- README.md                  : phase-command-table(フェーズとエージェント/プロンプト対応表)
                               selftest-counts(フック数・自己テスト件数の1行)
- .github/harness/overview.html : overview-stats(統計タイルのエージェント数・スキル数・
                                  コマンド数・フック自己テスト静的ケース数)
                                  selftest-counts(検査スイート説明の件数 span)
- .github/harness/README.md  : harness-doc-counts(agents/skills/commands/guardrails 各一覧の件数文言)
- .github/harness/COMPARISON.md   : selftest-counts(§6 検証スイートの件数行)
                                    decision-count(§6 設計決定の件数行。DECISIONS.md の ^## D 静的計数)
                                    comparison-measured(§7 A/B 実測表。evaluation/REPORT.md からの機械転記)
- .github/harness/COMPARISON.html : selftest-counts(3行サマリの件数 span)
                                    decision-count-stat / decision-count(統計タイルと固有資産カードの決定数)
                                    comparison-measured(実測 A/B 節の走行別 verdict・費用表)
- .github/harness/guardrails.html : selftest-counts / selftest-counts-table(件数 span ×2)
- .github/harness/USAGE.md   : usage-config-thresholds(セッション分割表の文脈閾値。tools/usage-config.json が正)
                               usage-session-table(セッション分割表の本体。コマンド集合は prompts が正)
- .github/harness/agents.html   : agents-hero-counts(冒頭の 11/7/3/1 の件数文)
                                  agents-phase-table(フェーズ専属エージェント表。役割=frontmatter description、
                                  起動コマンド=prompts の agent: バインド、ツール権限=tools alias、
                                  呼べるサブエージェント=agents 許可リスト)
                                  agents-sub-table(サブエージェント表と本体保守エージェントの注記)
- .github/harness/skills.html   : skills-hero-count(冒頭の件数) / skills-sections(カテゴリ別スキル表。
                                  何をするか=SKILL.md description。^NN- のハーネス入口スキルは
                                  自動で「ハーネス入口」カテゴリに分離=w2-skills の Copilot 入口スキル向け)
- .github/harness/commands.html : commands-hero-count(冒頭の件数) / commands-sections(カテゴリ別コマンド表。
                                  何が起きるか=prompt description、担当=agent: バインド)
- .github/harness/PLATFORM.md     : platform-requirements(最低安全バージョン表。一次データは
                                    .github/harness/platform-requirements.json。A6-8 / A3-3)
- DECISIONS.md               : decisions-index(D 番号 / 短縮題名 / 日付の索引表。任意ブロック=マーカー未設置なら SKIP。
                                    A7-M-1。分割しない理由は DECISIONS_INDEX_NOTE)
(第6回 A3-4b / A7-A1-1〜4 / A5-7 で 6 面 9 ブロック → 11 面 22 ブロックへ拡張)

一次データから生成できない列・数値(フェーズ名・成果物列・フック数・カテゴリ分け・セッション欄・
サブエージェントの基本姿勢等)は、このファイル内の引き継ぎ辞書(PHASE_TABLE_ROWS / CARRYOVER /
AGENT_* / SKILL_* / COMMAND_* / USAGE_SESSION_ROWS)が正。プロンプト・スキル・エージェントを
追加・削除・改名したときは対応する辞書も更新する(未登録・孤児はエラーになり CI で検出される)。
件数(スキル数・コマンド数・エージェント数・決定数)は文書に手で書かず、生成ブロックか
「件数の正は X」の非数値表現にする(tools/validate-harness.py (c-2) がハードコードを検出する)。

使い方(リポジトリルートで):
    python tools/gen-docs.py            # dry-run: 生成結果と現状の差分を表示(書き込まない)
    python tools/gen-docs.py --apply    # マーカー間に生成結果を書き込む
    python tools/gen-docs.py --check    # 再生成しても差分が出ないかを exit code で返す
    python tools/gen-docs.py --selftest # REPORT.md 転記パーサ・frontmatter リスト解釈・辞書照合の自己テスト

終了コード: 0 = 差分なし(--apply では書込成功)
            1 = --check で差分あり(python tools/gen-docs.py --apply で再生成する)
            2 = 実行エラー(マーカー欠落・表メタと prompts の不整合等)
依存: Python 3.x 標準ライブラリのみ
"""
from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Copilot 入口スキル(.github/skills/<nn>-<name>/。prompts からの生成物)はスキル数に数えない。
# 判定の正は tools/copilot_entry_skills.py(標準ライブラリだけで import できる)。無ければ名前規則で代替
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from copilot_entry_skills import is_entry_skill_name as _is_entry_skill_name
except Exception:  # noqa: BLE001
    def _is_entry_skill_name(name):
        return bool(re.match(r"^\d\d-", name or ""))

# ---- 引き継ぎ辞書(一次データから生成できない値。ここが正) -------------------------
# README の対応表のうち、frontmatter に無い列(#・フェーズ・成果物)と、
# 対応エージェント列の注記(→ subagent 経路)の引き継ぎ。
# 各行: (No, フェーズ, [コマンドbase名...], エージェント注記サフィックス, 成果物)
# エージェント名そのものは各プロンプトの frontmatter(agent:)から取る(行内で不一致ならエラー)。
PHASE_TABLE_ROWS = [
    ("-", "進捗確認", ["00-start-project", "99-status"], "",
     "`docs/00-overview/progress.md`"),
    ("1", "要件定義（初期）", ["01-requirements-intake"], "",
     "質問リスト"),
    ("2", "要件定義（詳細化）", ["02-requirements-deepdive"], "（→ `spec-critic`を承認前に1回）",
     "`docs/01-requirements/*.md`"),
    ("3", "設計（アーキテクチャ）", ["03-design-architecture"], "",
     "`docs/02-design/architecture.md`, `adr/`"),
    ("4", "設計（詳細）", ["04-design-detailed"], "（→ `spec-critic`を承認前に1回）",
     "`docs/02-design/detailed-design/`"),
    ("5", "実装計画", ["05-implementation-plan"], "",
     "`docs/03-implementation/tasks.md`"),
    ("6", "実装", ["06-implement-task"],
     "（→ `task-worker`をタスクごとに、`reviewer`を完了タスク10個ごと・完了時にsubagent呼び出し）",
     "ソースコード + テスト"),
    ("7", "テスト計画", ["07-test-plan"], "",
     "`docs/04-test/test-plan.md`"),
    ("8", "テスト実行", ["08-test-execute"], "（→ `reviewer`をsubagent呼び出し）",
     "`docs/04-test/test-report.md`"),
    ("9", "リリース", ["09-release-checklist"], "",
     "`docs/05-release/release-checklist.md`, `CHANGELOG.md`"),
    ("10", "振り返り", ["10-retrospective"], "",
     "`docs/06-retrospective/retrospective.md`"),
    ("11", "既存コードベース導入", ["11-brownfield-intake"], "",
     "as-is逆起こしdocs一式"),
    ("12", "**変更請求（運用中の日常）**", ["12-change-request"],
     "（→ `task-worker` / `spec-critic` / `reviewer`）",
     "`docs/00-overview/change-requests.md` + 差分更新されたdocs"),
    ("13", "収束監査（運用中の定期棚卸し）", ["13-converge"], "",
     "docs⇔実態の乖離の棚卸しと起票（対処は `/06`・`/12` の通常フロー）"),
    ("-", "還流適用（**本体リポジトリ専用**）", ["90-apply-retrospective"], "",
     "改善提案の本体適用 + `DECISIONS.md` 追記"),
    ("-", "逆同期（本体の改善をプロジェクトへ）", ["91-sync-from-harness"], "",
     "ハーネスコピーの更新（`tools/sync-harness.py` と併用）"),
    ("-", "使い方ヘルプ", ["98-harness-help"], "",
     "（次の一手の案内）"),
]

CARRYOVER = {
    # 既定サブエージェント経路の数(agents.html の解説と対)
    "subagent_routes": "5",
}

# ---- 生成テンプレート(現行文書の文言と一致させる。文言変更はここを直して --apply) ----
OVERVIEW_STATS_TEMPLATE = (
    '    <div class="stat"><b>{agents}</b><span>専属エージェント<br>'
    '({subagent_routes}つの既定サブエージェント経路)</span></div>\n'
    '    <div class="stat"><b>{skills}</b><span>Agent Skills<br>(段階的開示で低コスト)</span></div>\n'
    '    <div class="stat"><b>{commands}</b><span>スラッシュコマンド<br>(Copilot / Claude Code の2環境)</span></div>\n'
    '    <div class="stat"><b>{hook_count}</b><span>機械的フック (+opt-in {hook_opt_in})<br>'
    '(自己テスト sh {selftest} / ps1 {selftest_ps1} ケース。pass/fail の正は各 selftest の実行結果)</span></div>'
)

HARNESS_DOC_COUNTS_TEMPLATE = (
    "- [agents.html](agents.html) — エージェント一覧（{agents}の役割・ツール権限・"
    "{subagent_routes}つの既定サブエージェント経路）。\n"
    "- [skills.html](skills.html) — スキル一覧（{skills}の手順部品をカテゴリ別に解説）。\n"
    "- [commands.html](commands.html) — コマンド一覧（{commands}のスラッシュコマンド。"
    "いつ・何が起きる・セッションの目安）。\n"
    "- [guardrails.html](guardrails.html) — ガードレール解説（{hook_count}のフック + opt-in {hook_opt_in}・"
    "多層防御・環境別の強度差）。"
)

# フック数・自己テスト件数の1行(README / COMPARISON.md 用。Markdown)。
# 静的計数だけを書き、pass/fail・「全PASS」は書かない(RG-12)。
SELFTEST_COUNTS_MD_TEMPLATE = (
    "- **機械的フック {hook_count} 本（+opt-in {hook_opt_in}）・フック自己テスト "
    "selftest.sh {selftest} / selftest.ps1 {selftest_ps1} ケース**"
    "（件数は check 行の静的計数＝生成物。pass/fail の正は各 selftest の実行結果であり、"
    "CI 緑を確認するまで「全PASS」は書かない）。"
)

# HTML 文中に差し込む件数 span(COMPARISON.html / guardrails.html / overview.html 用)
SELFTEST_COUNTS_HTML_TEMPLATE = (
    '<span class="gen-count">sh {selftest} / ps1 {selftest_ps1} ケース'
    '（静的計数。pass/fail の正は各 selftest の実行結果）</span>'
)


def read_frontmatter(path):
    """frontmatter の単一行スカラー(agent/description 等)を最小構文で読む(PyYAML 非依存)。"""
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    fm = {}
    if not m:
        return fm
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not km:
            continue
        v = km.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1].replace("''", "'") if v[0] == "'" else v[1:-1]
        fm[km.group(1)] = v
    return fm


def count_selftest_cases(path):
    """selftest.sh の実測ケース数。^check 呼び出し行 + 手動判定ブロック数
    (pass カウンタ増分から check() 関数内の1回を除いた数)。
    tools/validate-harness.py の (c) 検査と同一の数え方に保つこと。"""
    text = open(path, encoding="utf-8").read()
    checks = len(re.findall(r"^check ", text, re.M))
    manual = max(0, len(re.findall(r"pass=\$\(\(pass\+1\)\)", text)) - 1)
    return checks + manual


def count_selftest_ps1_cases(path):
    """selftest.ps1 の静的ケース数。^Check 呼び出し行 + 手動判定ブロック数
    ($script:pass++ の出現数から Check() 関数内の1回を除いた数)。
    count_selftest_cases の ps1 版(再監査 2026-09-09 RG-12: ps1 件数が手書きだった)。
    tools/validate-harness.py の (c) 検査と同一の数え方に保つこと。"""
    text = open(path, encoding="utf-8-sig").read()
    checks = len(re.findall(r"^Check ", text, re.M))
    manual = max(0, len(re.findall(r"\$script:pass\+\+", text)) - 1)
    return checks + manual


# フック本体ではない補助(statusline は statusLine.command から参照されるがフックではない=監査 §5.3-1。
# session-baseline / log-subagent は配線フックとして数える。_paths / _log は全ガードが source する
# 共通ライブラリで、フックではない=D072 / A6-20)
HOOK_HELPER_SCRIPTS = {"selftest", "run-python", "_paths", "_log", "statusline"}


def count_hooks(errors):
    """配線済みフック数と opt-in(未配線)フック数を一次データから数える。
    配線の正 = .claude/settings.json / gate-hooks.json / security-hooks.json が
    参照するスクリプト名(補助スクリプトを除く。同名の .sh/.ps1/.py は1フックと数える)。
    opt-in = scripts/ に実在するのにどの配線にも現れないフック(例: watchdog-continue)。
    このハードコードの排除は再監査(external-reaudit-2026-08-31)の「フック全文書13 vs
    実測14」指摘への恒久対策。"""
    wired = set()
    for parts in (
        (".claude", "settings.json"),
        (".github", "hooks", "gate-hooks.json"),
        (".github", "hooks", "security-hooks.json"),
    ):
        path = os.path.join(ROOT, *parts)
        if not os.path.exists(path):
            errors.append(f"{os.path.join(*parts)} が見つからない(フック数を数えられない)")
            continue
        text = open(path, encoding="utf-8-sig").read()
        for m in re.finditer(r"scripts[/\\]+([A-Za-z0-9_.-]+?)\.(?:sh|ps1|py)\b", text):
            wired.add(m.group(1))
    wired -= HOOK_HELPER_SCRIPTS
    on_disk = {
        re.sub(r"\.(sh|ps1|py)$", "", os.path.basename(p))
        for p in glob.glob(os.path.join(ROOT, ".github", "hooks", "scripts", "*"))
        if re.search(r"\.(sh|ps1|py)$", p)
    } - HOOK_HELPER_SCRIPTS
    if not wired:
        errors.append("フック配線が1件も見つからない(配線ファイルの形式変更を疑う)")
    return len(wired), len(on_disk - wired)


def collect_data(errors):
    """一次データを収集する。"""
    prompts = {}
    for f in sorted(glob.glob(os.path.join(ROOT, ".github", "prompts", "*.prompt.md"))):
        base = os.path.basename(f)[: -len(".prompt.md")]
        fm = read_frontmatter(f)
        if not fm.get("agent"):
            errors.append(f".github/prompts/{base}.prompt.md: frontmatter に agent が無い")
        prompts[base] = fm
    skills = sorted(
        name for name in (os.path.basename(os.path.dirname(f))
                          for f in glob.glob(os.path.join(ROOT, ".github", "skills", "*", "SKILL.md")))
        if not _is_entry_skill_name(name)
    )
    agents = sorted(
        os.path.basename(f)[: -len(".agent.md")]
        for f in glob.glob(os.path.join(ROOT, ".github", "agents", "*.agent.md"))
    )
    selftest_sh = os.path.join(ROOT, ".github", "hooks", "scripts", "selftest.sh")
    if os.path.exists(selftest_sh):
        selftest = count_selftest_cases(selftest_sh)
    else:
        selftest = None
        errors.append(".github/hooks/scripts/selftest.sh が見つからない(実測ケース数を出せない)")
    selftest_ps1_path = os.path.join(ROOT, ".github", "hooks", "scripts", "selftest.ps1")
    if os.path.exists(selftest_ps1_path):
        selftest_ps1 = count_selftest_ps1_cases(selftest_ps1_path)
    else:
        selftest_ps1 = None
        errors.append(".github/hooks/scripts/selftest.ps1 が見つからない(静的ケース数を出せない)")
    hook_count, hook_opt_in = count_hooks(errors)
    # 第6回追補: 一覧本文の一次データ(frontmatter 全体)・決定数・A/B 実測レポート
    agent_fms = {n: read_frontmatter(os.path.join(ROOT, ".github", "agents", f"{n}.agent.md")) for n in agents}
    skill_fms = {n: read_frontmatter(os.path.join(ROOT, ".github", "skills", n, "SKILL.md")) for n in skills}
    decisions_md = os.path.join(ROOT, "DECISIONS.md")
    decisions = count_decisions(decisions_md) if os.path.exists(decisions_md) else None
    eval_report = _load_eval_report(errors)
    return {"prompts": prompts, "skills": skills, "agents": agents, "selftest": selftest,
            "selftest_ps1": selftest_ps1,
            "hook_count": hook_count, "hook_opt_in": hook_opt_in,
            "agent_fms": agent_fms, "skill_fms": skill_fms, "decisions": decisions,
            "eval_report": eval_report}


def gen_phase_table(data, errors):
    """README.md のフェーズとエージェント/プロンプト対応表を生成する。"""
    prompts = data["prompts"]
    used = set()
    lines = [
        "| # | フェーズ | プロンプト | 対応エージェント | 成果物 |",
        "|---|---|---|---|---|",
    ]
    for num, phase, commands, agent_suffix, artifact in PHASE_TABLE_ROWS:
        row_agents = []
        for c in commands:
            if c in used:
                errors.append(f"PHASE_TABLE_ROWS: /{c} が複数行に登録されている")
            if c not in prompts:
                errors.append(f"PHASE_TABLE_ROWS: 行「{phase}」が参照する "
                              f".github/prompts/{c}.prompt.md が存在しない(表メタの更新漏れ)")
                continue
            used.add(c)
            row_agents.append(prompts[c].get("agent", ""))
        if not row_agents:
            continue
        if len(set(row_agents)) > 1:
            errors.append(f"PHASE_TABLE_ROWS: 行「{phase}」内のコマンド {commands} の "
                          f"frontmatter agent が不一致: {row_agents}")
        cmd_cell = ", ".join(f"`/{c}`" for c in commands)
        lines.append(f"| {num} | {phase} | {cmd_cell} | {row_agents[0]}{agent_suffix} | {artifact} |")
    for base in sorted(prompts):
        if base not in used:
            errors.append(f".github/prompts/{base}.prompt.md が PHASE_TABLE_ROWS に未登録"
                          "(新プロンプトを追加したら tools/gen-docs.py の表メタにも行を足す)")
    return "\n".join(lines)


def gen_overview_stats(data, errors):
    return OVERVIEW_STATS_TEMPLATE.format(
        agents=len(data["agents"]),
        skills=len(data["skills"]),
        commands=len(data["prompts"]),
        selftest=data["selftest"] if data["selftest"] is not None else "?",
        selftest_ps1=data["selftest_ps1"] if data["selftest_ps1"] is not None else "?",
        hook_count=data["hook_count"],
        hook_opt_in=data["hook_opt_in"],
        subagent_routes=CARRYOVER["subagent_routes"],
    )


def gen_harness_doc_counts(data, errors):
    return HARNESS_DOC_COUNTS_TEMPLATE.format(
        agents=len(data["agents"]),
        skills=len(data["skills"]),
        commands=len(data["prompts"]),
        subagent_routes=CARRYOVER["subagent_routes"],
        hook_count=data["hook_count"],
        hook_opt_in=data["hook_opt_in"],
    )


def _selftest_fields(data):
    return dict(
        selftest=data["selftest"] if data["selftest"] is not None else "?",
        selftest_ps1=data["selftest_ps1"] if data["selftest_ps1"] is not None else "?",
        hook_count=data["hook_count"],
        hook_opt_in=data["hook_opt_in"],
    )


def gen_selftest_counts_md(data, errors):
    return SELFTEST_COUNTS_MD_TEMPLATE.format(**_selftest_fields(data))


def gen_selftest_counts_html(data, errors):
    return SELFTEST_COUNTS_HTML_TEMPLATE.format(**_selftest_fields(data))


def gen_hook_count_inline(data, errors):
    """guardrails.html 見出しの「N + opt-in M」(フック数の一次データは count_hooks)。"""
    return "{hook_count} + opt-in {hook_opt_in}".format(**_selftest_fields(data))
def gen_usage_thresholds(data, errors):
    """USAGE.md セッション分割表の文脈閾値の文言。数値の正は tools/usage-config.json(1か所。監査 §5.5-4)。
    受領書(session-receipt.py)・振り返り(effort-report.py)も同じファイルを読む。"""
    path = os.path.join(ROOT, "tools", "usage-config.json")
    try:
        cfg = json.load(open(path, encoding="utf-8-sig"))
        pct = int(cfg["context_warn_pct"])
        src = str(cfg.get("context_warn_source") or "")
    except Exception as e:
        errors.append(f"tools/usage-config.json を読めない(context_warn_pct が必要): {e}")
        return ""
    return (f"コンテキスト窓の**約{pct}%を超えたあたりから想起の劣化が始まる**という実測"
            f"（{src.split('。')[0] if src else 'HumanLayer の大規模セッション分析'}）もあり、\n"
            "「窓に入るならまだ大丈夫」ではありません。この閾値の正は `tools/usage-config.json` の\n"
            "`context_warn_pct`（受領書の文脈警告・振り返りの逸脱判定も同じ値）で、本文はそこから生成されます。")


# =====================================================================================
# 第6回追補(A3-4b / A7-A1-1〜4 / A5-7): agents / skills / commands の一覧本文・USAGE の
# セッション分割表・COMPARISON の実測表・決定数を一次データから生成する。
# 一次データ: .github/agents の frontmatter(description / tools / agents / handoffs / user-invocable)、
# .github/skills の SKILL.md frontmatter(name / description / user-invocable)、.github/prompts の
# frontmatter(agent / description)、DECISIONS.md の ^## D 見出し、evaluation/REPORT.md(eval-report の生成物)。
# =====================================================================================

def parse_fm_list(v):
    """frontmatter の単一行リスト `['read', 'edit']` / `[a, b]` を Python リストにする(PyYAML 非依存)。
    リストでなければ [v](空文字は [])。"""
    if isinstance(v, list):
        return v
    v = (v or "").strip()
    if not v:
        return []
    if v.startswith("[") and v.endswith("]"):
        items = []
        for tok in v[1:-1].split(","):
            tok = tok.strip()
            if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
                tok = tok[1:-1]
            if tok:
                items.append(tok)
        return items
    return [v]


# ツール alias(validate-harness.py の VALID_TOOL_ALIASES と同じ語彙)の表示名。未知はエラー
TOOL_LABELS = {
    "read": "読取", "edit": "編集", "search": "検索", "execute": "実行", "web": "Web",
    "agent": "サブエージェント委譲", "todo": "タスクリスト", "playwright": "Playwright",
}
# エージェントの種別は frontmatter から導く: user-invocable キーあり=サブエージェント(Copilot の
# 単独起動可否を宣言する役割)、handoffs あり=フェーズ専属、それ以外=本体保守。
PHASE_AGENT_ORDER = ["orchestrator", "requirements", "design", "implement", "test", "release", "change"]
# 呼べるサブエージェント列の注記(経路の回数。agents 許可リストが一次データ、注記は引き継ぎ)
AGENT_SUBAGENT_NOTES = {
    ("requirements", "spec-critic"): "承認前に1回",
    ("design", "spec-critic"): "承認前に1回",
    ("implement", "task-worker"): "タスク数だけ",
    ("implement", "reviewer"): "完了タスク10個ごと＋全タスク完了時",
    ("test", "reviewer"): "1回",
    ("change", "task-worker"): "CR の実装タスクごと",
    ("change", "spec-critic"): "再ゲート前",
    ("change", "reviewer"): "コード変更 CR は必須",
}
# サブエージェントの基本姿勢(agents.html の列。frontmatter に無い引き継ぎ。未登録はエラー)
SUBAGENT_STANCE = {
    "spec-critic": "「承認推奨」をデフォルトにせず、まず反証を探す（懐疑チューニング）",
    "reviewer": "「合格」をデフォルトにせず、壊れるシナリオを探す（懐疑チューニング）",
    "task-worker": "完了条件を満たせなければ「失敗」として返す（テストを緩めて通さない）",
}

# スキルのカテゴリ(表示順)。ハーネス入口(^\d\d-)は自動分類で最初に出す(空なら節ごと省く)。
SKILL_CATEGORIES = [
    ("entry", "HARNESS ENTRY", "ハーネス入口（Copilot Agent Host 向け user-invocable スキル）",
     "<p class=\"sec-desc\">`/NN-…` のプロンプトと同名の入口スキル。Agent Host は prompt files を読まないため"
     "（再監査 2026-09-09 CP-2）、同じ起動指示をスキルとして置く。担当は同名プロンプトの agent: バインド。</p>", ""),
    ("phase", "PHASE PROCEDURES", "フェーズの手順", "", ""),
    ("quality", "QUALITY & SECURITY", "品質・セキュリティ", "", ""),
    ("release", "RELEASE & ENVIRONMENT", "リリース・環境",
     "<p class=\"sec-desc\">デプロイ環境のスキルは事前に全収録せず、<b>実際に判明した時点で作って育てる</b>のが方針です。"
     "2回目以降のリリースはスキルにより自動化されます。</p>", ""),
    ("scale", "SCALE & INTAKE", "規模・既存コード対応", "", ""),
    ("growth", "GROWTH LOOP & MAINTENANCE", "成長ループ・本体保守", "",
     "<div class=\"note\">\n    <b>プロジェクトごとに増えるスキル:</b> <code>deploy-&lt;environment&gt;</code>"
     "（デプロイ先が判明した時点で作成）と\n    <code>stack-conventions</code>（技術スタック確定時に作成）は、"
     "各プロジェクトで動的に追加され、\n    汎用的なものは振り返りで本体へ還流されます。"
     "<b>ハーネスは使うたびにスキルが増えて賢くなります。</b>\n  </div>"),
]
# 各スキルのカテゴリと「主に使うエージェント」(引き継ぎ。^\d\d- の入口スキルは登録不要)。
SKILL_META = {
    "requirements-elicitation": ("phase", "requirements"),
    "adr-writing": ("phase", "design"),
    "ui-design-mockup": ("phase", "design"),
    "test-case-design": ("phase", "test"),
    "gate-check": ("phase", "全エージェント"),
    "request-routing": ("phase", "全エージェント"),
    "fast-track": ("phase", "orchestrator（判定）→ 各フェーズ"),
    "change-request": ("phase", "change"),
    "converge": ("phase", "change"),
    "release-security-review": ("quality", "reviewer / release"),
    "mutation-verification": ("quality", "task-worker / test"),
    "skill-authoring": ("release", "design / release"),
    "deploy-local-npx": ("release", "release"),
    "deploy-local-zip": ("release", "release"),
    "windows-shell-conventions": ("release", "実行系の全エージェント"),
    "large-scale-development": ("scale", "orchestrator / design"),
    "brownfield-intake": ("scale", "requirements"),
    "harness-retrospective": ("growth", "orchestrator"),
    "harness-apply-retrospective": ("growth", "harness-maintainer"),
    "harness-sync": ("growth", "orchestrator"),
    "harness-guide": ("growth", "orchestrator"),
    "harness-stats": ("growth", "利用者（user-invocable）"),
}

# コマンドのカテゴリ(表示順)と末尾の注記
COMMAND_CATEGORIES = [
    ("main", "MAIN PIPELINE", "初回構築の一本道（/01〜/09）",
     "<p class=\"sec-desc\">新規開発はこの順で進みます。セッション列は「新しいチャットで打つべきか」の目安"
     "（詳細は USAGE.md のセッション分割表）。</p>", ""),
    ("ops", "OPERATIONS", "運用中・既存コード（日常はこちら）", "", ""),
    ("growth", "GROWTH & SYNC", "振り返り・還流", "", ""),
    ("anytime", "ANYTIME", "いつでも使える", "",
     "<div class=\"note\">\n    <b>環境ごとの呼び方:</b> Copilot は Agent Host（現在の既定）では入口スキル\n"
     "    <code>.github/skills/&lt;nn&gt;-&lt;name&gt;/</code>（prompt files から生成）、Local ハーネスでは\n"
     "    プロンプトファイル、Claude Code はスラッシュコマンドとして同名で存在します（Antigravity 用\n"
     "    ワークフローも同梱・検証凍結）。Agent Host は prompt files を読まないため（公式・再監査 2026-09-09\n"
     "    CP-2）、入口スキルの本文が「担当エージェント定義の読込→役割設定→prompt 本文の実行」を指示します\n"
     "    （役割設定は指示層。ピッカーで担当エージェントを選んでから実行すればツール制限とハンドオフも\n"
     "    機械的に効く。Agent Host での補完表示・役割遵守は実機未確認）。どの環境でも入口は\n"
     "    <code>/00-start-project</code>（新規）・<code>/11-brownfield-intake</code>（既存）・\n"
     "    <code>/12-change-request</code>（運用中）の3つだけ覚えれば十分——それすら、\n"
     "    普通に依頼すればエージェントが選びます。\n  </div>"),
]
# 各コマンドの (カテゴリ, セッション pill の class, セッション欄, 追加 pill)。表示順は辞書順(挿入順)。
COMMAND_META = {
    "00-start-project": ("main", "same", "どこでも", ""),
    "01-requirements-intake": ("main", "new", "新規", ""),
    "02-requirements-deepdive": ("main", "same", "01の続き", ""),
    "03-design-architecture": ("main", "new", "新規", ""),
    "04-design-detailed": ("main", "same", "03の続き", ""),
    "05-implementation-plan": ("main", "new", "新規", ""),
    "06-implement-task": ("main", "same", "05の続き", ""),
    "07-test-plan": ("main", "same", "続きでよい", ""),
    "08-test-execute": ("main", "same", "続きでよい", ""),
    "09-release-checklist": ("main", "same", "自動継続", ""),
    "12-change-request": ("ops", "new", "CR1件ごとに新規", ""),
    "13-converge": ("ops", "new", "新規", ""),
    "11-brownfield-intake": ("ops", "new", "新規", ""),
    "10-retrospective": ("growth", "new", "新規", ""),
    "90-apply-retrospective": ("growth", "new", "本体リポジトリで新規", " <span class=\"pill body\">本体専用</span>"),
    "91-sync-from-harness": ("growth", "new", "新規", ""),
    "98-harness-help": ("anytime", "same", "どこでも", ""),
    "99-status": ("anytime", "same", "どこでも", ""),
}

# USAGE.md セッション分割表: (作業セルの表示, [コマンド base...], セッション欄)。
# 全プロンプトがちょうど1行に現れることを検査する(コマンド集合が一次データ、文言は引き継ぎ)。
USAGE_SESSION_ROWS = [
    ("`/00`, `/98`, `/99` 進捗確認・ヘルプ", ["00-start-project", "98-harness-help", "99-status"],
     "どこでもよい（`/00` の初回実行は progress.md 作成等の初期化を伴う）"),
    ("`/01`-`/02` 要件定義", ["01-requirements-intake", "02-requirements-deepdive"], "新規"),
    ("`/03`-`/04` 設計", ["03-design-architecture", "04-design-detailed"], "新規（要件と別）"),
    ("`/05` 実装計画 → `/06` 実装 → `/07`-`/08` テスト",
     ["05-implementation-plan", "06-implement-task", "07-test-plan", "08-test-execute"],
     "1セッションでよい（コーディネーターは軽量、実装はtask-workerに隔離。ただし完了タスク10個ごとに `reviewer` の"
     "チェックポイントレビューを `review-log.md` に記録してから分割。tasks.md と review-log.md が正なので `/06` で途中再開可）"),
    ("`/09` リリース", ["09-release-checklist"],
     "自動継続（ノンストップ設計）。手動で再開する場合は新チャット+`/09`でも同じ動作"),
    ("`/10` 振り返り", ["10-retrospective"], "新規"),
    ("`/11` 既存アプリの取り込み（brownfield）", ["11-brownfield-intake"],
     "新規（逆起こし〜整合検証で1セッション。大規模なら領域ごとに分割）"),
    ("`/12` 変更請求（運用中の改修・バグ修正）", ["12-change-request"],
     "1件（CR1本）につき新規。1セッションで実装〜テストまで通す"),
    ("`/13` 収束監査（運用中の定期棚卸し）", ["13-converge"],
     "新規（検出〜分類〜起票までで1セッション。対処は起票先の `/06`・`/12` の通常フローで別セッション）"),
    ("`/90` 振り返りの本体適用", ["90-apply-retrospective"],
     "新規（ハーネス**本体リポジトリ**を開いたセッションで実行）"),
    ("`/91` 本体からの逆同期", ["91-sync-from-harness"], "新規（dry-run→人間の--apply→検証まで1セッション）"),
]


def html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _short_cmds(prompts, agent):
    """agent にバインドされたプロンプトを `/NN` 形式で列挙する(番号順)。"""
    return [f"<code>/{b[:2]}</code>" for b in sorted(prompts) if prompts[b].get("agent") == agent]


def agent_kind(fm):
    if "user-invocable" in fm:
        return "subagent"
    if "handoffs" in fm:
        return "phase"
    return "maintainer"


def _tools_cell(name, fm, errors):
    labels = []
    for t in parse_fm_list(fm.get("tools")):
        if t not in TOOL_LABELS:
            errors.append(f".github/agents/{name}.agent.md: 未知の tools alias {t!r}(gen-docs の TOOL_LABELS に表示名を足す)")
            continue
        if t == "agent":
            continue  # 委譲可否は「呼べるサブエージェント」列で示す
        labels.append(TOOL_LABELS[t])
    cell = "・".join(labels) or "—"
    if "execute" not in parse_fm_list(fm.get("tools")):
        cell += "（<b>execute無し</b>）" if fm.get("handoffs") is not None else ""
    return cell


def gen_agents_hero_counts(data, errors):
    kinds = {k: sorted(n for n, fm in data["agent_fms"].items() if agent_kind(fm) == k)
             for k in ("phase", "subagent", "maintainer")}
    return (f"    このハーネスには{len(data['agent_fms'])}のエージェントがいます。フェーズ専属の{len(kinds['phase'])}つが開発を進め、\n"
            f"    独立コンテキストのサブエージェント{len(kinds['subagent'])}つが品質を守り、"
            f"{len(kinds['maintainer'])}つが本体の保守を担います。")


def gen_agents_phase_table(data, errors):
    fms = data["agent_fms"]
    phase = [n for n, fm in fms.items() if agent_kind(fm) == "phase"]
    for n in phase:
        if n not in PHASE_AGENT_ORDER:
            errors.append(f".github/agents/{n}.agent.md はフェーズ専属(handoffs あり)だが gen-docs の PHASE_AGENT_ORDER に未登録")
    for n in PHASE_AGENT_ORDER:
        if n not in phase:
            errors.append(f"PHASE_AGENT_ORDER の {n} が .github/agents に無い(または handoffs を持たない)")
    lines = [f"  <h2>フェーズ専属エージェント（{len(phase)}）</h2>",
             "  <p class=\"sec-desc\">対応するスラッシュコマンドを実行すると自動的にそのエージェントに切り替わります"
             "（手動切り替え不要）。役割は各 <code>.agent.md</code> の description、起動コマンドは prompts の "
             "<code>agent:</code> バインド、ツール権限と呼べるサブエージェントは frontmatter の <code>tools</code> / "
             "<code>agents</code> から生成。</p>",
             "  <div class=\"scroll\">", "  <table>",
             "    <tr><th>エージェント</th><th>役割</th><th>主な起動コマンド</th><th>ツール権限</th><th>呼べるサブエージェント</th></tr>"]
    for n in [x for x in PHASE_AGENT_ORDER if x in phase]:
        fm = fms[n]
        subs = parse_fm_list(fm.get("agents"))
        sub_cell = " / ".join(
            f"{s}（{AGENT_SUBAGENT_NOTES[(n, s)]}）" if (n, s) in AGENT_SUBAGENT_NOTES else s for s in subs) or "—"
        cmds = " ".join(_short_cmds(data["prompts"], n)) or "—"
        lines.append(f"    <tr><td><b>{n}</b></td><td>{html_escape(fm.get('description', ''))}</td>"
                     f"<td>{cmds}</td><td>{_tools_cell(n, fm, errors)}</td><td>{sub_cell}</td></tr>")
    lines += ["  </table>", "  </div>"]
    return "\n".join(lines)


def gen_agents_sub_table(data, errors):
    fms = data["agent_fms"]
    subs = sorted(n for n, fm in fms.items() if agent_kind(fm) == "subagent")
    maint = sorted(n for n, fm in fms.items() if agent_kind(fm) == "maintainer")
    lines = [f"  <h2>サブエージェント（{len(subs)}）— 独立コンテキストの品質担当</h2>",
             "  <p class=\"sec-desc\">", "    呼び出されるたびに<b>まっさらな別コンテキスト</b>で起動します。書いた本人の思い込みを",
             "    引き継がないこと（バイアス除去）と、会話を肥大化させないこと（コンテキストロット対策）の",
             "    両方を1つの仕組みで実現しています。役割は各 <code>.agent.md</code> の description から生成。", "  </p>",
             "  <div class=\"scroll\">", "  <table>",
             "    <tr><th>サブエージェント</th><th>役割</th><th>ツール権限</th><th>基本姿勢</th></tr>"]
    for n in subs:
        fm = fms[n]
        if n not in SUBAGENT_STANCE:
            errors.append(f".github/agents/{n}.agent.md はサブエージェントだが gen-docs の SUBAGENT_STANCE に未登録")
        tools = parse_fm_list(fm.get("tools"))
        ro = " <span class=\"pill sub\">読取専用</span>" if "edit" not in tools and "execute" not in tools else ""
        tools_cell = "・".join(TOOL_LABELS.get(t, t) for t in tools if t != "agent") + ("のみ（編集不可）" if ro else "")
        lines.append(f"    <tr><td><b>{n}</b>{ro}</td><td>{html_escape(fm.get('description', ''))}</td>"
                     f"<td>{tools_cell}</td><td>{SUBAGENT_STANCE.get(n, '—')}</td></tr>")
    lines += ["  </table>", "  </div>"]
    for n in maint:
        cmds = " ".join(_short_cmds(data["prompts"], n)) or "—"
        lines.append(f"  <div class=\"note\"><b>ハーネス本体専用:</b> <b>{n}</b>（{cmds}）— "
                     f"{html_escape(fms[n].get('description', ''))}</div>")
    return "\n".join(lines)


ENTRY_SKILL_RE = re.compile(r"^\d\d-")


def skill_category(name):
    """^\\d\\d- はハーネス入口(自動)。それ以外は SKILL_META(未登録は None)。"""
    if ENTRY_SKILL_RE.match(name):
        return "entry"
    return SKILL_META.get(name, (None, None))[0]


def gen_skills_hero_count(data, errors):
    return (f"  <h1>スキル一覧 <span class=\"grad\">— 手順とチェックリストの部品（{len(data['skill_fms'])}）</span></h1>")


def gen_skills_sections(data, errors):
    fms = data["skill_fms"]
    by_cat = {}
    for name in sorted(fms):
        cat = skill_category(name)
        if cat is None:
            errors.append(f".github/skills/{name}/SKILL.md が gen-docs の SKILL_META に未登録"
                          "(カテゴリと主に使うエージェントを足す)")
            continue
        by_cat.setdefault(cat, []).append(name)
    for name in SKILL_META:
        if name not in fms:
            errors.append(f"SKILL_META の {name} が .github/skills に無い(削除/改名したら辞書からも消す)")
    # 表示順: 非入口は SKILL_META の登録順、入口は名前順
    order = {n: i for i, n in enumerate(SKILL_META)}
    sections = []
    for key, label, title, desc, tail in SKILL_CATEGORIES:
        names = by_cat.get(key, [])
        if not names:
            continue
        names.sort(key=lambda n: (order.get(n, -1), n))
        lines = ["<section><div class=\"wrap\">", f"  <div class=\"sec-label\">{label}</div>",
                 f"  <h2>{title}（{len(names)}）</h2>"]
        if desc:
            lines.append("  " + desc)
        lines += ["  <div class=\"scroll\">", "  <table>",
                  "    <tr><th>スキル</th><th>何をするか（SKILL.md の description）</th><th>主に使うエージェント</th></tr>"]
        for n in names:
            fm = fms[n]
            if key == "entry":
                base = n
                agent = data["prompts"].get(base, {}).get("agent") or "—"
                who = f"{agent}（同名プロンプトの担当）" if agent != "—" else "—"
            else:
                who = SKILL_META[n][1]
            inv = " <span class=\"pill sub\">user-invocable</span>" if str(fm.get("user-invocable", "")).lower() == "true" else ""
            lines.append(f"    <tr><td><b>{n}</b>{inv}</td><td>{html_escape(fm.get('description', ''))}</td><td>{who}</td></tr>")
        lines += ["  </table>", "  </div>"]
        if tail:
            lines.append("  " + tail)
        lines.append("</div></section>")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def gen_commands_hero_count(data, errors):
    return f"  <h1>コマンド一覧 <span class=\"grad\">— {len(data['prompts'])}のスラッシュコマンド</span></h1>"


def gen_commands_sections(data, errors):
    prompts = data["prompts"]
    for base in prompts:
        if base not in COMMAND_META:
            errors.append(f".github/prompts/{base}.prompt.md が gen-docs の COMMAND_META に未登録(カテゴリとセッション欄を足す)")
    for base in COMMAND_META:
        if base not in prompts:
            errors.append(f"COMMAND_META の {base} が .github/prompts に無い(削除/改名したら辞書からも消す)")
    sections = []
    for key, label, title, desc, tail in COMMAND_CATEGORIES:
        bases = [b for b, m in COMMAND_META.items() if m[0] == key and b in prompts]
        if not bases:
            continue
        lines = ["<section><div class=\"wrap\">", f"  <div class=\"sec-label\">{label}</div>", f"  <h2>{title}</h2>"]
        if desc:
            lines.append("  " + desc)
        lines += ["  <div class=\"scroll\">", "  <table>",
                  "    <tr><th>コマンド</th><th>何が起きるか（prompt の description）</th><th>担当</th><th>セッション</th></tr>"]
        for b in bases:
            _cat, pill_cls, session, extra = COMMAND_META[b]
            lines.append(f"    <tr><td><code>/{b}</code>{extra}</td><td>{html_escape(prompts[b].get('description', ''))}</td>"
                         f"<td>{prompts[b].get('agent', '')}</td><td><span class=\"pill {pill_cls}\">{session}</span></td></tr>")
        lines += ["  </table>", "  </div>"]
        if tail:
            lines.append("  " + tail)
        lines.append("</div></section>")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def gen_usage_session_table(data, errors):
    prompts = data["prompts"]
    seen = {}
    lines = ["| 作業 | セッション |", "|---|---|"]
    for label, bases, session in USAGE_SESSION_ROWS:
        for b in bases:
            if b in seen:
                errors.append(f"USAGE_SESSION_ROWS: /{b} が複数行に登録されている")
            if b not in prompts:
                errors.append(f"USAGE_SESSION_ROWS: 行「{label}」が参照する .github/prompts/{b}.prompt.md が存在しない")
            seen[b] = label
        lines.append(f"| {label} | {session} |")
    for b in sorted(prompts):
        if b not in seen:
            errors.append(f".github/prompts/{b}.prompt.md が USAGE_SESSION_ROWS に未登録(セッション分割表に行を足す)")
    return "\n".join(lines)


def count_decisions(path):
    """DECISIONS.md の `## DNNN` 見出しの静的計数と最大番号。(件数, 最大番号)。"""
    text = open(path, encoding="utf-8-sig").read()
    nums = [int(n) for n in re.findall(r"^## D(\d+)", text, re.M)]
    return len(nums), (max(nums) if nums else 0)


def gen_decision_count_md(data, errors):
    d = data.get("decisions")
    if d is None:
        return "- **設計決定**: 件数の正は `DECISIONS.md`（本体リポジトリのみ。配布物には含まれない）。"
    return (f"- **設計決定**: {d[0]}件（D001〜D{d[1]:03d}。`DECISIONS.md` の `## D` 見出しの静的計数＝生成物。"
            "各決定は「決定 / 根拠・出典 / 捨てた選択肢 / 実装コミット / 検証結果」の形式）。")


def gen_decision_count_html_h3(data, errors):
    d = data.get("decisions")
    return f"        <h3>決定台帳（DECISIONS.md・{d[0] if d else '?'}件）</h3>"


def gen_decision_count_html_stat(data, errors):
    d = data.get("decisions")
    return (f"    <div class=\"stat\"><b>{d[0] if d else '?'}</b><span>根拠つき設計決定<br>"
            f"（DECISIONS.md D001〜D{d[1]:03d}。静的計数）</span></div>" if d else
            "    <div class=\"stat\"><b>?</b><span>根拠つき設計決定<br>（DECISIONS.md 不在）</span></div>")


# ---- evaluation/REPORT.md(eval-report の生成物)からの機械転記 ----------------------------
def parse_md_tables(text):
    """Markdown の表を [(header_cells, [row_cells...])] で返す(区切り行は捨てる)。"""
    tables = []
    cur = None
    for line in text.splitlines():
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                continue
            if cur is None:
                cur = (cells, [])
                tables.append(cur)
            else:
                cur[1].append(cells)
        else:
            cur = None
    return tables


def parse_eval_report(text):
    """REPORT.md から (要約 dict, 走行行 list) を取る。走行行は「結果 JSON」列を持つ表の行:
    {"file","task","arm","verdict","cost","legacy"}。要約は先頭の「結果 JSON: N 本(装置 v2: a / 装置 v1(参考値): b)…比較可能…: c 本」。"""
    summary = {}
    m = re.search(r"結果 JSON:\s*(\d+)\s*本\(装置 v2:\s*(\d+)\s*/\s*装置 v1\(参考値\):\s*(\d+)\)", text)
    if m:
        summary.update(total=int(m.group(1)), v2=int(m.group(2)), v1=int(m.group(3)))
    m = re.search(r"比較可能\([^)]*\):\s*(\d+)\s*本", text)
    if m:
        summary["comparable"] = int(m.group(1))
    m = re.search(r"最終結果\s*([0-9T:\-]+)", text)
    if m:
        summary["latest"] = m.group(1)
    runs = []
    for header, rows in parse_md_tables(text):
        if not header or header[0] != "結果 JSON":
            continue
        idx = {h: i for i, h in enumerate(header)}
        vcol = next((h for h in header if h.startswith("verdict")), None)
        if "課題" not in idx or "アーム" not in idx or vcol is None or "cost" not in idx:
            continue
        legacy = "系列" not in idx  # 装置 v2 の表には系列列がある
        for r in rows:
            if len(r) < len(header):
                continue
            cm = re.search(r"\$?\s*([0-9]+(?:\.[0-9]+)?)", r[idx["cost"]])
            runs.append({"file": r[idx["結果 JSON"]], "task": r[idx["課題"]], "arm": r[idx["アーム"]],
                         "verdict": r[idx[vcol]], "cost": float(cm.group(1)) if cm else None, "legacy": legacy})
    return summary, runs


def _median(vals):
    vals = sorted(vals)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def measured_rows(runs):
    """(装置, 課題, アーム) ごとの n / PASS / FAIL / DNF 内訳 / 費用 min・median・max。"""
    groups = {}
    for r in runs:
        groups.setdefault((r["legacy"], r["task"], r["arm"]), []).append(r)
    out = []
    for (legacy, task, arm), rs in sorted(groups.items()):
        dnf = {}
        for r in rs:
            if r["verdict"].startswith("DNF") or r["verdict"].startswith("INVALID"):
                dnf[r["verdict"]] = dnf.get(r["verdict"], 0) + 1
        costs = [r["cost"] for r in rs if r["cost"] is not None]
        out.append({
            "apparatus": "v1（参考値）" if legacy else "v2", "task": task, "arm": arm, "n": len(rs),
            "pass": sum(1 for r in rs if r["verdict"] == "PASS"),
            "fail": sum(1 for r in rs if r["verdict"] == "FAIL"),
            "dnf": ", ".join(f"{k}:{v}" for k, v in sorted(dnf.items())) or "0",
            "cost": (f"{min(costs):.2f} / {_median(costs):.2f} / {max(costs):.2f}" if costs else "—"),
        })
    return out


def _load_eval_report(errors):
    path = os.path.join(ROOT, "evaluation", "REPORT.md")
    if not os.path.exists(path):
        errors.append("evaluation/REPORT.md が存在しない(python tools/eval-report.py --report で生成。COMPARISON の実測表の一次データ)")
        return {}, []
    return parse_eval_report(open(path, encoding="utf-8-sig").read())


def _measured_intro(summary):
    return (f"結果 JSON {summary.get('total', '?')} 本（装置 v2: {summary.get('v2', '?')} / 装置 v1・参考値: {summary.get('v1', '?')}）、"
            f"比較可能（valid_for_comparison=true かつ系列条件充足）: {summary.get('comparable', '?')} 本"
            f"（最終結果 {summary.get('latest', '?')}）。")


def gen_comparison_measured_md(data, errors):
    summary, runs = data["eval_report"]
    rows = measured_rows(runs)
    lines = [f"  - 一次データ: `evaluation/REPORT.md`（`python tools/eval-report.py --report` の生成物）からの機械転記。{_measured_intro(summary)}"]
    if not summary.get("comparable"):
        lines.append("  - 比較可能な走行が無いため、本表から優位・費用比は主張しない（装置 v1 は予算非対等・model 未記録・2.1.201＝比較条件不成立）。")
    lines += ["", "  | 装置 | 課題 | アーム | n | PASS | FAIL | DNF（種別:件数） | 費用 min / median / max（USD） |",
              "  |---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"  | {r['apparatus']} | {r['task']} | {r['arm']} | {r['n']} | {r['pass']} | {r['fail']} | {r['dnf']} | {r['cost']} |")
    if not rows:
        lines.append("  | — | — | — | 0 | 0 | 0 | 0 | — |")
    return "\n".join(lines)


def gen_comparison_measured_html(data, errors):
    summary, runs = data["eval_report"]
    rows = measured_rows(runs)
    lines = ["    <h3 class=\"sub\">走行ごとの verdict・費用（<code>evaluation/REPORT.md</code> からの機械転記）</h3>",
             f"    <p class=\"sec-desc\">{html_escape(_measured_intro(summary))}"
             + ("" if summary.get("comparable") else " 比較可能な走行が無いため、本表から優位・費用比は主張しません（装置 v1 は比較条件不成立）。")
             + "</p>",
             "    <div class=\"scroll\"><table>",
             "      <tr><th>装置</th><th>課題</th><th>アーム</th><th>n</th><th>PASS</th><th>FAIL</th><th>DNF（種別:件数）</th><th>費用 min / median / max（USD）</th></tr>"]
    for r in rows:
        lines.append(f"      <tr><td>{r['apparatus']}</td><td>{r['task']}</td><td>{r['arm']}</td><td>{r['n']}</td>"
                     f"<td>{r['pass']}</td><td>{r['fail']}</td><td>{r['dnf']}</td><td>{r['cost']}</td></tr>")
    if not rows:
        lines.append("      <tr><td colspan=\"8\">走行なし</td></tr>")
    lines.append("    </table></div>")
    return "\n".join(lines)

def gen_platform_requirements(data, errors):
    """PLATFORM.md「最低安全バージョン表」。数値の正は .github/harness/platform-requirements.json
    (ホスト版の要求とホスト機能の導入版。モデル関連機能の版は model-policy.yml が正で、表は名前で参照する。
    2026-09-10 A6-8 / A3-3)。描画は tools/platform_requirements.py(標準ライブラリのみ。doctor と共用)。"""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import platform_requirements as preq  # noqa: E402
        return preq.render_markdown(preq.load(ROOT))
    except Exception as e:  # noqa: BLE001
        errors.append(f".github/harness/platform-requirements.json を読めない(PLATFORM.md の版表に必要): {e}")
        return ""


# ---- DECISIONS.md の索引(A7-M-1。再監査 2026-08-31 §6 記録系・衛生「DECISIONS.md 210KB・71 件の単一ファイル運用が
# 『改修前に必ず通読』と両立せず、索引不在」) ------------------------------------------------------
# 一次データ: DECISIONS.md 本文の `## Dnnn: 題名` 見出しと、その記録ブロック内の `日付: YYYY-MM-DD` 行(無い記録は「—」)。
# 分割はしない(理由は DECISIONS_INDEX_NOTE)。索引だけで「該当 D を探す」コストを下げ、通読要求は「索引で該当 D だけを精読」に
# 読み替える。行番号は載せない(本文を 1 行足すたびに索引が変わり、--check が毎回赤になる)。
DECISIONS_INDEX_NOTE = (
    "（`python tools/gen-docs.py` が本文の `## Dnnn:` 見出しと各記録の `日付:` 行から生成。手で編集しない。"
    "検査: `python tools/gen-docs.py --check`。題名は「 — 」以降の副題と末尾の括弧書きを省いた短縮形、"
    "日付の無い記録は「—」。**分割しない理由**: D 記録の参照単位は D 番号 1 つで、1 ファイルなら `## D0NN` の検索 1 回で届く。"
    "分割すると「どのファイルにあるか」の索引がもう 1 段要り、台帳・CHANGELOG・コミットメッセージの既存の D 番号参照と "
    "grep の単純さを失う。通読は求めず、この索引で該当 D だけを精読する（AGENTS.md「改修する前に必ず目を通す」の読み替え）。）"
)


def short_title(title, limit=64):
    """索引用の短い題名: 「 — 」以降の副題と末尾の括弧書き(ID 列挙等)を落とし、limit 字で切る。表のセル用に | を escape。"""
    t = re.sub(r"\s*[—–]\s+.*$", "", title).strip()
    t2 = re.sub(r"\s*[（(][^（）()]*[）)]\s*$", "", t).strip()
    t = t2 or t
    if len(t) > limit:
        t = t[:limit - 1] + "…"
    return t.replace("|", "\\|")


def parse_decisions_index(text):
    """[(番号, 短縮題名, 日付 or None)]。日付は見出しから次の `## D` 見出しまでの間の最初の `日付:` 行。"""
    heads = list(re.finditer(r"^## D(\d+)[:：]\s*(.*?)\s*$", text, re.M))
    rows = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        dm = re.search(r"^日付[:：]\s*(\d{4}-\d{2}-\d{2})", text[m.end():end], re.M)
        rows.append((int(m.group(1)), short_title(m.group(2)), dm.group(1) if dm else None))
    return rows


def gen_decisions_index(data, errors):
    path = os.path.join(ROOT, "DECISIONS.md")
    if not os.path.exists(path):
        errors.append("DECISIONS.md が存在しない(索引を生成できない)")
        return ""
    rows = parse_decisions_index(open(path, encoding="utf-8-sig").read())
    lines = [DECISIONS_INDEX_NOTE, "", "| D | 題名 | 日付 |", "|---|---|---|"]
    for num, title, date in rows:
        lines.append(f"| D{num:03d} | {title} | {date or '—'} |")
    return "\n".join(lines)


# 任意ブロック: マーカーが未設置のファイルでは SKIP して差分に数えない(DECISIONS.md は統合担当が書く文書のため、
# マーカー行の設置は統合時の断片適用に委ね、設置された時点から生成・検査の対象になる)。設置後は通常ブロックと同じ扱い
OPTIONAL_BLOCKS = [
    ("DECISIONS.md", "decisions-index", gen_decisions_index),
]
OPTIONAL_KEYS = {(rel, name) for rel, name, _g in OPTIONAL_BLOCKS}

# (相対パス, マーカー名, 生成関数)
BLOCKS = [
    ("README.md", "phase-command-table", gen_phase_table),
    ("README.md", "selftest-counts", gen_selftest_counts_md),
    (os.path.join(".github", "harness", "overview.html"), "overview-stats", gen_overview_stats),
    (os.path.join(".github", "harness", "overview.html"), "selftest-counts", gen_selftest_counts_html),
    (os.path.join(".github", "harness", "README.md"), "harness-doc-counts", gen_harness_doc_counts),
    (os.path.join(".github", "harness", "COMPARISON.md"), "selftest-counts", gen_selftest_counts_md),
    (os.path.join(".github", "harness", "COMPARISON.html"), "selftest-counts", gen_selftest_counts_html),
    (os.path.join(".github", "harness", "guardrails.html"), "selftest-counts", gen_selftest_counts_html),
    (os.path.join(".github", "harness", "guardrails.html"), "selftest-counts-table", gen_selftest_counts_html),
    (os.path.join(".github", "harness", "guardrails.html"), "hook-count", gen_hook_count_inline),
    (os.path.join(".github", "harness", "USAGE.md"), "usage-config-thresholds", gen_usage_thresholds),
    # 第6回追補(A3-4b / A7-A1-1〜4 / A5-7)
    (os.path.join(".github", "harness", "USAGE.md"), "usage-session-table", gen_usage_session_table),
    (os.path.join(".github", "harness", "agents.html"), "agents-hero-counts", gen_agents_hero_counts),
    (os.path.join(".github", "harness", "agents.html"), "agents-phase-table", gen_agents_phase_table),
    (os.path.join(".github", "harness", "agents.html"), "agents-sub-table", gen_agents_sub_table),
    (os.path.join(".github", "harness", "skills.html"), "skills-hero-count", gen_skills_hero_count),
    (os.path.join(".github", "harness", "skills.html"), "skills-sections", gen_skills_sections),
    (os.path.join(".github", "harness", "commands.html"), "commands-hero-count", gen_commands_hero_count),
    (os.path.join(".github", "harness", "commands.html"), "commands-sections", gen_commands_sections),
    (os.path.join(".github", "harness", "COMPARISON.md"), "decision-count", gen_decision_count_md),
    (os.path.join(".github", "harness", "COMPARISON.md"), "comparison-measured", gen_comparison_measured_md),
    (os.path.join(".github", "harness", "COMPARISON.html"), "decision-count-stat", gen_decision_count_html_stat),
    (os.path.join(".github", "harness", "COMPARISON.html"), "decision-count", gen_decision_count_html_h3),
    (os.path.join(".github", "harness", "COMPARISON.html"), "comparison-measured", gen_comparison_measured_html),
    # w2-doctor(A6-8 / A3-3): ホスト版要求の表
    (os.path.join(".github", "harness", "PLATFORM.md"), "platform-requirements", gen_platform_requirements),
]


def selftest():
    """一次データに依存しない部分(frontmatter リスト解釈・REPORT.md 転記パーサ・集計)の自己テスト。"""
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    check("parse_fm_list: クォート付き配列", parse_fm_list("['read', 'edit', \"agent\"]") == ["read", "edit", "agent"])
    check("parse_fm_list: 空配列と空文字", parse_fm_list("[]") == [] and parse_fm_list("") == [])
    check("parse_fm_list: スカラーは 1 要素", parse_fm_list("*") == ["*"])
    check("agent_kind: user-invocable=subagent / handoffs=phase / それ以外=maintainer",
          agent_kind({"user-invocable": "true"}) == "subagent" and agent_kind({"handoffs": ""}) == "phase"
          and agent_kind({"description": "x"}) == "maintainer")
    check("skill_category: ^\\d\\d- は entry、未登録は None、登録済みはその値",
          skill_category("06-implement-task") == "entry" and skill_category("nope") is None
          and skill_category("gate-check") == "phase")
    fixture = (
        "# evaluation/REPORT.md\n\n- 結果 JSON: 3 本(装置 v2: 1 / 装置 v1(参考値): 2)、最終結果 2026-09-01T00:00:00。"
        "比較可能(valid_for_comparison=true かつ系列条件充足): 0 本。\n\n"
        "## 装置 v2 の全走行\n\n"
        "| 結果 JSON | 系列 | run | 課題 | アーム | verdict | valid | cost | ratio | 打切り | 督促 | 無効理由 |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
        "| a.json | S1 | 1 | todo-cli | bare | PASS | True | $3.00 | 0.1 | 0 | 0 | — |\n\n"
        "## 参考値\n\n"
        "| 結果 JSON | 課題 | アーム | verdict(再判定) | check.pass | cost | 上限(call/total) | ratio | 打切り(推定) | 復元 model | 主な無効理由 |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
        "| b.json | todo-cli | harness | DNF_BUDGET_TOTAL | True | $45.48 | 8.0/40.0 | 1.1 | 1 | m | x |\n"
        "| c.json | todo-cli | harness | PASS | True | $24.72 | 8.0/40.0 | 0.6 | 1 | m | y |\n\n"
        "| 課題 | アーム | n | PASS | FAIL | DNF(種別) |\n|---|---|---|---|---|---|\n| todo-cli | bare | 1 | 1 | 0 | 0 |\n")
    summary, runs = parse_eval_report(fixture)
    check("parse_eval_report: 要約(総数・v2・v1・比較可能・最終結果)",
          summary == {"total": 3, "v2": 1, "v1": 2, "comparable": 0, "latest": "2026-09-01T00:00:00"}, str(summary))
    check("parse_eval_report: 走行行 3 件(v2 1 + v1 2)、集計表は走行に数えない",
          len(runs) == 3 and sum(1 for r in runs if r["legacy"]) == 2 and runs[0]["cost"] == 3.0, str(runs))
    rows = measured_rows(runs)
    h = next(r for r in rows if r["arm"] == "harness")
    check("measured_rows: harness v1 は n=2 PASS 1 DNF_BUDGET_TOTAL:1 費用 24.72 / 35.10 / 45.48",
          h["n"] == 2 and h["pass"] == 1 and h["dnf"] == "DNF_BUDGET_TOTAL:1" and h["cost"] == "24.72 / 35.10 / 45.48"
          and h["apparatus"].startswith("v1"), str(h))
    b = next(r for r in rows if r["arm"] == "bare")
    check("measured_rows: bare v2 は装置 v2 として別行", b["apparatus"] == "v2" and b["pass"] == 1, str(b))
    md = gen_comparison_measured_md({"eval_report": (summary, runs)}, [])
    check("gen_comparison_measured_md: 比較可能 0 のとき優位を主張しない注記が入る", "優位・費用比は主張しない" in md and "| v2 | todo-cli | bare |" in md)
    check("count_decisions: DECISIONS.md の見出し計数", count_decisions(os.path.join(ROOT, "DECISIONS.md"))[0] > 0
          if os.path.exists(os.path.join(ROOT, "DECISIONS.md")) else True)
    # DECISIONS 索引(A7-M-1): 見出し+日付行の解釈、副題・括弧書きの短縮、日付なし、| の escape
    dfix = ("# DECISIONS\n\n最終更新: 2026-09-17（D002）\n\n## D001: 短い題名 | 縦棒つき\n\n- **決定**: x\n\n"
            "## D002: 長い題名 — 副題と ID 列挙（A1-1 / RG-2）\n\n日付: 2026-09-10\n状態: 承認済み\n\n本文 日付: 1999-01-01\n")
    drows = parse_decisions_index(dfix)
    check("parse_decisions_index: 番号・短縮題名・日付(無ければ None)",
          drows == [(1, "短い題名 \\| 縦棒つき", None), (2, "長い題名", "2026-09-10")], str(drows))
    check("short_title: limit 超は … で切る", short_title("あ" * 80).endswith("…") and len(short_title("あ" * 80)) == 64)
    check("gen_decisions_index: 表の行を出す", "| D001 |" in gen_decisions_index({}, []) if os.path.exists(os.path.join(ROOT, "DECISIONS.md")) else True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def splice_block(raw, name, generated_lf, rel, errors):
    """マーカー間に生成結果を差し込んだ全文を返す(ファイルの改行コードを保存する)。"""
    begin = f"<!-- BEGIN GENERATED: {name} -->"
    end = f"<!-- END GENERATED: {name} -->"
    if raw.count(begin) != 1 or raw.count(end) != 1:
        errors.append(f"{rel}: マーカー {begin} / {end} がちょうど1組見つからない"
                      f"(BEGIN={raw.count(begin)}, END={raw.count(end)})")
        return raw
    eol = "\r\n" if "\r\n" in raw else "\n"
    m = re.search(
        re.escape(begin) + r"\r?\n(.*?)[ \t]*" + re.escape(end),
        raw, re.DOTALL)
    if not m:
        errors.append(f"{rel}: マーカーの並びが不正(BEGIN の後に END が無い)")
        return raw
    block = generated_lf.replace("\n", eol) + eol
    return raw[: m.start(1)] + block + raw[m.end(1):]


def main(argv=None):
    ap = argparse.ArgumentParser(description="説明文書の件数・対応表を一次データから生成する")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="マーカー間に生成結果を書き込む")
    mode.add_argument("--check", action="store_true",
                      help="再生成しても差分が出ないかを exit code で返す(0=差分なし, 1=差分あり)")
    mode.add_argument("--selftest", action="store_true", help="転記パーサ・辞書照合の自己テスト")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()

    errors = []
    data = collect_data(errors)
    results = []  # (相対パス, 旧全文, 新全文)
    # 同一ファイルに複数ブロックがある場合は1回だけ読み、順に差し込んで1回だけ書く
    # (ブロックごとに読み書きすると後のブロックが前の書込を上書きして --check が常に STALE になる)
    current = {}  # rel -> 差し込み途中の全文
    for rel, name, gen in BLOCKS + OPTIONAL_BLOCKS:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            errors.append(f"{rel}: 生成先ファイルが存在しない")
            continue
        if rel not in current:
            # newline="" で改行変換を止め、CRLF/LF をそのまま保存・比較する
            raw = open(path, encoding="utf-8", newline="").read()
            if (rel, name) in OPTIONAL_KEYS and f"<!-- BEGIN GENERATED: {name} -->" not in raw:
                print(f"SKIP: {rel} にマーカー {name} が未設置(任意ブロック。設置すれば生成・検査の対象になる)")
                continue
            current[rel] = raw
            results.append((rel, raw, raw))
        generated = gen(data, errors)
        current[rel] = splice_block(current[rel], name, generated, rel, errors)
    results = [(rel, old, current[rel]) for rel, old, _ in results]

    if errors:
        for e in errors:
            print("ERROR:", e)
        return 2

    changed = [(rel, old, new) for rel, old, new in results if new != old]
    if args.check:
        for rel, _old, _new in changed:
            print(f"STALE: {rel} (再生成すると差分が出る。python tools/gen-docs.py --apply で更新)")
        print(f"gen-docs --check: {'NG' if changed else 'OK'} "
              f"(対象 {len(results)} ファイル中 差分 {len(changed)})")
        return 1 if changed else 0
    if args.apply:
        for rel, _old, new in changed:
            with open(os.path.join(ROOT, rel), "w", encoding="utf-8", newline="") as f:
                f.write(new)
            print(f"WROTE: {rel}")
        for rel, _old, _new in results:
            if all(rel != c[0] for c in changed):
                print(f"UNCHANGED: {rel}")
        return 0
    # dry-run 既定: 差分表示のみ(書き込まない)
    for rel, old, new in changed:
        diff = difflib.unified_diff(
            old.splitlines(keepends=False), new.splitlines(keepends=False),
            fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="")
        for line in diff:
            print(line)
    print(f"gen-docs (dry-run): 対象 {len(results)} ファイル中 差分 {len(changed)}"
          + (" — 書き込むには --apply" if changed else ""))
    return 0


if __name__ == "__main__":
    # Windows コンソール/サブプロセスの双方で日本語出力を安定させる
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

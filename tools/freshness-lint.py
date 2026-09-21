#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""説明文書の鮮度語と一次データの乖離を WARN で列挙する freshness lint(codex 監査 2026-08-31 R-06「document freshness は
手動 /13-converge 中心」。OpenAI 事例の freshness lint + 定期 doc-gardening の前半。後半=定期棚卸しの儀式は
harness-retrospective スキルが本ツールの実行を含む)。

鮮度語(人が「その時点で確かめた」と主張する表記)と、その主張を裏づける一次データ:
  A 「最終更新: YYYY-MM-DD(D0NN)」 … git のファイル最終変更日(commit 日)。それより後に変更されていれば WARN
      (shallow clone では git 日付が HEAD の 1 点しか無いので INFO に落とす)。D0NN が DECISIONS.md の最大 D 番号より
      小さければ WARN
  B 「YYYY-MM-DD 時点」「YYYY-MM 時点」「YYYY年M月時点」 … as_of(既定: 今日)から stale_days(既定 90)以上前なら WARN
  C 「version x.y.z」(同じ行にホスト/他製品名が無いもの) … plugin.json の version と違えば WARN。裸の vX.Y.Z は
      他製品の版(Spec Kit v1.0.5・GSD v1.11.0・Copilot CLI v1.0.83)と区別できないため見ない(2026-09-17 実測: 28 件中
      真陽性は 0 だった)
  D 「D0NN まで」 … DECISIONS.md の最大 D 番号より小さければ WARN
  E 「執筆時点」「現時点」(日付なし) … INFO で件数だけ(日付を添える候補。強制しない)
  履歴文書(DECISIONS.md)は本文の日付・版・D 番号が「当時の事実」なので A だけを見る

--fix は意図して付けない: 鮮度語は人の主張であり、日付や版だけ機械で書き換えると「確かめていない主張」を製造する
(D079「CI 緑を確認するまで検証済みを書かない」と同じ理由)。直すのは人で、本ツールは気づかせるだけ。
exit は常に 0(--strict で WARN があれば 1)。CI は表示のみ(CI の actions/checkout は depth 1 なので A は INFO になる。
git 日付まで見たければ fetch-depth: 0 にする)。

既定の走査対象(本体の説明文書。CHANGELOG.md と audits/ は履歴なので対象外):
  README.md / README.en.md / CONTRIBUTING.md / SECURITY.md / CLAUDE.md / AGENTS.md / DECISIONS.md /
  .github/harness/*.md / .github/hooks/README.md / .github/skills/*/SKILL.md / .github/agents/*.agent.md / evaluation/README.md

使い方(リポジトリルートで):
    python tools/freshness-lint.py                       # WARN / INFO を列挙して集計行
    python tools/freshness-lint.py --as-of 2026-12-01    # 基準日を固定(時間依存の再現)
    python tools/freshness-lint.py --stale-days 60
    python tools/freshness-lint.py --paths README.md .github/harness/USAGE.md
    python tools/freshness-lint.py --strict              # WARN があれば exit 1
    python tools/freshness-lint.py --selftest            # 陽性 4 種(A/B/C/D)と陰性の自己テスト(git・実ファイル非依存)
依存: 標準ライブラリ(+ git があれば A の日付比較)
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STALE_DAYS = 90
DEFAULT_GLOBS = [
    "README.md", "README.en.md", "CONTRIBUTING.md", "SECURITY.md", "CLAUDE.md", "AGENTS.md", "DECISIONS.md",
    ".github/harness/*.md", ".github/hooks/README.md", ".github/skills/*/SKILL.md", ".github/agents/*.agent.md",
    "evaluation/README.md",
]

RE_LAST_UPDATED = re.compile(r"最終更新[:：]\s*(\d{4}-\d{2}-\d{2})(?:[^\n]{0,20}?[（(]D(\d{3})[）)])?")
# 「2026-08-31 時点」「2026-08 時点」「2026年8月時点」「2026年8月31日時点」
RE_ASOF = re.compile(r"(?<![\d.])(\d{4})[-/年](\d{1,2})(?:月|[-/])?(?:(\d{1,2})日?)?\s*時点")
# 「version 1.1.0」(この文書が名乗る自身の版)。同じ行にホスト/他製品名があれば他製品の版とみなして対象外。
# 裸の vX.Y.Z は他製品の版(Spec Kit v1.0.5・GSD v1.11.0・Copilot CLI v1.0.83 等)と区別できないため見ない
RE_VERSION_WORD = re.compile(r"\bversion\s+(\d+\.\d+\.\d+)\b", re.I)
RE_OTHER_PRODUCT = re.compile(r"Claude Code|Copilot|VS Code|CLI|Spec Kit|GSD|OMC|BMAD|superpowers|node|npm|python|PyYAML", re.I)
RE_UNTIL_D = re.compile(r"D(\d{3})\s*まで")
RE_UNDATED = re.compile(r"執筆時点|現時点")
# 履歴文書(DECISIONS.md)は本文の日付・版・D 番号が「当時の事実」なので A(ヘッダの最終更新)だけを見る
HEADER_ONLY_FILES = {"DECISIONS.md"}


def _date(y, m, d=1):
    try:
        return dt.date(int(y), int(m), int(d))
    except ValueError:
        return None


def lint_text(rel, text, ctx):
    """1 ファイル分の所見 [(level, rel, line_no, message)]。ctx = {as_of, stale_days, plugin_version, max_decision,
    git_date(そのファイルの最終 commit 日 or None), shallow(bool)}。"""
    findings = []
    as_of = ctx["as_of"]
    stale_days = int(ctx.get("stale_days") or DEFAULT_STALE_DAYS)
    plugin_version = ctx.get("plugin_version")
    max_dec = ctx.get("max_decision")
    git_date = ctx.get("git_date")
    header_only = os.path.basename(rel) in HEADER_ONLY_FILES
    undated = 0
    for ln_no, line in enumerate(text.splitlines(), 1):
        for m in RE_LAST_UPDATED.finditer(line):
            stated = _date(*m.group(1).split("-"))
            if stated is None:
                findings.append(("WARN", rel, ln_no, f"「最終更新: {m.group(1)}」が日付として読めない"))
                continue
            if git_date is not None and git_date > stated:
                lvl = "INFO" if ctx.get("shallow") else "WARN"
                findings.append((lvl, rel, ln_no, f"「最終更新: {m.group(1)}」より後の {git_date.isoformat()} に git で変更されている"
                                 + ("(shallow clone のため git 日付は HEAD の 1 点=参考)" if ctx.get("shallow") else "(表記を更新するか、内容変更でないなら無視)")))
            if m.group(2) and max_dec is not None and int(m.group(2)) < max_dec:
                findings.append(("WARN", rel, ln_no, f"「最終更新 …(D{m.group(2)})」だが DECISIONS.md は D{max_dec:03d} まで進んでいる"))
        if header_only:
            continue
        for m in RE_ASOF.finditer(line):
            stated = _date(m.group(1), m.group(2), m.group(3) or 1)
            if stated is None:
                continue
            age = (as_of - stated).days
            if age >= stale_days:
                findings.append(("WARN", rel, ln_no, f"「{m.group(0)}」は as_of {as_of.isoformat()} から {age} 日前(stale_days {stale_days})。"
                                 "内容を見直して日付を更新するか、時点表現を外す"))
        if plugin_version and not RE_OTHER_PRODUCT.search(line):
            for m in RE_VERSION_WORD.finditer(line):
                if m.group(1) != plugin_version:
                    findings.append(("WARN", rel, ln_no, f"「version {m.group(1)}」が plugin.json の version {plugin_version} と違う"))
        if max_dec is not None:
            for m in RE_UNTIL_D.finditer(line):
                if int(m.group(1)) < max_dec:
                    findings.append(("WARN", rel, ln_no, f"「D{m.group(1)} まで」だが DECISIONS.md は D{max_dec:03d} まで進んでいる"))
        undated += len(RE_UNDATED.findall(line))
    if undated:
        findings.append(("INFO", rel, 0, f"日付の無い時点表現(執筆時点 / 現時点)が {undated} 件(日付を添えると本 lint の対象になる。強制しない)"))
    return findings


# ---------------------------------------------------------------- 一次データ

def plugin_version_of(root):
    try:
        return str(json.load(open(os.path.join(root, "plugin.json"), encoding="utf-8-sig")).get("version") or "") or None
    except Exception:  # noqa: BLE001
        return None


def max_decision_of(root):
    path = os.path.join(root, "DECISIONS.md")
    if not os.path.isfile(path):
        return None
    nums = [int(n) for n in re.findall(r"^## D(\d+)", open(path, encoding="utf-8-sig", errors="replace").read(), re.M)]
    return max(nums) if nums else None


def git_state(root):
    """(git 利用可, shallow)。"""
    git = shutil.which("git")
    if not git:
        return False, False
    try:
        cp = subprocess.run([git, "-C", root, "rev-parse", "--is-shallow-repository"], capture_output=True,
                            encoding="utf-8", errors="replace", timeout=30)
        if cp.returncode != 0:
            return False, False
        return True, cp.stdout.strip() == "true"
    except Exception:  # noqa: BLE001
        return False, False


def git_last_date(root, rel):
    git = shutil.which("git")
    try:
        cp = subprocess.run([git, "-C", root, "log", "-1", "--format=%cs", "--", rel], capture_output=True,
                            encoding="utf-8", errors="replace", timeout=30)
        s = cp.stdout.strip()
        return _date(*s.split("-")) if cp.returncode == 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None
    except Exception:  # noqa: BLE001
        return None


def collect_files(root, globs):
    out = []
    for g in globs:
        for p in sorted(glob.glob(os.path.join(root, g))):
            if os.path.isfile(p):
                out.append(os.path.relpath(p, root).replace(os.sep, "/"))
    seen = set()
    return [r for r in out if not (r in seen or seen.add(r))]


def run(root, globs, as_of, stale_days):
    git_ok, shallow = git_state(root)
    ctx_base = {"as_of": as_of, "stale_days": stale_days, "plugin_version": plugin_version_of(root),
                "max_decision": max_decision_of(root), "shallow": shallow}
    findings = []
    files = collect_files(root, globs)
    for rel in files:
        try:
            text = open(os.path.join(root, rel), encoding="utf-8-sig", errors="replace").read()
        except OSError:
            continue
        ctx = dict(ctx_base, git_date=git_last_date(root, rel) if git_ok else None)
        findings.extend(lint_text(rel, text, ctx))
    return findings, files, ctx_base, git_ok


def summary(findings, files, ctx_base, git_ok):
    n_warn = sum(1 for f in findings if f[0] == "WARN")
    n_info = sum(1 for f in findings if f[0] == "INFO")
    git_note = "no" if not git_ok else ("shallow" if ctx_base.get("shallow") else "yes")
    return (f"freshness-lint: WARN {n_warn} / INFO {n_info} (scanned {len(files)} files, as_of {ctx_base['as_of'].isoformat()}, "
            f"stale_days {ctx_base['stale_days']}, plugin {ctx_base.get('plugin_version') or '-'}, "
            f"DECISIONS max {('D%03d' % ctx_base['max_decision']) if ctx_base.get('max_decision') else '-'}, git dates: {git_note})")


# ---------------------------------------------------------------- selftest

def selftest():
    failures = []

    def expect(cond, label):
        (failures.append(label) if not cond else None)
        print(("PASS: " if cond else "FAIL: ") + label)

    base = {"as_of": dt.date(2026, 12, 1), "stale_days": 90, "plugin_version": "1.1.0", "max_decision": 90,
            "git_date": dt.date(2026, 11, 20), "shallow": False}
    # 陽性 A: 最終更新の日付より後に git で変更 + D 番号が古い
    f = lint_text("DECISIONS.md", "最終更新: 2026-09-17（D086）\n", base)
    expect(any(x[0] == "WARN" and "git で変更" in x[3] for x in f) and any(x[0] == "WARN" and "D090 まで進んでいる" in x[3] for x in f),
           "陽性 A: 最終更新より後の git 変更と古い D 番号を WARN")
    # 陽性 B: 90 日以上前の「時点」(3 形式)
    f = lint_text("README.md", "現在地（2026-08-31 時点）\n2026年8月時点の仕様\n2026-08 時点の再検証\n", base)
    expect(sum(1 for x in f if x[0] == "WARN" and "日前" in x[3]) == 3, "陽性 B: YYYY-MM-DD / YYYY年M月 / YYYY-MM の「時点」を stale_days 超で WARN(3 形式)")
    # 陽性 C: version の不一致(他製品の版=同じ行にホスト名があるものと、裸の vX.Y.Z は対象外)
    f = lint_text("README.md", "実機動作（version 1.0.0）。\nClaude Code version 2.1.201 で実測\nSpec Kit v1.0.5 と GSD v1.11.0\n", base)
    expect([x for x in f if x[0] == "WARN"] and all("version 1.0.0" in x[3] for x in f if x[0] == "WARN")
           and sum(1 for x in f if x[0] == "WARN") == 1,
           "陽性 C: version 1.0.0 は WARN 1 件、Claude Code version 2.1.201 と裸の v1.0.5 / v1.11.0 は対象外")
    # 履歴文書: DECISIONS.md はヘッダの最終更新だけを見る(本文の当時の版・日付・D 番号は対象外)
    f = lint_text("DECISIONS.md", "最終更新: 2026-11-20（D090）\n\n## D010: x\n2026-01-01 時点で version 0.9.0。D037 まで\n", base)
    expect([x for x in f if x[0] == "WARN"] == [], "履歴文書 DECISIONS.md は本文の時点・版・D 番号を見ない(ヘッダのみ)")
    # 陽性 D: 「D0NN まで」が古い
    f = lint_text("USAGE.md", "D085 まで適用済み\n", base)
    expect(any(x[0] == "WARN" and "D085 まで" in x[3] for x in f), "陽性 D: 「D0NN まで」が最大 D 番号より小さければ WARN")
    # 陰性: すべて一次データと整合
    f = lint_text("README.md", "最終更新: 2026-11-20（D090）\n現在地（2026-11-01 時点）\n2026年11月1日時点\nversion 1.1.0\nD090 まで\n", base)
    expect([x for x in f if x[0] == "WARN"] == [], "陰性: 整合する表記は WARN 0")
    # E: 日付なしは INFO のみ / shallow は A を INFO に落とす
    f = lint_text("README.md", "執筆時点で Preview\n", base)
    expect([x[0] for x in f] == ["INFO"], "E: 日付の無い時点表現は INFO だけ")
    f = lint_text("DECISIONS.md", "最終更新: 2026-09-17\n", dict(base, shallow=True))
    expect([x[0] for x in f] == ["INFO"], "shallow clone: git 日付との比較は INFO に落とす")
    f = lint_text("x.md", "最終更新: 2026-09-17\n", dict(base, git_date=None, plugin_version=None, max_decision=None))
    expect(f == [], "一次データが無ければ何も言わない(fail-open)")
    s = summary([("WARN", "a", 1, "x"), ("INFO", "b", 0, "y")], ["a", "b"], base, True)
    expect(s.startswith("freshness-lint: WARN 1 / INFO 1 (scanned 2 files, as_of 2026-12-01"), "summary: 集計行の形式")
    print("freshness-lint selftest: " + ("FAIL " + str(len(failures)) if failures else "all passed"))
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="説明文書の鮮度語(最終更新・時点・version・D0NN まで)と一次データの乖離を WARN で列挙する(--fix は無い)")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--as-of", default=None, help="基準日 YYYY-MM-DD(既定: 今日)")
    ap.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    ap.add_argument("--paths", nargs="*", default=None, help="走査するパス/glob(既定: 本体の説明文書一式)")
    ap.add_argument("--strict", action="store_true", help="WARN があれば exit 1")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    try:
        as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
    except ValueError:
        print(f"ERROR: --as-of {args.as_of!r} は YYYY-MM-DD")
        return 2
    findings, files, ctx_base, git_ok = run(os.path.abspath(args.root), args.paths or DEFAULT_GLOBS, as_of, args.stale_days)
    for lvl, rel, ln, msg in findings:
        print(f"{lvl}: {rel}{':' + str(ln) if ln else ''}: {msg}")
    print(summary(findings, files, ctx_base, git_ok))
    return 1 if (args.strict and any(f[0] == "WARN" for f in findings)) else 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

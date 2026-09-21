#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""トレーサビリティ整合チェッカー(drift ゲートの最小実装)。

トレーサビリティ表が一次データ。将来のグラフ一次データ化(D052参照)への布石。

既存の Markdown トレーサビリティ表を一次データとしてパースし、要件IDの参照整合を
決定論的に検査する:
- docs/02-design/architecture.md の要件対応表(トレーサビリティ)
- docs/03-implementation/tasks.md の「対応要件:」注記
- docs/04-test/test-plan.md のケース対応(対応要件ID列)

検査内容:
(a) orphan 要件: requirements.md の全要件ID(US-/FR-/NFR-数字)のうち、
    設計・タスク・テストのいずれからも参照されないもの。
(b) dangling 参照: 設計・タスク・テストが参照する要件IDのうち、
    requirements.md に実在しないもの。

照合前の正規化: 要件側・参照側の両方のテキストに NFKC 正規化(全角英数字→半角等)+
大文字化+区切り文字の統一(−/–/‐/_ 等 → -)を適用してから ID を抽出・照合する
(全角ID・小文字表記・ダッシュ類の表記ゆらぎで照合が静かに空振りする盲点の対策)。

寛容設計: 成果物が存在しないものは SKIP して失敗にしない。ただし requirements.md が
存在するのに要件IDを1件も検出できない場合は SKIP ではなく WARN を出す
(--strict では exit 1。--id-pattern でIDパターンを上書きできる)。
表・注記が見つからないファイルは本文全体のID出現にフォールバックする
(検査を弱めるより、参照の証拠を広めに拾う方を選ぶ)。

使い方:
    python tools/trace-check.py <プロジェクトルート>            # レポート表示(dry-run 既定・常に exit 0)
    python tools/trace-check.py <プロジェクトルート> --strict   # NG/WARN があれば exit 1
    python tools/trace-check.py <ルート> --id-pattern <正規表現> # 要件IDパターンの上書き
    python tools/trace-check.py --selftest                      # 合成フィクスチャで自己テスト

--id-pattern の正規表現は、正規化(NFKC・大文字化・区切り統一)後のテキストに適用される。

終了コード: 0 = NG/WARN なし、または dry-run(既定)
            1 = --strict で NG/WARN 検出 / --selftest 失敗
            2 = 実行エラー(引数なし・指定パスがディレクトリではない・--id-pattern が不正)
"""
from __future__ import annotations

import re
import sys
import tempfile
import unicodedata
from pathlib import Path

# 要件IDの既定パターン。--id-pattern オプションで上書きできる。
# 照合は normalize_text()(NFKC・大文字化・区切り統一)適用後のテキストに対して行う。
DEFAULT_ID_PATTERN = r"\b(?:US|FR|NFR)-\d+\b"
ID_RE = re.compile(DEFAULT_ID_PATTERN)

# 区切り文字のゆらぎ(ハイフン類 U+2010..2015・マイナス記号 U+2212・アンダースコア)を - に
# 統一する。全角ハイフンマイナス(U+FF0D)は NFKC で - になるため、NFKC 後も残る文字を並べる。
# 長音記号「ー」(U+30FC)はカタカナ語(トレーサビリティ等)を壊すため対象にしない。
SEPARATOR_RE = re.compile("[‐‑‒–—―−_]")


def normalize_text(text: str) -> str:
    """照合前の正規化: NFKC(全角→半角等)+大文字化+区切り文字の統一(→ -)。"""
    return SEPARATOR_RE.sub("-", unicodedata.normalize("NFKC", text).upper())

REQUIREMENTS = "docs/01-requirements/requirements.md"
DESIGN = "docs/02-design/architecture.md"
TASKS = "docs/03-implementation/tasks.md"
TESTPLAN = "docs/04-test/test-plan.md"


def extract_design_refs(text: str) -> set[str]:
    """architecture.md の要件対応表(トレーサビリティ)セクションの表行から要件IDを集める。

    セクション見出しが見つからない、またはセクション内でIDを1件も検出できない場合は
    本文全体のID出現にフォールバックする。
    """
    ids: set[str] = set()
    in_section = False
    section_level = 0
    found = False
    for line in text.splitlines():
        m = re.match(r"^(#+)\s*(.*)", line)
        if m:
            if in_section and len(m.group(1)) <= section_level:
                in_section = False
            if "要件対応表" in m.group(2) or "トレーサビリティ" in m.group(2):
                in_section = True
                found = True
                section_level = len(m.group(1))
            continue
        if in_section and line.lstrip().startswith("|"):
            ids.update(ID_RE.findall(line))
    if found and ids:
        return ids
    return set(ID_RE.findall(text))


def extract_task_refs(text: str) -> set[str]:
    """tasks.md の「対応要件: …」注記から要件IDを集める。

    値は「/」区切りの複数ID(例: 対応要件: US-001 / US-002)に対応する。
    「完了条件:」以降は値に含めない。注記が無ければ本文全体にフォールバック。
    """
    segs = re.findall(r"対応要件[::]([^)\n]*)", text)
    if segs:
        values = " ".join(re.split(r"完了条件[::]", s)[0] for s in segs)
        return set(ID_RE.findall(values))
    return set(ID_RE.findall(text))


def extract_testplan_refs(text: str) -> set[str]:
    """test-plan.md の表行(ケースID・対応要件ID列)から要件IDを集める。表が無ければ本文全体にフォールバック。"""
    rows = [ln for ln in text.splitlines() if ln.lstrip().startswith("|")]
    if rows:
        return set(ID_RE.findall("\n".join(rows)))
    return set(ID_RE.findall(text))


SOURCES = (
    ("design", DESIGN, extract_design_refs),
    ("tasks", TASKS, extract_task_refs),
    ("test", TESTPLAN, extract_testplan_refs),
)


def check_project(root: Path) -> list[tuple[str, str, str]]:
    """検査結果 [(判定, 対象, 理由)] を返す。判定は OK / NG / WARN / SKIP。"""
    results: list[tuple[str, str, str]] = []
    req_path = root / REQUIREMENTS
    if not req_path.exists():
        results.append(("SKIP", "requirements", f"{REQUIREMENTS} が無いため検査対象なし"))
        return results
    req_ids = set(ID_RE.findall(
        normalize_text(req_path.read_text(encoding="utf-8", errors="replace"))))
    if not req_ids:
        results.append(("WARN", "requirements",
                        "requirements.md は存在するが要件IDを1件も検出できない。"
                        "ID形式が既定パターンに一致しない可能性がある。--id-pattern で指定可能"))
        return results

    referenced: set[str] = set()
    any_source = False
    for name, rel, extractor in SOURCES:
        path = root / rel
        if not path.exists():
            results.append(("SKIP", name, f"{rel} が無い(未着手の成果物は検査しない)"))
            continue
        any_source = True
        ids = extractor(normalize_text(path.read_text(encoding="utf-8", errors="replace")))
        referenced |= ids
        dangling = sorted(ids - req_ids)
        if dangling:
            results.append(("NG", name,
                            f"dangling 参照: requirements.md に実在しないID {', '.join(dangling)}"))
        else:
            results.append(("OK", name, f"{rel}: 参照 {len(ids)} 件、dangling なし"))

    if any_source:
        orphans = sorted(req_ids - referenced)
        if orphans:
            results.append(("NG", "orphan",
                            "orphan 要件: 設計・タスク・テストのいずれからも参照されない "
                            f"{', '.join(orphans)}"))
        else:
            results.append(("OK", "orphan",
                            f"要件 {len(req_ids)} 件すべてがいずれかの成果物から参照されている"))
    else:
        results.append(("SKIP", "orphan", "参照側の成果物が1つも無いため orphan 検査を行わない"))
    return results


def run(root: Path, strict: bool) -> int:
    if not root.is_dir():
        print(f"エラー: {root} はディレクトリではありません")
        return 2
    results = check_project(root)
    ng = sum(1 for r in results if r[0] == "NG")
    warn = sum(1 for r in results if r[0] == "WARN")
    for verdict, scope, reason in results:
        print(f"{verdict}\t{scope}\t{reason}")
    print()
    if ng or warn:
        print(f"NG {ng} 件 / WARN {warn} 件。" + ("--strict のため exit 1。" if strict
                                                  else "dry-run(既定)のため exit 0。--strict で失敗にできる。"))
    else:
        print("トレーサビリティの orphan / dangling は検出されなかった。")
    return 1 if ((ng or warn) and strict) else 0


def selftest() -> int:
    failures = []

    def expect(cond, label):
        (failures.append(label) if not cond else None)
        print(("PASS: " if cond else "FAIL: ") + label)

    def make(root: Path, files: dict[str, str]) -> None:
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    arch_tbl = ("# 設計\n## 7. 要件対応表(トレーサビリティ)\n"
                "| 要件ID | 対応する設計要素 |\n|---|---|\n| US-001 | CompA |\n")

    with tempfile.TemporaryDirectory() as td:
        # fixture A: 整合(全要件が参照され、dangling も無い)
        a = Path(td) / "ok"
        make(a, {
            REQUIREMENTS: "# 要件定義書\n### US-001: A\n### US-002: B\n",
            DESIGN: arch_tbl + "| US-002 | CompB |\n",
            TASKS: "- [ ] TASK-001: x(対応要件: US-001 / 完了条件: npm test)\n",
            TESTPLAN: "## 単体テスト\n| ケースID | 対応要件ID | 内容 |\n|---|---|---|\n| UT-001 | US-002 | y |\n",
        })
        results = check_project(a)
        expect(not any(v == "NG" for v, _, _ in results), "整合: NG なし")
        expect(any(v == "OK" and s == "orphan" for v, s, _ in results), "整合: orphan 検査が OK")

        # fixture B: orphan 要件(US-002 / US-003 がどこからも参照されない)
        b = Path(td) / "orphan"
        make(b, {
            REQUIREMENTS: "# 要件定義書\n### US-001: A\n### US-002: B\n### US-003: C\n",
            DESIGN: arch_tbl,
        })
        results = check_project(b)
        expect(any(v == "NG" and s == "orphan" and "US-002" in r and "US-003" in r
                   for v, s, r in results), "orphan: 未参照の US-002/US-003 を NG 報告")
        expect(any(v == "SKIP" and s == "tasks" for v, s, _ in results),
               "orphan: 存在しない tasks.md は SKIP(寛容設計)")

        # fixture C: dangling 参照(tasks.md が実在しない US-009 を参照)
        c = Path(td) / "dangling"
        make(c, {
            REQUIREMENTS: "# 要件定義書\n### US-001: A\n",
            DESIGN: arch_tbl,
            TASKS: "- [ ] TASK-001: x(対応要件: US-009 / 完了条件: npm test)\n",
        })
        results = check_project(c)
        expect(any(v == "NG" and s == "tasks" and "US-009" in r for v, s, r in results),
               "dangling: 実在しない US-009 への参照を NG 報告")
        expect(not any(v == "NG" and s == "orphan" for v, s, _ in results),
               "dangling: US-001 は設計から参照されており orphan ではない")

        # fixture D: 表記ゆらぎの正規化(全角ID・小文字表記・「/」区切りの複数ID)
        # 設計は全角英字+マイナス記号(U+2212)の「ＵＳ−001」、タスクは小文字 us-002 と
        # スラッシュ区切りの US-003 で参照する。正規化により3件とも照合できること。
        d = Path(td) / "normalize"
        make(d, {
            REQUIREMENTS: "# 要件定義書\n### US-001: A\n### US-002: B\n### US-003: C\n",
            DESIGN: ("# 設計\n## 7. 要件対応表(トレーサビリティ)\n"
                     "| 要件ID | 対応する設計要素 |\n|---|---|\n| ＵＳ−001 | CompA |\n"),
            TASKS: "- [ ] TASK-001: x(対応要件: us-002 / US-003 / 完了条件: npm test)\n",
        })
        results = check_project(d)
        expect(not any(v == "NG" for v, _, _ in results),
               "正規化: 全角ID・小文字表記でも dangling を誤検出しない")
        expect(any(v == "OK" and s == "orphan" for v, s, _ in results),
               "正規化: 全角/小文字/スラッシュ区切りの参照がすべて拾われ orphan なし")

        # fixture E: requirements.md はあるがIDを1件も検出できない → SKIP ではなく WARN
        e = Path(td) / "no-ids"
        make(e, {REQUIREMENTS: "# 要件定義書\n### REQ-001: A\n"})
        results = check_project(e)
        expect(any(v == "WARN" and s == "requirements" and "--id-pattern" in r
                   for v, s, r in results),
               "ID形式不一致: SKIP ではなく WARN(--id-pattern を案内)")
        expect(not any(v == "SKIP" and s == "requirements" for v, s, _ in results),
               "ID形式不一致: requirements を SKIP にしない")

        # fixture F: セクションはあるが表内にIDが0件の設計書 → 全文フォールバックで拾う
        f = Path(td) / "design-fallback"
        make(f, {
            REQUIREMENTS: "# 要件定義書\n### US-001: A\n",
            DESIGN: ("# 設計\n## 7. 要件対応表(トレーサビリティ)\n(表は別紙)\n\n"
                     "本文中の対応: US-001\n"),
        })
        results = check_project(f)
        expect(any(v == "OK" and s == "orphan" for v, s, _ in results),
               "設計フォールバック: セクション内ID0件なら全文から US-001 を拾い orphan なし")

    print()
    print(f"trace-check selftest: {'FAIL ' + str(len(failures)) if failures else 'all passed'}")
    return 1 if failures else 0


def main() -> int:
    global ID_RE
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        return selftest()
    strict = "--strict" in args
    pattern: str | None = None
    paths: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--id-pattern":
            if i + 1 >= len(args):
                print("エラー: --id-pattern に正規表現が指定されていません")
                return 2
            pattern = args[i + 1]
            i += 2
            continue
        if a.startswith("--id-pattern="):
            pattern = a.split("=", 1)[1]
        elif not a.startswith("--"):
            paths.append(a)
        i += 1
    if pattern is not None:
        try:
            ID_RE = re.compile(pattern)
        except re.error as exc:
            print(f"エラー: --id-pattern の正規表現が不正です: {exc}")
            return 2
    if not paths:
        print(__doc__)
        return 2
    return run(Path(paths[0]), strict)


if __name__ == "__main__":
    sys.exit(main())

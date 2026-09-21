#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Golden Eval 最小版(D046): 「完了宣言」と「成果物の実態」の突き合わせチェッカー。

ハーネスの中核KPI「エージェントが完了と宣言した成果物のうち、機械的検証を通る割合」を
プロジェクトの docs/ 構造から決定論的に測る。エージェントの自己申告(GATE_STATUS の done・
tasks.md の [x])を信用せず、対応する成果物・証拠の実在で裏を取る。

使い方:
    python tools/golden-eval.py <プロジェクトルート>   # 検査して結果と割合を表示
    python tools/golden-eval.py --selftest             # 合成フィクスチャで自己テスト

終了コード: 0 = NG なし(完了宣言に対する検証がすべて通った、完了宣言がまだ無い、
                または progress.md 不在=未初期化のため検査対象なし(SKIP)。
                WARN は失敗にしない。trace-check と同じ
                「exit 0=乖離なし / 非0=乖離あり」契約)
            1 = NG を検出(完了宣言と実態の食い違い、progress.md はあるが
                GATE_STATUS を読めない等)
            2 = 実行エラー(引数なし・指定パスがディレクトリではない)

位置づけ: テストフェーズ・リリース前の自己点検、/99-status の補助、ハーネス本体CIの
自己テスト。アプリのテストスイート実行までは行わない(それは test フェーズの責務。
本ツールは「テストを実行した証拠が文書として残っているか」までを機械検査する。
証拠の鮮度も見る: test-report.md に実行日時(YYYY-MM-DD形式)が1つも無ければ
WARN「実行日時の無いテスト証拠(鮮度検証不能)」。独立レビューの記録も見る:
implementation が done なのに docs/04-test/review-log.md に日時付きエントリが無く
security-review-report.md も無ければ WARN「独立レビューの記録が無い」、記録が最新の
完了証拠より古ければ鮮度 WARN。A6-14 / RD-2。warn-gate-tamper フックと同一規則)。

事故的停止(A2-1。watchdog / StopFailure の記録を目標達成評価につなぐ): `.github/hooks/logs/abnormal-stop-<sid>.json`
(mark-abnormal-stop.py = API エラーで途中終了)と `watchdog-stop-<sid>.json`(watchdog-continue.py = 継続上限・
トークン枯渇で in_progress のまま停止)を `_log.py` の iter_stop_records で読み、記録の GATE_STATUS スナップショットが
今の GATE_STATUS と同じ(=その後に整合確認の編集が無い)なら、そのセッション中に done になったフェーズ
(SessionStart の baseline `logs/usage/<sid>.baseline.json` の gate と比較)を **達成扱いにしない(NG)**。
baseline が無く帰属できなければ WARN、done 遷移が無ければ WARN(整合確認の督促。確認後は記録ファイルを消す)。
GATE_STATUS が変わっていれば(整合済みとみなし)何も出さない。読み手 _log.py が無い配布先では検査しない。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

PHASES = ("requirements", "design", "implementation", "test", "release")
ARTIFACTS = {
    "requirements": "docs/01-requirements/requirements.md",
    "design": "docs/02-design/architecture.md",
    "implementation": "docs/03-implementation/tasks.md",
    "test": "docs/04-test/test-report.md",
    "release": "docs/05-release/release-checklist.md",
}
# WARN 正規表現検査の前に除くテンプレート(定型文)の対応表。成果物がテンプレートの
# 素コピーのままでも定型文(見出し・表ヘッダ)が正規表現にマッチして全通過してしまうため、
# テンプレートに verbatim で存在する行を行単位の集合差で除いた本文に対して検査する。
TEMPLATES = {
    "design": "docs/02-design/architecture_template.md",
    "test": "docs/04-test/test_report_template.md",
}
# 検査キーワードを含むテンプレ行(表ヘッダの「成功/失敗」・見出しの「トレーサビリティ」等)は
# 集合差の対象から除外して本文に残す。これを除去してしまうと、テンプレートに正しく数値を
# 埋めた成果物でも検査正規表現のマッチ先ごと消えて偽 WARN になるため
# (素コピーの検出は template_only() が別途担う)。
CHECK_KEYWORD_RE = re.compile(r"成功|失敗|pass|fail|トレーサビリティ", re.IGNORECASE)


def strip_template_lines(root: Path, text: str, template_rel: str) -> str:
    """text からテンプレートに verbatim で存在する行を除いた本文を返す。

    ただし検査キーワード(CHECK_KEYWORD_RE)を含むテンプレ行は除去しない(上記コメント参照)。
    テンプレートが無いプロジェクト(旧構成等)では text をそのまま返す(検査を弱めない)。
    """
    tpl = root / template_rel
    if not tpl.exists():
        return text
    # rstrip() で末尾空白を正規化して比較する(末尾空白差だけで定型行除去が失敗しないように)
    tpl_lines = {line.rstrip()
                 for line in tpl.read_text(encoding="utf-8", errors="replace").splitlines()
                 if not CHECK_KEYWORD_RE.search(line)}
    return "\n".join(line for line in text.splitlines() if line.rstrip() not in tpl_lines)


def template_line_set(root: Path, template_rel: str) -> set[str]:
    """テンプレートの行集合(rstrip 済み)を返す。テンプレートが無ければ空集合。"""
    tpl = root / template_rel
    if not tpl.exists():
        return set()
    return {line.rstrip()
            for line in tpl.read_text(encoding="utf-8", errors="replace").splitlines()}


def template_only(root: Path, text: str, template_rel: str) -> bool:
    """text の非空行がすべてテンプレートに verbatim で存在する(=素コピー)なら True。

    strip_template_lines() が検査キーワード行を本文に残すようになったため、素コピーの
    検出はこちらで行う(キーワード行を残した集合差だけでは素コピーが全通過してしまう)。
    テンプレートが無い場合は False(素コピーと断定しない)。
    """
    tpl = root / template_rel
    if not tpl.exists():
        return False
    tpl_lines = {line.rstrip()
                 for line in tpl.read_text(encoding="utf-8", errors="replace").splitlines()}
    return all(line.rstrip() in tpl_lines for line in text.splitlines() if line.strip())


def read_gate_status(root: Path) -> dict[str, str] | None:
    progress = root / "docs" / "00-overview" / "progress.md"
    if not progress.exists():
        return None
    text = progress.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<!--\s*GATE_STATUS(.*?)-->", text, re.S)
    if not m:
        return None
    status = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^(\w+):\s*(\S+)", line.strip())
        if mm and mm.group(1) in PHASES:
            status[mm.group(1)] = mm.group(2)
    return status


# 独立レビュー記録(A6-14 / RD-2)の日時(review-log エントリ見出し・完了証拠の3点セットと同じ書式)
REVIEW_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def review_record(root: Path, artifact_rel: str) -> tuple[bool, str | None, str | None]:
    """独立レビュー記録の有無と鮮度を返す(warn-gate-tamper.sh/.ps1 と同一規則)。

    戻り値: (記録あり, review-log/security-review-report の最新日時, 成果物の最新の完了証拠日時)。
    記録あり = docs/04-test/review-log.md に日時(YYYY-MM-DD HH:MM)付きエントリがある、
    または docs/04-test/security-review-report.md が非空で存在する。
    「フェーズ開始以降」は機械可読な開始日が無いため、成果物(tasks.md / test-report.md)の
    最新の完了証拠日時と同日以降で近似する。
    """
    log = root / "docs" / "04-test" / "review-log.md"
    rep = root / "docs" / "04-test" / "security-review-report.md"
    review_ts: list[str] = []
    for f in (log, rep):
        if f.exists():
            review_ts += REVIEW_TS_RE.findall(f.read_text(encoding="utf-8", errors="replace"))
    has_record = bool(review_ts) or (rep.exists() and rep.stat().st_size > 0)
    art = root / artifact_rel
    evidence_ts = (REVIEW_TS_RE.findall(art.read_text(encoding="utf-8", errors="replace"))
                   if art.exists() else [])
    return has_record, (max(review_ts) if review_ts else None), (max(evidence_ts) if evidence_ts else None)


def _hook_log_module(root: Path):
    """事故的停止の記録の読み手 .github/hooks/scripts/_log.py(プロジェクト側 → 本ツール自身のハーネスの順)。無ければ None。"""
    for base in (root, Path(__file__).resolve().parents[1]):
        path = base / ".github" / "hooks" / "scripts" / "_log.py"
        if path.is_file():
            try:
                spec = importlib.util.spec_from_file_location("harness_hook_log", str(path))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
            except Exception:  # noqa: BLE001
                return None
    return None


def baseline_gate(root: Path, session_id) -> dict | None:
    """session-baseline.py が SessionStart で残した logs/usage/<sid>.baseline.json の gate(開始時点の GATE_STATUS)。"""
    if not session_id:
        return None
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id))
    path = root / ".github" / "hooks" / "logs" / "usage" / f"{safe}.baseline.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        gate = data.get("gate") if isinstance(data, dict) else None
        return gate if isinstance(gate, dict) else None
    except Exception:  # noqa: BLE001
        return None


def stop_findings(root: Path, status: dict, ok_phases: set) -> tuple[list[tuple[str, str, str]], set]:
    """事故的停止の記録と GATE_STATUS / baseline の突き合わせ。戻り値: (結果行, 達成扱いにしないフェーズ集合)。"""
    mod = _hook_log_module(root)
    if mod is None:
        return [], set()
    logs = root / ".github" / "hooks" / "logs"
    try:
        records = mod.iter_stop_records(str(logs))
    except Exception:  # noqa: BLE001
        return [], set()
    results: list[tuple[str, str, str]] = []
    revoked: set = set()
    for rec in records:
        snap = mod.parse_gate_status(rec.get("gate_status"))
        sid = rec.get("session_id") or "?"
        label = f"セッション {sid} は事故的停止({rec.get('kind')}: {rec.get('reason')})で終わっている"
        fname = Path(str(rec.get("path") or "")).name
        if not snap or snap != status:
            continue  # スナップショットが読めない、または GATE_STATUS がその後変わっている(整合済みとみなす)
        start = baseline_gate(root, sid)
        if start is None:
            results.append(("WARN", "session", f"{label}が、GATE_STATUS はその時点から不変。開始時の baseline が無く"
                            f"どの done 宣言がそのセッション中かを帰属できない(整合を確認したら {fname} を削除)"))
            continue
        newly_done = [p for p in PHASES if status.get(p) == "done" and start.get(p) != "done"]
        if not newly_done:
            results.append(("WARN", "session", f"{label}が、そのセッション中の done 遷移は無い(GATE_STATUS 不変。"
                            f"progress.md / tasks.md の整合を確認したら {fname} を削除)"))
            continue
        for p in newly_done:
            revoked.add(p)
            results.append(("NG", p, f"done 宣言が事故的停止で終わったセッション {sid}({rec.get('kind')}: {rec.get('reason')})の"
                            f"中で行われ、以後 GATE_STATUS が変わっていない(整合未確認)。達成扱いにしない"
                            f"(確認・修正後に再宣言し {fname} を削除)"))
    return results, revoked & ok_phases


def check_project(root: Path) -> tuple[list[tuple[str, str, str]], int, int]:
    """検査結果 [(判定, フェーズ, 理由)], 完了宣言数, 検証通過数 を返す。"""
    results: list[tuple[str, str, str]] = []
    if not (root / "docs" / "00-overview" / "progress.md").exists():
        # 未初期化(素の配布物)は NG ではなく SKIP(trace-check と意味論を統一)
        results.append(("SKIP", "progress",
                        "progress.md が無い(未初期化のため検査対象なし)"))
        return results, 0, 0
    status = read_gate_status(root)
    if status is None:
        results.append(("NG", "progress",
                        "progress.md はあるが GATE_STATUS ブロックを読めない"))
        return results, 0, 0
    if not status:
        # 空 dict のまま進めると全フェーズ SKIP・exit 0 で書式破損が素通りしてしまう
        results.append(("NG", "progress",
                        "GATE_STATUS ブロックはあるがフェーズキーを1件も読めない(書式破損の可能性)"))
        return results, 0, 0

    declared = passed = 0
    ok_phases: set = set()
    for phase in PHASES:
        st = status.get(phase, "not_started")
        if st != "done":
            results.append(("SKIP", phase, f"状態 {st}(完了宣言なし)"))
            continue
        declared += 1
        art = root / ARTIFACTS[phase]
        if not art.exists() or art.stat().st_size == 0:
            results.append(("NG", phase, f"done 宣言だが成果物 {ARTIFACTS[phase]} が無い/空"))
            continue
        ok = True
        if phase == "implementation":
            text = art.read_text(encoding="utf-8", errors="replace")
            unchecked = re.findall(r"^\s*-\s*\[\s\]", text, re.M)
            if unchecked:
                results.append(("NG", phase, f"done 宣言だが未完了タスクが {len(unchecked)} 件残っている"))
                ok = False
            # done契約(完了条件)の証拠: [x] タスクの周辺に検証の痕跡があるか(緩い検査)
            checked = len(re.findall(r"^\s*-\s*\[x\]", text, re.M | re.I))
            if ok and checked and "完了条件" not in text:
                results.append(("WARN", phase, "tasks.md に完了条件(done契約)の記載が無い(旧テンプレの可能性)"))
            # 独立レビュー記録(A6-14 / RD-2): done 宣言に対し reviewer の実施記録が無い、または
            # 最新の完了証拠より古い記録しか無ければ WARN(NG にはしない。実運用で reviewer 起動
            # 0 回のまま実装フェーズが done になった実例の機械検出。warn-gate-tamper と同一規則)
            has_record, latest_review, latest_evidence = review_record(root, ARTIFACTS[phase])
            if not has_record:
                results.append(("WARN", phase, "implementation done だが独立レビューの記録が無い"
                                "(docs/04-test/review-log.md に日時付きエントリが無く、"
                                "security-review-report.md も無い。reviewer 起動 0 回の疑い)"))
            elif latest_review and latest_evidence and latest_review[:10] < latest_evidence[:10]:
                results.append(("WARN", phase, f"最新の完了証拠({latest_evidence[:10]})以降の独立レビューの"
                                f"記録が無い(review-log.md の最新エントリは {latest_review[:10]})"))
        if phase == "test":
            # 素コピー検出 → 結果表の「実データ存在検査」。検査キーワード入りのテンプレ
            # ヘッダ行を本文に残す方式は「ヘッダだけ保持して実データ0行」の成果物が
            # キーワード検査をすり抜けるため、結果表ヘッダ(キーワードを含む表行)の後に
            # テンプレに無い表データ行(| を含む非テンプレ行・区切り行 |---| は除く)が
            # 1行以上あるかを検査する。結果表が無い成果物(旧構成・散文レポート)は
            # 従来どおりテンプレ定型行を除いたキーワード検査にフォールバックする。
            raw = art.read_text(encoding="utf-8", errors="replace")
            if template_only(root, raw, TEMPLATES["test"]):
                results.append(("WARN", phase, "test-report.md がテンプレートの素コピーのまま"
                                "(実データ行が無い)"))
            else:
                lines = raw.splitlines()
                header_idx = next((i for i, ln in enumerate(lines)
                                   if ln.lstrip().startswith("|")
                                   and CHECK_KEYWORD_RE.search(ln)), None)
                if header_idx is not None:
                    tpl_lines = template_line_set(root, TEMPLATES["test"])
                    has_data = any("|" in ln and ln.rstrip() not in tpl_lines
                                   and not re.fullmatch(r"[|\s:\-]+", ln)
                                   for ln in lines[header_idx + 1:])
                    if not has_data:
                        results.append(("WARN", phase,
                                        "test-report.md の結果表にヘッダ行しか無く"
                                        "実データ行が無い(テンプレに無い表データ行が0行)"))
                else:
                    text = strip_template_lines(root, raw, TEMPLATES["test"])
                    if not re.search(r"(成功|失敗|pass|fail|PASS|FAIL|✅|❌|\d+\s*件)", text):
                        results.append(("WARN", phase, "test-report.md にテスト結果らしい記載を検出できない"
                                        "(テンプレート定型行は除いて判定)"))
            # 鮮度検査(証拠鮮度規律): 実行日時の記載が無いテスト証拠は「いつの実行か」を
            # 検証できない(古い証拠の使い回し・完了ハルシネーションを見抜けない)。
            # YYYY-MM-DD 形式の日付が本文に1つも無ければ WARN(NG にはしない。
            # 独自書式の旧レポートを一律に失敗させないため)
            if not re.search(r"\d{4}-\d{2}-\d{2}", raw):
                results.append(("WARN", phase,
                                "実行日時の無いテスト証拠(鮮度検証不能): test-report.md に"
                                "実行日時(YYYY-MM-DD形式)の記載が1つも無い"))
        if phase == "release":
            text = art.read_text(encoding="utf-8", errors="replace")
            unchecked = re.findall(r"^\s*-\s*\[\s\]", text, re.M)
            if unchecked:
                results.append(("NG", phase, f"done 宣言だが未チェック項目が {len(unchecked)} 件残っている"))
                ok = False
        if ok:
            passed += 1
            ok_phases.add(phase)
            results.append(("OK", phase, f"{ARTIFACTS[phase]} と整合"))
    # 事故的停止(A2-1): 記録のスナップショットが今の GATE_STATUS と同じなら、そのセッション中の done 宣言は達成扱いにしない
    stop_results, revoked = stop_findings(root, status, ok_phases)
    results += stop_results
    passed -= len(revoked)
    # 横断の軽い検査(NGにはしない): learnings とトレーサビリティ
    if (root / ARTIFACTS["design"]).exists():
        # 素コピー検出 → テンプレート定型行(検査キーワード行は除く)を除いてから検査する
        draw = (root / ARTIFACTS["design"]).read_text(encoding="utf-8", errors="replace")
        if template_only(root, draw, TEMPLATES["design"]):
            results.append(("WARN", "design", "architecture.md がテンプレートの素コピーのまま"
                            "(実データ行が無い)"))
        else:
            dtext = strip_template_lines(root, draw, TEMPLATES["design"])
            if not re.search(r"(トレーサビリティ|要件対応表)", dtext):
                results.append(("WARN", "design", "architecture.md にトレーサビリティ表の記載が見当たらない"
                                "(テンプレート定型行は除いて判定)"))
    if declared >= 3 and not (root / "docs" / "00-overview" / "learnings.md").exists():
        results.append(("WARN", "growth", "learnings.md が無い(成長ループが回っていない可能性)"))
    return results, declared, passed


def run(root: Path) -> int:
    if not root.is_dir():
        print(f"エラー: {root} はディレクトリではありません")
        return 2
    results, declared, passed = check_project(root)
    ng = sum(1 for r in results if r[0] == "NG")
    for verdict, phase, reason in results:
        print(f"{verdict}\t{phase}\t{reason}")
    print()
    if declared:
        rate = 100 * passed // declared
        print(f"完了宣言 {declared} 件中、機械的検証を通過 {passed} 件({rate}%)。目標は90%以上。")
    else:
        print("完了宣言(done)がまだ無いため、割合の算出対象はありません。")
    return 1 if ng else 0


def selftest() -> int:
    failures = []

    def expect(cond, label):
        (failures.append(label) if not cond else None)
        print(("PASS: " if cond else "FAIL: ") + label)

    with tempfile.TemporaryDirectory() as td:
        # fixture A: 整合したプロジェクト(要3フェーズdone)
        a = Path(td) / "ok"
        (a / "docs" / "00-overview").mkdir(parents=True)
        (a / "docs" / "00-overview" / "progress.md").write_text(
            "<!-- GATE_STATUS\nrequirements: done\ndesign: done\nimplementation: done\n"
            "test: in_progress\nrelease: not_started\n-->\n", encoding="utf-8")
        (a / "docs" / "00-overview" / "learnings.md").write_text("- [2026-08-12] x\n", encoding="utf-8")
        (a / "docs" / "01-requirements").mkdir(parents=True)
        (a / "docs" / "01-requirements" / "requirements.md").write_text("# 要件\nUS-001\n", encoding="utf-8")
        (a / "docs" / "02-design").mkdir(parents=True)
        (a / "docs" / "02-design" / "architecture.md").write_text("# 設計\n## トレーサビリティ\n", encoding="utf-8")
        (a / "docs" / "03-implementation").mkdir(parents=True)
        (a / "docs" / "03-implementation" / "tasks.md").write_text(
            "- [x] TASK-001: x(完了条件: npm test 12件成功)\n", encoding="utf-8")
        results, declared, passed = check_project(a)
        expect(declared == 3 and passed == 3, "整合プロジェクト: 完了宣言3件すべて検証通過")
        expect(not any(v == "NG" for v, _, _ in results), "整合プロジェクト: NGなし")

        # fixture B: 完了宣言と実態が食い違うプロジェクト
        b = Path(td) / "broken"
        (b / "docs" / "00-overview").mkdir(parents=True)
        (b / "docs" / "00-overview" / "progress.md").write_text(
            "<!-- GATE_STATUS\nrequirements: done\ndesign: not_started\nimplementation: done\n"
            "test: not_started\nrelease: not_started\n-->\n", encoding="utf-8")
        (b / "docs" / "03-implementation").mkdir(parents=True)
        (b / "docs" / "03-implementation" / "tasks.md").write_text(
            "- [x] TASK-001: x\n- [ ] TASK-002: y\n", encoding="utf-8")
        results, declared, passed = check_project(b)
        ngs = {(p) for v, p, _ in results if v == "NG"}
        expect("requirements" in ngs, "食い違い: done宣言なのに requirements.md が無い -> NG")
        expect("implementation" in ngs, "食い違い: done宣言なのに未完了タスク残り -> NG")
        expect(declared == 2 and passed == 0, "食い違い: 検証通過0/2")

        # fixture C: progress.md なし(未初期化/素の配布物) → SKIP・exit 0(NG にしない)
        c = Path(td) / "empty"
        c.mkdir()
        results, declared, _ = check_project(c)
        expect(any(v == "SKIP" and p == "progress" for v, p, _ in results) and declared == 0,
               "progress.md なし -> SKIP(未初期化のため検査対象なし)")
        expect(not any(v == "NG" for v, _, _ in results),
               "progress.md なし -> NG を出さない(trace-check と意味論統一)")

        # fixture C2: progress.md はあるが GATE_STATUS ブロックが無い → こちらは NG のまま
        c2 = Path(td) / "no-block"
        (c2 / "docs" / "00-overview").mkdir(parents=True)
        (c2 / "docs" / "00-overview" / "progress.md").write_text("# 進捗\n", encoding="utf-8")
        results, _, _ = check_project(c2)
        expect(any(v == "NG" and p == "progress" for v, p, _ in results),
               "GATE_STATUS ブロックなし -> NG(未初期化とは区別する)")

        # fixture D: テンプレート素コピー(定型文だけで検査を全通過させない)
        d = Path(td) / "template-copy"
        tpl_design = "# アーキテクチャ設計書\n## 7. 要件対応表(トレーサビリティ)\n| 要件ID | 対応する設計要素 |\n"
        tpl_test = "# テスト結果レポート\n| 種別 | 実施数 | 成功 | 失敗 | スキップ |\n"
        tpl_release = "# リリースチェックリスト\n- [ ] 全テストがグリーンである\n- [ ] ロールバック手順が用意されている\n"
        (d / "docs" / "00-overview").mkdir(parents=True)
        (d / "docs" / "00-overview" / "progress.md").write_text(
            "<!-- GATE_STATUS\nrequirements: not_started\ndesign: not_started\n"
            "implementation: not_started\ntest: done\nrelease: done\n-->\n", encoding="utf-8")
        for rel, content in ((TEMPLATES["design"], tpl_design), (TEMPLATES["test"], tpl_test),
                             ("docs/05-release/release_checklist_template.md", tpl_release),
                             (ARTIFACTS["design"], tpl_design), (ARTIFACTS["test"], tpl_test),
                             (ARTIFACTS["release"], tpl_release)):
            f = d / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
        results, declared, passed = check_project(d)
        expect(any(v == "WARN" and p == "test" for v, p, _ in results),
               "テンプレ素コピー: test-report.md が定型文のみ -> WARN")
        expect(any(v == "WARN" and p == "design" for v, p, _ in results),
               "テンプレ素コピー: architecture.md が定型文のみ -> WARN")
        expect(any(v == "NG" and p == "release" for v, p, _ in results),
               "テンプレ素コピー: release-checklist.md に未チェック [ ] 残り -> NG")

        # fixture E: GATE_STATUS ブロックはあるがフェーズキーが0件(書式破損)
        e = Path(td) / "broken-keys"
        (e / "docs" / "00-overview").mkdir(parents=True)
        (e / "docs" / "00-overview" / "progress.md").write_text(
            "<!-- GATE_STATUS\n(書式が壊れていてキーを読めない)\n-->\n", encoding="utf-8")
        results, declared, _ = check_project(e)
        expect(any(v == "NG" and p == "progress" for v, p, _ in results) and declared == 0,
               "GATE_STATUS キー0件 -> NG(全SKIP・exit 0 で素通りしない)")

        # fixture F: テンプレートに正しく数値を埋めた test-report が偽 WARN にならない回帰ケース
        # (表ヘッダ「成功/失敗」を集合差で除去していた頃は、実データ行に検査キーワードが
        #  残らず「テスト結果らしい記載を検出できない」と誤警告していた)
        ff = Path(td) / "filled-report"
        (ff / "docs" / "00-overview").mkdir(parents=True)
        (ff / "docs" / "00-overview" / "progress.md").write_text(
            "<!-- GATE_STATUS\nrequirements: not_started\ndesign: not_started\n"
            "implementation: not_started\ntest: done\nrelease: not_started\n-->\n", encoding="utf-8")
        tpl_test_f = "# テスト結果レポート\n| 種別 | 実施数 | 成功 | 失敗 | スキップ |\n|---|---|---|---|---|\n"
        for rel, content in ((TEMPLATES["test"], tpl_test_f),
                             (ARTIFACTS["test"], tpl_test_f + "| 単体 | 12 | 12 | 0 | 0 |\n"
                              "実施日: 2026-08-22\n")):
            p = ff / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        results, declared, passed = check_project(ff)
        expect(not any(v == "WARN" and p == "test" for v, p, _ in results),
               "数値を埋めた test-report: 偽 WARN を出さない(ヘッダ行は検査対象に残す)")
        expect(not any("鮮度" in r for v, p, r in results if p == "test"),
               "実行日時入り test-report: 鮮度 WARN を出さない")
        expect(declared == 1 and passed == 1, "数値を埋めた test-report: 検証通過1/1")

        # fixture G: 検査キーワード入りヘッダは保持+実データ0行+表以外の追記1行 → WARN
        # (ヘッダ行のキーワードだけでキーワード検査をすり抜ける偽陰性の反例ケース)
        g = Path(td) / "header-only"
        (g / "docs" / "00-overview").mkdir(parents=True)
        (g / "docs" / "00-overview" / "progress.md").write_text(
            "<!-- GATE_STATUS\nrequirements: not_started\ndesign: not_started\n"
            "implementation: not_started\ntest: done\nrelease: not_started\n-->\n", encoding="utf-8")
        tpl_test_g = "# テスト結果レポート\n| 種別 | 実施数 | 成功 | 失敗 | スキップ |\n|---|---|---|---|---|\n"
        for rel, content in ((TEMPLATES["test"], tpl_test_g),
                             (ARTIFACTS["test"], tpl_test_g + "実施メモ: 後で埋める\n")):
            p = g / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        results, declared, passed = check_project(g)
        expect(any(v == "WARN" and p == "test" for v, p, _ in results),
               "ヘッダ保持+実データ0行+追記1行 -> WARN(表データ行の実在で判定)")

        # fixture H: 実データ行はあるが実行日時(YYYY-MM-DD)が1つも無い → 鮮度 WARN
        # (証拠鮮度規律: いつの実行かを検証できない証拠は完了ハルシネーションを見抜けない)
        hh = Path(td) / "no-date-report"
        (hh / "docs" / "00-overview").mkdir(parents=True)
        (hh / "docs" / "00-overview" / "progress.md").write_text(
            "<!-- GATE_STATUS\nrequirements: not_started\ndesign: not_started\n"
            "implementation: not_started\ntest: done\nrelease: not_started\n-->\n", encoding="utf-8")
        tpl_test_h = "# テスト結果レポート\n| 種別 | 実施数 | 成功 | 失敗 | スキップ |\n|---|---|---|---|---|\n"
        for rel, content in ((TEMPLATES["test"], tpl_test_h),
                             (ARTIFACTS["test"], tpl_test_h + "| 単体 | 12 | 12 | 0 | 0 |\n")):
            p = hh / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        results, declared, passed = check_project(hh)
        expect(any(v == "WARN" and p == "test" and "鮮度" in r for v, p, r in results),
               "実行日時なし test-report -> WARN(実行日時の無いテスト証拠・鮮度検証不能)")
        expect(declared == 1 and passed == 1,
               "実行日時なし test-report: WARN のみで検証通過は維持(1/1)")

        # fixture I: A6-14 / RD-2 独立レビュー記録。implementation done で review-log が無い → WARN、
        # 日時付きエントリがあれば WARN なし、最新の完了証拠より古ければ鮮度 WARN、
        # security-review-report.md だけでも記録ありとみなす(warn-gate-tamper と同一規則)
        ii = Path(td) / "review-log"
        (ii / "docs" / "00-overview").mkdir(parents=True)
        (ii / "docs" / "00-overview" / "progress.md").write_text(
            "<!-- GATE_STATUS\nrequirements: not_started\ndesign: not_started\n"
            "implementation: done\ntest: not_started\nrelease: not_started\n-->\n", encoding="utf-8")
        (ii / "docs" / "03-implementation").mkdir(parents=True)
        tasks_i = ii / "docs" / "03-implementation" / "tasks.md"
        tasks_i.write_text("- [x] TASK-001: x（完了条件: npm test 12件成功 2026-09-10 09:00）\n", encoding="utf-8")
        results, declared, passed = check_project(ii)
        expect(any(v == "WARN" and p == "implementation" and "独立レビュー" in r for v, p, r in results),
               "review-log なし implementation done -> WARN(独立レビューの記録が無い)")
        expect(declared == 1 and passed == 1, "review-log なし: WARN のみで検証通過は維持(1/1)")
        (ii / "docs" / "04-test").mkdir(parents=True)
        log_i = ii / "docs" / "04-test" / "review-log.md"
        log_i.write_text("# 独立レビュー記録\n\n### 2026-09-10 10:30 / implementation / 対象: TASK-001 / verdict: 承認\n",
                         encoding="utf-8")
        results, _, _ = check_project(ii)
        expect(not any("独立レビュー" in r for v, p, r in results if p == "implementation"),
               "日時付き review-log あり -> 独立レビュー WARN を出さない")
        tasks_i.write_text(tasks_i.read_text(encoding="utf-8")
                           + "- [x] TASK-002: y（完了条件: npm test 13件成功 2026-09-12 09:00）\n", encoding="utf-8")
        results, _, _ = check_project(ii)
        expect(any(v == "WARN" and p == "implementation" and "以降の独立レビュー" in r for v, p, r in results),
               "最新の完了証拠より古い review-log -> 鮮度 WARN")
        log_i.unlink()
        (ii / "docs" / "04-test" / "security-review-report.md").write_text("report\n", encoding="utf-8")
        results, _, _ = check_project(ii)
        expect(not any("独立レビュー" in r for v, p, r in results if p == "implementation"),
               "security-review-report.md のみ -> 記録ありとみなし WARN を出さない")

        # fixture J: A2-1 事故的停止の記録(abnormal-stop-* / watchdog-stop-*)と GATE_STATUS / baseline の突き合わせ
        jj = Path(td) / "abnormal-stop"
        (jj / "docs" / "00-overview").mkdir(parents=True)
        gate_j = "<!-- GATE_STATUS\nrequirements: done\ndesign: not_started\nimplementation: done\ntest: not_started\nrelease: not_started\n-->\n"
        (jj / "docs" / "00-overview" / "progress.md").write_text(gate_j, encoding="utf-8")
        (jj / "docs" / "01-requirements").mkdir(parents=True)
        (jj / "docs" / "01-requirements" / "requirements.md").write_text("# 要件\nUS-001\n", encoding="utf-8")
        (jj / "docs" / "03-implementation").mkdir(parents=True)
        (jj / "docs" / "03-implementation" / "tasks.md").write_text(
            "- [x] TASK-001: x（完了条件: npm test 12件成功 2026-09-10 09:00）\n", encoding="utf-8")
        (jj / "docs" / "04-test").mkdir(parents=True)
        (jj / "docs" / "04-test" / "review-log.md").write_text("### 2026-09-10 10:30 / implementation / 対象: TASK-001 / verdict: 承認\n", encoding="utf-8")
        logs_j = jj / ".github" / "hooks" / "logs"
        (logs_j / "usage").mkdir(parents=True)
        results, declared, passed = check_project(jj)
        expect(declared == 2 and passed == 2, "事故的停止なし: 2/2 通過(基準)")
        (logs_j / "abnormal-stop-s1.json").write_text(json.dumps({"recorded_at": "2026-09-17T10:00:00+09:00", "session_id": "s1",
                                                                  "error_type": "rate_limit", "gate_status": gate_j}), encoding="utf-8")
        (logs_j / "usage" / "s1.baseline.json").write_text(json.dumps({"session_id": "s1", "gate": {"requirements": "done", "implementation": "in_progress"}}),
                                                           encoding="utf-8")
        results, declared, passed = check_project(jj)
        expect(any(v == "NG" and p == "implementation" and "事故的停止" in r for v, p, r in results),
               "事故的停止(StopFailure)の中で done になった implementation は NG(達成扱いにしない)")
        expect(declared == 2 and passed == 1, "達成扱いにしないフェーズは通過数から除く(1/2)")
        expect(not any(v == "NG" and p == "requirements" for v, p, _ in results), "セッション開始前から done の requirements は NG にしない")
        (logs_j / "usage" / "s1.baseline.json").write_text(json.dumps({"session_id": "s1", "gate": {"requirements": "done", "implementation": "done"}}),
                                                           encoding="utf-8")
        results, declared, passed = check_project(jj)
        expect(passed == 2 and any(v == "WARN" and p == "session" and "done 遷移は無い" in r for v, p, r in results),
               "そのセッション中に done 遷移が無ければ WARN のみ(整合確認の督促)")
        (logs_j / "usage" / "s1.baseline.json").unlink()
        results, declared, passed = check_project(jj)
        expect(passed == 2 and any(v == "WARN" and p == "session" and "baseline" in r for v, p, r in results),
               "baseline が無く帰属できなければ WARN のみ")
        (logs_j / "usage" / "s1.baseline.json").write_text(json.dumps({"session_id": "s1", "gate": {"requirements": "done", "implementation": "in_progress"}}),
                                                           encoding="utf-8")
        (jj / "docs" / "00-overview" / "progress.md").write_text(gate_j.replace("test: not_started", "test: in_progress"), encoding="utf-8")
        results, declared, passed = check_project(jj)
        expect(passed == 2 and not any(p == "session" or "事故的停止" in r for v, p, r in results),
               "GATE_STATUS がその後変わっていれば整合済みとみなし何も出さない")
        (jj / "docs" / "00-overview" / "progress.md").write_text(gate_j, encoding="utf-8")
        (logs_j / "abnormal-stop-s1.json").unlink()
        (logs_j / "watchdog-stop-s1.json").write_text(json.dumps({"recorded_at": "2026-09-17T10:00:00+09:00", "session_id": "s1",
                                                                  "kind": "watchdog_limit", "reason": "limit-reached 3/3", "gate_status": gate_j}),
                                                      encoding="utf-8")
        results, declared, passed = check_project(jj)
        expect(passed == 1 and any(v == "NG" and p == "implementation" and "watchdog_limit" in r for v, p, r in results),
               "watchdog の上限到達記録(watchdog-stop-*)も同じ規則で NG")
        expect(run(jj) == 1, "run: 達成扱いにしないフェーズがあれば exit 1")

    print()
    print(f"golden-eval selftest: {'FAIL ' + str(len(failures)) if failures else 'all passed'}")
    return 1 if failures else 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        return selftest()
    if not args:
        print(__doc__)
        return 2
    return run(Path(args[0]))


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""既存アプリをハーネステンプレートベースの新プロジェクトへ取り込むセットアップツール。

多数の既存アプリにハーネスを展開するときの機械的なセットアップ(テンプレート複製 →
app/ へのアプリコピー → git 初期化 → 初回コミット)を決定的に自動化する
(brownfield-intake スキルの経路A)。as-is の解析・docs への逆起こしは本ツールの
範囲外で、作成後のプロジェクトを開いた新しいセッションの /11-brownfield-intake が担う。

使い方(ハーネス本体で実行。clone でも ZIP 展開コピーでも可):
  python tools/intake-app.py --app <既存アプリのパス> --project <新プロジェクトの作成先> [--dir app] [--apply]
  自己テスト: python tools/intake-app.py --selftest
      (一時ディレクトリの合成フィクスチャで本体判定・テンプレート除外・入れ子拒否の
       回帰を検証する。実リポジトリには書き込まない)

設計方針:
- sync-harness.py と同じく決定的・既定は dry-run(検査とレポートのみ)。書き込みは --apply の時だけ。
- テンプレートは本体 HEAD の追跡ファイルのみ(git archive)。.git・ローカル設定・
  フックログは構造的に混入しない(USAGE.md「0. 準備」経路2と同じ安全なZIPの作り方)。
  本体が git リポジトリでない場合(ZIP でダウンロードした本体コピー)は、
  ローカル専用ファイルを除外したフォルダコピーに自動でフォールバックする。
- アプリは内部構成そのまま --dir(既定 app/)配下へコピーする。仕分け・再編成はしない
  (再編成は as-is 確定後の改修タスク。brownfield-intake スキルのアンチパターン参照)。
- アプリ側ファイルは一切改変しない。AI設定資産(CLAUDE.md / .github / .claude 等)は
  検出して報告するだけで、棚卸し(知識回収・無効化の提案)は /11-brownfield-intake が
  ユーザー承認つきで行う(検知は機械・判断はエージェント・承認は人)。
- レポートはプロジェクト側 docs/00-overview/intake-report.md に書く(/11 の入力になる)。
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# ハーネスが占有するルート名。--dir がこれらと衝突すると探索・フックが壊れる
RESERVED_NAMES = {"docs", "requirements", "tools", ".github", ".claude", ".agents",
                  ".vscode", ".git"}
# アプリコピー時に除外するもの(.git はネストするとgitlink化して外側のリポジトリが壊れる)
EXCLUDE_DIRS = {".git", "__pycache__", "node_modules"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}
# 非git本体(ZIP展開コピー)からのテンプレート作成時に追加で除外するローカル専用物。
# DECISIONS.md / audits/ は本体の開発記録で配布しない(.gitattributes の export-ignore・
# D049 と同一。git archive 経路と配布物を一致させる)。参考資料(ハーネス調査)/ は
# gitignore 対象のローカル調査資料で、使用中の本体コピーに残存しうるため同様に除外する。
# CHANGELOG.md / SECURITY.md / CONTRIBUTING.md / README.en.md はハーネス本体専用の
# メタ文書のため配布しない(第3回監査。.gitattributes の export-ignore と同一に保つ)
TEMPLATE_EXCLUDE_REL = {".claude/settings.local.json", ".github/copilot/settings.local.json", "DECISIONS.md",
                        "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "README.en.md",
                        "tools/e2e-run.py", "tools/eval-report.py",  # 本体専用の測定装置(三重台帳: .gitattributes / sync-harness.py と同一)
                        "tools/gen-docs.py"}  # 本体専用の文書生成器
TEMPLATE_EXCLUDE_REL_DIRS = {".github/hooks/logs", ".claude/skills/.system", "audits",
                             "参考資料（ハーネス調査）", "evaluation"}
# Claude Code はサブディレクトリの CLAUDE.md / AGENTS.md を配下の作業時に自動で
# 文脈に読み込むため、app/ 配下でも「実効性あり」(唯一の要棚卸し対象)
EFFECTIVE_NAMES = {"claude.md", "agents.md"}
# ルート起点でしか探索されないため app/ 配下では不活性なAI設定ディレクトリ
AI_CONFIG_DIRS = {".github", ".claude", ".agents", ".cursor", ".vscode", ".windsurf"}


# この判定は intake-app.py / sync-harness.py で同一に保つこと(片方だけ変えると
# 経路A(取り込み)と経路B(逆同期)で本体/プロジェクトの判定がずれる)。
def looks_like_harness(root: Path) -> bool:
    """本体= .github/harness/USAGE.md があり progress.md 実体が無い(プロジェクトをテンプレに誤用しない)。

    DECISIONS.md は D049 の export-ignore により git archive / GitHub ZIP 由来の
    本体コピーに含まれないため、判定には使わない(有無を問わない)。
    intake 直後のプロジェクトは progress.md がまだ無い(/11 実行前)ため、
    本ツールが書く intake-report.md の存在もプロジェクトの証拠として使う。
    """
    return ((root / ".github/harness/USAGE.md").is_file()
            and not (root / "docs/00-overview/progress.md").exists()
            and not (root / "docs/00-overview/intake-report.md").exists())


def harness_version(harness: Path) -> str:
    """本体のバージョン表記。DECISIONS.md の最大D番号 → plugin.json の version → unknown。

    ZIP展開コピーの本体には D049 の export-ignore で DECISIONS.md が無いため、
    plugin.json の version へ順にフォールバックする(sync-harness.py と同一に保つ)。
    """
    decisions = harness / "DECISIONS.md"
    if decisions.is_file():
        text = decisions.read_text(encoding="utf-8", errors="replace")
        nums = [int(m) for m in re.findall(r"^## D(\d+)", text, re.M)]
        if nums:
            return f"D{max(nums):03d}"
    plugin = harness / "plugin.json"
    if plugin.is_file():
        try:
            ver = json.loads(plugin.read_text(encoding="utf-8", errors="replace")).get("version")
        except (ValueError, AttributeError):
            ver = None
        if ver:
            return f"v{ver}"
    return "unknown"


def harness_git_info(harness: Path) -> tuple:
    """本体の HEAD sha と origin URL(git が無い / リポジトリでなければ unknown)。sync-harness.py と同一。"""
    git = shutil.which("git")
    sha = url = "unknown"
    if git:
        try:
            r = subprocess.run([git, "-C", str(harness), "rev-parse", "HEAD"], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=15)
            if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{7,40}", r.stdout.strip()):
                sha = r.stdout.strip()
            r = subprocess.run([git, "-C", str(harness), "remote", "get-url", "origin"], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                url = r.stdout.strip()
        except Exception:  # noqa: BLE001
            pass
    return sha, url


def write_origin(project: Path, harness: Path, version: str, route: str = "intake") -> None:
    """本体パスと配布鮮度をプロジェクトへ自動記録する(tools/sync-harness.py / tools/doctor.py --write-origin と
    同じ書式。以後の「ハーネスを更新して」(/91)で --harness を省略でき、SessionStart フックと doctor が
    latest_decision と本体の最新 D 番号の差で「/91 を先に実行」を案内できる)。
    source_commit / archive_sha256 / synced_at / latest_decision は 2026-09-10(A6-15 / RD-4)の共通フィールド名。"""
    p = project / "docs/00-overview/harness-origin.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().astimezone()
    today = now.date().isoformat()
    sha, url = harness_git_info(harness)
    p.write_text(f"""<!-- HARNESS_ORIGIN
path: {harness.as_posix()}
version: {version}
synced: {today}
source_url: {url}
source_commit: {sha}
archive_sha256: n/a
synced_at: {now.strftime("%Y-%m-%dT%H:%M:%S%z")}
latest_decision: {version}
route: {route}
-->

# ハーネス本体の場所と配布鮮度(自動記録)

このプロジェクトのハーネスは `{harness.as_posix()}` からコピー/同期された({version} まで・{today}・経路 {route})。
「ハーネスを更新して」の依頼(/91-sync-from-harness)ではこのパスが既定の本体として使われ、
SessionStart フック(inject-progress)と `python tools/doctor.py` は `latest_decision` と本体の
DECISIONS.md の最新 D 番号の差で「/91 を先に実行」を案内する。`tools/sync-harness.py --apply` の
たびに自動更新されるため、手で編集しない(本体を移動した場合は次回 `--harness` で明示するか
`python tools/doctor.py --write-origin --harness <本体パス> --force` で書き直す)。
""", encoding="utf-8", newline="\n")


def run_git(git: str, *args, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([git, *args], cwd=str(cwd), capture_output=True,
                          encoding="utf-8", errors="replace")


def scan_app(app: Path):
    """コピー対象(相対POSIXパス)と除外一覧を列挙する。os.walk相当をpathlibで実装"""
    files, excluded = [], []

    def walk(d: Path):
        for p in sorted(d.iterdir()):
            rel = p.relative_to(app).as_posix()
            if p.is_dir():
                if p.name in EXCLUDE_DIRS:
                    excluded.append(rel + "/")
                else:
                    walk(p)
            elif p.name in EXCLUDE_FILES:
                excluded.append(rel)
            else:
                files.append(rel)

    walk(app)
    return files, excluded


def scan_ai_assets(files, dirname: str):
    """アプリ内のAI設定資産を検出し分類する(報告のみ。改変しない)。

    戻り値: (実効性ありファイル, 旧CIディレクトリ, 不活性ディレクトリ) いずれも
    プロジェクトルートからの相対パス(dirname/ 前置)で返す。
    """
    effective, ci_dirs, inert_dirs = [], {}, {}
    for rel in files:
        parts = rel.split("/")
        name = parts[-1].lower()
        if name in EFFECTIVE_NAMES:
            effective.append(f"{dirname}/{rel}")
        for i, part in enumerate(parts[:-1]):
            if part in AI_CONFIG_DIRS:
                root = f"{dirname}/" + "/".join(parts[: i + 1])
                if part == ".github" and "workflows" in parts[i + 1:-1]:
                    ci_dirs[root + "/workflows"] = ci_dirs.get(root + "/workflows", 0) + 1
                else:
                    inert_dirs[root] = inert_dirs.get(root, 0) + 1
                break
    return sorted(effective), dict(sorted(ci_dirs.items())), dict(sorted(inert_dirs.items()))


def scan_harness(harness: Path):
    """非gitの本体コピー(ZIP展開)向けに、テンプレートへ含めるファイルを列挙する。

    git archive(HEAD) の代替。GitHub の ZIP は追跡ファイルのみだが、使用中のコピーには
    ローカル専用ファイル(フックログ・settings.local.json 等)が生じうるため除外する。
    """
    files = []

    def walk(d: Path):
        for p in sorted(d.iterdir()):
            rel = p.relative_to(harness).as_posix()
            if p.is_dir():
                if p.name in EXCLUDE_DIRS or rel in TEMPLATE_EXCLUDE_REL_DIRS:
                    continue
                walk(p)
            elif (p.name in EXCLUDE_FILES or rel in TEMPLATE_EXCLUDE_REL
                  or (rel.startswith(".vscode/") and rel.endswith(".local.json"))):
                continue
            else:
                files.append(rel)

    walk(harness)
    return files


def copy_app(app: Path, dest: Path) -> None:
    def ignore(_src, names):
        return [n for n in names if n in EXCLUDE_DIRS or n in EXCLUDE_FILES]

    shutil.copytree(app, dest, ignore=ignore)


def readme_stub(app_name: str, dirname: str) -> str:
    return "\n".join([
        f"# {app_name}(仮)",
        "",
        "このリポジトリは、開発ハーネス(テンプレート)に既存アプリを取り込んだプロジェクトです。",
        "",
        f"- アプリ本体: [{dirname}/]({dirname}/) 配下(取り込み時の構成のまま)",
        "- 取り込みレポート: [docs/00-overview/intake-report.md](docs/00-overview/intake-report.md)",
        "- ハーネスの使い方: [.github/harness/USAGE.md](.github/harness/USAGE.md)",
        "",
        "この README は as-is 逆起こし(`/11-brownfield-intake`)とリリース工程で、",
        "アプリの正式な README に置き換えてください。",
        "",
    ])


# ---------------------------------------------------------------- selftest

# 配布除外の三重台帳(tools/ 配下)の三者一致検査。sync-harness.py と同一に保つ
# (.gitattributes export-ignore / sync EXCLUDE_FILES / intake TEMPLATE_EXCLUDE_REL。
# 既知の例外は無し: 2026-09-10 A7-H-9/RG-10 の解消で tools/gen-docs.py も .gitattributes に載せた)。
LEDGER_KNOWN_EXCEPTIONS = set()
LEDGER_APPARATUS = ("tools/e2e-run.py", "tools/eval-report.py")


def distribution_ledgers(root: Path) -> dict:
    ga = set()
    ga_path = root / ".gitattributes"
    if ga_path.is_file():
        for ln in ga_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^/?(tools/[\w.-]+\.py)\s+export-ignore\b", ln.strip())
            if m:
                ga.add(m.group(1))

    def const_set(path: Path, name: str):
        if not path.is_file():
            return None
        m = re.search(name + r"\s*=\s*\{(.*?)\}", path.read_text(encoding="utf-8"), re.DOTALL)
        return set(re.findall(r'"(tools/[^"]+)"', m.group(1))) if m else None

    return {"gitattributes": ga,
            "sync": const_set(root / "tools" / "sync-harness.py", "EXCLUDE_FILES"),
            "intake": const_set(root / "tools" / "intake-app.py", "TEMPLATE_EXCLUDE_REL")}


def ledger_mismatches(ledgers: dict) -> list:
    problems = []
    sync, intake, ga = ledgers["sync"], ledgers["intake"], ledgers["gitattributes"]
    if sync is None or intake is None:
        return ["台帳の定数を読めない(sync-harness.py / intake-app.py の定義形式を確認)"]
    if sync != intake:
        problems.append(f"sync EXCLUDE_FILES と intake TEMPLATE_EXCLUDE_REL が不一致: {sorted(sync ^ intake)}")
    expected_ga = sync - LEDGER_KNOWN_EXCEPTIONS
    if ga != expected_ga:
        problems.append(f".gitattributes の export-ignore(tools/)が python 台帳と不一致: {sorted(ga ^ expected_ga)}")
    for must in LEDGER_APPARATUS:
        if not (must in sync and must in intake and must in ga):
            problems.append(f"測定装置 {must} が三重台帳のどれかに無い")
    return problems


def selftest() -> int:
    failures = []

    def expect(cond, label):
        (failures.append(label) if not cond else None)
        print(("PASS: " if cond else "FAIL: ") + label)

    def make_harness(root: Path, with_decisions: bool = True) -> None:
        """looks_like_harness を満たす最小の合成本体を作る。"""
        (root / ".github/harness").mkdir(parents=True)
        (root / ".github/harness/USAGE.md").write_text("# USAGE(フィクスチャ)\n",
                                                       encoding="utf-8", newline="\n")
        (root / "tools").mkdir()
        # 自分自身のコピーを置き、フィクスチャを「本体」としてサブプロセス実行できるようにする
        shutil.copyfile(Path(__file__).resolve(), root / "tools/intake-app.py")
        if with_decisions:
            (root / "DECISIONS.md").write_text("## D001: フィクスチャ\n",
                                               encoding="utf-8", newline="\n")

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        # (a) looks_like_harness の4象限(sync-harness.py と同一判定。片側だけの変更を検出する)
        clone = base / "clone"
        make_harness(clone, with_decisions=True)
        zipcopy = base / "zipcopy"
        make_harness(zipcopy, with_decisions=False)
        applied = base / "applied"
        make_harness(applied)
        (applied / "docs/00-overview").mkdir(parents=True)
        (applied / "docs/00-overview/progress.md").write_text("# progress\n", encoding="utf-8")
        intaken = base / "intaken"
        make_harness(intaken)
        (intaken / "docs/00-overview").mkdir(parents=True)
        (intaken / "docs/00-overview/intake-report.md").write_text("# intake\n", encoding="utf-8")
        expect(looks_like_harness(clone), "(a) 本体clone(DECISIONS.md あり) -> 本体と判定")
        expect(looks_like_harness(zipcopy), "(a) 本体ZIPコピー(DECISIONS.md なし) -> 本体と判定")
        expect(not looks_like_harness(applied), "(a) progress.md ありのプロジェクト -> 本体と判定しない")
        expect(not looks_like_harness(intaken), "(a) intake直後(intake-report.md あり) -> 本体と判定しない")

        # (e) 非git本体のフォールバック列挙が開発記録・ローカル調査資料を配布しないこと
        (zipcopy / "audits").mkdir()
        (zipcopy / "audits/audit.md").write_text("# audit\n", encoding="utf-8")
        (zipcopy / "参考資料（ハーネス調査）").mkdir()
        (zipcopy / "参考資料（ハーネス調査）/調査メモ.md").write_text("# メモ\n", encoding="utf-8")
        (zipcopy / "DECISIONS.md").write_text("## D001: フィクスチャ\n", encoding="utf-8")
        meta4 = ("CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "README.en.md")
        for meta in meta4:
            (zipcopy / meta).write_text(f"# {meta}(フィクスチャ)\n", encoding="utf-8")
        files = scan_harness(zipcopy)
        expect("DECISIONS.md" not in files, "(e) 非gitフォールバックが DECISIONS.md を除外する")
        expect(not any(m in files for m in meta4),
               "(e) 非gitフォールバックがハーネス専用メタ4ファイル"
               "(CHANGELOG/SECURITY/CONTRIBUTING/README.en)を除外する")
        expect(not any(f.startswith("audits/") for f in files),
               "(e) 非gitフォールバックが audits/ を除外する")
        expect(not any(f.startswith("参考資料（ハーネス調査）/") for f in files),
               "(e) 非gitフォールバックが 参考資料(ハーネス調査)/ を除外する")
        expect(".github/harness/USAGE.md" in files, "(e) 通常の配布物は列挙に含まれる")

        # (d) 入れ子 --project の拒否(本体配下への作成は本体を汚す)
        app = base / "app"
        app.mkdir()
        (app / "main.py").write_text("print('hi')\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(clone / "tools/intake-app.py"),
                            "--app", str(app), "--project", str(clone / "sub")],
                           capture_output=True, encoding="utf-8", errors="replace")
        expect(r.returncode != 0 and "本体リポジトリ配下" in (r.stdout + r.stderr),
               "(d) 本体配下への --project(入れ子)を拒否する")
        expect(not (clone / "sub").exists(), "(d) 拒否時に何も書き込まれない")

        # (f) --apply 主経路の実走行: テンプレート展開 → アプリコピー → git init/commit →
        #     intake-report / harness-origin 生成までを一時ディレクトリで検証する
        #     (フィクスチャ本体は非gitのためフォルダコピーのフォールバック経路を通る)
        if shutil.which("git") is None:
            print("SKIP: (f) --apply 主経路(git が無い環境のためスキップ)")
        else:
            # git のユーザー設定・グローバル設定に依存しないよう環境を固定する
            # (未設定環境での commit 失敗や gpgsign 等のローカル設定の影響を避ける)
            empty_cfg = base / "gitconfig-empty"
            empty_cfg.write_text("", encoding="utf-8")
            env = dict(os.environ,
                       GIT_CONFIG_GLOBAL=str(empty_cfg), GIT_CONFIG_SYSTEM=str(empty_cfg),
                       GIT_AUTHOR_NAME="selftest", GIT_AUTHOR_EMAIL="selftest@example.com",
                       GIT_COMMITTER_NAME="selftest", GIT_COMMITTER_EMAIL="selftest@example.com")
            proj = base / "proj_apply"
            r = subprocess.run([sys.executable, str(clone / "tools/intake-app.py"),
                                "--app", str(app), "--project", str(proj), "--apply"],
                               capture_output=True, encoding="utf-8", errors="replace", env=env)
            expect(r.returncode == 0, f"(f) --apply が成功する(exit={r.returncode})")
            expect((proj / ".github/harness/USAGE.md").is_file(),
                   "(f) テンプレート(ハーネス配布物)が展開される")
            expect(not (proj / "DECISIONS.md").exists(),
                   "(f) --apply でも DECISIONS.md は配布されない")
            expect((proj / "app/main.py").is_file(), "(f) アプリが app/ 配下へコピーされる")
            expect((proj / "docs/00-overview/intake-report.md").is_file(),
                   "(f) intake-report.md が生成される(/11 の入力)")
            expect((proj / "docs/00-overview/harness-origin.md").is_file(),
                   "(f) harness-origin.md(本体パスの自動記録)が生成される")
            _origin = (proj / "docs/00-overview/harness-origin.md").read_text(encoding="utf-8")
            expect("\nlatest_decision: " in _origin and "\nsource_commit: " in _origin
                   and "\nsynced_at: " in _origin and "\nroute: intake\n" in _origin,
                   "(f) harness-origin.md に latest_decision / source_commit / synced_at / route を記録する(A6-15)")
            expect(not (proj / "tools/gen-docs.py").exists(),
                   "(f) 本体専用の tools/gen-docs.py はテンプレートに含めない(A7-H-9)")
            head = subprocess.run([shutil.which("git"), "rev-parse", "--verify", "HEAD"],
                                  cwd=str(proj), capture_output=True,
                                  encoding="utf-8", errors="replace", env=env)
            expect((proj / ".git").is_dir() and head.returncode == 0,
                   "(f) git init と初回コミットが行われる")

            # (h) git 本体の未追跡ファイルは配布されない(git archive HEAD の性質。A7-M-1)。件数を警告として報告する
            gith = base / "githarness"
            make_harness(gith, with_decisions=True)
            for step in (("init", "-q", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "init")):
                subprocess.run([shutil.which("git"), "-C", str(gith), *step], capture_output=True, env=env)
            (gith / ".github/harness/UNTRACKED.md").write_text("# untracked\n", encoding="utf-8", newline="\n")
            proj_g = base / "proj_git"
            r = subprocess.run([sys.executable, str(gith / "tools/intake-app.py"),
                                "--app", str(app), "--project", str(proj_g), "--apply"],
                               capture_output=True, encoding="utf-8", errors="replace", env=env)
            expect(r.returncode == 0 and (proj_g / ".github/harness/USAGE.md").is_file()
                   and not (proj_g / ".github/harness/UNTRACKED.md").exists(),
                   "(h) git 本体の未追跡ファイルはテンプレートに含まれない(追跡済みは含まれる)")
            expect("未追跡ファイルが 1 件" in (r.stdout + r.stderr),
                   "(h) 未追跡ファイルの件数を警告として報告する")

    # (g) 配布除外の三重台帳(.gitattributes / sync EXCLUDE_FILES / intake TEMPLATE_EXCLUDE_REL)の三者一致
    problems = ledger_mismatches(distribution_ledgers(Path(__file__).resolve().parents[1]))
    expect(not problems, "(g) 配布除外の三重台帳(tools/)が三者一致"
           + ("" if not problems else ": " + " / ".join(problems)))
    expect(ledger_mismatches({"gitattributes": {"tools/e2e-run.py", "tools/eval-report.py"},
                              "sync": {"tools/e2e-run.py", "tools/eval-report.py"},
                              "intake": {"tools/e2e-run.py"}}) != [],
           "(g) 1 台帳だけの書き忘れを検出する")

    print()
    print(f"intake-app selftest: {'FAIL ' + str(len(failures)) if failures else 'all passed'}")
    return 1 if failures else 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if "--selftest" in sys.argv[1:]:
        return selftest()

    ap = argparse.ArgumentParser(
        description="既存アプリをテンプレートベースの新プロジェクトへ取り込む(brownfield 経路A)")
    ap.add_argument("--app", required=True, help="既存アプリのパス(無改変で読み取るだけ)")
    ap.add_argument("--project", required=True, help="新プロジェクトの作成先(未存在か空であること)")
    ap.add_argument("--dir", default="app", help="アプリを置くディレクトリ名(既定: app)")
    ap.add_argument("--apply", action="store_true", help="実際に作成する(省略時はdry-run)")
    args = ap.parse_args()

    harness = Path(__file__).resolve().parents[1]
    app = Path(args.app).resolve()
    project = Path(args.project).resolve()
    dirname = args.dir

    # ---- 事前検査(導入前評価)。1つでもNGなら何も書かずに中止する
    errors, warnings = [], []
    git = shutil.which("git")
    if git is None:
        errors.append("git が見つかりません(PATHを確認)。")
    if not looks_like_harness(harness):
        errors.append(f"{harness} は本体リポジトリに見えません"
                      "(.github/harness/USAGE.md があり docs/00-overview/progress.md と"
                      " intake-report.md が無い状態が本体。"
                      "プロジェクトのコピーをテンプレートとして使わないでください)。")
    # 保守モード中(ガード解除中)の本体からは配布しない。ガードを外した settings.json を
    # テンプレートとして複製してしまうため(tools/harness-maintenance.py 参照)
    if (harness / ".claude" / "settings.json.locked").is_file():
        errors.append("保守モード中は取り込みできません。"
                      "python tools/harness-maintenance.py --off --apply で復元してから実行してください。")
    if not app.is_dir():
        errors.append(f"--app が存在しないかディレクトリではありません: {app}")
    if project.exists() and (not project.is_dir() or any(project.iterdir())):
        errors.append(f"--project が既に存在し空ではありません: {project}")
    if project == harness or harness in project.parents:
        errors.append("--project を本体リポジトリ配下に作成することはできません(本体を汚さない)。")
    if app.is_dir() and (project == app or app in project.parents or project in app.parents):
        errors.append("--app と --project が入れ子関係です(自分自身へのコピーになる)。")
    if not re.fullmatch(r"[^/\\]+", dirname) or dirname in {".", ".."}:
        errors.append(f"--dir はパス区切りを含まない単一のディレクトリ名にしてください: {dirname}")

    top_level, template_files, harness_git = [], [], False
    if git and looks_like_harness(harness):
        harness_git = run_git(git, "rev-parse", "--is-inside-work-tree",
                              cwd=harness).returncode == 0
        if harness_git:
            r = run_git(git, "ls-tree", "--name-only", "HEAD", cwd=harness)
            if r.returncode != 0:
                errors.append(f"本体で git ls-tree に失敗しました: {r.stderr.strip()}")
            else:
                top_level = r.stdout.splitlines()
            if run_git(git, "status", "--porcelain", cwd=harness).stdout.strip():
                warnings.append("本体に未コミットの変更があります。テンプレートは HEAD 時点の内容です。")
            # 未追跡ファイルは git archive HEAD に構造的に含まれない(=配布されない)。件数を明示して
            # 「入れたつもりのファイルが届かない」を気づかせる(A7-M-1。sync-harness.py は ls-files 照合で除外する)
            _u = run_git(git, "ls-files", "--others", "--exclude-standard", cwd=harness)
            _n_untracked = len([ln for ln in _u.stdout.splitlines() if ln.strip()]) if _u.returncode == 0 else 0
            if _n_untracked:
                warnings.append(f"本体に未追跡ファイルが {_n_untracked} 件あります(git archive HEAD には含まれない=配布されない。"
                                "配布したいものは本体でコミットする。A7-M-1)。")
        else:
            # ZIP でダウンロードした本体コピー(非git)。フォルダコピーにフォールバック
            template_files = scan_harness(harness)
            top_level = sorted({rel.split("/")[0] for rel in template_files})
            warnings.append("本体が git リポジトリではない(ZIP展開コピー)ため、テンプレートは"
                            "フォルダ内容のコピーで作成します(ローカル専用ファイルは除外)。")
    reserved = {n.casefold() for n in RESERVED_NAMES} | {n.casefold() for n in top_level}
    if dirname.casefold() in reserved:
        errors.append(f"--dir '{dirname}' はハーネスの予約名/テンプレートのルート名と衝突します。")

    if errors:
        print("中止しました(事前検査NG)。人間が以下を解消してから再実行してください:")
        for e in errors:
            print(f"  NG: {e}")
        return 1

    # ---- 列挙(dry-run/apply共通。ここまで書き込みなし)
    if harness_git:
        template_count = len(run_git(git, "ls-tree", "-r", "--name-only", "HEAD",
                                     cwd=harness).stdout.splitlines())
    else:
        template_count = len(template_files)
    files, excluded = scan_app(app)
    effective, ci_dirs, inert_dirs = scan_ai_assets(files, dirname)
    version = harness_version(harness)
    mode = "apply" if args.apply else "dry-run"

    # ---- 適用
    commit_ok, ignored = None, []
    if args.apply:
        project.mkdir(parents=True, exist_ok=True)
        if harness_git:
            with tempfile.TemporaryDirectory() as td:
                zpath = Path(td) / "harness.zip"
                r = run_git(git, "archive", "--format=zip", "-o", str(zpath), "HEAD", cwd=harness)
                if r.returncode != 0:
                    print(f"ERROR: git archive に失敗しました: {r.stderr.strip()}")
                    return 1
                with zipfile.ZipFile(zpath) as zf:
                    zf.extractall(project)
        else:
            for rel in template_files:
                dst = project / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(harness / rel, dst)
        copy_app(app, project / dirname)
        (project / "README.md").write_text(readme_stub(app.name, dirname),
                                           encoding="utf-8", newline="\n")
        for step in (("init", "-b", "main"), ("add", "-A")):
            r = run_git(git, *step, cwd=project)
            if r.returncode != 0:
                print(f"ERROR: git {step[0]} に失敗しました: {r.stderr.strip()}")
                return 1
        # テンプレートの .gitignore(node_modules/ dist/ build/ 等)に食われたアプリ内
        # ファイルは黙って消える(コミットされない)ため、明示的に報告する
        st = run_git(git, "status", "--porcelain=v1", "-z", "--ignored", cwd=project)
        ignored = [e[3:] for e in st.stdout.split("\0")
                   if e.startswith("!! ") and e[3:].startswith(dirname + "/")]
        r = run_git(git, "commit", "-m",
                    f"ハーネステンプレートに既存アプリ({dirname}/)を取り込んだ初期状態 (tools/intake-app.py)",
                    cwd=project)
        commit_ok = r.returncode == 0
        if not commit_ok:
            warnings.append("git commit に失敗しました。user.name/user.email 設定などを確認し、"
                            f"手動でコミットしてください: {r.stderr.strip()}")

    # ---- レポート
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 既存アプリ取り込みレポート(tools/intake-app.py)",
        "",
        f"- 実行モード: **{mode}** / 日時: {now}",
        f"- テンプレート: `{harness}`(**{version}** まで / {template_count} ファイル / "
        + ("git archive(HEAD)" if harness_git else "フォルダコピー(非git本体)") + ")",
        f"- アプリ元: `{app}` → 配置先: `{project}` の `{dirname}/`(内部構成そのまま・無仕分け)",
        f"- アプリ: {len(files)} ファイル(除外 {len(excluded)} 件: "
        + (", ".join(f"`{e}`" for e in excluded[:10])
           + (f" 他{len(excluded) - 10}件" if len(excluded) > 10 else "") if excluded else "なし")
        + ")",
        "- アプリ側ファイルは無改変(検出したAI設定資産の棚卸しは /11-brownfield-intake が行う)。",
        "",
    ]
    if warnings:
        lines += ["## 警告"] + [f"- {w}" for w in warnings] + [""]
    lines.append("## AI設定資産の検出")
    lines.append("### 実効性あり(要棚卸し: Claude Code が配下の作業時に自動で読み込む)")
    lines += [f"- `{p}` — 知識を回収したうえで `.pre-harness` へのリネームをユーザー承認つきで提案する"
              for p in effective] or ["- なし"]
    lines.append("### 旧CI(ルートの .github/workflows/ でないため実行されない)")
    lines += [f"- `{d}`({n} ファイル)— 活かすならルート移設を改修候補リストへ(取り込み時には判断しない)"
              for d, n in ci_dirs.items()] or ["- なし"]
    lines.append("### 不活性(ルート起点の探索対象外。現状維持)")
    lines += [f"- `{d}`({n} ファイル)" for d, n in inert_dirs.items()] or ["- なし"]
    lines.append("")
    if args.apply:
        lines.append("## テンプレートの .gitignore により無視されたアプリ内ファイル")
        lines += ([f"- `{p}`" for p in ignored[:20]]
                  + ([f"- 他{len(ignored) - 20}件"] if len(ignored) > 20 else [])
                  or ["- なし"])
        lines += ["", "必要なファイルが無視されている場合はルートの `.gitignore` を調整して再コミットする。", ""]
    lines.append("## 次の手順")
    if not args.apply:
        lines.append("1. 内容に問題がなければ `--apply` を付けて再実行する。")
    else:
        lines += [
            f"1. `{project}` を VS Code / Claude Code で開き、**新しいセッション**で "
            "`/11-brownfield-intake` を実行する(本レポートが入力になる)。",
            "2. その中で「AI設定資産の検出」の実効性あり一覧を棚卸しする(知識回収 → 無効化提案は人が承認)。",
            "3. as-is 文書のレビュー後、GATE_STATUS の初期化を承認する。",
            "4. リモートに置く場合: `gh repo create <名前> --private --source . --push` 等"
            "(push は確認つきの操作)。",
        ]
    lines.append("")
    report = "\n".join(lines)
    print(report)
    if args.apply:
        report_path = project / "docs/00-overview/intake-report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8", newline="\n")
        print(f"(レポートを {report_path} に書き出しました)")
        write_origin(project, harness, version)  # 以後の /91 で本体パスを省略できる
        if commit_ok:
            # レポート自体もプロジェクトの記録としてコミットに含める
            run_git(git, "add", "-A", cwd=project)
            run_git(git, "commit", "--amend", "--no-edit", cwd=project)
        else:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

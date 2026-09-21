#!/usr/bin/env python3
"""ハーネス本体 → 個別プロジェクトの逆同期ツール。

本体リポジトリで適用された改善(DECISIONS.md の D 番号)を、テンプレートコピーで
作られた個別プロジェクトのハーネスコピーへ反映する。generate-adapters.py と同じく
決定的・冪等で、既定は dry-run(差分レポートのみ)。書き込みは --apply を付けた時だけ。

使い方(どちらの側からでも実行できる。方向は常に 本体 → プロジェクト):
  本体リポジトリで:       python tools/sync-harness.py --project <対象リポジトリ> [--apply]
      対象の状態から実行モードを自動判定する(呼び出し側はモードを選ばない):
      - 適用済み(progress.md あり) → 逆同期(更新+欠けているファイルの追加)
      - 未適用                     → 初回注入(brownfield 経路B。新規追加のみ行い、
        既存ファイルは一切上書きしない。衝突はレポートに列挙され /11 の棚卸しで処理。
        適用後は docs/00-overview/intake-report.md を生成して /11 へ接続する)
  プロジェクト側で:       python tools/sync-harness.py --harness <本体のパス> [--apply]
  プロジェクト側(2回目以降): python tools/sync-harness.py [--apply]
      (--harness 省略時は docs/00-overview/harness-origin.md に自動記録された
       前回の本体パスを使う。--apply のたびに自動更新される)
  自己テスト:             python tools/sync-harness.py --selftest
      (一時ディレクトリの合成フィクスチャで本体/プロジェクト判定・配布除外・
       入れ子拒否の回帰を検証する。実リポジトリには書き込まない)
  照合:                   python tools/sync-harness.py [--project <対象> | --harness <本体>] --verify
      (harness-origin.md の source_commit / archive_sha256 と本体の HEAD を照合する。
       0 = 一致 / 1 = 不一致 / 2 = 照合不能)

設計方針:
- マニフェスト方式: ハーネス所有ファイル(下の SYNC_GLOBS)だけを対象にする。
  プロジェクト固有物(docs/ の実体・requirements/・プロジェクトが新設したスキル等)には
  一切触れない。削除も行わない(本体で消えたファイルの掃除は手動。レポートに注記)。
- 混在ファイル(deploy-* スキルのように汎用テンプレ + プロジェクト固有値表を持つもの、
  プロジェクトが書き換えてよい README 等)は「要レビュー」とし、自動では上書きしない
  (プロジェクト側に存在しない場合の新規追加だけは安全なので行う)。
- コピーはバイト単位(shutil.copyfile)。.ps1 の BOM・.sh の LF を壊さない。
- レポートはプロジェクト側 docs/00-overview/harness-sync-report.md に書く。
  execute 権限の無いエージェント(orchestrator)でもレポートを読んで続きを進められる。
- 配布元の同一性(codex 監査 2026-08-31 H-07 / IA-20260831-09、再監査 2026-08-31 A7-M-1 / RG-14):
  harness-origin.md に本体の HEAD(source_commit。未コミットの変更があれば -dirty)・origin URL(source_url)と
  `git archive --format=tar HEAD` の SHA-256(archive_sha256)・synced_at・latest_decision・route を記録し、
  未コミットの本体からの --apply は既定で拒否(--allow-dirty で明示)、未追跡ファイル(git ls-files に
  無い)は配布しない。--verify で記録と本体を照合する。署名・attestation は未実装(設計のみ)。
"""
import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ハーネス所有(自動同期)の対象。ここに無いパスには絶対に触れない。
# 注: .github/workflows/(harness-ci.yml)は意図して対象外。CI 定義はプロジェクトが
# 独自ワークフローを追加して所有する領域のため自動同期しない(初回は経路Aの
# git archive で配布される。本体側 harness-ci.yml の更新の取り込みは手動)。
SYNC_GLOBS = [
    ".github/agents/**/*",
    ".github/harness/**/*",
    ".github/copilot/**/*",  # Copilot CLI 設定(model-policy.yml からの生成物。settings.local.json は除外)
    ".github/hooks/**/*",
    ".github/instructions/**/*",
    ".github/prompts/**/*",
    ".github/skills/**/*",
    ".claude/**/*",
    ".agents/**/*",
    ".vscode/settings.json",
    "tools/**/*",
    "docs/**/*_template.md",
    "docs/**/README.md",  # docs/ 配下のフォルダ説明README(ハーネス所有)
    "AGENTS.md",
    "CLAUDE.md",
    "plugin.json",
    ".claude-plugin/**/*",  # Claude Code plugin マニフェスト(plugin.json からの生成物。tools/plugin_manifests.py)
    # DECISIONS.md は本体の開発記録で、プロジェクトへは配布しない(D049。あると受付
    # ルーチンが本体と誤認する)。大文字小文字違いの二重掲載は Windows で同一ファイルを
    # 2件収集するため、PRテンプレートは実ファイル名(小文字)のみを載せる。
    ".gitattributes",
    ".gitignore",
    ".github/pull_request_template.md",
    "README.md",
    "LICENSE",  # 要レビュー扱い(下の REVIEW_FILES 参照。無条件上書きしない)
    ".github/CODEOWNERS",
]
# 【規則】ルート直下のプロジェクト所有になり得るファイル(README / LICENSE /
# CHANGELOG 類)を SYNC_GLOBS に足すときは、必ず REVIEW_FILES とセットで登録する。
# SYNC_GLOBS 単独だと無条件上書きになり、プロジェクトが差し替えた内容を黙って壊す。
# 両側に存在して差分がある場合、自動上書きせず手動マージに回すもの
REVIEW_PREFIXES = (".github/skills/deploy-",)  # 固有値表をプロジェクトが埋める
# README.md はプロジェクトでアプリのREADMEに置き換わる(ハーネス文書は .github/harness/ が正)
# .gitignore はプロジェクトが自分の除外を追記しうるため、新規追加のみ行い既存は衝突扱い
# .claude/settings.json はプロジェクト側で opt-in 配線(watchdog 等)を足すファイルのため
# 無言上書き禁止(第2回監査で配線消失を実測)。新規は追加・既存との差分は衝突として提示する
# LICENSE はプロジェクト所有になり得るルートファイル(独自ライセンスの可能性)。
# 第3回監査で無言上書きを実測したため、新規のみ自動追加・既存は衝突として提示する
# .github/harness/model-policy.yml はプロジェクト固有の plan_fallbacks / session pin を持ちうるため要レビュー
# (取り込み後は python tools/generate-adapters.py で鏡を再生成する。監査 2026-09-09 §4.5)
REVIEW_FILES = {"README.md", ".github/CODEOWNERS", ".gitignore",
                ".claude/settings.json", "LICENSE",
                ".github/harness/model-policy.yml"}  # プロジェクトが書き換えうる
# 常に対象外
EXCLUDE_PREFIXES = (
    ".github/hooks/logs/",      # 実行時ログ(gitignore対象)
    ".claude/skills/.system/",  # ローカル生成物(intake-app.py の TEMPLATE_EXCLUDE_REL_DIRS と同一に保つ)
    "evaluation/",              # 測定タスク・結果(ハーネス本体専用。配布しない)
)
EXCLUDE_FILES = {".claude/settings.local.json",
                 ".github/copilot/settings.local.json",  # Copilot CLI のローカル上書き(gitignore 対象)
                 ".claude/settings.json.locked",  # 保守モード中の退避(ローカル一時ファイル)
                 "tools/e2e-run.py",              # ハーネス本体の測定装置(配布しない。intake側と同一に保つ)
                 "tools/eval-report.py",          # 同(評価集計・REPORT 生成。三重台帳: .gitattributes / intake-app.py と同一)
                 "tools/gen-docs.py"}             # 本体文書の生成器(生成対象がプロジェクトに無いため配布しない)
EXCLUDE_PARTS = {"__pycache__", "node_modules"}
# 本体で置き場を移した生成物の旧パス(配布先に残ると Copilot CLI が .github/hooks/**/*.json をフック設定と誤認し
# fail-closed になる。2026-09-21 実測、D097)。このツールは削除しないのが原則だが、この一覧だけは --apply で消す
# (本体側に同じパスが無いことを確認したうえで)。値は移動先(レポート用)。
STALE_GENERATED = {".github/hooks/plugin-hooks.json": ".claude-plugin/hooks.json",
                   ".github/hooks/scripts/_privacy-patterns.json": ".github/harness/privacy-patterns.json"}


ORIGIN_REL = "docs/00-overview/harness-origin.md"


# この判定は intake-app.py / sync-harness.py で同一に保つこと(片方だけ変えると
# 経路A(取り込み)と経路B(逆同期)で本体/プロジェクトの判定がずれる)。
def looks_like_harness(root: Path) -> bool:
    """本体= .github/harness/USAGE.md があり progress.md 実体が無い(プロジェクトを本体と誤認しない)。

    DECISIONS.md は D049 の export-ignore により git archive / GitHub ZIP 由来の
    本体コピーに含まれないため、判定には使わない(有無を問わない)。
    intake 直後のプロジェクトは progress.md がまだ無い(/11 実行前)ため、
    intake-report.md の存在もプロジェクトの証拠として使う(/11 未完了プロジェクトへの
    再同期を本体と誤認して拒否しない)。
    """
    return ((root / ".github/harness/USAGE.md").is_file()
            and not (root / "docs/00-overview/progress.md").exists()
            and not (root / "docs/00-overview/intake-report.md").exists())


def looks_like_project(root: Path) -> bool:
    return (root / "docs/00-overview/progress.md").is_file()


def read_origin(project: Path):
    """harness-origin.md に記録された前回の本体パスを返す(無ければ None)。"""
    p = project / ORIGIN_REL
    if not p.is_file():
        return None
    m = re.search(r"^path:\s*(.+)$", p.read_text(encoding="utf-8", errors="replace"), re.M)
    return Path(m.group(1).strip()) if m else None


def write_origin(project: Path, harness: Path, version: str, info: dict = None, route: str = "sync") -> None:
    """本体パスと配布鮮度・配布元の同一性をプロジェクトに自動記録する(次回の /91 で --harness を省略できる)。

    書式は intake-app.py / tools/doctor.py --write-origin と同一に保つ(読み手: sync の read_origin=path、
    inject-progress.sh/.ps1 と doctor の distribution 検査=path + latest_decision、受領書=source_commit、
    --verify=source_commit + archive_sha256)。source_url / source_commit / archive_sha256 / synced_at /
    latest_decision / route は 2026-09-10(A6-15 / RD-4)の共通フィールド名。sync 経路は本体の
    `git archive --format=tar HEAD` の SHA-256 を archive_sha256 に記録する(codex 監査 2026-08-31 H-07 /
    IA-20260831-09。git 不在・非リポジトリは unknown / n/a=intake / doctor と同じ表記)。未コミットの変更を
    含む本体からの同期は source_commit に -dirty を付ける(--allow-dirty)。
    """
    info = info or {}
    p = project / ORIGIN_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().astimezone()
    today = now.date().isoformat()
    commit = info.get("source_commit") or "unknown"
    if commit != "unknown" and info.get("dirty"):
        commit += "-dirty"
    url = info.get("source_url") or "unknown"
    archive = info.get("archive_sha256") or "n/a"
    latest = latest_decision(harness) or version
    synced_at = now.strftime("%Y-%m-%dT%H:%M:%S%z")
    p.write_text(f"""<!-- HARNESS_ORIGIN
path: {harness.as_posix()}
version: {version}
synced: {today}
source_url: {url}
source_commit: {commit}
archive_sha256: {archive}
synced_at: {synced_at}
latest_decision: {latest}
route: {route}
-->

# ハーネス本体の場所と配布鮮度(自動記録)

このプロジェクトのハーネスは `{harness.as_posix()}` からコピー/同期された({version} まで・{today}・経路 {route})。
「ハーネスを更新して」の依頼(/91-sync-from-harness)ではこのパスが既定の本体として使われ、
SessionStart フック(inject-progress)と `python tools/doctor.py` は `latest_decision` と本体の
DECISIONS.md の最新 D 番号の差で「/91 を先に実行」を案内する。`tools/sync-harness.py --apply` の
たびに自動更新されるため、手で編集しない(本体を移動した場合は次回 `--harness` で明示するか
`python tools/doctor.py --write-origin --harness <本体パス> --force` で書き直す)。

配布元の同一性: `source_commit` は本体の HEAD(`-dirty` は未コミットの変更を含む作業ツリーからの
同期)、`archive_sha256` は本体の `git archive --format=tar HEAD` の SHA-256、`latest_decision` は
本体 DECISIONS.md の最大 D 番号。`python tools/sync-harness.py --verify` で本体の現在の HEAD と
照合できる(0 = 一致 / 1 = 不一致 / 2 = 照合不能)。
""", encoding="utf-8", newline="\n")


def _git(harness: Path, *args: str):
    """本体で git を実行し stdout(bytes)を返す。git 不在・非リポジトリ・失敗は None(fail-open)。"""
    try:
        r = subprocess.run(["git", "-C", str(harness), *args], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def harness_git_info(harness: Path) -> dict:
    """配布元の同一性(codex 監査 2026-08-31 H-07 / IA-20260831-09)。
    source_commit: HEAD の SHA / source_url: origin の URL / dirty: 追跡ファイルに未コミットの変更があるか /
    archive_sha256: `git archive --format=tar HEAD` の SHA-256(export-ignore 適用後の配布物の同一性。
    同じ commit・同じ git 版なら再現する) / tracked: `git ls-files` の集合(未追跡ファイルを配布しない)。
    git が無い・リポジトリでない場合はすべて None(記録は unknown / n/a、--verify は照合不能)。"""
    info = {"source_commit": None, "source_url": None, "dirty": None, "archive_sha256": None, "tracked": None}
    head = _git(harness, "rev-parse", "HEAD")
    if head is None:
        return info
    info["source_commit"] = head.decode("ascii", "replace").strip()
    url = _git(harness, "remote", "get-url", "origin")
    if url is not None and url.strip():
        info["source_url"] = url.decode("utf-8", "replace").strip()
    st = _git(harness, "status", "--porcelain", "--untracked-files=no")
    info["dirty"] = bool(st.strip()) if st is not None else None
    tar = _git(harness, "archive", "--format=tar", "HEAD")
    if tar is not None:
        info["archive_sha256"] = hashlib.sha256(tar).hexdigest()
    ls = _git(harness, "ls-files", "-z")
    if ls is not None:
        info["tracked"] = {x.decode("utf-8", "replace") for x in ls.split(b"\0") if x}
    return info


def latest_decision(harness: Path):
    """本体 DECISIONS.md の最大 D 番号(D0NN 形式)。無ければ None。"""
    decisions = harness / "DECISIONS.md"
    if not decisions.is_file():
        return None
    nums = [int(m) for m in re.findall(r"^## D(\d+)",
                                       decisions.read_text(encoding="utf-8", errors="replace"), re.M)]
    return f"D{max(nums):03d}" if nums else None


def read_origin_fields(project: Path) -> dict:
    """harness-origin.md の HARNESS_ORIGIN ブロック(<!-- … -->)の key: value を辞書で返す(無ければ空)。"""
    p = project / ORIGIN_REL
    if not p.is_file():
        return {}
    head = p.read_text(encoding="utf-8", errors="replace").split("-->", 1)[0]
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^([a-z_]+):\s*(.*)$", head, re.M)}


def verify_origin(project: Path, harness: Path, info: dict) -> int:
    """harness-origin.md の記録(source_commit / archive_sha256)と本体の現在の HEAD を照合する。
    戻り値: 0 = 一致 / 1 = 不一致(本体が進んでいる・記録が dirty な作業ツリー由来・別の本体) / 2 = 照合不能。"""
    rec = read_origin_fields(project)
    rec_commit = rec.get("source_commit") or ""
    rec_arch = rec.get("archive_sha256") or ""
    cur_commit = info.get("source_commit") or ""
    cur_arch = info.get("archive_sha256") or ""
    cur_label = (cur_commit + ("-dirty" if info.get("dirty") else "")) if cur_commit else "(git なし)"
    print(f"VERIFY: 記録(harness-origin.md) source_commit={rec_commit or '(なし)'} archive_sha256={rec_arch or '(なし)'}")
    print(f"VERIFY: 本体({harness}) source_commit={cur_label} archive_sha256={cur_arch or '(git なし)'}")
    if not rec_commit or rec_commit in ("null", "unknown") or not cur_commit:
        print("VERIFY: 照合不能(記録が無い・旧形式(2026-09-14 以前の同期)・本体で git が使えない)。"
              "--apply で記録を作り直してください。")
        return 2
    same = (rec_commit == cur_commit and not info.get("dirty")
            and (not rec_arch or rec_arch in ("null", "n/a") or not cur_arch or rec_arch == cur_arch))
    if same:
        print("VERIFY: 一致(このプロジェクトのハーネスは本体の HEAD と同じ配布物から同期されている)")
        return 0
    if rec_commit.endswith("-dirty"):
        print("VERIFY: 不一致(記録が未コミットの作業ツリー由来。本体をコミットして /91 で同期し直す)")
    elif info.get("dirty"):
        print("VERIFY: 不一致(本体に未コミットの変更がある)")
    else:
        print("VERIFY: 不一致(本体が進んでいる、または記録と別の本体。/91-sync-from-harness で同期する)")
    return 1


def harness_version(harness: Path) -> str:
    """本体のバージョン表記。DECISIONS.md の最大D番号 → plugin.json の version → unknown。

    ZIP展開コピーの本体には D049 の export-ignore で DECISIONS.md が無いため、
    plugin.json の version へ順にフォールバックする(intake-app.py と同一に保つ)。
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


def is_excluded(rel: str) -> bool:
    if rel in EXCLUDE_FILES:
        return True
    if any(rel.startswith(p) for p in EXCLUDE_PREFIXES):
        return True
    return any(part in EXCLUDE_PARTS for part in rel.split("/"))


def is_review(rel: str) -> bool:
    return rel in REVIEW_FILES or any(rel.startswith(p) for p in REVIEW_PREFIXES)


def normalized(data: bytes) -> bytes:
    """比較用にEOLを正規化する(.gitattributesの * text=auto と同じ意味論)。

    Windowsのworking treeはCRLF・LFが混在しうるため、バイト一致で比較すると
    改行コード差だけの「偽の更新」が大量に出る。バイナリはそのまま比較する。
    """
    if b"\0" in data:
        return data
    return data.replace(b"\r\n", b"\n")


def collect(harness: Path) -> list:
    rels = set()
    for pattern in SYNC_GLOBS:
        for p in harness.glob(pattern):
            if p.is_file():
                rel = p.relative_to(harness).as_posix()
                if not is_excluded(rel):
                    rels.add(rel)
    return sorted(rels)


# ---------------------------------------------------------------- selftest

# 配布除外の三重台帳(tools/ 配下): .gitattributes の export-ignore / sync-harness.py の
# EXCLUDE_FILES / intake-app.py の TEMPLATE_EXCLUDE_REL は同じ集合でなければならない
# (1 か所でも書き忘れると測定装置がプロジェクトへ配布される。監査 2026-09-09 の設計レビュー)。
# 既知の例外は無し(2026-09-10 A7-H-9/RG-10 の解消: tools/gen-docs.py を .gitattributes の export-ignore にも
# 載せ、三重台帳を完全一致にした。validate-harness.py は DECISIONS.md の無い配布先では gen-docs 検査を
# 対象外にする)。例外を足すときは intake-app.py と同一に保つ。
LEDGER_KNOWN_EXCEPTIONS = set()
LEDGER_APPARATUS = ("tools/e2e-run.py", "tools/eval-report.py")


def distribution_ledgers(root: Path) -> dict:
    """三重台帳の tools/ 配下エントリを読む(他ツールは import せず正規表現で定数を読む)。"""
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


def _make_harness_fixture(root: Path, with_decisions: bool = True) -> None:
    """looks_like_harness を満たす最小の合成本体(同期対象ファイル数点つき)を作る。"""
    (root / ".github/harness").mkdir(parents=True)
    (root / ".github/harness/USAGE.md").write_text("# USAGE(フィクスチャ)\n",
                                                   encoding="utf-8", newline="\n")
    (root / "tools").mkdir()
    # 自分自身のコピーを置き、フィクスチャを「本体」としてサブプロセス実行できるようにする
    shutil.copyfile(Path(__file__).resolve(), root / "tools/sync-harness.py")
    (root / "AGENTS.md").write_text("# AGENTS(フィクスチャ)\n", encoding="utf-8", newline="\n")
    (root / "LICENSE").write_text("Harness License(フィクスチャ)\n",
                                  encoding="utf-8", newline="\n")
    # ハーネス本体専用のメタ文書(配布対象外。SYNC_GLOBS に無いことの回帰検査用)
    for meta in ("CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "README.en.md"):
        (root / meta).write_text(f"# {meta}(フィクスチャ)\n", encoding="utf-8", newline="\n")
    (root / ".claude").mkdir()
    (root / ".claude/settings.json").write_text('{"hooks": "harness"}\n',
                                                encoding="utf-8", newline="\n")
    if with_decisions:
        (root / "DECISIONS.md").write_text("## D001: フィクスチャ\n", encoding="utf-8", newline="\n")


def selftest() -> int:
    failures = []

    def expect(cond, label):
        (failures.append(label) if not cond else None)
        print(("PASS: " if cond else "FAIL: ") + label)

    def run_tool(harness: Path, *args):
        return subprocess.run([sys.executable, str(harness / "tools/sync-harness.py"), *args],
                              capture_output=True, encoding="utf-8", errors="replace")

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        # (a) looks_like_harness の4象限(intake-app.py と同一判定。片側だけの変更を検出する)
        clone = base / "clone"
        _make_harness_fixture(clone, with_decisions=True)
        zipcopy = base / "zipcopy"
        _make_harness_fixture(zipcopy, with_decisions=False)
        applied = base / "applied"
        _make_harness_fixture(applied)
        (applied / "docs/00-overview").mkdir(parents=True)
        (applied / "docs/00-overview/progress.md").write_text("# progress\n", encoding="utf-8")
        intaken = base / "intaken"
        _make_harness_fixture(intaken)
        (intaken / "docs/00-overview").mkdir(parents=True)
        (intaken / "docs/00-overview/intake-report.md").write_text("# intake\n", encoding="utf-8")
        expect(looks_like_harness(clone), "(a) 本体clone(DECISIONS.md あり) -> 本体と判定")
        expect(looks_like_harness(zipcopy), "(a) 本体ZIPコピー(DECISIONS.md なし) -> 本体と判定")
        expect(not looks_like_harness(applied), "(a) progress.md ありのプロジェクト -> 本体と判定しない")
        expect(not looks_like_harness(intaken), "(a) intake直後(intake-report.md あり) -> 本体と判定しない")

        # (b)(c) 逆同期の実走行: DECISIONS.md を配布せず、既存 settings.json を上書きしない
        proj = base / "proj"
        (proj / "docs/00-overview").mkdir(parents=True)
        (proj / "docs/00-overview/progress.md").write_text("# progress\n", encoding="utf-8")
        (proj / ".claude").mkdir()
        (proj / ".claude/settings.json").write_text('{"hooks": "project-custom"}\n',
                                                    encoding="utf-8", newline="\n")
        (proj / "LICENSE").write_text("Project Own License\n", encoding="utf-8", newline="\n")
        (proj / ".github/hooks").mkdir(parents=True)
        (proj / ".github/hooks/plugin-hooks.json").write_text("{}\n", encoding="utf-8", newline="\n")  # 旧生成物(D097)
        r = run_tool(clone, "--project", str(proj), "--apply")
        expect(r.returncode == 0, f"逆同期 --apply が成功する(exit={r.returncode})")
        expect(not (proj / "DECISIONS.md").exists(), "(b) sync が DECISIONS.md を配布しない")
        expect(not any((proj / m).exists()
                       for m in ("CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "README.en.md")),
               "(b) sync がハーネス専用メタ4ファイルを配布しない")
        expect((proj / ".claude/settings.json").read_text(encoding="utf-8")
               == '{"hooks": "project-custom"}\n',
               "(c) 既存の .claude/settings.json を自動上書きしない(衝突として保持)")
        expect((proj / "LICENSE").read_text(encoding="utf-8") == "Project Own License\n",
               "(c) 既存の LICENSE を自動上書きしない(第3回監査で実測した無言上書きの回帰)")
        expect((proj / "AGENTS.md").is_file(), "同期対象(AGENTS.md)は配布される")
        expect(not (proj / ".github/hooks/plugin-hooks.json").exists(),
               "(i) 旧生成物 .github/hooks/plugin-hooks.json は --apply で掃除される(STALE_GENERATED。D097)")
        _origin = (proj / "docs/00-overview/harness-origin.md").read_text(encoding="utf-8")
        expect("\npath: " in _origin and "\nlatest_decision: D001\n" in _origin
               and "\nsource_commit: " in _origin and "\nsynced_at: " in _origin and "\nroute: sync\n" in _origin,
               "(b) harness-origin.md に path / latest_decision / source_commit / synced_at / route を記録する")
        expect(not (proj / "tools/gen-docs.py").exists(), "(b) sync が本体専用の tools/gen-docs.py を配布しない(A7-H-9)")

        # (c) 新規追加は要レビュー対象でも安全に行う(プロジェクト側に無い場合のみ)
        proj2 = base / "proj2"
        (proj2 / "docs/00-overview").mkdir(parents=True)
        (proj2 / "docs/00-overview/progress.md").write_text("# progress\n", encoding="utf-8")
        r = run_tool(clone, "--project", str(proj2), "--apply")
        expect(r.returncode == 0 and (proj2 / ".claude/settings.json").is_file(),
               "(c) プロジェクト側に無い settings.json は新規追加される")
        expect((proj2 / "LICENSE").is_file(),
               "(c) プロジェクト側に無い LICENSE は新規追加される")

        # (d) 入れ子 --project の拒否(本体配下への注入は本体を汚す)
        r = run_tool(clone, "--project", str(clone / "sub"))
        expect(r.returncode != 0, "(d) 本体配下への --project(入れ子)を拒否する")

        # (h) 配布元の同一性(codex 監査 2026-08-31 H-07 / IA-20260831-09、A7-M-1): 非 git の本体では
        #     source_commit は unknown・archive_sha256 は n/a で同期は成功する。git の本体では 40 桁 SHA と 64 桁の
        #     archive_sha256 を記録し、未追跡ファイルを配布せず、dirty な本体からの --apply は既定拒否、
        #     --verify は一致 0 / 本体が進めば 1 を返す(git が無い環境では git 側のケースを SKIP)
        origin_text = (proj / ORIGIN_REL).read_text(encoding="utf-8")
        expect(re.search(r"^source_commit: unknown$", origin_text, re.M) is not None
               and re.search(r"^archive_sha256: n/a$", origin_text, re.M) is not None
               and re.search(r"^synced_at: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{4})?$", origin_text, re.M) is not None
               and re.search(r"^latest_decision: D001$", origin_text, re.M) is not None
               and re.search(r"^route: sync$", origin_text, re.M) is not None,
               "(h) 非 git の本体からの同期は source_commit を unknown・archive_sha256 を n/a とし、synced_at/latest_decision/route を記録する")
        gitrepo = base / "gitrepo"
        _make_harness_fixture(gitrepo, with_decisions=True)
        gitbin = shutil.which("git")
        ginfo = None
        if gitbin:
            def fx_git(*a):
                return subprocess.run([gitbin, "-C", str(gitrepo), "-c", "user.name=selftest",
                                       "-c", "user.email=selftest@example.invalid", *a],
                                      capture_output=True, encoding="utf-8", errors="replace")
            if (fx_git("init", "-q").returncode == 0 and fx_git("add", "-A").returncode == 0
                    and fx_git("commit", "-q", "-m", "fixture").returncode == 0):
                ginfo = harness_git_info(gitrepo)
        if ginfo and ginfo["source_commit"]:
            proj3 = base / "proj3"
            (proj3 / "docs/00-overview").mkdir(parents=True)
            (proj3 / "docs/00-overview/progress.md").write_text("# progress\n", encoding="utf-8")
            (gitrepo / ".github/harness/UNTRACKED-note.md").write_text("x\n", encoding="utf-8", newline="\n")
            r = run_tool(gitrepo, "--project", str(proj3), "--apply")
            ot = (proj3 / ORIGIN_REL).read_text(encoding="utf-8") if (proj3 / ORIGIN_REL).is_file() else ""
            expect(r.returncode == 0
                   and re.search(r"^source_commit: [0-9a-f]{40}$", ot, re.M) is not None
                   and re.search(r"^archive_sha256: [0-9a-f]{64}$", ot, re.M) is not None,
                   f"(h) git の本体からの同期は source_commit(40 桁)と archive_sha256(64 桁)を記録する(exit={r.returncode})")
            expect(not (proj3 / ".github/harness/UNTRACKED-note.md").exists() and "UNTRACKED-note.md" in r.stdout,
                   "(h) 未追跡ファイルは配布せずレポートに列挙する(git ls-files 照合。A7-M-1 / RG-14)")
            r = run_tool(gitrepo, "--project", str(proj3), "--verify")
            expect(r.returncode == 0, f"(h) --verify: 記録と本体 HEAD が一致 -> exit 0(exit={r.returncode})")
            (gitrepo / "AGENTS.md").write_text("# AGENTS(変更)\n", encoding="utf-8", newline="\n")
            r = run_tool(gitrepo, "--project", str(proj3), "--apply")
            expect(r.returncode != 0 and "dirty" in r.stdout,
                   "(h) 未コミットの変更がある本体からの --apply を既定で拒否する(dirty source)")
            r = run_tool(gitrepo, "--project", str(proj3), "--apply", "--allow-dirty")
            ot = (proj3 / ORIGIN_REL).read_text(encoding="utf-8")
            expect(r.returncode == 0 and re.search(r"^source_commit: [0-9a-f]{40}-dirty$", ot, re.M) is not None,
                   "(h) --allow-dirty で同期でき、記録の source_commit は -dirty 付きになる")
            fx_git("commit", "-q", "-am", "change")
            r = run_tool(gitrepo, "--project", str(proj3), "--verify")
            expect(r.returncode == 1, f"(h) --verify: 本体が進んだ(記録と不一致) -> exit 1(exit={r.returncode})")
        else:
            print("SKIP: (h) git fixture cases (git not available or init failed)")

    # (g) 配布除外の三重台帳(.gitattributes / sync EXCLUDE_FILES / intake TEMPLATE_EXCLUDE_REL)の三者一致
    ledgers = distribution_ledgers(Path(__file__).resolve().parents[1])
    problems = ledger_mismatches(ledgers)
    expect(not problems, "(g) 配布除外の三重台帳(tools/)が三者一致"
           + ("" if not problems else ": " + " / ".join(problems)))
    expect(ledger_mismatches({"gitattributes": {"tools/e2e-run.py"},
                              "sync": {"tools/e2e-run.py", "tools/eval-report.py"},
                              "intake": {"tools/e2e-run.py", "tools/eval-report.py"}}) != [],
           "(g) 1 台帳だけの書き忘れを検出する")

    print()
    print(f"sync-harness selftest: {'FAIL ' + str(len(failures)) if failures else 'all passed'}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ハーネス本体からプロジェクトへの逆同期・初回注入")
    ap.add_argument("--project", help="プロジェクトリポジトリのパス(本体側から実行する場合)")
    ap.add_argument("--harness", help="本体リポジトリのパス(プロジェクト側から実行する場合。"
                                      "省略時は harness-origin.md の記録を使う)")
    ap.add_argument("--init", action="store_true",
                    help="(通常は不要)初回注入モードの明示ヒント。実行モードは対象の状態から"
                         "自動判定されるため、付けても付けなくても正しいモードで動く")
    ap.add_argument("--apply", action="store_true", help="実際に書き込む(省略時はdry-run)")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="本体に未コミットの変更があっても --apply を許す(記録の source_commit は -dirty 付き)")
    ap.add_argument("--verify", action="store_true",
                    help="harness-origin.md の source_commit / archive_sha256 と本体の HEAD を照合して終了する"
                         "(0 = 一致 / 1 = 不一致 / 2 = 照合不能)")
    ap.add_argument("--selftest", action="store_true",
                    help="合成フィクスチャで自己テストを実行して終了する(実リポジトリには書き込まない)")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if args.selftest:
        return selftest()

    own = Path(__file__).resolve().parents[1]
    if args.init and not args.project:
        ap.error("--init は本体リポジトリ側から --project <対象リポジトリ> と共に使ってください")
        return 2
    if args.project and args.harness:
        ap.error("--project と --harness は同時に指定できません")
        return 2
    if args.project:
        harness, project = own, Path(args.project).resolve()
    elif args.harness:
        harness, project = Path(args.harness).resolve(), own
    else:
        # プロジェクト側で引数なし: harness-origin.md の自動記録から本体パスを補完する
        origin = read_origin(own)
        if origin is None:
            ap.error("--project / --harness のどちらかを指定してください"
                     "(docs/00-overview/harness-origin.md に前回の本体パスの記録がありません)")
            return 2
        harness, project = origin.resolve(), own
        print(f"(--harness 省略: harness-origin.md の記録 `{harness}` を使用)")

    # 本体とプロジェクトの入れ子は方向を問わず拒否する(intake-app.py の同種検査と同じ趣旨。
    # 本体サブディレクトリへの注入は本体を汚し、判定ファイルの探索も狂わせる)
    if project == harness or harness in project.parents or project in harness.parents:
        print(f"ERROR: 本体とプロジェクトが同一または入れ子関係です"
              f"(本体: {harness} / プロジェクト: {project})。本体配下への注入・"
              "本体を含むディレクトリへの同期はできません。")
        return 1

    # 保守モード中(ガード解除中)の本体からは配布しない。ガードを外した settings.json を
    # プロジェクトへ同期してしまうため(tools/harness-maintenance.py 参照)
    if (harness / ".claude" / "settings.json.locked").is_file():
        print("ERROR: 保守モード中は同期できません。"
              "python tools/harness-maintenance.py --off --apply で復元してから実行してください。")
        return 1

    if not looks_like_harness(harness):
        print(f"ERROR: {harness} は本体リポジトリに見えません"
              "(.github/harness/USAGE.md があり docs/00-overview/progress.md と"
              " intake-report.md が無い状態が本体。プロジェクトのコピーを本体として"
              "使わないでください)。")
        return 1

    # 実行モードは対象の状態から自動判定する。「注入か更新か」を呼び出し側(人・エージェント)に
    # 選ばせると指示が間違いうるため、判断をツール側に持つ(D045。--init は明示ヒントに格下げ)。
    init_mode = False
    if args.project:  # 本体側から実行
        if not project.is_dir():
            print(f"ERROR: {project} が存在しません(対象は既存リポジトリ。新規作成は intake-app.py)。")
            return 1
        if looks_like_harness(project):
            print(f"ERROR: {project} は本体リポジトリに見えます。本体への注入・同期はできません。")
            return 1
        if looks_like_project(project):
            init_mode = False  # 適用済み → 通常の同期(欠けているファイルの追加も行う)
            if args.init:
                print("(対象は適用済み(progress.md あり)のため、通常の同期として実行します)")
        else:
            init_mode = True   # 未適用(または/11未完了) → 注入(新規追加のみ・上書きなし)
            if not args.init:
                print("(対象はハーネス未適用のため、初回注入モードで実行します:"
                      " 新規追加のみ・既存ファイルは上書きしない)")
    elif not looks_like_project(project):
        if (project / "AGENTS.md").exists():
            hint = ("ハーネスは注入済みに見えます。先に /11-brownfield-intake を完了してください"
                    "(GATE_STATUS 初期化で progress.md が作られ、以後この同期が使えます)。")
        else:
            hint = "初回の注入は本体側から --project <対象リポジトリ> で実行してください(モードは自動判定)。"
        print(f"ERROR: {project} はプロジェクトに見えません"
              f"(docs/00-overview/progress.md が存在するのがプロジェクト)。{hint}")
        return 1

    version = harness_version(harness)
    # 配布元の同一性(codex 監査 2026-08-31 H-07 / IA-20260831-09): HEAD・dirty・archive の SHA-256・追跡集合
    info = harness_git_info(harness)
    if args.verify:
        return verify_origin(project, harness, info)
    if info["dirty"] and args.apply and not args.allow_dirty:
        print("ERROR: 本体に未コミットの変更があります(dirty source)。配布物がどのコミットとも一致しなくなるため"
              "既定では拒否します。コミットしてから実行するか、--allow-dirty で明示してください"
              "(記録の source_commit は -dirty 付きになります)。")
        return 1
    if info["dirty"]:
        print("(警告: 本体に未コミットの変更があります。記録の source_commit は -dirty 付きになります)")
    untracked = []
    added, updated, review, unchanged = [], [], [], 0
    for rel in collect(harness):
        if info["tracked"] is not None and rel not in info["tracked"]:
            untracked.append(rel)  # 未追跡ファイルは配布しない(git ls-files 照合。再監査 RG-14 / A7-M-1)
            continue
        src, dst = harness / rel, project / rel
        src_bytes = src.read_bytes()
        if not dst.exists():
            added.append(rel)  # 新規追加は要レビュー対象でも安全(壊すものが無い)
        elif normalized(dst.read_bytes()) == normalized(src_bytes):
            unchanged += 1  # 改行コード差のみは変更なし扱い(gitのtext=autoと同じ)
        elif is_review(rel) or init_mode:
            # 初回注入では既存ファイルとの衝突を一切上書きしない(既存アプリの
            # CLAUDE.md / .vscode/settings.json 等。処理は /11 の棚卸しに委ねる)
            review.append(rel)
        else:
            updated.append(rel)

    stale = [rel for rel in STALE_GENERATED if (project / rel).is_file() and not (harness / rel).exists()]
    mode = "init+apply" if (init_mode and args.apply) else \
           "init(dry-run)" if init_mode else \
           "apply" if args.apply else "dry-run"
    if args.apply:
        for rel in added + updated:
            dst = project / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(harness / rel, dst)
        for rel in stale:
            (project / rel).unlink()  # 旧生成物の掃除(STALE_GENERATED。D097)
        # 本体パスをプロジェクトへ自動記録(次回から --harness を省略できる)
        write_origin(project, harness, version, info)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    origin_commit = ((info["source_commit"] + ("-dirty" if info["dirty"] else ""))
                     if info["source_commit"] else "unknown")
    review_title = ("衝突(既存ファイルを保持。上書きしない。/11 の棚卸しで処理する)" if init_mode
                    else "要レビュー(自動上書きしない。差分を確認し手動でマージする)")
    review_note = ("  - 既存の CLAUDE.md / AGENTS.md 等は /11-brownfield-intake の"
                   "AI設定資産の棚卸し(知識回収 → .pre-harness リネームをユーザー承認つきで)で処理する。"
                   if init_mode
                   else "  - 混在ファイル(deploy-* の固有値表など)は、汎用部分の変更だけを手で取り込む。")
    lines = [
        "# ハーネス" + ("初回注入" if init_mode else "逆同期") + "レポート",
        "",
        f"- 実行モード: **{mode}** / 日時: {now}",
        f"- 本体: `{harness}`(**{version}** まで) → プロジェクト: `{project}`",
        f"- 配布元の同一性: source_commit `{origin_commit}` / archive_sha256 `{info['archive_sha256'] or 'n/a'}`"
        + ("(git 不在または非リポジトリのため記録できない)" if not info["source_commit"] else "")
        + "(`python tools/sync-harness.py --verify` で照合)",
        f"- 追加: {len(added)} / 更新: {len(updated)} / "
        + ("衝突(保持)" if init_mode else "要レビュー") + f": {len(review)} / 変更なし: {unchanged}",
        "- 旧生成物の掃除: " + (f"{len(stale)}(" + ", ".join(f"`{r}` → `{STALE_GENERATED[r]}`" for r in stale) + ")" if stale else "0")
        + ("(--apply で削除済み)" if (stale and args.apply) else ("(--apply で削除する)" if stale else ""))
        + "。この一覧(本体で置き場を移した生成物)以外は削除しない=本体側で廃止されたファイルの掃除は手動で行う。",
        "",
    ]
    if init_mode and not (project / ".git").is_dir():
        lines += ["> **警告**: 対象は git リポジトリではありません(.git なし)。注入前の状態に"
                  "戻せるよう、先に `git init` + コミットしておくことを推奨します。", ""]
    sections = [
        ("追加" + ("(適用済み)" if args.apply else "(予定)"), added, ""),
        ("更新" + ("(適用済み)" if args.apply else "(予定)"), updated, ""),
        (review_title, review, review_note),
    ]
    if info["tracked"] is not None:
        sections.append(("未追跡のため配布しない(git ls-files 照合。本体でコミットしてから同期する。A7-M-1 / RG-14)",
                         untracked, ""))
    for title, items, note in sections:
        lines.append(f"## {title}")
        lines.extend([f"- `{r}`" for r in items] or ["- なし"])
        if items and note:
            lines.append(note)
        lines.append("")
    if init_mode:
        lines += [
            "## 次の手順",
            ("1. 内容に問題がなければ `--apply` を付けて再実行する。" if not args.apply else
             f"1. `{project}` を開いた**新しいセッション**で `/11-brownfield-intake` を実行する"
             "(手順1.5 の配線確認 → as-is 逆起こし → ゲート初期化。上の衝突一覧が棚卸しの入力になる)。"),
            "",
        ]
    else:
        lines += [
            "## 次の手順",
            ("1. 差分に問題がなければ `--apply` を付けて再実行する。" if not args.apply
             else "1. 検証: `bash .github/hooks/scripts/selftest.sh` と "
                  "`python tools/validate-harness.py` をプロジェクト側で実行し、全PASS/エラー0を確認する。"),
            f"2. `docs/00-overview/progress.md` の申し送りを「ハーネス同期: {version} まで適用済み」に更新し、"
            "還流待ちマーカー・learnings.md の暫定運用行を消す(/91-sync-from-harness の完了処理)。",
            "",
        ]
    report = "\n".join(lines)
    report_path = project / "docs/00-overview/harness-sync-report.md"
    # init の dry-run だけは対象リポジトリに書き込まない(完全に無害な下見)
    report_written = not init_mode or args.apply
    if report_written:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8", newline="\n")
    if init_mode and args.apply:
        # /11-brownfield-intake の入力マーカー。これが「未初期化のプロジェクト」の目印になり、
        # 受付ルーチンが本体リポジトリと誤認しない(注入で .github/harness/USAGE.md も
        # コピーされるため、このマーカーが無いと looks_like_harness の本体判定に合致してしまう)
        intake_path = project / "docs/00-overview/intake-report.md"
        if not intake_path.exists():
            intake_path.write_text(report, encoding="utf-8", newline="\n")

    print(report)
    if report_written:
        print(f"(レポートを {report_path} に書き出しました)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

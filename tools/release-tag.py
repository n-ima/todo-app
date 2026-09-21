#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CHANGELOG の版と annotated git tag の対応を機械照合し、欠けている tag の作成コマンド列を出力する
(A7-M-1。再監査 2026-08-31 §6 記録系・衛生「CHANGELOG 4 版に対応する git tag が 1 つも無い」/ 2026-09-09 OP-5)。

実行はしない: tag の作成と push は外部反映(AGENTS.md「必ず止まる条件」2)であり、guard-dangerous-git が ask にする
操作でもあるため、人がターミナルで実行する。本ツールは「どのコミットに・どの名前で」を一次データ
(CHANGELOG.md の `## [x.y.z] - YYYY-MM-DD` 見出し・plugin.json の version 変更履歴・git tag)から決めて示すだけ。

対象コミットの解決(優先順):
  1. plugin.json の `"version": "x.y.z"` を最初に導入したコミット(`git log --reverse -S`)。CHANGELOG 規約
     「版数は plugin.json の version が正」に従う。
  2. 見つからなければ CHANGELOG の日付の終わりまでで最後のコミット(`git log -1 --until=<date>T23:59:59`)。
     日付だけの近似なので出力に approx と明記する。
  3. どちらも無理(git 不在・履歴が無い)なら unresolved。

tag の状態: ok(annotated tag が解決先を指す)/ missing / mismatch(別コミットを指す)/ lightweight(annotated でない)/ unresolved。
tag 名は `vX.Y.Z`(既存が `X.Y.Z` ならそれも受け付ける)。

使い方(リポジトリルートで):
    python tools/release-tag.py            # 版ごとの状態表と、欠けている tag の git tag -a コマンド列(実行しない)
    python tools/release-tag.py --check    # 表と集計行だけ(tools/validate-harness.py (u) が INFO に転記。tag 無しの WARN 自体は (p))。exit は常に 0
    python tools/release-tag.py --strict   # missing / mismatch / lightweight があれば exit 1
                                           # (CI では使わない: actions/checkout は fetch-tags: true でも depth 1 で履歴が無く、
                                           #  版の導入コミットを解決できない=unresolved になる。完全 clone で実行する)
    python tools/release-tag.py --selftest # 一時 git リポジトリで版→コミット解決と tag 判定の自己テスト(git が無ければ SKIP)
依存: 標準ライブラリ + git
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANGELOG_REL = "CHANGELOG.md"
PLUGIN_REL = "plugin.json"
VERSION_HEAD_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]\s*-\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)


def git_bin():
    return shutil.which("git")


def run_git(root, *args, env=None):
    """(rc, stdout)。git が無い / 起動できないときは (None, '')。"""
    git = git_bin()
    if not git:
        return None, ""
    try:
        cp = subprocess.run([git, "-C", root, *args], capture_output=True, encoding="utf-8", errors="replace",
                            timeout=60, env=env)
        return cp.returncode, cp.stdout
    except Exception:  # noqa: BLE001
        return None, ""


def parse_changelog(text):
    """`## [x.y.z] - YYYY-MM-DD` 見出しを [(version, date)] で返す(記載順=新しい順)。Unreleased は含まない。"""
    return [(m.group(1), m.group(2)) for m in VERSION_HEAD_RE.finditer(text)]


def is_git_repo(root):
    rc, out = run_git(root, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out.strip() == "true"


def is_shallow(root):
    rc, out = run_git(root, "rev-parse", "--is-shallow-repository")
    return rc == 0 and out.strip() == "true"


def existing_tags(root):
    """{tag名: (objecttype, 指すコミット sha)}。annotated tag は peel した先(%(*objectname))を使う。"""
    rc, out = run_git(root, "for-each-ref", "refs/tags",
                      "--format=%(refname:short)%09%(objecttype)%09%(objectname)%09%(*objectname)")
    tags = {}
    if rc != 0:
        return tags
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, otype, sha, peeled = parts[0], parts[1], parts[2], parts[3]
        tags[name] = (otype, peeled or sha)
    return tags


def resolve_version_commit(root, version, date):
    """(sha, method)。method は 'plugin.json' / 'date' / None。"""
    needle = f'"version": "{version}"'
    rc, out = run_git(root, "log", "--reverse", "--format=%H", "-S" + needle, "--", PLUGIN_REL)
    if rc == 0:
        for sha in out.split():
            rc2, blob = run_git(root, "show", f"{sha}:{PLUGIN_REL}")
            if rc2 == 0 and needle in blob:
                return sha, "plugin.json"
    rc, out = run_git(root, "log", "-1", "--format=%H", f"--until={date}T23:59:59")
    if rc == 0 and out.strip():
        return out.strip(), "date"
    return None, None


def status(root):
    """版ごとの状態 [{version, date, sha, method, tag, state}] と環境情報 {git, shallow, tags_total}。"""
    info = {"git": is_git_repo(root), "shallow": False, "tags_total": 0}
    path = os.path.join(root, CHANGELOG_REL)
    if not os.path.isfile(path):
        return [], info
    versions = parse_changelog(open(path, encoding="utf-8-sig").read())
    rows = []
    if not info["git"]:
        return [{"version": v, "date": d, "sha": None, "method": None, "tag": None, "state": "unresolved"}
                for v, d in versions], info
    info["shallow"] = is_shallow(root)
    tags = existing_tags(root)
    info["tags_total"] = len(tags)
    for v, d in versions:
        if info["shallow"]:
            # shallow clone(CI の actions/checkout は depth 1)では境界コミットが全文字列を「導入した」ように見え、
            # `git log -S` が現行版を HEAD に誤帰属する。履歴が無い環境では解決しない(unresolved)。
            rows.append({"version": v, "date": d, "sha": None, "method": None, "tag": None, "state": "unresolved"})
            continue
        sha, method = resolve_version_commit(root, v, d)
        tag_name = next((n for n in (f"v{v}", v) if n in tags), None)
        if sha is None:
            state = "unresolved"
        elif tag_name is None:
            state = "missing"
        else:
            otype, target = tags[tag_name]
            if target != sha:
                state = "mismatch"
            elif otype != "tag":
                state = "lightweight"
            else:
                state = "ok"
        rows.append({"version": v, "date": d, "sha": sha, "method": method, "tag": tag_name, "state": state})
    return rows, info


def commands_for(rows):
    """missing / mismatch / lightweight の版に対する人が実行するコマンド列(実行しない)。"""
    lines = []
    for r in rows:
        if r["state"] == "missing":
            lines.append(f'git tag -a v{r["version"]} {r["sha"]} -m "v{r["version"]} ({r["date"]})"')
        elif r["state"] in ("mismatch", "lightweight"):
            lines.append(f'# {r["tag"]} は {"別コミットを指す" if r["state"] == "mismatch" else "annotated でない"}: '
                         f'確認のうえ git tag -d {r["tag"]} && git tag -a v{r["version"]} {r["sha"]} -m "v{r["version"]} ({r["date"]})"')
    if lines:
        lines.append("git push origin " + " ".join(f"v{r['version']}" for r in rows
                                                     if r["state"] in ("missing", "mismatch", "lightweight"))
                     + "   # 外部反映。ユーザーの承認のうえ人が実行する(AGENTS.md 必ず止まる条件 2)")
    return lines


def summary_line(rows, info):
    c = {k: sum(1 for r in rows if r["state"] == k) for k in ("ok", "missing", "mismatch", "lightweight", "unresolved")}
    fetched = "no(git 不在)" if not info["git"] else ("shallow clone" if info["shallow"] else f"{info['tags_total']} tags")
    return (f"release-tag --check: versions {len(rows)} / ok {c['ok']} / missing {c['missing']} / mismatch {c['mismatch']}"
            f" / lightweight {c['lightweight']} / unresolved {c['unresolved']} (tags: {fetched})")


def render(rows, info, with_commands=True):
    out = ["| version | CHANGELOG date | commit | resolved by | tag | state |", "|---|---|---|---|---|---|"]
    for r in rows:
        sha = (r["sha"] or "")[:12] or "-"
        method = {"plugin.json": "plugin.json version", "date": "date (approx)"}.get(r["method"], "-")
        out.append(f"| {r['version']} | {r['date']} | {sha} | {method} | {r['tag'] or '-'} | {r['state']} |")
    if info["git"] and info["shallow"]:
        out.append("(shallow clone: 履歴と tag が無いので unresolved / missing は環境由来。ローカルの完全 clone で実行する)")
    if with_commands:
        cmds = commands_for(rows)
        if cmds:
            out.append("")
            out.append("欠けている tag を作るコマンド(実行はしない。人が確認して実行する):")
            out.extend("  " + c for c in cmds)
    out.append(summary_line(rows, info))
    return "\n".join(out)


# ---------------------------------------------------------------- selftest

def selftest():
    failures = []

    def expect(cond, label):
        (failures.append(label) if not cond else None)
        print(("PASS: " if cond else "FAIL: ") + label)

    expect(parse_changelog("## [Unreleased]\n\n## [1.1.0] - 2026-08-30\n\n## [0.9.0] - 2026-08-22\n")
           == [("1.1.0", "2026-08-30"), ("0.9.0", "2026-08-22")],
           "parse_changelog: `## [x.y.z] - YYYY-MM-DD` を新しい順に読む(Unreleased は無視)")
    rows, info = status(os.path.join(tempfile.gettempdir(), "no-such-repo-release-tag"))
    expect(rows == [] and info["git"] is False, "status: CHANGELOG が無い場所では空(例外にしない)")
    if git_bin() is None:
        print("SKIP: git が無いため版→コミット解決の自己テストを省略")
        print("release-tag selftest: " + ("FAIL " + str(len(failures)) if failures else "all passed"))
        return 1 if failures else 0
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        os.makedirs(repo)
        empty_cfg = os.path.join(td, "gitconfig-empty")
        open(empty_cfg, "w", encoding="utf-8").close()
        env = dict(os.environ, GIT_CONFIG_GLOBAL=empty_cfg, GIT_CONFIG_SYSTEM=empty_cfg,
                   GIT_AUTHOR_NAME="selftest", GIT_AUTHOR_EMAIL="selftest@example.com",
                   GIT_COMMITTER_NAME="selftest", GIT_COMMITTER_EMAIL="selftest@example.com")

        def commit(msg, date, files):
            for rel, text in files.items():
                open(os.path.join(repo, rel), "w", encoding="utf-8", newline="\n").write(text)
            e = dict(env, GIT_AUTHOR_DATE=f"{date}T12:00:00+0900", GIT_COMMITTER_DATE=f"{date}T12:00:00+0900")
            run_git(repo, "add", "-A", env=e)
            run_git(repo, "commit", "-q", "-m", msg, env=e)
            return run_git(repo, "rev-parse", "HEAD", env=e)[1].strip()

        run_git(repo, "init", "-q", "-b", "main", env=env)
        c1 = commit("init 0.1.0", "2026-07-02",
                    {PLUGIN_REL: '{"name": "x", "version": "0.1.0"}\n',
                     CHANGELOG_REL: "# Changelog\n\n## [0.1.0] - 2026-07-02\n"})
        c2 = commit("bump 0.9.0", "2026-08-22",
                    {PLUGIN_REL: '{"name": "x", "version": "0.9.0"}\n',
                     CHANGELOG_REL: "# Changelog\n\n## [0.9.0] - 2026-08-22\n\n## [0.1.0] - 2026-07-02\n"})
        c3 = commit("docs only (changelog entry without plugin bump)", "2026-08-25",
                    {CHANGELOG_REL: "# Changelog\n\n## [0.9.5] - 2026-08-25\n\n## [0.9.0] - 2026-08-22\n\n## [0.1.0] - 2026-07-02\n"})
        run_git(repo, "tag", "-a", "v0.1.0", c1, "-m", "v0.1.0", env=env)
        rows, info = status(repo)
        by = {r["version"]: r for r in rows}
        expect(info["git"] and not info["shallow"] and info["tags_total"] == 1, "status: git 情報(非 shallow・tag 1 本)")
        expect(by["0.1.0"]["sha"] == c1 and by["0.1.0"]["method"] == "plugin.json" and by["0.1.0"]["state"] == "ok"
               and by["0.1.0"]["tag"] == "v0.1.0", "resolve: 0.1.0 は plugin.json 導入コミットに解決し annotated tag があるので ok")
        expect(by["0.9.0"]["sha"] == c2 and by["0.9.0"]["method"] == "plugin.json" and by["0.9.0"]["state"] == "missing",
               "resolve: 0.9.0 は plugin.json の version 変更コミットに解決し tag 無しなので missing")
        expect(by["0.9.5"]["sha"] == c3 and by["0.9.5"]["method"] == "date" and by["0.9.5"]["state"] == "missing",
               "resolve: plugin.json に無い版は CHANGELOG 日付までの最後のコミットへ近似(approx)")
        cmds = commands_for(rows)
        expect(any(c.startswith(f"git tag -a v0.9.0 {c2} ") for c in cmds) and any(c.startswith("git push origin v0.9.5 v0.9.0") or c.startswith("git push origin v0.9.0 v0.9.5") for c in cmds),
               "commands: missing の版に git tag -a <sha> と push の案内を出す(実行はしない)")
        text = render(rows, info)
        expect("| 0.9.0 | 2026-08-22 |" in text and "missing" in text and "release-tag --check: versions 3 / ok 1 / missing 2" in text,
               "render: 表と集計行を出す")
        run_git(repo, "tag", "-a", "v0.9.0", c3, "-m", "wrong", env=env)
        run_git(repo, "tag", "v0.9.5", c3, env=env)
        rows, info = status(repo)
        by = {r["version"]: r for r in rows}
        expect(by["0.9.0"]["state"] == "mismatch", "tag: 別コミットを指す annotated tag は mismatch")
        expect(by["0.9.5"]["state"] == "lightweight", "tag: 正しい位置でも annotated でない tag は lightweight")
        expect(any("git tag -d v0.9.0" in c for c in commands_for(rows)), "commands: mismatch は確認のうえ張り直す案内(削除+再作成)")
        # shallow clone(CI の depth 1 相当): 境界コミットが全文字列を導入したように見えるため解決しない(unresolved)
        shallow = os.path.join(td, "shallow")
        rc_c, _ = run_git(td, "clone", "-q", "--depth", "1", "file:///" + os.path.abspath(repo).replace("\\", "/").lstrip("/"), shallow, env=env)
        if rc_c == 0 and is_shallow(shallow):
            rows_s, info_s = status(shallow)
            expect(info_s["shallow"] and all(r["state"] == "unresolved" and r["sha"] is None for r in rows_s) and len(rows_s) == 3,
                   "shallow clone では全版が unresolved(HEAD への誤帰属をしない)")
            expect("shallow clone" in render(rows_s, info_s), "render: shallow clone の注記を出す")
        else:
            print("SKIP: shallow clone のケース(file:// の --depth 1 clone が作れない環境)")
    print("release-tag selftest: " + ("FAIL " + str(len(failures)) if failures else "all passed"))
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="CHANGELOG の版と annotated git tag の対応を照合し、欠けている tag のコマンド列を出す(実行しない)")
    ap.add_argument("--root", default=ROOT, help="リポジトリルート(既定: このファイルの親の親)")
    ap.add_argument("--check", action="store_true", help="表と集計行だけ(コマンド列を出さない)。exit 0")
    ap.add_argument("--strict", action="store_true", help="missing / mismatch / lightweight があれば exit 1")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    rows, info = status(os.path.abspath(args.root))
    if not rows and not os.path.isfile(os.path.join(args.root, CHANGELOG_REL)):
        print(f"release-tag --check: {CHANGELOG_REL} が無い(配布先ではハーネス本体の CHANGELOG は配布されない=対象外)")
        return 0
    print(render(rows, info, with_commands=not args.check))
    if args.strict and any(r["state"] in ("missing", "mismatch", "lightweight") for r in rows):
        return 1
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

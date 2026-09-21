#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code サブエージェント(.claude/agents/<role>.md)の生成・検査: .github/agents/<role>.agent.md の本文を展開する(A5-6)。

公式(https://code.claude.com/docs/en/sub-agents、2026-09-17 確認): サブエージェントの本文(system prompt)は
「そのサブエージェントが起動したときだけ読み込まれ」、メイン会話に常駐するのは description だけ
("Keep descriptions brief ... move detail into each subagent's system prompt, which only loads when that subagent runs")。
実測(本体 .github/hooks/logs/instructions-loaded.jsonl 104 行、2026-09-17): session_start で読み込まれたのは CLAUDE.md だけで
.claude/agents/*.md は 1 件も無い=従来のポインタ方式(本文は「.github/agents を読め」の 5 行)に「常駐削減」の効果は
存在しない。ポインタ方式は起動後に `.github/agents/<role>.agent.md` を Read する 1 往復(本文と同量のトークンを
tool_result として読む)と「読まずに始める」逸脱の余地だけを残すため、本文を展開した生成物に替える。

- 正は引き続き `.github/agents/<role>.agent.md`(description・本文)。`.claude/agents/<role>.md` は生成物
  (frontmatter: name / description / tools = Claude Code のツール名。model / effort / maxTurns / background / memory は
  tools/model_policy.py(generate-adapters 第4節)が方針から差し込む=本モジュールは方針が読める環境ではその結果を
  期待値にする)。
- 本文先頭に生成マーカー `<!-- generated from .github/agents/<role>.agent.md ... -->` と Claude Code での読み替え
  (runSubagent → Agent ツール、Copilot のツール名 → frontmatter tools)を置き、その下に正の本文をそのまま展開する。
- 鮮度: `python tools/generate-adapters.py --check` と validate-harness.py (z) が status() で検査する
  (正を直して再生成し忘れた鏡割れ・未生成を ERROR)。

使い方(リポジトリルートで。通常は generate-adapters.py 経由):
    python tools/generate-adapters.py              # 第2節が本モジュールで .claude/agents を再生成
    python tools/subagent_adapters.py --check      # 0=一致 / 1=差分・欠落 / 2=エラー
    python tools/subagent_adapters.py --apply
    python tools/subagent_adapters.py --selftest
依存: Python 3.x(+ PyYAML があれば frontmatter を YAML として読む。無ければ description だけ正規表現で読む)。
model-policy.yml が読めない環境(PyYAML 不在・ファイル無し)では方針行なしの基本形を期待値にする。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile

try:
    import yaml
except ImportError:  # 標準ライブラリだけの環境でも import 自体は落とさない(read_source が AdapterError にする)
    yaml = None

AGENTS_DIR = os.path.join(".github", "agents")
CLAUDE_AGENTS_DIR = os.path.join(".claude", "agents")
# Claude Code 側のツール名(正の .agent.md の tools は Copilot の語彙。読み取り専用役には Edit / Write / Bash を渡さない)
SUBAGENT_TOOLS = {
    "reviewer": "Read, Grep, Glob",
    "spec-critic": "Read, Grep, Glob",
    "task-worker": "Read, Edit, Write, Grep, Glob, Bash",
}
GENERATED_MARK = "<!-- generated from .github/agents/"
_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


class AdapterError(Exception):
    pass


def read_source(root, name):
    """正 .github/agents/<name>.agent.md の (frontmatter, description, 本文) を返す。欠落・不正は AdapterError。"""
    rel = f".github/agents/{name}.agent.md"
    path = os.path.join(root, AGENTS_DIR, f"{name}.agent.md")
    if not os.path.exists(path):
        raise AdapterError(f"{rel} が無い(サブエージェントの正)")
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    m = _FM_RE.match(text)
    if not m:
        raise AdapterError(f"{rel}: frontmatter を読めない(先頭に --- で囲まれた YAML が必要)")
    if yaml is not None:
        fm = yaml.safe_load(m.group(1)) or {}
    else:
        # PyYAML 不在(配布先・素の python)でも description だけは読む(単一引用符 / 二重引用符 / 裸のスカラー。
        # platform_requirements.py の regex 読みと同じ流儀。他のキーは使わない)
        fm = {}
        dm = re.search(r"^description:[ \t]*(?:'((?:[^']|'')*)'|\"([^\"]*)\"|(.+?))[ \t]*$", m.group(1), re.M)
        if dm:
            fm["description"] = (dm.group(1).replace("''", "'") if dm.group(1) is not None
                                 else dm.group(2) if dm.group(2) is not None else dm.group(3))
    desc = fm.get("description") if isinstance(fm, dict) else None
    if not isinstance(desc, str) or not desc.strip():
        raise AdapterError(f"{rel}: description が無い(.claude/agents の description になる)")
    body = text[m.end():].replace("\r\n", "\n").strip("\n")
    if not body:
        raise AdapterError(f"{rel}: 本文が空(展開する役割定義が無い)")
    return fm, desc, body


def render(name, desc, tools, body):
    """基本形(方針行なし)。model / effort 等は tools/model_policy.py が後から差し込む。"""
    return f"""---
name: {name}
description: {desc}
tools: {tools}
---

{GENERATED_MARK}{name}.agent.md (本文はその役割定義を展開した生成物。編集は正の側で行い python tools/generate-adapters.py で再生成する。A5-6) -->

あなたは {name} サブエージェントです。以下は `.github/agents/{name}.agent.md`（正）の役割定義をそのまま展開したもので、
この本文だけがサブエージェント起動時に system prompt として読み込まれます（別ファイルを読みに行く必要はありません）。
Claude Code での読み替え: 役割定義中の `runSubagent` は Agent ツール（旧称 Task）で `.claude/agents/` の同名サブエージェントを
呼ぶこと、Copilot のツール名（`read` / `search` / `edit` / `execute`）は上の frontmatter `tools` に対応します。

## 役割定義（`.github/agents/{name}.agent.md` の展開）

{body}
"""


def _load_policy(root):
    """model-policy.yml を tools/model_policy.py で読む(読めなければ None=基本形を期待値にする)。"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import model_policy  # noqa: E402
        return model_policy, model_policy.load_policy(root)
    except Exception:  # noqa: BLE001
        return None, None


def expected(root, name, policy=None):
    """最終形の期待値(方針が読める環境では model_policy.render_claude_agent を掛けた後の文面)。"""
    _fm, desc, body = read_source(root, name)
    base = render(name, desc, SUBAGENT_TOOLS[name], body)
    mp, p = policy if policy is not None else _load_policy(root)
    if mp is not None and p is not None:
        try:
            return mp.render_claude_agent(base, p, name, f".claude/agents/{name}.md")
        except Exception:  # noqa: BLE001
            return base
    return base


def targets(root):
    """生成対象を [(rel, new_text)] で返す(役割名順)。"""
    policy = _load_policy(root)
    return [(f".claude/agents/{name}.md", expected(root, name, policy)) for name in sorted(SUBAGENT_TOOLS)]


def _read_text(path):
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8")


def _same_text(cur, new):
    """改行コードの差(CRLF/LF)だけは一致とみなす(core.autocrlf=true のチェックアウト対策。model_policy と同じ)。"""
    if cur is None:
        return False
    return cur.replace("\r\n", "\n") == new.replace("\r\n", "\n")


def status(root):
    """(stale, missing) を返す。stale は差分のある rel、missing は未生成の rel。"""
    stale, missing = [], []
    for rel, new in targets(root):
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            missing.append(rel)
        elif not _same_text(_read_text(path), new):
            stale.append(rel)
    return stale, missing


def apply_all(root, log=print):
    """差分のある .claude/agents/<role>.md だけ書く(冪等)。戻り値: 書いた数。"""
    written = 0
    for rel, new in targets(root):
        path = os.path.join(root, rel)
        cur = _read_text(path) if os.path.exists(path) else None
        if not _same_text(cur, new):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new)
            written += 1
            log(rel)
    return written


def cmd_check(root):
    try:
        stale, missing = status(root)
    except AdapterError as e:
        print("ERROR:", e)
        return 2
    for rel in stale:
        print(f"STALE: {rel} (正 .github/agents の本文・description とずれている。python tools/generate-adapters.py で再生成)")
    for rel in missing:
        print(f"MISSING: {rel} (python tools/generate-adapters.py で生成)")
    bad = len(stale) + len(missing)
    print(f"subagent-adapters --check: {'NG' if bad else 'OK'} (対象 {len(SUBAGENT_TOOLS)} ファイル中 差分 {len(stale)} / 欠落 {len(missing)})")
    return 1 if bad else 0


# ---------------------------------------------------------------- selftest

def _fake_root(tmp, body="## 観点\n\n1. 正しさ\n"):
    root = os.path.join(tmp, "root")
    os.makedirs(os.path.join(root, AGENTS_DIR))
    for name in SUBAGENT_TOOLS:
        with open(os.path.join(root, AGENTS_DIR, f"{name}.agent.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f"---\ndescription: '{name} の説明'\ntools: ['read', 'search']\nagents: []\n---\n\nあなたは {name} です。\n\n{body}")
    return root


def selftest():
    passed = failed = 0
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    def expect(cond, name, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("PASS:", name)
        else:
            failed += 1
            print("FAIL:", name + (f" ({detail})" if detail else ""))

    tmp = tempfile.mkdtemp(prefix="subagent-adapters-selftest-")
    try:
        root = _fake_root(tmp)
        text = expected(root, "reviewer")
        expect(text.startswith("---\nname: reviewer\ndescription: reviewer の説明\ntools: Read, Grep, Glob\n---\n"),
               "render: frontmatter は name / description(正の値) / tools(Claude Code のツール名)", text[:120])
        expect(GENERATED_MARK + "reviewer.agent.md" in text and "## 観点" in text and "1. 正しさ" in text
               and "あなたは reviewer です。" in text, "render: 生成マーカーと正の本文がそのまま展開される")
        expect("runSubagent" in text and "Agent ツール" in text and "別ファイルを読みに行く必要はありません" in text,
               "render: Claude Code での読み替え(runSubagent → Agent ツール)を本文先頭に置く")
        expect("model:" not in text, "render: 方針(model-policy.yml)が読めない root では model 行を入れない(第4節が差し込む)")
        stale, missing = status(root)
        expect(stale == [] and len(missing) == 3, "status: 未生成なら missing 3 件", str((stale, missing)))
        n = apply_all(root, log=lambda rel: None)
        expect(n == 3 and status(root) == ([], []), "apply: 3 件書いて一致、再実行は 0 件", str(status(root)))
        expect(apply_all(root, log=lambda rel: None) == 0, "apply: 冪等")
        src = os.path.join(root, AGENTS_DIR, "spec-critic.agent.md")
        with open(src, "a", encoding="utf-8", newline="\n") as f:
            f.write("\n2. 抜け\n")
        stale, missing = status(root)
        expect(stale == [".claude/agents/spec-critic.md"] and missing == [], "status: 正の本文を変えると stale(鏡割れの検出)", str(stale))
        expect(cmd_check(root) == 1, "cmd_check: 差分ありは exit 1")
        apply_all(root, log=lambda rel: None)
        expect(cmd_check(root) == 0, "cmd_check: 再生成後は exit 0")
        gen = _read_text(os.path.join(root, CLAUDE_AGENTS_DIR, "spec-critic.md"))
        expect("2. 抜け" in gen and "\r" not in gen, "apply: 追記が展開され LF で書かれる")
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write("---\ndescription: 'x'\n---\n\r\n本文\r\n")
        expect("本文\n" in expected(root, "spec-critic") and "\r" not in expected(root, "spec-critic"), "render: 正の CRLF は LF に正規化")
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write("---\ntools: ['read']\n---\n\n本文\n")
        try:
            expected(root, "spec-critic")
            expect(False, "read_source: description 欠落は AdapterError")
        except AdapterError:
            expect(True, "read_source: description 欠落は AdapterError")
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write("本文だけ\n")
        try:
            expected(root, "spec-critic")
            expect(False, "read_source: frontmatter 欠落は AdapterError")
        except AdapterError:
            expect(True, "read_source: frontmatter 欠落は AdapterError")
        os.remove(src)
        try:
            targets(root)
            expect(False, "targets: 正が無ければ AdapterError")
        except AdapterError:
            expect(True, "targets: 正が無ければ AdapterError")
        real_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if yaml is not None and os.path.exists(os.path.join(real_root, ".github", "harness", "model-policy.yml")):  # 方針は PyYAML 必須
            real = expected(real_root, "reviewer")
            expect("\nmodel: " in real and "model-policy.yml" in real, "本体: 方針が読める root では model 行と方針注記を含む最終形が期待値")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"subagent-adapters selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Claude Code サブエージェント(.claude/agents/<role>.md)の本文展開の生成・検査(A5-6)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help=".claude/agents を再生成する(generate-adapters.py 第2節と同じ)")
    g.add_argument("--check", action="store_true", help="生成物が正とずれていないか(0=一致 / 1=差分・欠落 / 2=エラー)")
    g.add_argument("--selftest", action="store_true", help="自己テスト")
    args = ap.parse_args(argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.selftest:
            return selftest()
        if args.check:
            return cmd_check(root)
        if args.apply:
            n = apply_all(root, log=lambda rel: print("wrote:", rel))
            print(f"subagent-adapters: {n} ファイル更新")
            return 0
    except AdapterError as e:
        print("ERROR:", e)
        return 2
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

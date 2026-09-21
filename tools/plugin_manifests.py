#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plugin マニフェストの単一ソース(plugin.json)から Claude Code plugin の鏡を生成・検査する。

再監査 2026-09-09 CP-7 / RG-17 / A2-2 / A6-23(codex IA-20260831-10 / R-08)の実装。
正は リポジトリ直下の `plugin.json` の 1 ファイル(Agent Plugins 1.0 準拠。$schema と name が必須、
トップレベルは $schema/name/version/description/author/homepage/repository/license/keywords/extensions
だけで additionalProperties=false。一次情報: https://agent-plugins.org/schemas/1.0.0/plugin.schema.json)。
版数は plugin.json の version が正(CHANGELOG 規約)。ハーネス固有の情報は `extensions` の
逆ドメイン名前空間にだけ置く(旧マニフェストの agents/skills/hooks/commands/commands_note は
スキーマ違反だったため extensions へ移した)。

次の 2 面が生成物(本モジュールが書く。手で編集しない):

- `.claude-plugin/plugin.json` — Claude Code plugin マニフェスト(name 必須、パス系は ./ 始まりで
  plugin ルート相対。一次情報: https://code.claude.com/docs/en/plugins-reference)。skills は正の
  `.github/skills/`、commands / agents は `.claude/` のアダプタ、hooks は下の hooks.json(.claude-plugin) を指す
- `.claude-plugin/hooks.json` — `.claude/settings.json` の hooks を plugin 形式に写したもの
  (スクリプトパスを `${CLAUDE_PLUGIN_ROOT}` 相対に書き換える)。**plugin 経由の配布時のみ**使われる。
  本リポジトリ(またはテンプレコピー)を直接開く場合は `.claude/settings.json` が同じフックを配線して
  いるため、`--plugin-dir .` で二重に読み込ませない

使い方(リポジトリルートで。generate-adapters.py の第6節が apply_all を呼ぶ):
    python tools/generate-adapters.py            # 通常モード: 第1〜6節を全部再生成(第6節が本モジュール)
    python tools/generate-adapters.py --check    # model-policy と plugin manifests の両方の差分検査
    python tools/plugin_manifests.py --check     # 本モジュールだけ(0=一致 / 1=差分 / 2=エラー)
    python tools/plugin_manifests.py --apply     # 本モジュールだけ書く
    python tools/plugin_manifests.py --selftest  # 自己テスト

未検証(open): VS Code / Copilot CLI / Claude Code への plugin 実インストール、plugin 経由の hooks の
PreToolUse 発火(#2540)。アダプタ(.claude/commands|agents)のポインタは「cwd にハーネスのコピーがある」
前提で書かれており、別リポジトリへの plugin インストールでは `${CLAUDE_PLUGIN_ROOT}` を知らない。
依存: Python 3.x 標準ライブラリのみ
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import tempfile

SOURCE_REL = "plugin.json"
CLAUDE_MANIFEST_REL = ".claude-plugin/plugin.json"
PLUGIN_HOOKS_REL = ".claude-plugin/hooks.json"  # 2026-09-21: .github/hooks/ から移動(Copilot CLI が .github/hooks/**/*.json を全部フック設定として読み ${CLAUDE_PLUGIN_ROOT} 未展開で fail-closed した。D097)
SETTINGS_REL = ".claude/settings.json"

AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGINS_KEYS = ("$schema", "name", "version", "description", "author", "homepage",
                      "repository", "license", "keywords", "extensions")
AUTHOR_KEYS = ("name", "email", "url")
NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
NS_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9-]+)+$")
# R-08(codex 監査): 「Antigravity対応」は凍結済み(D057)の環境を対応済みと誤認させる表現
FORBIDDEN_DESCRIPTION = ("Antigravity対応", "Antigravity 対応")

# Claude Code plugin のコンポーネント配置(ハーネスの固定レイアウト。値は plugin ルート相対で ./ 始まり)。
# agents だけはディレクトリ文字列ではなくファイルの配列にする(実機 2.1.201 の `claude plugin validate` は
# ディレクトリ指定を "agents: Invalid input" で拒否し、配列なら通った。公式 docs は string|array)。
# `metadata` は載せない(2.1.201 は Unknown field と警告し --strict で赤になる。生成元は plugin.json の
# extensions と本モジュールの docstring が正)。
CLAUDE_SKILLS_DIR = "./.github/skills/"
CLAUDE_COMMANDS_DIR = "./.claude/commands/"
CLAUDE_AGENTS_GLOB = (".claude", "agents", "*.md")
CLAUDE_PATH_KEYS = ("skills", "commands", "agents", "workflows", "hooks", "mcpServers",
                    "outputStyles", "lspServers")
CLAUDE_PASSTHROUGH = ("version", "description", "author", "homepage", "repository", "license", "keywords")
HOOKS_SCRIPT_RE = re.compile(r'(?<![\w"/${])(\.github/hooks/scripts/[A-Za-z0-9_.\-]+)')
PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"


class ManifestError(Exception):
    """マニフェストの構造エラー(生成を止める)。"""


# ---------------------------------------------------------------- 読込・検査

def _read_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def load_source(root):
    path = os.path.join(root, SOURCE_REL)
    if not os.path.exists(path):
        raise ManifestError(f"{SOURCE_REL} が無い(plugin マニフェストの正)")
    try:
        data = _read_json(path)
    except Exception as e:  # noqa: BLE001
        raise ManifestError(f"{SOURCE_REL}: JSON parse error: {e}")
    problems = validate_source(data)
    if problems:
        raise ManifestError(f"{SOURCE_REL}: " + " / ".join(problems))
    return data


def validate_source(data):
    """plugin.json を Agent Plugins 1.0 スキーマ(+ R-08 表現規則)で検査し、問題の一覧を返す。"""
    problems = []
    if not isinstance(data, dict):
        return ["トップレベルがオブジェクトではない"]
    if data.get("$schema") != AGENT_PLUGINS_SCHEMA:
        problems.append(f"$schema は {AGENT_PLUGINS_SCHEMA} 固定(必須)")
    name = data.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 64) or not NAME_RE.match(name):
        problems.append("name は必須で、小文字英数字とハイフン・ドット(連続不可・先頭末尾は英数字)の 1〜64 文字")
    for k in data:
        if k not in AGENT_PLUGINS_KEYS:
            problems.append(f"トップレベルの未知キー {k!r}(additionalProperties=false。ハーネス固有情報は extensions へ)")
    for k in ("version", "description", "homepage", "repository", "license"):
        if k in data and not isinstance(data[k], str):
            problems.append(f"{k} は文字列")
    if "version" in data and isinstance(data["version"], str) and not SEMVER_RE.match(data["version"]):
        problems.append(f"version {data['version']!r} は semver(MAJOR.MINOR.PATCH)ではない")
    if "keywords" in data and (not isinstance(data["keywords"], list)
                               or not all(isinstance(x, str) for x in data["keywords"])):
        problems.append("keywords は文字列の配列")
    if "author" in data:
        a = data["author"]
        if not isinstance(a, dict):
            problems.append("author はオブジェクト")
        else:
            for k in a:
                if k not in AUTHOR_KEYS:
                    problems.append(f"author の未知キー {k!r}({'/'.join(AUTHOR_KEYS)} のみ)")
                elif not isinstance(a[k], str):
                    problems.append(f"author.{k} は文字列")
    if "extensions" in data:
        ext = data["extensions"]
        if not isinstance(ext, dict):
            problems.append("extensions はオブジェクト")
        else:
            for ns, v in ext.items():
                if not NS_RE.match(ns):
                    problems.append(f"extensions の名前空間 {ns!r} は逆ドメイン形式(例: com.github.copilot)")
                if not isinstance(v, dict):
                    problems.append(f"extensions[{ns!r}] はオブジェクト")
    desc = data.get("description") or ""
    for bad in FORBIDDEN_DESCRIPTION:
        if bad in desc:
            problems.append(f"description に「{bad}」(R-08: Antigravity は D057 で凍結。「アダプタ同梱・未検証」と書く)")
    return problems


def validate_claude_manifest(root, source=None):
    """生成済み .claude-plugin/plugin.json の Claude Code 規則を検査し、問題の一覧を返す
    (name 必須・パス系は ./ 始まり・参照先が実在・version/name が plugin.json と一致)。"""
    problems = []
    path = os.path.join(root, CLAUDE_MANIFEST_REL)
    if not os.path.exists(path):
        return [f"{CLAUDE_MANIFEST_REL} が無い(python tools/generate-adapters.py で生成)"]
    try:
        data = _read_json(path)
    except Exception as e:  # noqa: BLE001
        return [f"{CLAUDE_MANIFEST_REL}: JSON parse error: {e}"]
    if not isinstance(data.get("name"), str) or not data["name"]:
        problems.append(f"{CLAUDE_MANIFEST_REL}: name が無い(Claude Code の必須キー)")
    if source is not None:
        for k in ("name", "version"):
            if data.get(k) != source.get(k):
                problems.append(f"{CLAUDE_MANIFEST_REL}: {k} {data.get(k)!r} が {SOURCE_REL} の {source.get(k)!r} と不一致")
    for k in CLAUDE_PATH_KEYS:
        if k not in data:
            continue
        v = data[k]
        vals = v if isinstance(v, list) else [v]
        for p in vals:
            if not isinstance(p, str):
                continue  # inline object(hooks / mcpServers)は対象外
            if p != "." and not p.startswith("./"):
                problems.append(f"{CLAUDE_MANIFEST_REL}: {k} のパス {p!r} が ./ 始まりではない(plugin ルート相対)")
                continue
            if not os.path.exists(os.path.join(root, p)):
                problems.append(f"{CLAUDE_MANIFEST_REL}: {k} の参照先 {p!r} が存在しない")
    return problems


# ---------------------------------------------------------------- 生成

def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def claude_agent_files(root):
    """.claude/agents/*.md(第2節の生成物)を plugin ルート相対 ./ 始まりの配列で返す(ソート済み)。"""
    pattern = os.path.join(root, *CLAUDE_AGENTS_GLOB)
    rels = sorted(os.path.relpath(p, root).replace(os.sep, "/") for p in glob.glob(pattern))
    if not rels:
        raise ManifestError(".claude/agents/*.md が無い(先に generate-adapters.py 第2節が生成する)")
    return ["./" + r for r in rels]


def render_claude_manifest(source, root):
    out = {"name": source["name"]}
    for k in CLAUDE_PASSTHROUGH:
        if k in source:
            out[k] = source[k]
    out["skills"] = CLAUDE_SKILLS_DIR
    out["commands"] = CLAUDE_COMMANDS_DIR
    out["agents"] = claude_agent_files(root)
    out["hooks"] = "./" + PLUGIN_HOOKS_REL
    return _dump(out)


def rewrite_hook_command(cmd):
    """`bash .github/hooks/scripts/x.sh` → `bash "${CLAUDE_PLUGIN_ROOT}/.github/hooks/scripts/x.sh"`。
    run-python.sh 経由の `.py` も含め、スクリプトパスのトークンだけを plugin ルート相対に写す。"""
    return HOOKS_SCRIPT_RE.sub(lambda m: f'"{PLUGIN_ROOT_VAR}/{m.group(1)}"', cmd)


def render_plugin_hooks(settings):
    """.claude/settings.json(dict)の hooks を plugin 形式(hooks/hooks.json と同型)に写す。"""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict) or not hooks:
        raise ManifestError(f"{SETTINGS_REL} に hooks が無い(hooks.json(.claude-plugin) の元データ)")
    out = {}
    for event, matchers in hooks.items():
        new_matchers = []
        for entry in matchers:
            e = {k: v for k, v in entry.items() if k != "hooks"}
            e["hooks"] = []
            for h in entry.get("hooks", []):
                h2 = dict(h)
                if h2.get("type") == "command" and isinstance(h2.get("command"), str):
                    h2["command"] = rewrite_hook_command(h2["command"])
                e["hooks"].append(h2)
            new_matchers.append(e)
        out[event] = new_matchers
    return _dump({"hooks": out})


def targets(root, source=None):
    """生成対象を [(rel, new_text)] で返す。"""
    if source is None:
        source = load_source(root)
    spath = os.path.join(root, SETTINGS_REL)
    if not os.path.exists(spath):
        raise ManifestError(f"{SETTINGS_REL} が無い(hooks.json(.claude-plugin) の元データ)")
    try:
        settings = _read_json(spath)
    except Exception as e:  # noqa: BLE001
        raise ManifestError(f"{SETTINGS_REL}: JSON parse error: {e}")
    return [(CLAUDE_MANIFEST_REL, render_claude_manifest(source, root)),
            (PLUGIN_HOOKS_REL, render_plugin_hooks(settings))]


def _read_text(path):
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8")


def _same_text(cur, new):
    """改行コードの差(CRLF/LF)だけは一致とみなす(model_policy と同じ。core.autocrlf=true 対策)。"""
    if cur is None:
        return False
    return cur.replace("\r\n", "\n") == new.replace("\r\n", "\n")


def stale_targets(root, source=None):
    stale = []
    for rel, new in targets(root, source):
        path = os.path.join(root, rel)
        cur = _read_text(path) if os.path.exists(path) else None
        if not _same_text(cur, new):
            stale.append(rel)
    return stale


def apply_all(root, log=print):
    source = load_source(root)
    written = 0
    for rel, new in targets(root, source):
        path = os.path.join(root, rel)
        cur = _read_text(path) if os.path.exists(path) else None
        if not _same_text(cur, new):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new)
            written += 1
            log(rel)
    return written


# ---------------------------------------------------------------- CLI

def cmd_check(root):
    try:
        source = load_source(root)
        stale = stale_targets(root, source)
    except ManifestError as e:
        print("ERROR:", e)
        return 2
    for rel in stale:
        print(f"STALE: {rel} (python tools/generate-adapters.py で再生成)")
    for p in validate_claude_manifest(root, source):
        print("WARN:", p)
    print(f"plugin-manifests --check: {'NG' if stale else 'OK'} (対象 2 ファイル中 差分 {len(stale)})")
    return 1 if stale else 0


def _fixture_source():
    return {
        "$schema": AGENT_PLUGINS_SCHEMA,
        "name": "fixture-harness",
        "version": "1.2.3",
        "description": "fixture(Antigravity はアダプタ同梱・未検証)",
        "license": "MIT",
        "extensions": {"com.github.copilot": {"agents": ".github/agents"}},
    }


def selftest():
    passed = failed = 0

    def expect(cond, name):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("PASS:", name)
        else:
            failed += 1
            print("FAIL:", name)

    good = _fixture_source()
    # (1) Agent Plugins 1.0 スキーマ検査
    expect(validate_source(good) == [], "validate_source: compliant manifest passes")
    bad = dict(good); del bad["$schema"]
    expect(any("$schema" in p for p in validate_source(bad)), "validate_source: missing $schema")
    bad = dict(good); bad["name"] = "App--Dev"
    expect(any("name" in p for p in validate_source(bad)), "validate_source: name pattern (uppercase / --)")
    bad = dict(good); bad["agents"] = ".github/agents"
    expect(any("未知キー" in p for p in validate_source(bad)), "validate_source: legacy top-level key rejected")
    bad = dict(good); bad["version"] = "v1"
    expect(any("semver" in p for p in validate_source(bad)), "validate_source: version must be semver")
    bad = dict(good); bad["description"] = "…(GitHub Copilot / Claude Code / Antigravity対応)"
    expect(any("R-08" in p for p in validate_source(bad)), "validate_source: 'Antigravity対応' rejected (R-08)")
    bad = dict(good); bad["extensions"] = {"harness": {}}
    expect(any("逆ドメイン" in p for p in validate_source(bad)), "validate_source: extension namespace must be reverse-domain")
    bad = dict(good); bad["author"] = {"name": "x", "twitter": "y"}
    expect(any("author" in p for p in validate_source(bad)), "validate_source: author unknown key rejected")
    # (2) Claude マニフェストの生成(name 必須・./ 始まり・version 透過・agents はファイル配列・metadata 無し)
    with tempfile.TemporaryDirectory() as tmp2:
        os.makedirs(os.path.join(tmp2, ".claude", "agents"))
        for a in ("reviewer", "task-worker"):
            open(os.path.join(tmp2, ".claude", "agents", a + ".md"), "w").close()
        cm = json.loads(render_claude_manifest(good, tmp2))
        expect(cm["name"] == "fixture-harness" and cm["version"] == "1.2.3" and cm["license"] == "MIT",
               "render_claude_manifest: name/version/license pass through")
        expect(cm["skills"].startswith("./") and cm["commands"].startswith("./") and cm["hooks"] == "./" + PLUGIN_HOOKS_REL,
               "render_claude_manifest: component paths start with ./")
        expect(cm["agents"] == ["./.claude/agents/reviewer.md", "./.claude/agents/task-worker.md"],
               "render_claude_manifest: agents is a sorted array of ./ files (2.1.201 rejects a directory string)")
        expect("extensions" not in cm and "$schema" not in cm and "metadata" not in cm,
               "render_claude_manifest: no Agent Plugins-only keys, no metadata (2.1.201 warns Unknown field)")
        os.remove(os.path.join(tmp2, ".claude", "agents", "reviewer.md"))
        os.remove(os.path.join(tmp2, ".claude", "agents", "task-worker.md"))
        try:
            render_claude_manifest(good, tmp2)
            expect(False, "render_claude_manifest: no agents -> ManifestError")
        except ManifestError:
            expect(True, "render_claude_manifest: no agents -> ManifestError")
    # (3) hooks の書き換え(スクリプトパスだけ ${CLAUDE_PLUGIN_ROOT} 相対に。引数・run-python 経由・冪等)
    expect(rewrite_hook_command("bash .github/hooks/scripts/inject-progress.sh PreCompact")
           == 'bash "${CLAUDE_PLUGIN_ROOT}/.github/hooks/scripts/inject-progress.sh" PreCompact',
           "rewrite_hook_command: script path rewritten, argument kept")
    expect(rewrite_hook_command("bash .github/hooks/scripts/run-python.sh .github/hooks/scripts/log-effort.py")
           == 'bash "${CLAUDE_PLUGIN_ROOT}/.github/hooks/scripts/run-python.sh" "${CLAUDE_PLUGIN_ROOT}/.github/hooks/scripts/log-effort.py"',
           "rewrite_hook_command: run-python.sh and .py both rewritten")
    once = rewrite_hook_command("bash .github/hooks/scripts/x.sh")
    expect(rewrite_hook_command(once) == once, "rewrite_hook_command: idempotent")
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "bash .github/hooks/scripts/guard-dangerous-git.sh", "timeout": 5}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "bash .github/hooks/scripts/run-python.sh .github/hooks/scripts/remind-record.py", "timeout": 15}]}]},
        "permissions": {"deny": ["Edit(plugin.json)"]}, "statusLine": {"type": "command", "command": "x"}}
    ph = json.loads(render_plugin_hooks(settings))
    expect(set(ph) == {"hooks"} and set(ph["hooks"]) == {"PreToolUse", "Stop"}
           and ph["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
           and ph["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] == 5
           and PLUGIN_ROOT_VAR in ph["hooks"]["Stop"][0]["hooks"][0]["command"],
           "render_plugin_hooks: only hooks copied, matcher/timeout kept, commands rewritten")
    try:
        render_plugin_hooks({"permissions": {}})
        expect(False, "render_plugin_hooks: settings without hooks -> ManifestError")
    except ManifestError:
        expect(True, "render_plugin_hooks: settings without hooks -> ManifestError")
    # (4) 一時ディレクトリで apply / stale / 冪等 / 手編集の正規化 / Claude マニフェスト検査
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, ".claude", "commands"))
        os.makedirs(os.path.join(tmp, ".claude", "agents"))
        open(os.path.join(tmp, ".claude", "agents", "reviewer.md"), "w").close()
        os.makedirs(os.path.join(tmp, ".github", "skills"))
        os.makedirs(os.path.join(tmp, ".github", "hooks"))
        with open(os.path.join(tmp, SOURCE_REL), "w", encoding="utf-8") as f:
            json.dump(good, f, ensure_ascii=False)
        with open(os.path.join(tmp, SETTINGS_REL), "w", encoding="utf-8") as f:
            json.dump(settings, f)
        expect(stale_targets(tmp) == [CLAUDE_MANIFEST_REL, PLUGIN_HOOKS_REL], "stale_targets: both missing -> stale")
        n = apply_all(tmp, log=lambda rel: None)
        expect(n == 2 and stale_targets(tmp) == [], "apply_all: writes 2 files, then no stale")
        expect(apply_all(tmp, log=lambda rel: None) == 0, "apply_all: idempotent")
        expect(validate_claude_manifest(tmp, good) == [], "validate_claude_manifest: generated manifest passes")
        cp = os.path.join(tmp, CLAUDE_MANIFEST_REL)
        text = _read_text(cp).replace('"version": "1.2.3"', '"version": "9.9.9"').replace("./.claude/agents/reviewer.md", "agents/reviewer.md")
        with open(cp, "w", encoding="utf-8") as f:
            f.write(text)
        probs = validate_claude_manifest(tmp, good)
        expect(any("version" in p for p in probs) and any("./ 始まり" in p for p in probs),
               "validate_claude_manifest: version drift and non-./ path detected")
        expect(stale_targets(tmp) == [CLAUDE_MANIFEST_REL], "stale_targets: hand edit detected")
        with open(cp, "w", encoding="utf-8", newline="") as f:
            f.write(render_claude_manifest(good, tmp).replace("\n", "\r\n"))
        expect(stale_targets(tmp) == [], "stale_targets: CRLF-only difference is not stale")
        bad_src = dict(good); bad_src["commands_note"] = "x"
        with open(os.path.join(tmp, SOURCE_REL), "w", encoding="utf-8") as f:
            json.dump(bad_src, f, ensure_ascii=False)
        expect(cmd_check(tmp) == 2, "cmd_check: non-compliant plugin.json -> exit 2")
    # (5) 実リポジトリ(存在すれば): 正が読めて生成対象が列挙でき、生成済みマニフェストの参照先が実在する
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(root, SOURCE_REL)) and os.path.exists(os.path.join(root, SETTINGS_REL)):
        try:
            src = load_source(root)
            expect(len(targets(root, src)) == 2, "real repo: 2 targets enumerated")
            if os.path.exists(os.path.join(root, CLAUDE_MANIFEST_REL)):
                probs = validate_claude_manifest(root, src)
                expect(probs == [], f"real repo: .claude-plugin/plugin.json paths exist ({'; '.join(probs) or 'ok'})")
        except ManifestError as e:
            expect(False, f"real repo: plugin.json loads: {e}")
    print(f"plugin_manifests selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="plugin マニフェスト(plugin.json → .claude-plugin / hooks.json(.claude-plugin))の生成・検査")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="生成物を書く(generate-adapters.py の第6節と同じ)")
    g.add_argument("--check", action="store_true", help="生成物が正とずれていないか(0=一致 / 1=差分 / 2=エラー)")
    g.add_argument("--print", action="store_true", help="生成結果を標準出力に出す(書かない)")
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
        if args.print:
            for rel, text in targets(root):
                print(f"### {rel}")
                print(text)
            return 0
        if args.apply:
            n = apply_all(root, log=lambda rel: print("wrote:", rel))
            print(f"plugin-manifests: {n} ファイル更新")
            return 0
    except ManifestError as e:
        print("ERROR:", e)
        return 2
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

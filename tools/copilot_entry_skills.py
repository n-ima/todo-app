#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Copilot Agent Host 向け入口スキル(.github/skills/<nn>-<name>/SKILL.md)の生成・検査。

再監査 2026-09-09 §2.4 CP-2 / A6-17 の実装。VS Code の既定セッション基盤 Agent Host は prompt files
(.github/prompts/*.prompt.md)を読み込まず(公式: "Prompt files are deprecated for Agent Host sessions and
aren't loaded by Agent Host")、Local ハーネスは将来削除される。本ハーネスの入口 18 本(/00〜/99)は
Copilot 経路で起動不能になるため、正(prompt + `agent:` バインド)を参照するだけの薄い user-invocable
スキルを **生成物** として置く。

- 正は引き続き `.github/prompts/*.prompt.md`(agent: バインド含む)と `.github/agents/*.agent.md`。
- 生成先は `.github/skills/<prompt base 名>/SKILL.md`(name = prompt の base 名 = フォルダ名。
  frontmatter: name / description(prompt の description) / user-invocable: true /
  disable-model-invocation: false / metadata.harness-entry: copilot。本文はポインタ + 読み替え)。
- **Claude Code 側に `.claude/skills/<nn>-*/` のポインタは作らない**(スラッシュ名 /<nn>-… が
  generate-adapters 第1節の `.claude/commands/<nn>-….md` と衝突する。Claude Code の入口は従来どおり commands)。
  `tools/validate-harness.py` は「.github/skills には必ず .claude/skills アダプタ」検査からこの入口スキルを
  除外し、ポインタが存在すれば ERROR にする。
- 入口スキルの判定: フォルダ名が `^\\d\\d-` か、frontmatter に `harness-entry: copilot`
  (トップレベルまたは `metadata:` 配下。agentskills.io 仕様は独自キーを `metadata` に置く)を持つ。
- 判定と生成の正はこのモジュール 1 か所。tools/gen-docs.py はスキル数(手順部品)の計数から入口スキルを除く。

使い方(リポジトリルートで。通常は generate-adapters.py 経由で実行される):
    python tools/generate-adapters.py                    # 通常モード: 第1〜5節を再生成(第5節が本モジュール)
    python tools/generate-adapters.py --check            # 第4節(model-policy)と本モジュールの鮮度を両方検査
    python tools/copilot_entry_skills.py --check         # 本モジュールだけ(0=一致 / 1=差分・孤児・欠落 / 2=エラー)
    python tools/copilot_entry_skills.py --apply         # 本モジュールだけ再生成
    python tools/copilot_entry_skills.py --budget        # 全スキルの name/description 文字数(常駐予算の実測)
    python tools/copilot_entry_skills.py --selftest      # 自己テスト

依存: Python 3.x + PyYAML(frontmatter の読込のみ。判定関数 is_entry_skill_name は標準ライブラリだけで動く)
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys
import tempfile

try:
    import yaml
except ImportError:  # gen-docs.py(標準ライブラリのみ)からの import でも落ちないようにする
    yaml = None

PROMPTS_DIR = os.path.join(".github", "prompts")
AGENTS_DIR = os.path.join(".github", "agents")
SKILLS_DIR = os.path.join(".github", "skills")
CLAUDE_SKILLS_DIR = os.path.join(".claude", "skills")

ENTRY_NAME_RE = re.compile(r"^\d\d-")
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")  # agentskills.io: 小文字英数とハイフン、先頭末尾・連続ハイフン不可
DESCRIPTION_MAX = 1024  # VS Code docs / agentskills.io の上限(validate-harness.py の検査値と同じ)
HARNESS_ENTRY_KEY = "harness-entry"
HARNESS_ENTRY_VALUE = "copilot"
GENERATED_MARK = "<!-- generated from"


class EntryError(Exception):
    """正(prompt / agent)の構造エラー。生成を止める。"""


# ---------------------------------------------------------------- 判定

def is_entry_skill_name(name):
    """フォルダ名だけで判定する(gen-docs.py 等、frontmatter を読まない呼び手向け)。"""
    return bool(ENTRY_NAME_RE.match(name or ""))


def has_entry_marker(fm):
    """frontmatter に harness-entry: copilot を持つか(トップレベル or metadata 配下)。"""
    if not isinstance(fm, dict):
        return False
    if fm.get(HARNESS_ENTRY_KEY) == HARNESS_ENTRY_VALUE:
        return True
    meta = fm.get("metadata")
    return isinstance(meta, dict) and meta.get(HARNESS_ENTRY_KEY) == HARNESS_ENTRY_VALUE


def is_entry_skill(name, fm=None):
    """validate-harness.py の例外分岐に使う判定(名前規則 または frontmatter の印)。"""
    return is_entry_skill_name(name) or has_entry_marker(fm)


# ---------------------------------------------------------------- 読込

def read_frontmatter(path, rel=None):
    rel = rel or path
    if yaml is None:
        raise EntryError("PyYAML が無い(pip install -r requirements-dev.txt)")
    text = open(path, encoding="utf-8-sig").read()
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not m:
        raise EntryError(f"{rel}: frontmatter を読めない(先頭に --- で囲まれた YAML が必要)")
    try:
        fm = yaml.safe_load(m.group(1))
    except Exception as e:  # noqa: BLE001
        raise EntryError(f"{rel}: YAML parse error: {e}")
    if not isinstance(fm, dict):
        raise EntryError(f"{rel}: frontmatter がマップではない")
    return fm


def _yaml_single_quoted(s):
    return "'" + str(s).replace("'", "''") + "'"


# ---------------------------------------------------------------- 生成

def handoff_guidance(handoffs, session_word="チャット", command_word="スキル名"):
    """バインド先エージェントの handoffs を、ハンドオフボタンが無い状況向けの案内文に変換する
    (generate-adapters.py の handoff_guidance と同じ規則。Agent Host で入口スキルから役割を設定した場合、
    ピッカーで役割エージェントを選んでいなければハンドオフボタンは出ない)。

    send: false → 「新しい<チャット>で /<スキル名> を実行」の案内。
    send: true  → 同一チャット内で次エージェントの役割に切り替えて自動継続(ノンストップ設計を環境差で失わない)。
    """
    handoffs = handoffs or []
    sf = [h.get("label", h.get("agent", "")) for h in handoffs if not h.get("send")]
    st = [h.get("label", h.get("agent", "")) for h in handoffs if h.get("send")]
    if not st:
        return (f"ハンドオフボタンが出ない場合、フェーズ移行の案内は\n"
                f"   「新しい{session_word}で /<{command_word}> を実行」の形にしてください。")
    lines = ["ハンドオフボタンが出ない場合、ハンドオフは次のように読み替えてください。"]
    if sf:
        lines.append(f"   - `send: false` のハンドオフ({' / '.join(sf)}): フェーズ移行の案内を")
        lines.append(f"     「新しい{session_word}で /<{command_word}> を実行」の形にしてください。")
    lines.append(f"   - `send: true` のハンドオフ({' / '.join(st)}): このフェーズ完了後は止まらず、")
    lines.append(f"     同一{session_word}内で次エージェント(/<{command_word}> 相当)の役割に")
    lines.append(f"     切り替えて自動継続してください。新しい{session_word}の案内はしません")
    lines.append("     (会話が既に長い場合のみ `.github/harness/USAGE.md` のセッション分割表に従う)。")
    return "\n".join(lines)


def render_skill(base, desc, agent, handoffs):
    """入口スキル SKILL.md の全文(LF)。冪等: 同じ入力から常に同じ文字列を返す。"""
    prompt_rel = f".github/prompts/{base}.prompt.md"
    agent_rel = f".github/agents/{agent}.agent.md"
    note = handoff_guidance(handoffs)
    return f"""---
name: {base}
description: {_yaml_single_quoted(desc)}
user-invocable: true
disable-model-invocation: false
metadata:
  {HARNESS_ENTRY_KEY}: {HARNESS_ENTRY_VALUE}
  generated-from: {prompt_rel}
---

{GENERATED_MARK} {prompt_rel} + {agent_rel} by tools/generate-adapters.py (Copilot Agent Host 向け入口スキル。手で編集しない。再生成: python tools/generate-adapters.py) -->

このスキルは Copilot Agent Host 向けの薄い入口アダプタです。振る舞いの正は参照先にあり、
このファイル自体は振る舞いを持ちません(Local ハーネスでは同名の prompt file、Claude Code では
同名のスラッシュコマンドが同じ正を参照します。再監査 2026-09-09 CP-2 / A6-17)。

1. `{agent_rel}` を読み、その役割定義(役割・手順・`tools` / `agents` の範囲・`handoffs`)に
   従ってこの会話のロールを設定してください。Agent Host では prompt files の `agent:` バインド
   (D008)が効かないため、役割はこの本文の指示で設定します(指示層=劣化モード。
   エージェントピッカーで `{agent}` を選んでから実行すると `tools` / `agents` / `handoffs` が
   機械的にも効きます)。
2. その上で `{prompt_rel}` の本文の指示を実行してください。
3. 役割定義の中の `runSubagent` は、そのまま `.github/agents/` の同名サブエージェント
   (reviewer / task-worker / spec-critic)を呼びます。{note}
"""


def targets(root):
    """生成対象を [(rel, new_text)] で返す(prompt の base 名順)。正の欠落・不正は EntryError。"""
    out = []
    prompt_files = sorted(glob.glob(os.path.join(root, PROMPTS_DIR, "*.prompt.md")))
    agent_cache = {}
    for pf in prompt_files:
        base = os.path.basename(pf)[: -len(".prompt.md")]
        prompt_rel = f".github/prompts/{base}.prompt.md"
        fm = read_frontmatter(pf, prompt_rel)
        agent = fm.get("agent")
        if not agent:
            raise EntryError(f"{prompt_rel}: frontmatter に 'agent' がありません(プロンプトは必ずエージェントにバインドする)")
        desc = fm.get("description")
        if not isinstance(desc, str) or not desc.strip():
            raise EntryError(f"{prompt_rel}: description が無い(入口スキルの description になる)")
        if len(desc) > DESCRIPTION_MAX:
            raise EntryError(f"{prompt_rel}: description が {len(desc)} 文字(上限 {DESCRIPTION_MAX}。VS Code / agentskills.io)")
        if not SKILL_NAME_RE.match(base) or len(base) > 64:
            raise EntryError(f"{prompt_rel}: base 名 {base!r} はスキル名に使えない(小文字英数とハイフン、64 文字以内)")
        if not is_entry_skill_name(base):
            raise EntryError(f"{prompt_rel}: base 名 {base!r} が入口スキルの名前規則 ^\\d\\d- に合わない"
                             "(規則を変えるなら is_entry_skill_name と validate の例外を同時に変える)")
        if agent not in agent_cache:
            ap = os.path.join(root, AGENTS_DIR, f"{agent}.agent.md")
            if not os.path.exists(ap):
                raise EntryError(f"{prompt_rel}: agent {agent!r} の .github/agents/{agent}.agent.md が存在しない")
            agent_cache[agent] = read_frontmatter(ap, f".github/agents/{agent}.agent.md")
        handoffs = agent_cache[agent].get("handoffs") or []
        rel = f".github/skills/{base}/SKILL.md"
        out.append((rel, render_skill(base, desc, agent, handoffs)))
    return out


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


def _is_generated(path):
    try:
        return GENERATED_MARK in _read_text(path)
    except Exception:  # noqa: BLE001
        return False


def orphan_dirs(root, expected_bases):
    """prompt に対応しない入口スキル(名前規則 ^\\d\\d- に合致するフォルダ)を返す。"""
    out = []
    for sf in sorted(glob.glob(os.path.join(root, SKILLS_DIR, "*", "SKILL.md"))):
        name = os.path.basename(os.path.dirname(sf))
        if is_entry_skill_name(name) and name not in expected_bases:
            out.append(name)
    return out


def pointer_conflicts(root, bases):
    """作ってはいけない .claude/skills/<nn>-…/ のポインタ(スラッシュ名が .claude/commands と衝突)。"""
    return [b for b in bases if os.path.isdir(os.path.join(root, CLAUDE_SKILLS_DIR, b))]


def status(root):
    """(stale, orphans, missing, conflicts) を返す。stale は差分のある rel、missing は未生成の rel。"""
    tg = targets(root)
    bases = [rel.split("/")[2] for rel, _ in tg]
    stale, missing = [], []
    for rel, new in tg:
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            missing.append(rel)
        elif not _same_text(_read_text(path), new):
            stale.append(rel)
    return stale, orphan_dirs(root, set(bases)), missing, pointer_conflicts(root, bases)


def apply_all(root, log=print):
    """差分のある入口スキルだけ書き、生成マーカー付きの孤児フォルダを削除する。戻り値: 書いた数。"""
    written = 0
    tg = targets(root)
    for rel, new in tg:
        path = os.path.join(root, rel)
        cur = _read_text(path) if os.path.exists(path) else None
        if not _same_text(cur, new):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new)
            written += 1
            log(rel)
    for name in orphan_dirs(root, {rel.split("/")[2] for rel, _ in tg}):
        d = os.path.join(root, SKILLS_DIR, name)
        if _is_generated(os.path.join(d, "SKILL.md")):
            shutil.rmtree(d)
            log(f".github/skills/{name}/ (孤児の入口スキルを削除)")
        else:
            print(f"WARN: .github/skills/{name}/ は入口スキルの名前規則に合うが生成マーカーが無い(手書き? 対応する prompt も無い)")
    return written


def budget(root):
    """全 .github/skills の name / description 文字数(Agent Host は起動時に name+description を全件ロードする)。"""
    rows = []
    for sf in sorted(glob.glob(os.path.join(root, SKILLS_DIR, "*", "SKILL.md"))):
        name = os.path.basename(os.path.dirname(sf))
        try:
            fm = read_frontmatter(sf)
        except EntryError:
            fm = {}
        desc = str(fm.get("description") or "")
        rows.append({"name": name, "entry": is_entry_skill(name, fm), "name_chars": len(name), "desc_chars": len(desc)})
    entry = [r for r in rows if r["entry"]]
    proc = [r for r in rows if not r["entry"]]

    def tot(rs):
        return {"skills": len(rs), "name_chars": sum(r["name_chars"] for r in rs),
                "desc_chars": sum(r["desc_chars"] for r in rs),
                "max_desc": max((r["desc_chars"] for r in rs), default=0)}

    return {"rows": rows, "entry": tot(entry), "procedural": tot(proc), "all": tot(rows)}


# ---------------------------------------------------------------- CLI

def cmd_check(root):
    try:
        stale, orphans, missing, conflicts = status(root)
        n = len(targets(root))
    except EntryError as e:
        print("ERROR:", e)
        return 2
    for rel in stale:
        print(f"STALE: {rel} (python tools/generate-adapters.py で再生成)")
    for rel in missing:
        print(f"MISSING: {rel} (python tools/generate-adapters.py で生成)")
    for name in orphans:
        print(f"ORPHAN: .github/skills/{name}/ (対応する .github/prompts/{name}.prompt.md が無い。再生成で削除される)")
    for name in conflicts:
        print(f"CONFLICT: .claude/skills/{name}/ (入口スキルのポインタは作らない。/{name} が .claude/commands と衝突する)")
    bad = len(stale) + len(missing) + len(orphans) + len(conflicts)
    print(f"copilot-entry-skills --check: {'NG' if bad else 'OK'} (対象 {n} ファイル中 差分 {len(stale)} / 欠落 {len(missing)}"
          f" / 孤児 {len(orphans)} / ポインタ衝突 {len(conflicts)})")
    return 1 if bad else 0


def cmd_budget(root):
    b = budget(root)
    for r in b["rows"]:
        print(f"{'entry' if r['entry'] else 'skill'}  {r['name']:<28} name {r['name_chars']:>3}  desc {r['desc_chars']:>5}")
    for k in ("procedural", "entry", "all"):
        t = b[k]
        print(f"{k:<11} skills {t['skills']:>3}  name {t['name_chars']:>5}  desc {t['desc_chars']:>6}  max_desc {t['max_desc']:>5}"
              f"  name+desc {t['name_chars'] + t['desc_chars']:>6}")
    print(f"(description 上限 {DESCRIPTION_MAX} 文字/件。Agent Host は起動時に name+description を全件ロードする)")
    return 0


def _fake_root(tmp, handoffs_yaml="", desc="'進捗を確認する'"):
    os.makedirs(os.path.join(tmp, PROMPTS_DIR))
    os.makedirs(os.path.join(tmp, AGENTS_DIR))
    with open(os.path.join(tmp, PROMPTS_DIR, "01-x.prompt.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"---\nagent: a\ndescription: {desc}\n---\n\n本文\n")
    with open(os.path.join(tmp, AGENTS_DIR, "a.agent.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("---\ndescription: 'agent a'\ntools: ['read']\nagents: []\n" + handoffs_yaml + "---\n\n本文\n")


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

    # (1) 判定
    expect(is_entry_skill_name("00-start-project") and not is_entry_skill_name("gate-check") and not is_entry_skill_name("3d-model"),
           "is_entry_skill_name: ^\\d\\d- only")
    expect(is_entry_skill("x", {"metadata": {"harness-entry": "copilot"}}) and is_entry_skill("x", {"harness-entry": "copilot"})
           and not is_entry_skill("x", {"metadata": {"harness-entry": "claude"}}) and not is_entry_skill("x", None),
           "is_entry_skill: marker at top level or under metadata")
    # (2) handoff の読み替え文(generate-adapters と同じ規則)
    expect("「新しいチャットで /<スキル名> を実行」" in handoff_guidance([]), "handoff_guidance: no handoffs")
    g = handoff_guidance([{"agent": "b", "label": "戻る", "send": False}, {"agent": "c", "label": "進む", "send": True}])
    expect("`send: false` のハンドオフ(戻る)" in g and "`send: true` のハンドオフ(進む)" in g and "自動継続" in g,
           "handoff_guidance: send false/true both rendered")
    # (3) 描画: frontmatter・マーカー・冪等・YAML の引用符エスケープ
    txt = render_skill("01-x", "進捗を確認する(it's ok)", "a", [])
    expect(txt.startswith("---\nname: 01-x\ndescription: '進捗を確認する(it''s ok)'\nuser-invocable: true\ndisable-model-invocation: false\n"
                          "metadata:\n  harness-entry: copilot\n  generated-from: .github/prompts/01-x.prompt.md\n---\n"),
           "render_skill: frontmatter fields in order")
    expect(GENERATED_MARK in txt and ".github/agents/a.agent.md" in txt and ".github/prompts/01-x.prompt.md" in txt
           and "model: auto" not in txt, "render_skill: pointer body with marker, no model: auto")
    expect(render_skill("01-x", "d", "a", []) == render_skill("01-x", "d", "a", []), "render_skill: deterministic")
    if yaml is not None:
        m = re.match(r"^---\n(.*?)\n---\n", txt, re.DOTALL)
        fm = yaml.safe_load(m.group(1))
        expect(fm["description"] == "進捗を確認する(it's ok)" and fm["user-invocable"] is True
               and fm["disable-model-invocation"] is False and has_entry_marker(fm), "render_skill: frontmatter round-trips through YAML")
    else:
        print("SKIP: YAML round trip (PyYAML なし)")
    if yaml is None:
        print("SKIP: apply/check selftests (PyYAML なし)")
        print(f"copilot_entry_skills selftest: {passed} passed, {failed} failed")
        return 1 if failed else 0
    # (4) 生成・鮮度・孤児・ポインタ衝突(一時ディレクトリ)
    with tempfile.TemporaryDirectory() as tmp:
        _fake_root(tmp, "handoffs:\n  - agent: a\n    label: '次へ'\n    prompt: 'p'\n    send: true\n")
        tg = targets(tmp)
        expect([rel for rel, _ in tg] == [".github/skills/01-x/SKILL.md"], "targets: one entry skill per prompt")
        stale, orphans, missing, conflicts = status(tmp)
        expect(missing == [".github/skills/01-x/SKILL.md"] and not stale and not orphans and not conflicts, "status: missing before apply")
        expect(apply_all(tmp, log=lambda r: None) == 1, "apply_all: writes the missing skill")
        expect(apply_all(tmp, log=lambda r: None) == 0, "apply_all: idempotent")
        expect(status(tmp) == ([], [], [], []), "status: clean after apply")
        p = os.path.join(tmp, SKILLS_DIR, "01-x", "SKILL.md")
        with open(p, "a", encoding="utf-8") as f:
            f.write("手書きの追記\n")
        expect(status(tmp)[0] == [".github/skills/01-x/SKILL.md"], "status: hand edit detected as stale")
        apply_all(tmp, log=lambda r: None)
        generated = _read_text(p)  # 先に読む(open(p, "wb") は引数評価より先に中身を空にする)
        with open(p, "wb") as f:
            f.write(generated.replace("\n", "\r\n").encode("utf-8"))
        expect(status(tmp)[0] == [], "status: CRLF-only difference is not stale")
        with open(p, "wb") as f:
            f.write((chr(0xFEFF) + generated).encode("utf-8"))
        expect(status(tmp)[0] == [], "status: BOM is tolerated when comparing")
        expect("自動継続" in _read_text(p) and "`send: true` のハンドオフ(次へ)" in _read_text(p), "apply_all: handoff guidance from bound agent")
        # 孤児: マーカー付きは削除、手書きは残す
        os.makedirs(os.path.join(tmp, SKILLS_DIR, "02-old"))
        with open(os.path.join(tmp, SKILLS_DIR, "02-old", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: 02-old\ndescription: d\n---\n\n" + GENERATED_MARK + " x -->\n")
        os.makedirs(os.path.join(tmp, SKILLS_DIR, "03-hand"))
        with open(os.path.join(tmp, SKILLS_DIR, "03-hand", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: 03-hand\ndescription: d\n---\n\nhand written\n")
        os.makedirs(os.path.join(tmp, SKILLS_DIR, "gate-check"))
        with open(os.path.join(tmp, SKILLS_DIR, "gate-check", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: gate-check\ndescription: d\n---\n\nprocedural\n")
        expect(status(tmp)[1] == ["02-old", "03-hand"], "status: orphans = entry-named dirs without prompt (procedural skills ignored)")
        apply_all(tmp, log=lambda r: None)
        expect(not os.path.isdir(os.path.join(tmp, SKILLS_DIR, "02-old")) and os.path.isdir(os.path.join(tmp, SKILLS_DIR, "03-hand")),
               "apply_all: removes generated orphan, keeps hand-written dir")
        shutil.rmtree(os.path.join(tmp, SKILLS_DIR, "03-hand"))
        # ポインタ衝突
        os.makedirs(os.path.join(tmp, CLAUDE_SKILLS_DIR, "01-x"))
        expect(status(tmp)[3] == ["01-x"], "status: .claude/skills pointer for an entry skill is a conflict")
        expect(cmd_check(tmp) == 1, "cmd_check: conflict -> 1")
        shutil.rmtree(os.path.join(tmp, CLAUDE_SKILLS_DIR))
        expect(cmd_check(tmp) == 0, "cmd_check: clean -> 0")
        b = budget(tmp)
        expect(b["entry"]["skills"] == 1 and b["procedural"]["skills"] == 1 and b["all"]["desc_chars"] > 0, "budget: entry / procedural split")
    # (5) 正の不正
    with tempfile.TemporaryDirectory() as tmp:
        _fake_root(tmp, desc="'" + "x" * (DESCRIPTION_MAX + 1) + "'")
        try:
            targets(tmp)
            expect(False, "targets: description over limit -> EntryError")
        except EntryError:
            expect(True, "targets: description over limit -> EntryError")
    with tempfile.TemporaryDirectory() as tmp:
        _fake_root(tmp)
        os.remove(os.path.join(tmp, AGENTS_DIR, "a.agent.md"))
        try:
            targets(tmp)
            expect(False, "targets: missing bound agent -> EntryError")
        except EntryError:
            expect(True, "targets: missing bound agent -> EntryError")
    with tempfile.TemporaryDirectory() as tmp:
        _fake_root(tmp)
        with open(os.path.join(tmp, PROMPTS_DIR, "01-x.prompt.md"), "w", encoding="utf-8") as f:
            f.write("---\ndescription: 'd'\n---\n\n本文\n")
        try:
            targets(tmp)
            expect(False, "targets: prompt without agent -> EntryError")
        except EntryError:
            expect(True, "targets: prompt without agent -> EntryError")
        expect(cmd_check(tmp) == 2, "cmd_check: structural error -> 2")
    # (6) 実リポジトリ(存在すれば): prompt 数と入口スキル数が一致し、名前が規則に合う
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if glob.glob(os.path.join(root, PROMPTS_DIR, "*.prompt.md")):
        try:
            tg = targets(root)
            expect(len(tg) == len(glob.glob(os.path.join(root, PROMPTS_DIR, "*.prompt.md"))) and
                   all(is_entry_skill_name(rel.split("/")[2]) for rel, _ in tg), f"real repo: {len(tg)} entry skills enumerated")
        except EntryError as e:
            expect(False, f"real repo targets: {e}")
    print(f"copilot_entry_skills selftest: {passed} passed, {failed} failed")
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Copilot Agent Host 向け入口スキル(.github/skills/<nn>-<name>/)の生成・検査")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="入口スキルを再生成する(generate-adapters.py 第5節と同じ)")
    g.add_argument("--check", action="store_true", help="生成物が正とずれていないか(0=一致 / 1=差分・孤児・欠落・ポインタ衝突 / 2=エラー)")
    g.add_argument("--budget", action="store_true", help="全スキルの name / description 文字数を表示")
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
        if args.budget:
            return cmd_budget(root)
        if args.apply:
            n = apply_all(root, log=lambda rel: print("wrote:", rel))
            print(f"copilot-entry-skills: {n} ファイル更新")
            return 0
    except EntryError as e:
        print("ERROR:", e)
        return 2
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

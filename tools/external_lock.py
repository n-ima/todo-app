#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部由来の Skill / プラグイン / MCP のロック(.github/harness/external-lock.json)の読み手と検査。

codex 監査 2026-08-31 H-09 / IA-20260831-11(外部 Skill の exact revision・hash・license・local patch を lock する)、
H-02 / IA-20260831-04(Playwright MCP の @latest 禁止)の実装。AGENTS.md「外部 Skill・プラグイン・MCP は出所確認・
版固定・導入前レビュー」と skill-authoring スキルの規則を、指示ではなく機械検査にする。

ロックの形(schema external-lock/1。1 エントリ = 取り込んだ外部物 1 つ):
    {"schema": "external-lock/1", "as_of": "YYYY-MM-DD", "self": "<owner>/<repo>",
     "entries": [{"name", "kind"(skill|plugin|mcp|action|other), "source"(owner/repo か npm:<pkg>), "source_url",
                  "revision"(commit SHA。npm は gitHead) と/または "version"(固定版。浮動は不可),
                  "sha256"(取得物のダイジェスト。取れなければ null と sha256_note), "integrity"(npm dist.integrity 等。任意),
                  "license"(SPDX), "local_path"(取り込んだローカルファイル。任意), "local_patch"(bool),
                  "local_sha256"(local_path の現在のダイジェスト。--refresh で更新), "references"([...] この
                  エントリが正当化する参照トークン。パッケージ名 / owner/repo)}],
     "ignore": [{"reference": "<token>", "reason": "..."}]}  # 走査で拾うが外部 Skill/MCP ではないもの

検査(validate-harness.py (o) が check() を呼ぶ。exit code は --check):
  ERROR  ロックが無い(外部参照があるのに)・schema/必須欄の不備・エントリの version が浮動(latest/next/canary/main/
         master/*/^/~ …)・走査対象ファイルの外部参照がロックに無い・参照の版がロックの version と違う・
         参照が版なし(npx <pkg> のように固定されていない)・参照が浮動版・.claude/skills/.system/ のスキルが未ロック
  WARN   local_path のダイジェストがロックの local_sha256 と違う(local patch の追跡。--refresh で更新)
  INFO   sha256 が null のエントリ(取得元は open issue)・ロック済み参照の件数

走査対象(指示層=実行コードと同等の信頼境界にある面。説明文書 README / harness/*.md と docs/ は対象外):
  .github/skills/**/SKILL.md  .github/agents/*.md  .github/prompts/*.md  .github/instructions/*.md
  .claude/skills/**/SKILL.md  .claude/agents/*.md  .claude/commands/*.md  .agents/workflows/*.md
  .mcp.json  .vscode/mcp.json  .claude-plugin/plugin.json  plugin.json  .claude/skills/.system/*/SKILL.md
拾う参照:
  (1) 実行子付きパッケージ  npx|bunx|pnpm dlx|yarn dlx|uvx|pipx run <pkg>[@<ver>]
  (2) JSON/設定内の "<pkg>@<ver>"(scoped か、<ver> が版らしい文字列)
  (3) GitHub の owner/repo(`owner/repo` のバッククォート囲み、または github.com/owner/repo URL。
      ローカルパス(docs/… tools/… や実在するパス)・自分自身(self)は除外)

使い方:
    python tools/external_lock.py --check      # 検査(ERROR があれば exit 1)
    python tools/external_lock.py --refresh    # local_path の local_sha256 を再計算して書き戻す
    python tools/external_lock.py --print      # 参照の走査結果を表示
    python tools/external_lock.py --selftest
標準ライブラリのみ(配布先でも PyYAML 無しで動く)。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys

LOCK_REL = os.path.join(".github", "harness", "external-lock.json")
SCHEMA = "external-lock/1"
KINDS = ("skill", "plugin", "mcp", "action", "other")
FLOATING_WORDS = {"latest", "next", "canary", "main", "master", "dev", "nightly", "head", "*", "x"}
SCAN_GLOBS = [
    os.path.join(".github", "skills", "*", "SKILL.md"),
    os.path.join(".github", "skills", "*", "*", "SKILL.md"),
    os.path.join(".github", "agents", "*.md"),
    os.path.join(".github", "prompts", "*.md"),
    os.path.join(".github", "instructions", "*.md"),
    os.path.join(".claude", "skills", "*", "SKILL.md"),
    os.path.join(".claude", "agents", "*.md"),
    os.path.join(".claude", "commands", "*.md"),
    os.path.join(".agents", "workflows", "*.md"),
    ".mcp.json",
    os.path.join(".vscode", "mcp.json"),
    os.path.join(".claude-plugin", "plugin.json"),
    "plugin.json",
]
SYSTEM_SKILLS_GLOB = os.path.join(".claude", "skills", ".system", "*", "SKILL.md")
# owner/repo のバッククォート囲みのうち、ローカルパスとして読めるもの(owner がリポジトリ内のディレクトリ名)は除外
LOCAL_DIRS = {"docs", "tools", "scripts", "requirements", "adr", "interfaces", "app", "src", "evaluation", "audits",
              "references", "assets", "tests", "test", "ui", "logs", "hooks", "skills", "agents", "prompts",
              "instructions", "harness", "copilot", "workflows", "commands", "rules", "lib", "bin", "build", "dist",
              "node_modules", "templates", "config", "data", "public", "static", "detailed-design", "usage"}
PATH_EXT_RE = re.compile(r"\.(md|py|js|ts|json|ya?ml|sh|ps1|html|txt|csv|toml|ini|cfg|xml|svg|png)$", re.I)

RUNNER_RE = re.compile(
    r"\b(?:npx|bunx|pnpm\s+dlx|yarn\s+dlx|uvx|pipx\s+run)\s+(?:-[-\w]+\s+)*"
    r"(?P<pkg>@?[A-Za-z0-9][\w.-]*(?:/[\w.-]+)?)(?:@(?P<ver>[^\s\"'`\]\),]+))?")
JSON_ARG_RE = re.compile(r"[\"'](?P<pkg>@?[A-Za-z0-9][\w.-]*(?:/[\w.-]+)?)@(?P<ver>[^\"'\s]+)[\"']")
SLUG_TICK_RE = re.compile(r"`(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/(?P<repo>[A-Za-z0-9_.-]+)`")
SLUG_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:github\.com|raw\.githubusercontent\.com)/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/(?P<repo>[A-Za-z0-9_.-]+?)(?=[/#?\s\)\]`\"'<>]|$)")


class LockError(Exception):
    pass


def is_floating(ver):
    """浮動版か(latest / next / main / * / ^1 / ~1 / >=1 / 1.x / 空)。"""
    if ver is None:
        return True
    v = str(ver).strip().strip("\"'")
    if not v:
        return True
    low = v.lower()
    if low in FLOATING_WORDS or low.startswith(("^", "~", ">", "<", "=")) or ".x" in low or "*" in low:
        return True
    return False


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_lock(root):
    """ロックを読む。無ければ None、壊れていれば LockError。"""
    path = os.path.join(root, LOCK_REL)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception as e:  # noqa: BLE001
        raise LockError(f"{LOCK_REL}: JSON として読めない({e})")
    if not isinstance(data, dict):
        raise LockError(f"{LOCK_REL}: オブジェクトでない")
    return data


def validate_structure(lock):
    """schema / 必須欄 / 浮動版の構造検査。問題文字列のリストを返す(空なら OK)。"""
    problems = []
    if lock.get("schema") != SCHEMA:
        problems.append(f"schema が {SCHEMA!r} でない({lock.get('schema')!r})")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(lock.get("as_of") or "")):
        problems.append("as_of が YYYY-MM-DD でない")
    entries = lock.get("entries")
    if not isinstance(entries, list):
        problems.append("entries が配列でない")
        entries = []
    seen_refs = {}
    for i, e in enumerate(entries):
        tag = f"entries[{i}]"
        if not isinstance(e, dict):
            problems.append(f"{tag}: オブジェクトでない")
            continue
        tag = f"entries[{i}] {e.get('name') or ''}".rstrip()
        for k in ("name", "kind", "source", "license", "references"):
            if not e.get(k):
                problems.append(f"{tag}: {k} が無い")
        if e.get("kind") not in KINDS:
            problems.append(f"{tag}: kind {e.get('kind')!r} は {KINDS} のいずれか")
        if not e.get("revision") and not e.get("version"):
            problems.append(f"{tag}: revision(commit SHA)か version(固定版)のどちらかが必須")
        if e.get("revision") and not re.fullmatch(r"[0-9a-f]{7,64}", str(e["revision"])):
            problems.append(f"{tag}: revision {e['revision']!r} が commit SHA(16 進 7〜64 桁)でない")
        if e.get("version") is not None and is_floating(e.get("version")):
            problems.append(f"{tag}: version {e.get('version')!r} が浮動(固定版に直す)")
        if "local_patch" not in e or not isinstance(e.get("local_patch"), bool):
            problems.append(f"{tag}: local_patch(true/false)が無い")
        if "sha256" not in e:
            problems.append(f"{tag}: sha256 欄が無い(取れないなら null と sha256_note)")
        elif e.get("sha256") is None and not e.get("sha256_note"):
            problems.append(f"{tag}: sha256 が null なのに sha256_note(理由・取得元)が無い")
        refs = e.get("references") if isinstance(e.get("references"), list) else []
        for r in refs:
            if not isinstance(r, str) or not r:
                problems.append(f"{tag}: references に空でない文字列以外がある")
                continue
            if r in seen_refs and seen_refs[r] != i:
                problems.append(f"{tag}: references {r!r} が entries[{seen_refs[r]}] と重複")
            seen_refs[r] = i
    ign = lock.get("ignore", [])
    if not isinstance(ign, list):
        problems.append("ignore が配列でない")
    else:
        for j, g in enumerate(ign):
            if not isinstance(g, dict) or not g.get("reference") or not g.get("reason"):
                problems.append(f"ignore[{j}]: reference と reason が必要")
    return problems


def _norm_slug(owner, repo):
    repo = repo[:-4] if repo.lower().endswith(".git") else repo
    return f"{owner}/{repo}"


def scan_references(root):
    """走査対象ファイルから外部参照を集める。戻り値: [{file, line, token, version, kind}]
    kind: 'package'(実行子付き / JSON 内) / 'repo'(owner/repo)。version は無ければ None。"""
    refs = []
    seen_files = set()
    for pat in SCAN_GLOBS:
        for fp in sorted(glob.glob(os.path.join(root, pat))):
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            if rel in seen_files or not os.path.isfile(fp) or "/.system/" in rel:
                continue
            seen_files.add(rel)
            try:
                lines = open(fp, encoding="utf-8-sig", errors="replace").read().splitlines()
            except Exception:  # noqa: BLE001
                continue
            for ln_no, line in enumerate(lines, 1):
                for m in RUNNER_RE.finditer(line):
                    pkg = m.group("pkg")
                    if pkg in (".", "..") or pkg.startswith(("./", "../")) or ":" in pkg:
                        continue
                    refs.append({"file": rel, "line": ln_no, "token": pkg, "version": m.group("ver"), "kind": "package"})
                for m in JSON_ARG_RE.finditer(line):
                    pkg, ver = m.group("pkg"), m.group("ver")
                    if not (pkg.startswith("@") or re.match(r"^\d", ver) or is_floating(ver)):
                        continue  # メールアドレス等(alice@example.com)は版らしくないので拾わない
                    if any(r["file"] == rel and r["line"] == ln_no and r["token"] == pkg for r in refs):
                        continue
                    refs.append({"file": rel, "line": ln_no, "token": pkg, "version": ver, "kind": "package"})
                for rx in (SLUG_TICK_RE, SLUG_URL_RE):
                    for m in rx.finditer(line):
                        owner, repo = m.group("owner"), m.group("repo")
                        if owner.lower() in LOCAL_DIRS or owner.startswith("."):
                            continue
                        if rx is SLUG_TICK_RE and repo.lower() in LOCAL_DIRS:
                            continue  # `client/dist` のような相対パス(2 段目が一般的なディレクトリ名。3 実プロジェクトで誤検出)
                        if PATH_EXT_RE.search(repo) and rx is SLUG_TICK_RE:
                            continue
                        if os.path.exists(os.path.join(root, owner)):
                            continue  # リポジトリ内のパス(ディレクトリ名 / ファイル名)
                        slug = _norm_slug(owner, repo)
                        if any(r["file"] == rel and r["line"] == ln_no and r["token"] == slug for r in refs):
                            continue
                        refs.append({"file": rel, "line": ln_no, "token": slug, "version": None, "kind": "repo"})
    return refs


def system_skills(root):
    """Claude Code のローカル生成スキル(.claude/skills/.system/*/SKILL.md)。あれば local_path でロック済みであること。"""
    out = []
    for fp in sorted(glob.glob(os.path.join(root, SYSTEM_SKILLS_GLOB))):
        out.append(os.path.relpath(fp, root).replace(os.sep, "/"))
    return out


def self_slugs(root, lock):
    """自分自身の owner/repo(ロックの self と plugin.json の repository)。"""
    slugs = set()
    if lock and isinstance(lock.get("self"), str):
        slugs.add(lock["self"])
    try:
        pj = json.load(open(os.path.join(root, "plugin.json"), encoding="utf-8-sig"))
        m = SLUG_URL_RE.search(str(pj.get("repository") or ""))
        if m:
            slugs.add(_norm_slug(m.group("owner"), m.group("repo")))
    except Exception:  # noqa: BLE001
        pass
    return slugs


def check(root):
    """(errors, warnings, infos) を返す。validate-harness.py (o) はこれをそのまま取り込む。"""
    errors, warnings, infos = [], [], []
    try:
        lock = load_lock(root)
    except LockError as e:
        return [str(e)], [], []
    refs = scan_references(root)
    sys_skills = system_skills(root)
    if lock is None:
        if refs or sys_skills:
            errors.append(f"{LOCK_REL} が無い(外部参照 {len(refs)} 件・.system スキル {len(sys_skills)} 件があるのに"
                          "ロックが無い。IA-20260831-11)")
        else:
            infos.append(f"{LOCK_REL} は無いが外部参照も無い(取り込み時に作る)")
        return errors, warnings, infos
    for p in validate_structure(lock):
        errors.append(f"{LOCK_REL}: {p}")
    entries = [e for e in (lock.get("entries") or []) if isinstance(e, dict)]
    ref_to_entry = {}
    for e in entries:
        for r in (e.get("references") or []):
            if isinstance(r, str):
                ref_to_entry[r] = e
    ignored = {g.get("reference"): g.get("reason") for g in (lock.get("ignore") or []) if isinstance(g, dict)}
    selfs = self_slugs(root, lock)
    locked_hits = 0
    for r in refs:
        tok = r["token"]
        where = f"{r['file']}:{r['line']}"
        if tok in selfs or tok in ignored:
            continue
        e = ref_to_entry.get(tok)
        if e is None:
            errors.append(f"{where}: 外部参照 {tok!r} が {LOCK_REL} に無い(取り込み時にロックへ登録する。"
                          "IA-20260831-11 / skill-authoring スキル)")
            continue
        locked_hits += 1
        if r["kind"] == "package":
            if r["version"] is None:
                errors.append(f"{where}: {tok!r} が版なしで参照されている(ロックの version {e.get('version')!r} を明示する)")
            elif is_floating(r["version"]):
                errors.append(f"{where}: {tok!r} の版 {r['version']!r} が浮動(ロックの version {e.get('version')!r} に固定する。"
                              "IA-20260831-04)")
            elif e.get("version") and str(r["version"]).strip("\"'") != str(e["version"]):
                errors.append(f"{where}: {tok!r} の版 {r['version']!r} がロックの version {e['version']!r} と違う"
                              "(ロックを更新してから本文を直す)")
    for rel in sys_skills:
        if not any(e.get("local_path") == rel for e in entries):
            errors.append(f"{rel}: .claude/skills/.system/ のスキルが {LOCK_REL} に無い(local_path で登録する)")
    for e in entries:
        lp = e.get("local_path")
        if not lp:
            continue
        fp = os.path.join(root, lp.replace("/", os.sep))
        if not os.path.exists(fp):
            warnings.append(f"{LOCK_REL}: {e.get('name')} の local_path {lp} が存在しない")
            continue
        if e.get("local_sha256"):
            cur = sha256_file(fp)
            if cur != e["local_sha256"]:
                warnings.append(f"{LOCK_REL}: {e.get('name')} の local_path {lp} のダイジェストがロックと違う"
                                f"(現在 {cur[:12]}… / ロック {str(e['local_sha256'])[:12]}…。local patch を変えたなら "
                                "python tools/external_lock.py --refresh)")
        else:
            warnings.append(f"{LOCK_REL}: {e.get('name')} に local_sha256 が無い(python tools/external_lock.py --refresh)")
    no_sha = [e.get("name") for e in entries if e.get("sha256") is None]
    if no_sha:
        infos.append(f"{LOCK_REL}: sha256 未取得のエントリ {len(no_sha)} 件({', '.join(map(str, no_sha))}。"
                     "取得元と手順は DECISIONS の open。integrity / revision で代替)")
    infos.append(f"external-lock: エントリ {len(entries)} 件 / 走査した参照 {len(refs)} 件(ロック済み {locked_hits}・"
                 f"自分自身 {sum(1 for r in refs if r['token'] in selfs)}・ignore {sum(1 for r in refs if r['token'] in ignored)})")
    return errors, warnings, infos


def refresh(root):
    """local_path を持つエントリの local_sha256 を再計算してロックを書き戻す。更新した名前のリストを返す。"""
    lock = load_lock(root)
    if lock is None:
        raise LockError(f"{LOCK_REL} が無い")
    changed = []
    for e in lock.get("entries") or []:
        if not isinstance(e, dict) or not e.get("local_path"):
            continue
        fp = os.path.join(root, str(e["local_path"]).replace("/", os.sep))
        if not os.path.exists(fp):
            continue
        cur = sha256_file(fp)
        if e.get("local_sha256") != cur:
            e["local_sha256"] = cur
            changed.append(str(e.get("name")))
    path = os.path.join(root, LOCK_REL)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(lock, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return changed


# ---------------------------------------------------------------- selftest

def selftest():
    import shutil
    import tempfile
    ok = True
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    def chk(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    tmp = tempfile.mkdtemp(prefix="external-lock-selftest-")
    try:
        root = os.path.join(tmp, "proj")
        for d in (".github/skills/ext-skill", ".github/skills/test-case-design", ".github/harness", ".github/agents",
                  "tools", "docs/01-requirements"):
            os.makedirs(os.path.join(root, d.replace("/", os.sep)))
        open(os.path.join(root, "tools", "x.py"), "w").close()
        skill = os.path.join(root, ".github", "skills", "ext-skill", "SKILL.md")
        with open(skill, "w", encoding="utf-8") as fh:
            fh.write("---\nname: ext-skill\ndescription: x\n---\n出典: `acme/skills`（`skills/x/SKILL.md`）。`tools/x.py` と `docs/a.md` はローカル。`US-/FR-nnn` は ID。\n")
        tcd = os.path.join(root, ".github", "skills", "test-case-design", "SKILL.md")
        with open(tcd, "w", encoding="utf-8") as fh:
            fh.write('---\nname: test-case-design\ndescription: x\n---\n"args": ["@playwright/mcp@0.0.80"]\nnpx playwright test\nnpx . と npx ./bin は除外\n')
        with open(os.path.join(root, ".github", "agents", "design.agent.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ndescription: d\n---\nhttps://github.com/acme/skills/blob/main/README.md と https://github.com/me/self を参照。alice@example.com。\n")
        with open(os.path.join(root, "docs", "01-requirements", "x.md"), "w", encoding="utf-8") as fh:
            fh.write("`vercel/next.js` は docs なので走査しない\n")
        with open(os.path.join(root, "plugin.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "p", "repository": "https://github.com/me/self"}, fh)

        # 1. ロック無し + 外部参照あり → ERROR
        e, w, i = check(root)
        chk("ロック無しで外部参照があれば ERROR", any("が無い(外部参照" in x for x in e), str(e))
        # 2. 走査: パッケージ(JSON 内・実行子付き)と owner/repo(バッククォート・URL)を拾い、ローカルパス・ID・メールは拾わない
        refs = scan_references(root)
        toks = {(r["token"], r["version"]) for r in refs}
        chk("scan: @playwright/mcp@0.0.80(JSON 内)を拾う", ("@playwright/mcp", "0.0.80") in toks, str(toks))
        chk("scan: npx playwright(版なし)を拾う", ("playwright", None) in toks, str(toks))
        chk("scan: `acme/skills` と URL の acme/skills を拾う", ("acme/skills", None) in toks, str(toks))
        chk("scan: me/self(URL)を拾う(自分自身の除外は check 側)", ("me/self", None) in toks, str(toks))
        chk("scan: tools/x.py・docs/a.md・US-/FR-nnn・npx .・メールは拾わない",
            not any(t[0] in ("tools/x.py", "docs/a.md", "US-/FR-nnn", ".", "./bin", "alice") for t in toks), str(toks))
        chk("scan: docs/ 配下は走査しない", not any(t[0] == "vercel/next.js" for t in toks), str(toks))
        # 3. ロックあり(整合) → ERROR 0、ignore と self が効く、local_sha256 一致
        lock = {"schema": SCHEMA, "as_of": "2026-09-17", "self": "me/self",
                "entries": [
                    {"name": "acme skill", "kind": "skill", "source": "acme/skills", "source_url": "https://github.com/acme/skills",
                     "revision": "0123456789abcdef0123456789abcdef01234567", "sha256": None, "sha256_note": "未取得",
                     "license": "MIT", "local_path": ".github/skills/ext-skill/SKILL.md", "local_patch": True,
                     "local_sha256": sha256_file(skill), "references": ["acme/skills"]},
                    {"name": "@playwright/mcp", "kind": "mcp", "source": "npm:@playwright/mcp", "version": "0.0.80",
                     "sha256": None, "sha256_note": "tarball 未取得", "integrity": "sha512-x", "license": "Apache-2.0",
                     "local_patch": False, "references": ["@playwright/mcp"]}],
                "ignore": [{"reference": "playwright", "reason": "プロジェクト devDependency"}]}
        with open(os.path.join(root, LOCK_REL.replace("/", os.sep)), "w", encoding="utf-8") as fh:
            json.dump(lock, fh, ensure_ascii=False, indent=2)
        e, w, i = check(root)
        chk("整合するロック → ERROR 0 / WARN 0", not e and not w, str(e + w))
        chk("INFO に sha256 未取得と件数が出る", any("sha256 未取得" in x for x in i) and any("external-lock:" in x for x in i), str(i))
        # 4. 浮動版・版違い・版なし・未登録 → ERROR
        # 浮動版のリテラルは validate (o) の浮動参照検査に拾われないよう実行時に組み立てる
        floating = "@playwright/mcp@" + "latest"
        with open(tcd, "w", encoding="utf-8") as fh:
            fh.write('"args": ["' + floating + '"]\nnpx @playwright/mcp\nnpx -y @playwright/mcp@0.0.79\nuvx some-mcp@1.2.3\n')
        e, w, i = check(root)
        chk("@latest → ERROR(浮動)", any("浮動" in x and "@playwright/mcp" in x for x in e), str(e))
        chk("版なし npx @playwright/mcp → ERROR", any("版なし" in x for x in e), str(e))
        chk("版違い 0.0.79 → ERROR", any("ロックの version" in x and "0.0.79" in x for x in e), str(e))
        chk("ロックに無い some-mcp → ERROR", any("some-mcp" in x and "に無い" in x for x in e), str(e))
        # 5. 構造検査: 浮動 version / revision 不正 / 欄欠落 / references 重複
        bad = {"schema": "x", "as_of": "bad", "entries": [
            {"name": "a", "kind": "nope", "source": "s", "version": "^1.0", "sha256": None, "license": "MIT", "references": ["r"]},
            {"name": "b", "kind": "skill", "source": "s", "revision": "zzz", "sha256": None, "sha256_note": "n", "license": "MIT",
             "local_patch": False, "references": ["r"]}], "ignore": [{"reference": "q"}]}
        p = validate_structure(bad)
        chk("構造検査: schema/as_of/kind/浮動 version/local_patch 欠落/sha256_note 欠落/revision 不正/references 重複/ignore reason 欠落",
            len([x for x in p if "schema" in x]) == 1 and any("as_of" in x for x in p) and any("kind" in x for x in p)
            and any("浮動" in x for x in p) and any("local_patch" in x for x in p) and any("sha256_note" in x for x in p)
            and any("commit SHA" in x for x in p) and any("重複" in x for x in p) and any("ignore[0]" in x for x in p), str(p))
        chk("is_floating: latest/next/main/*/^/~/1.x/空は浮動、0.0.80 と v1.2.3 は固定",
            all(is_floating(v) for v in ("latest", "next", "main", "*", "^1.0", "~1", "1.x", "", None))
            and not is_floating("0.0.80") and not is_floating("v1.2.3"))
        # 6. local_sha256 のずれ → WARN、--refresh で解消
        with open(tcd, "w", encoding="utf-8") as fh:
            fh.write('"args": ["@playwright/mcp@0.0.80"]\n')
        with open(skill, "a", encoding="utf-8") as fh:
            fh.write("patched\n")
        e, w, i = check(root)
        chk("local patch を変えるとダイジェスト WARN", not e and any("ダイジェストがロックと違う" in x for x in w), str(e + w))
        changed = refresh(root)
        e, w, i = check(root)
        chk("--refresh で local_sha256 を更新 → WARN 0", changed == ["acme skill"] and not w, str((changed, w)))
        # 7. .system スキルの未ロック → ERROR、local_path 登録で解消
        sysd = os.path.join(root, ".claude", "skills", ".system", "vendor-skill")
        os.makedirs(sysd)
        with open(os.path.join(sysd, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: vendor-skill\ndescription: v\n---\nbody\n")
        e, w, i = check(root)
        chk(".claude/skills/.system の未ロックスキル → ERROR", any(".system/vendor-skill/SKILL.md" in x for x in e), str(e))
        lock2 = load_lock(root)
        lock2["entries"].append({"name": "vendor-skill", "kind": "skill", "source": "vendor/skills", "revision": "abcdef0",
                                 "sha256": None, "sha256_note": "n", "license": "MIT", "local_patch": False,
                                 "local_path": ".claude/skills/.system/vendor-skill/SKILL.md", "references": ["vendor/skills"]})
        with open(os.path.join(root, LOCK_REL.replace("/", os.sep)), "w", encoding="utf-8") as fh:
            json.dump(lock2, fh, ensure_ascii=False)
        refresh(root)
        e, w, i = check(root)
        chk("local_path で登録すれば ERROR 0 / WARN 0", not e and not w, str(e + w))
        # 8. 壊れたロック → ERROR 1 件で止まる
        with open(os.path.join(root, LOCK_REL.replace("/", os.sep)), "w", encoding="utf-8") as fh:
            fh.write("{broken")
        e, w, i = check(root)
        chk("壊れたロック → JSON エラー 1 件", len(e) == 1 and "JSON として読めない" in e[0], str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "--selftest" in argv:
        return selftest()
    if "--refresh" in argv:
        try:
            changed = refresh(root)
        except LockError as e:
            print(f"ERROR: {e}")
            return 1
        print("updated local_sha256: " + (", ".join(changed) if changed else "(no change)"))
        return 0
    if "--print" in argv:
        for r in scan_references(root):
            print(f"{r['file']}:{r['line']}: {r['kind']} {r['token']}" + (f"@{r['version']}" if r["version"] else ""))
        return 0
    errors, warnings, infos = check(root)
    for x in errors:
        print("ERROR:", x)
    for x in warnings:
        print("WARN:", x)
    for x in infos:
        print("INFO:", x)
    print(f"external-lock: errors {len(errors)} / warnings {len(warnings)}")
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main(sys.argv[1:]))

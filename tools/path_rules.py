"""パス限定ルールの生成(第3波 A6-19b(4) / A7-M-8。再監査 2026-08-31 §6「.claude/rules/ 未導入」)。

正は Copilot のパス限定指示 `.github/instructions/<name>.instructions.md`(frontmatter `applyTo:` のグロブ。
VS Code Copilot が該当ファイルを扱うときだけ読む)。Claude Code の同じ仕組みは `.claude/rules/<name>.md`
(frontmatter `paths:`。公式 memory 文書 2026-09-17 取得: 「Path-scoped rules trigger when Claude reads files
matching the pattern」「Rules without a paths field are loaded unconditionally」)で、本モジュールが正から
写す**生成物**にする(手書きの rule は置かない=正が二重になる)。両ホストとも「該当ファイルを読むときだけ
載る」ため、AGENTS.md の常駐予算(200 行)の外に出せる規範の置き場になる。

変換規則:
- `applyTo: "docs/**"`(文字列。カンマ区切りで複数可)/ `applyTo: ["a/**", "b/**"]`(配列)→ `paths:` の配列。
  グロブは両者とも `**` / `*` / `{a,b}` の同じ書式(公式の表と同じ)。
- `applyTo: "**"`(全ファイル)は常駐相当なので `paths:` を付けずに書く(公式: paths 無しは起動時ロード)。
  常駐予算に入るため validate (x) が WARN する。
- 本文はそのまま写し、先頭に生成マーカー(`<!-- generated from … -->`)を置く(公式: ブロック HTML コメントは
  文脈へ注入する前に剥がされる=トークンを使わない。rules での挙動は未検証)。

使い方: python tools/generate-adapters.py(第7節)/ --check / --selftest。validate-harness.py (x) が
status() で鮮度・未生成・孤児(正の無い .claude/rules/*.md)を ERROR にする。
"""
import glob
import os
import re
import sys

SOURCE_DIR = os.path.join(".github", "instructions")
TARGET_DIR = os.path.join(".claude", "rules")
SOURCE_SUFFIX = ".instructions.md"
GENERATED_MARK = "<!-- generated from"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RuleError(Exception):
    """正(.github/instructions)の構造エラー。生成を止める。"""


def _read_text(path):
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8")


def _same_text(cur, new):
    if cur is None:
        return False
    return cur.replace("\r\n", "\n") == new.replace("\r\n", "\n")


def parse_source(text, rel):
    """(applyTo のグロブ配列, 本文) を返す。frontmatter は PyYAML に依存せず applyTo だけを読む
    (配布先の validate / doctor が PyYAML 無しでも動くように platform_requirements と同じ方針)。"""
    text = text.replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        raise RuleError(f"{rel}: frontmatter が無い(applyTo: が必要)")
    fm, body = m.group(1), text[m.end():]
    am = re.search(r"^applyTo:[ \t]*(.*)$", fm, re.M)
    if not am:
        raise RuleError(f"{rel}: frontmatter に applyTo: が無い")
    raw = am.group(1).strip()
    globs = []
    if raw.startswith("["):
        inner = raw.strip("[]")
        globs = [g.strip().strip("'\"") for g in inner.split(",")]
    elif raw:
        globs = [g.strip() for g in raw.strip("'\"").split(",")]
    else:
        # ブロック形式の配列(次行以降の "- ...")
        lines = fm.splitlines()
        idx = next(i for i, ln in enumerate(lines) if ln.startswith("applyTo:"))
        for ln in lines[idx + 1:]:
            if re.match(r"^\s*-\s+", ln):
                globs.append(re.sub(r"^\s*-\s+", "", ln).strip().strip("'\""))
            else:
                break
    globs = [g for g in globs if g]
    if not globs:
        raise RuleError(f"{rel}: applyTo が空")
    for g in globs:
        if "\\" in g:
            raise RuleError(f"{rel}: applyTo {g!r} にバックスラッシュ(グロブは / 区切り)")
    return globs, body.lstrip("\n")


def render_rule(name, globs, body, source_rel):
    unconditional = any(g in ("**", "**/*") for g in globs)
    lines = []
    if not unconditional:
        lines.append("---")
        lines.append("paths:")
        for g in globs:
            lines.append(f'  - "{g}"')
        lines.append("---")
    lines.append(f"{GENERATED_MARK} {source_rel} by tools/generate-adapters.py 第7節 (tools/path_rules.py。"
                 "Claude Code のパス限定ルール。手で編集しない。正は applyTo 側。再生成: python tools/generate-adapters.py) -->")
    lines.append("")
    return "\n".join(lines) + "\n" + body.rstrip("\n") + "\n"


def targets(root):
    """[(生成先 rel, 内容, 正 rel, 常駐相当か)] を正の名前順で返す。"""
    out = []
    for sp in sorted(glob.glob(os.path.join(root, SOURCE_DIR, "*" + SOURCE_SUFFIX))):
        fname = os.path.basename(sp)
        name = fname[:-len(SOURCE_SUFFIX)]
        source_rel = f"{SOURCE_DIR}/{fname}".replace(os.sep, "/")
        if not NAME_RE.match(name):
            raise RuleError(f"{source_rel}: ファイル名 {name!r} が英数字・. _ - 以外を含む")
        globs, body = parse_source(_read_text(sp), source_rel)
        rel = f"{TARGET_DIR}/{name}.md".replace(os.sep, "/")
        out.append((rel, render_rule(name, globs, body, source_rel), source_rel,
                    any(g in ("**", "**/*") for g in globs)))
    return out


def orphans(root, expected_rels):
    """正の無い .claude/rules/*.md(手書き、または正を消した後の残骸)。再帰的に探す(公式は再帰探索)。"""
    out = []
    for p in sorted(glob.glob(os.path.join(root, TARGET_DIR, "**", "*.md"), recursive=True)):
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        if rel not in expected_rels:
            out.append(rel)
    return out


def status(root):
    """(stale, missing, orphans, resident) を返す。resident は paths 無し(常駐相当)の生成物 rel。"""
    tg = targets(root)
    stale, missing, resident = [], [], []
    for rel, new, _src, uncond in tg:
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            missing.append(rel)
        elif not _same_text(_read_text(path), new):
            stale.append(rel)
        if uncond:
            resident.append(rel)
    return stale, missing, orphans(root, {rel for rel, _, _, _ in tg}), resident


def apply_all(root, log=print):
    """差分のある生成物だけ書き、生成マーカー付きの孤児を削除する。戻り値: 書いた数。"""
    written = 0
    tg = targets(root)
    for rel, new, _src, _u in tg:
        path = os.path.join(root, rel)
        cur = _read_text(path) if os.path.exists(path) else None
        if not _same_text(cur, new):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new)
            written += 1
            log(rel)
    for rel in orphans(root, {r for r, _, _, _ in tg}):
        p = os.path.join(root, rel)
        try:
            generated = GENERATED_MARK in _read_text(p)
        except Exception:  # noqa: BLE001
            generated = False
        if generated:
            os.remove(p)
            log(f"{rel} (正の無い生成物を削除)")
        else:
            print(f"WARN: {rel} は手書きの rule(正は .github/instructions/<name>.instructions.md。移してから再生成する)")
    return written


def cmd_check(root):
    try:
        stale, missing, orph, resident = status(root)
    except RuleError as e:
        print(f"path-rules --check: ERROR {e}")
        return 2
    n = len(targets(root))
    for rel in stale:
        print(f"STALE: {rel} (python tools/generate-adapters.py で再生成)")
    for rel in missing:
        print(f"MISSING: {rel} (python tools/generate-adapters.py で生成)")
    for rel in orph:
        print(f"ORPHAN: {rel} (正 .github/instructions/<name>.instructions.md が無い)")
    ng = bool(stale or missing or orph)
    print(f"path-rules --check: {'NG' if ng else 'OK'} (対象 {n} ファイル中 差分 {len(stale)} / 欠落 {len(missing)} / 孤児 {len(orph)}"
          f" / 常駐相当 {len(resident)})")
    return 1 if ng else 0


def selftest():
    import shutil
    import tempfile
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    tmp = tempfile.mkdtemp(prefix="path-rules-selftest-")
    try:
        src = os.path.join(tmp, SOURCE_DIR)
        os.makedirs(src)

        def write_src(name, text):
            with open(os.path.join(src, name + SOURCE_SUFFIX), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

        write_src("docs", '---\napplyTo: "docs/**"\n---\n\n- 見出しを削らない。\n- 表を空欄で残さない。\n')
        tg = targets(tmp)
        check("targets: 1 件・生成先 .claude/rules/docs.md", len(tg) == 1 and tg[0][0] == ".claude/rules/docs.md", str(tg))
        body = tg[0][1]
        check("render: paths 配列と生成マーカーと本文", body.startswith('---\npaths:\n  - "docs/**"\n---\n') and GENERATED_MARK in body
              and "- 見出しを削らない。" in body, body[:120])
        st = status(tmp)
        check("status: 未生成は missing", st[1] == [".claude/rules/docs.md"] and not st[0] and not st[2], str(st))
        n = apply_all(tmp, log=lambda r: None)
        check("apply_all: 1 件書く", n == 1 and os.path.exists(os.path.join(tmp, TARGET_DIR, "docs.md")))
        check("apply 後は差分なし", status(tmp)[:3] == ([], [], []), str(status(tmp)))
        check("cmd_check: OK は 0", cmd_check(tmp) == 0)
        # 正を変えると stale
        write_src("docs", '---\napplyTo: "docs/**"\n---\n\n- 見出しを削らない。\n')
        check("正の変更 -> stale", status(tmp)[0] == [".claude/rules/docs.md"])
        check("cmd_check: stale は 1", cmd_check(tmp) == 1)
        apply_all(tmp, log=lambda r: None)
        # CRLF の生成物は一致扱い
        p = os.path.join(tmp, TARGET_DIR, "docs.md")
        with open(p, "rb") as f:
            raw = f.read()
        with open(p, "wb") as f:
            f.write(raw.replace(b"\n", b"\r\n"))
        check("CRLF の生成物は一致扱い", status(tmp)[0] == [])
        # 複数グロブ(カンマ区切り・配列・ブロック)
        write_src("src", '---\napplyTo: "src/**/*.ts, lib/**"\n---\n本文\n')
        write_src("arr", "---\napplyTo: ['a/**', \"b/*.md\"]\n---\n本文\n")
        write_src("blk", '---\napplyTo:\n  - "c/**"\n  - "d/**"\n---\n本文\n')
        tg = {rel: content for rel, content, _, _ in targets(tmp)}
        check("カンマ区切り -> 2 paths", '  - "src/**/*.ts"\n  - "lib/**"\n' in tg[".claude/rules/src.md"], tg[".claude/rules/src.md"][:80])
        check("フロー配列 -> 2 paths", '  - "a/**"\n  - "b/*.md"\n' in tg[".claude/rules/arr.md"], tg[".claude/rules/arr.md"][:80])
        check("ブロック配列 -> 2 paths", '  - "c/**"\n  - "d/**"\n' in tg[".claude/rules/blk.md"], tg[".claude/rules/blk.md"][:80])
        # 全ファイル(常駐相当)は paths 無し + resident に載る
        write_src("all", '---\napplyTo: "**"\n---\n常駐本文\n')
        tg = {rel: content for rel, content, _, _ in targets(tmp)}
        check("applyTo ** -> paths 無し", not tg[".claude/rules/all.md"].startswith("---"), tg[".claude/rules/all.md"][:60])
        check("applyTo ** -> resident に載る", ".claude/rules/all.md" in status(tmp)[3])
        apply_all(tmp, log=lambda r: None)
        # 孤児: 手書きは残して WARN、生成マーカー付きは削除
        with open(os.path.join(tmp, TARGET_DIR, "hand.md"), "w", encoding="utf-8") as f:
            f.write("手書き\n")
        os.remove(os.path.join(src, "all" + SOURCE_SUFFIX))
        st = status(tmp)
        check("孤児: 手書きと正を消した生成物の 2 件", sorted(st[2]) == [".claude/rules/all.md", ".claude/rules/hand.md"], str(st[2]))
        check("cmd_check: 孤児は 1", cmd_check(tmp) == 1)
        apply_all(tmp, log=lambda r: None)
        check("apply_all: 生成マーカー付きの孤児だけ削除(手書きは残す)",
              not os.path.exists(os.path.join(tmp, TARGET_DIR, "all.md")) and os.path.exists(os.path.join(tmp, TARGET_DIR, "hand.md")))
        # 構造エラー
        write_src("bad", "# no frontmatter\n")
        try:
            targets(tmp)
            check("frontmatter 無しは RuleError", False)
        except RuleError:
            check("frontmatter 無しは RuleError", True)
        os.remove(os.path.join(src, "bad" + SOURCE_SUFFIX))
        write_src("bad2", "---\napplyTo:\n---\n本文\n")
        try:
            targets(tmp)
            check("applyTo 空は RuleError", False)
        except RuleError:
            check("applyTo 空は RuleError", True)
        os.remove(os.path.join(src, "bad2" + SOURCE_SUFFIX))
        check("cmd_check: 構造エラーは 2", (lambda: (write_src("bad3", "x\n"), cmd_check(tmp))[1])() == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "--selftest" in argv:
        return selftest()
    if "--check" in argv:
        return cmd_check(root)
    try:
        n = apply_all(root)
    except RuleError as e:
        print(f"ERROR: path-rules: {e}")
        return 2
    print(f"path-rules: wrote {n}")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    sys.exit(main())

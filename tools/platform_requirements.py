#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ホスト版要求の機械可読な正(.github/harness/platform-requirements.json)の読み込みと比較。

正の分担(1 行): ホストに要求する版(min / required / 評価装置の起動門)とフック・予算・CLI フラグ等
ホスト機能の導入版は platform-requirements.json、サブエージェントのモデル解決・effort・FORCE など
モデル関連機能の導入版は .github/harness/model-policy.yml の min_version(JSON は名前で参照するだけ)。

利用側: tools/doctor.py(FAIL/WARN 判定)・tools/validate-harness.py (n)・tools/e2e-run.py(起動門の定数)・
tools/gen-docs.py(PLATFORM.md「最低安全バージョン表」の生成ブロック)。
依存: 標準ライブラリのみ(配布先の doctor / gen-docs から PyYAML 無しで読めること)。model-policy.yml の
min_version は正規表現で読む(regex_policy_min_versions)。yaml で読んだ結果との一致は validate (n) が検査する。

自己テスト: python tools/platform_requirements.py --selftest
"""
from __future__ import annotations

import json
import os
import re
import sys

REQ_REL = os.path.join(".github", "harness", "platform-requirements.json")
POLICY_REL = os.path.join(".github", "harness", "model-policy.yml")
LEVELS = ("pass", "warn", "fail", "unknown")


class RequirementsError(Exception):
    pass


def find_root(start=None):
    """start(既定: このファイルの親の親)から上へ辿り、platform-requirements.json を持つ最初のディレクトリ。"""
    cur = os.path.abspath(start or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    while True:
        if os.path.isfile(os.path.join(cur, REQ_REL)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def load(root=None):
    root = root or find_root()
    if root is None:
        raise RequirementsError(f"{REQ_REL} が見つからない")
    path = os.path.join(root, REQ_REL)
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as e:
        raise RequirementsError(f"{REQ_REL} を読めない: {e}")
    except ValueError as e:
        raise RequirementsError(f"{REQ_REL}: JSON parse error: {e}")
    problems = validate_structure(data)
    if problems:
        raise RequirementsError(f"{REQ_REL}: " + " / ".join(problems))
    return data


def vtuple(s):
    """'2.1.201' / '2.1.201 (Claude Code)' → (2, 1, 201)。解釈不能なら None。"""
    if s is None:
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(s))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def vstr(t):
    return ".".join(str(x) for x in t) if t else "unknown"


def validate_structure(data):
    """必須キー・版の形式・順序・feature 参照の自己整合を検査し、問題の一覧を返す(空なら OK)。"""
    problems = []
    if not isinstance(data, dict):
        return ["トップレベルがオブジェクトではない"]
    if data.get("schema") != "platform-requirements/1":
        problems.append(f"schema は platform-requirements/1(実際: {data.get('schema')!r})")
    cc = data.get("claude_code")
    if not isinstance(cc, dict):
        return problems + ["claude_code が無い"]
    vers = {}
    for key in ("min", "required", "verified_on_dev"):
        v = cc.get(key)
        if vtuple(v) is None or not re.fullmatch(r"\d+(\.\d+)+", str(v)):
            problems.append(f"claude_code.{key} = {v!r} は版番号の形ではない")
        else:
            vers[key] = vtuple(v)
    # verified_on_ci(CI 緑を確認した導入版。A3-6 ホスト機能吸収カナリア tools/host-canary.py の比較元)は任意だが、あれば版の形
    if cc.get("verified_on_ci") is not None and not re.fullmatch(r"\d+(\.\d+)+", str(cc.get("verified_on_ci"))):
        problems.append(f"claude_code.verified_on_ci = {cc.get('verified_on_ci')!r} は版番号の形ではない")
    feats = cc.get("features")
    if not isinstance(feats, dict) or not feats:
        problems.append("claude_code.features が無い")
        feats = {}
    for name, f in feats.items():
        if not isinstance(f, dict) or "version" not in f or not f.get("reason"):
            problems.append(f"features.{name}: version と reason が必要")
            continue
        if f["version"] is not None and not re.fullmatch(r"\d+(\.\d+)+", str(f["version"])):
            problems.append(f"features.{name}.version = {f['version']!r} は版番号の形ではない(未確認は null)")
    e2e = cc.get("e2e") or {}
    if vtuple(e2e.get("min_cli")) is None:
        problems.append("claude_code.e2e.min_cli が無い")
    if "min" in vers and "required" in vers and vers["min"] > vers["required"]:
        problems.append("claude_code.min が required より大きい")
    if vtuple(e2e.get("min_cli")) and "min" in vers and "required" in vers:
        mc = vtuple(e2e.get("min_cli"))
        if not (vers["min"] <= mc <= vers["required"]):
            problems.append("claude_code.e2e.min_cli は min 以上 required 以下")
    # 参照の自己整合: min / required / e2e.min_cli はそれぞれ名指しした feature の導入版と一致する
    for key, ref_key, holder in (("min", "min_feature", cc), ("required", "required_feature", cc),
                                 ("min_cli", "min_cli_feature", e2e)):
        ref = holder.get(ref_key)
        if ref is None:
            continue
        if ref not in feats:
            problems.append(f"{ref_key} = {ref!r} が features に無い")
        elif vtuple(feats[ref].get("version")) != vtuple(holder.get(key)):
            problems.append(f"{key} = {holder.get(key)!r} が features.{ref}.version = {feats[ref].get('version')!r} と一致しない")
    return problems


def level_for(actual, req):
    """実機版 actual(tuple / 文字列 / None)を min / required と比較して pass / warn / fail / unknown。"""
    a = vtuple(actual) if not isinstance(actual, tuple) else actual
    if a is None:
        return "unknown"
    cc = req["claude_code"]
    if a < vtuple(cc["min"]):
        return "fail"
    if a < vtuple(cc["required"]):
        return "warn"
    return "pass"


def features_below(req, actual):
    """actual より新しい導入版を持つ機能(=この版では使えない機能)を [(名前, 版)] で返す(版の昇順)。"""
    a = vtuple(actual) if not isinstance(actual, tuple) else actual
    out = []
    for name, f in req["claude_code"]["features"].items():
        v = vtuple(f.get("version"))
        if v is None:
            continue
        if a is None or a < v:
            out.append((name, vstr(v)))
    return sorted(out, key=lambda x: vtuple(x[1]))


def feature_rows(req):
    """PLATFORM.md の表の行(版の昇順。導入版未確認の行は末尾)。"""
    rows = []
    for name, f in req["claude_code"]["features"].items():
        rows.append((vtuple(f.get("version")), name, f.get("version"), f.get("reason", "")))
    known = sorted((r for r in rows if r[0] is not None), key=lambda r: r[0])
    unknown = [r for r in rows if r[0] is None]
    return known + unknown


def render_markdown(req):
    """PLATFORM.md「最低安全バージョン表」の生成ブロック本文(Markdown。末尾改行なし)。"""
    cc = req["claude_code"]
    L = [f"（`python tools/gen-docs.py` が `.github/harness/platform-requirements.json`（as_of {req.get('as_of')}）から生成。"
         "手で編集しない。検査: `python tools/gen-docs.py --check` / `python tools/validate-harness.py`。"
         "**正の分担（1 行）**: ホストに要求する版（min / required / 評価装置の起動門）とフック・予算・CLI フラグ等"
         "ホスト機能の導入版は platform-requirements.json、サブエージェントのモデル解決・effort・FORCE など"
         "モデル関連機能の導入版は model-policy.yml `min_version`（下の「モデル関連機能の最低版」表）が正。）",
         "",
         "| ツール | 最低版 | 機能キー | 理由 |",
         "|---|---|---|---|"]
    for _vt, name, ver, reason in feature_rows(req):
        col = f">= {ver}" if ver else "2.1.2xx（導入版未確認）"
        L.append(f"| Claude Code | {col} | `{name}` | {reason} |")
    L.append("")
    L.append(f"**最低版: Claude Code >= {cc['min']}**（`{cc.get('min_feature')}`。未満は `python tools/doctor.py` が FAIL、"
             f"`tools/validate-harness.py` が WARN）／**要求版: >= {cc['required']}**（`{cc.get('required_feature')}`。"
             f"未満は doctor が WARN）。{cc.get('min_reason', '')}。{cc.get('required_reason', '')}。"
             f"開発機の実機版: {cc['verified_on_dev']}（{cc.get('verified_note', '')}）。"
             + (f"CI の導入版（CI 緑を確認した版。`python tools/host-canary.py` の比較元）: {cc['verified_on_ci']}"
                f"（{cc.get('verified_on_ci_note', '')}）。" if cc.get("verified_on_ci") else "")
             + f"評価装置 v2（`tools/e2e-run.py`）の起動門は {cc['e2e']['min_cli']}（`{cc['e2e'].get('min_cli_feature')}`。"
             f"{cc['e2e'].get('min_cli_reason', '')}）。ホスト以外の前提ツール（python / PyYAML / git / Git Bash / node / jq）の"
             "要否は同 JSON の `tools` と `python tools/doctor.py` の表が正。")
    return "\n".join(L)


def regex_policy_min_versions(root):
    """model-policy.yml の min_version.claude_code.docs を PyYAML 無しで読む({キー: 版})。
    書式の前提: 2 スペース字下げのブロック形式・値は "x.y.z" の引用文字列(コメント可)。
    yaml で読んだ値との一致は validate-harness (n) が検査する(regex 読みの取りこぼしを鏡割れとして検出)。"""
    path = os.path.join(root, POLICY_REL)
    try:
        lines = open(path, encoding="utf-8-sig").read().splitlines()
    except OSError:
        return {}
    out = {}
    depth = {"min_version": 0, "claude_code": 1, "docs": 2}
    stack = []  # (indent, key)
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        m = re.match(r"^\s*([A-Za-z_][\w.-]*):\s*(.*?)\s*(?:#.*)?$", raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        chain = [k for _i, k in stack] + [key]
        if chain[:3] == ["min_version", "claude_code", "docs"] and len(chain) == 4:
            v = val.strip().strip('"').strip("'")
            if re.fullmatch(r"\d+(\.\d+)+", v):
                out[key] = v
        if val == "":
            stack.append((indent, key))
    return out


def selftest():
    import tempfile
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    root = find_root()
    check("find_root: platform-requirements.json を持つルートを見つける", root is not None, str(root))
    req = load(root)
    cc = req["claude_code"]
    check("load: schema / min / required / features / e2e を持つ", req.get("schema") == "platform-requirements/1"
          and vtuple(cc["min"]) and vtuple(cc["required"]) and cc["features"] and vtuple(cc["e2e"]["min_cli"]))
    check("vtuple: '2.1.201 (Claude Code)' → (2,1,201)", vtuple("2.1.201 (Claude Code)") == (2, 1, 201))
    check("vtuple: 解釈不能は None", vtuple("unknown") is None and vtuple(None) is None)
    check("level_for: min 未満は fail", level_for("2.1.201", req) == "fail")
    check("level_for: min 以上 required 未満は warn", level_for(vtuple(cc["min"]), req) == "warn")
    check("level_for: required 以上は pass", level_for(cc["required"], req) == "pass"
          and level_for("9.9.9", req) == "pass")
    check("level_for: 不明は unknown", level_for(None, req) == "unknown")
    below = features_below(req, "2.1.201")
    check("features_below: 2.1.201 では budget_subagent_inclusive 以降が未達(昇順)",
          [n for n, _v in below][:2] == ["budget_subagent_inclusive", "subagent_background_default"]
          and "deny_bulk_bypass_fix" not in [n for n, _v in below], str(below))
    check("features_below: 不明版は既知版の全機能を未達扱い",
          len(features_below(req, None)) == len([f for f in cc["features"].values() if f.get("version")]))
    rows = feature_rows(req)
    check("feature_rows: 版の昇順・未確認は末尾", [r[0] for r in rows if r[0]] == sorted(r[0] for r in rows if r[0])
          and rows[-1][0] is None)
    md = render_markdown(req)
    check("render_markdown: 表と最低版/要求版の行を含む", "| Claude Code | >= " + cc["min"] in md
          and f"最低版: Claude Code >= {cc['min']}" in md and f"要求版: >= {cc['required']}" in md
          and "導入版未確認" in md)
    pol = regex_policy_min_versions(root)
    check("regex_policy_min_versions: model-policy.yml の subagent_model_force を読める",
          re.fullmatch(r"\d+(\.\d+)+", pol.get("subagent_model_force", "")) is not None, str(pol))
    check("regex_policy_min_versions: copilot_cli 側や effort_frontmatter を混入させない",
          "settings_json_subagents" not in pol and "changelog" not in pol and "docs" not in pol, str(pol))
    # 構造検査の負例
    bad = json.loads(json.dumps(req))
    bad["claude_code"]["min"] = "9.9.9"
    check("validate_structure: min > required を検出", any("required より大きい" in p for p in validate_structure(bad)))
    bad2 = json.loads(json.dumps(req))
    bad2["claude_code"]["required_feature"] = "no_such_feature"
    check("validate_structure: 存在しない feature 参照を検出", any("features に無い" in p for p in validate_structure(bad2)))
    bad3 = json.loads(json.dumps(req))
    bad3["claude_code"]["features"][cc["min_feature"]]["version"] = "1.0.0"
    check("validate_structure: min と feature 版の不一致を検出", any("一致しない" in p for p in validate_structure(bad3)))
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, ".github", "harness"))
        with open(os.path.join(td, REQ_REL), "w", encoding="utf-8") as f:
            f.write("{broken")
        try:
            load(td)
            check("load: 壊れた JSON は RequirementsError", False)
        except RequirementsError:
            check("load: 壊れた JSON は RequirementsError", True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--print" in sys.argv:
        print(render_markdown(load()))
        sys.exit(0)
    print(__doc__)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ホスト版とビルトイン依存のカナリア(A3-6 ホスト機能吸収カナリア / A5-8 ビルトイン依存カナリア)。

CI の claude 導入後ステップで走らせ、赤にはしない(WARN / INFO のみ。--strict で WARN を exit 1)。

1. ホスト版(A3-6): `claude --version` を .github/harness/platform-requirements.json の verified_on_ci(CI 緑を確認した
   導入版)・verified_on_dev(開発機の実機版)と、harness-ci.yml の npm 版固定(`@anthropic-ai/claude-code@x.y.z`)と比べる。
     実機 > verified_on_ci … INFO「新しいホスト版で走った → Day-0 再ベンチ儀式(PLATFORM.md「新モデル/新ホスト版の Day-0 再ベンチ儀式」)」
     実機 < verified_on_ci … WARN(ホスト版の後退。再監査 2026-09-09 RC-14 の型)
     features の version が null(導入版未確認)・実機で未達のもの … INFO で列挙
   理由(赤にしない): 版の前進はリポジトリの欠陥ではなく「吸収すべき変化」の合図。ゲートは Day-0 儀式の実施。
2. ビルトイン依存(A5-8): .github/harness/builtin-dependencies.json の各依存(D070 で検出を委譲した /security-review・
   security-guidance、D071 の /code-review、CI が使う CLI フラグ等)を probes で確認する。
     cli-help      … `claude [command…] --help` の出力に pattern があるか
     plugin-list   … `claude plugin list` の出力に pattern があるか(導入済み plugin。利用者環境依存)
     bundle-string … claude 実行ファイル / npm の cli.js の中に pattern の文字列があるか(近似。無くなれば消えた合図)
     file-exists   … リポジトリ内のファイルの実在(Action の .example 等)
   1 つでも確認できれば PASS、確認できる probe が無ければ INFO(claude 不在)、全 probe が不一致なら WARN。
   理由(赤にしない): ビルトインの有無はホスト版と plugin 導入状態に依存しリポジトリの欠陥ではない。消えたときに気づく
   検知装置であって、対処は台帳の on_missing(D070 / D071 の再評価トリガ)。

使い方(リポジトリルートで):
    python tools/host-canary.py              # 両方
    python tools/host-canary.py --host       # ホスト版だけ / --builtins ビルトインだけ
    python tools/host-canary.py --json
    python tools/host-canary.py --strict     # WARN があれば exit 1
    python tools/host-canary.py --selftest   # claude を起動しない自己テスト(版比較・判定・バイト探索・台帳の構造)
依存: 標準ライブラリ + tools/platform_requirements.py / tools/doctor.py(同梱)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)
import platform_requirements as preq  # noqa: E402

ROOT = os.path.dirname(TOOLS_DIR)
DEPS_REL = os.path.join(".github", "harness", "builtin-dependencies.json")
CI_REL = os.path.join(".github", "workflows", "harness-ci.yml")
PROBE_TYPES = ("cli-help", "plugin-list", "bundle-string", "file-exists")
LEVEL_ORDER = ("warn", "pass", "info", "skip")


def _which(cmd):
    p = shutil.which(cmd)
    if p is None and os.name == "nt":
        for ext in (".exe", ".cmd", ".bat"):
            p = shutil.which(cmd + ext)
            if p:
                break
    return p


def _argv_for(exe):
    return ["cmd", "/c", exe] if exe.lower().endswith((".cmd", ".bat")) else [exe]


def _run(cmd, timeout=60):
    try:
        cp = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return cp.returncode, cp.stdout.decode("utf-8", "replace") + cp.stderr.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None, ""


# ---------------------------------------------------------------- ホスト版(A3-6)

def ci_pinned_version(root):
    path = os.path.join(root, CI_REL)
    try:
        m = re.search(r"@anthropic-ai/claude-code@(\d+\.\d+\.\d+)", open(path, encoding="utf-8-sig").read())
        return m.group(1) if m else None
    except OSError:
        return None


def compare_host(req, actual, pinned):
    """[(level, name, detail)]。actual は版文字列 or None(未導入)。claude は起動しない。"""
    cc = req["claude_code"]
    v_ci = cc.get("verified_on_ci")
    v_dev = cc.get("verified_on_dev")
    out = []
    if pinned and v_ci and preq.vtuple(pinned) != preq.vtuple(v_ci):
        out.append(("warn", "ci-pin", f"harness-ci.yml pins {pinned} but platform-requirements verified_on_ci is {v_ci}"
                    " (verified_on_ci は CI 緑を確認した版。pin を上げたら CI 緑後に JSON を更新する=Day-0 儀式)"))
    elif pinned:
        out.append(("pass", "ci-pin", f"harness-ci.yml pin {pinned} = verified_on_ci {v_ci}"))
    else:
        out.append(("info", "ci-pin", "harness-ci.yml に版固定が無い(配布先では CI 定義はプロジェクト所有)"))
    a = preq.vtuple(actual)
    if a is None:
        out.append(("info", "host-version", f"claude not found or unparsable ({actual!r}); version comparison skipped"))
        return out
    if v_ci:
        vc = preq.vtuple(v_ci)
        if a > vc:
            out.append(("info", "host-version", f"claude {preq.vstr(a)} > verified_on_ci {v_ci}: 新しいホスト版で走っている。"
                        "Day-0 再ベンチ儀式(PLATFORM.md)を実施し verified_on_ci を更新する"))
        elif a < vc:
            out.append(("warn", "host-version", f"claude {preq.vstr(a)} < verified_on_ci {v_ci}: ホスト版が後退している(RC-14 の型)"))
        else:
            out.append(("pass", "host-version", f"claude {preq.vstr(a)} = verified_on_ci {v_ci}"))
    if v_dev and preq.vtuple(v_dev) != a:
        out.append(("info", "dev-version", f"verified_on_dev {v_dev} != claude {preq.vstr(a)} (開発機の記録。開発機を更新したら JSON と model-policy の verified を上げる)"))
    unconfirmed = [n for n, f in cc["features"].items() if f.get("version") is None]
    if unconfirmed:
        out.append(("info", "features-unconfirmed", "導入版未確認の feature: " + ", ".join(unconfirmed)
                    + "(実機で発火を実測して版数を確定する)"))
    below = preq.features_below(req, a)
    if below:
        out.append(("info", "features-below", "この版では未達の feature: " + ", ".join(f"{n}({v})" for n, v in below)))
    else:
        out.append(("pass", "features-below", "全 feature の導入版以上"))
    return out


def actual_claude_version():
    exe = _which("claude")
    if not exe:
        return None, None
    rc, out = _run(_argv_for(exe) + ["--version"])
    return exe, (out.strip() if out.strip() else None)


# ---------------------------------------------------------------- ビルトイン依存(A5-8)

def load_deps(root):
    path = os.path.join(root, DEPS_REL)
    data = json.load(open(path, encoding="utf-8-sig"))
    problems = validate_manifest(data)
    if problems:
        raise ValueError(f"{DEPS_REL}: " + " / ".join(problems))
    return data


def validate_manifest(data):
    problems = []
    if not isinstance(data, dict) or data.get("schema") != "builtin-dependencies/1":
        return ["schema は builtin-dependencies/1"]
    deps = data.get("dependencies")
    if not isinstance(deps, list) or not deps:
        return ["dependencies が空"]
    ids = set()
    for d in deps:
        if not isinstance(d, dict) or not d.get("id"):
            problems.append("id の無い依存がある")
            continue
        if d["id"] in ids:
            problems.append(f"id が重複: {d['id']}")
        ids.add(d["id"])
        for key in ("kind", "decision", "used_by", "probes", "on_missing"):
            if not d.get(key):
                problems.append(f"{d['id']}: {key} が無い")
        for p in d.get("probes") or []:
            if not isinstance(p, dict) or p.get("type") not in PROBE_TYPES:
                problems.append(f"{d['id']}: probe type は {PROBE_TYPES}")
            elif p["type"] == "file-exists" and not p.get("path"):
                problems.append(f"{d['id']}: file-exists には path が必要")
            elif p["type"] != "file-exists" and not p.get("pattern"):
                problems.append(f"{d['id']}: {p['type']} には pattern が必要")
    return problems


def bytes_contain(path, patterns, chunk=8 << 20):
    """大きなバイナリを分割して読み、patterns(bytes)のうち見つかったものの集合を返す(境界はオーバーラップで担保)。"""
    found = set()
    overlap = max((len(p) for p in patterns), default=0)
    tail = b""
    try:
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                buf = tail + data
                for p in patterns:
                    if p not in found and p in buf:
                        found.add(p)
                if len(found) == len(patterns):
                    break
                tail = buf[-overlap:] if overlap else b""
    except OSError:
        return set()
    return found


def bundle_candidates(exe):
    """文字列探索の対象: claude 実行ファイル(実体)と npm 配布の cli.js。"""
    cands = []
    if exe:
        real = os.path.realpath(exe)
        cands.append(real)
        if real.lower().endswith((".cmd", ".bat")):
            # npm のシムは node_modules/@anthropic-ai/claude-code/cli.js を指す
            d = os.path.dirname(real)
            for rel in (os.path.join("node_modules", "@anthropic-ai", "claude-code", "cli.js"),
                        os.path.join("..", "lib", "node_modules", "@anthropic-ai", "claude-code", "cli.js")):
                p = os.path.normpath(os.path.join(d, rel))
                if os.path.isfile(p):
                    cands.append(p)
    npm = _which("npm")
    if npm:
        rc, out = _run(_argv_for(npm) + ["root", "-g"], timeout=60)
        if rc == 0 and out.strip():
            p = os.path.join(out.strip().splitlines()[-1].strip(), "@anthropic-ai", "claude-code", "cli.js")
            if os.path.isfile(p):
                cands.append(p)
    seen = set()
    return [c for c in cands if os.path.isfile(c) and not (c in seen or seen.add(c))]


class Probes:
    """外部コマンドの出力は依存ごとに再実行せず 1 回だけ取る。"""

    def __init__(self, root, exe):
        self.root, self.exe = root, exe
        self.help_cache = {}
        self.plugin_list = None
        self.bundles = None
        self.bundle_found = {}

    def cli_help(self, command):
        key = tuple(command or [])
        if key not in self.help_cache:
            if not self.exe:
                self.help_cache[key] = None
            else:
                rc, out = _run(_argv_for(self.exe) + list(key) + ["--help"])
                self.help_cache[key] = out if rc is not None else None
        return self.help_cache[key]

    def plugins(self):
        if self.plugin_list is None:
            if not self.exe:
                self.plugin_list = ""
            else:
                rc, out = _run(_argv_for(self.exe) + ["plugin", "list"], timeout=120)
                self.plugin_list = out if rc is not None else ""
        return self.plugin_list

    def bundle_has(self, pattern):
        if self.bundles is None:
            self.bundles = bundle_candidates(self.exe)
        if not self.bundles:
            return None
        if pattern not in self.bundle_found:
            pat = pattern.encode("utf-8")
            self.bundle_found[pattern] = any(pat in bytes_contain(b, [pat]) for b in self.bundles)
        return self.bundle_found[pattern]


def run_probe(probes, probe):
    """(結果 True/False/None=実行不能, 説明)。"""
    t = probe["type"]
    if t == "cli-help":
        text = probes.cli_help(probe.get("command"))
        if text is None:
            return None, "claude --help unavailable"
        return bool(re.search(re.escape(probe["pattern"]), text)), f"cli-help {' '.join(probe.get('command') or [])}".strip()
    if t == "plugin-list":
        text = probes.plugins()
        if not text:
            return None, "plugin list unavailable"
        return bool(re.search(re.escape(probe["pattern"]), text)), "plugin-list"
    if t == "bundle-string":
        r = probes.bundle_has(probe["pattern"])
        if r is None:
            return None, "bundle not found"
        return r, "bundle-string"
    if t == "file-exists":
        return os.path.isfile(os.path.join(probes.root, probe["path"])), f"file-exists {probe['path']}"
    return None, f"unknown probe {t}"


def evaluate_dependency(dep, results, req=None, actual=None):
    """results = [(ok, note)]。1 つでも True なら pass、実行できた probe が無ければ info、全 False なら warn。
    例外: since_feature(platform-requirements.json の feature 名)の導入版より実機が古ければ「この版には無くて当然」で info、
    optional(利用者ごとの opt-in plugin 等)は info(marketplace に残っているかは機械では未検証)。"""
    if any(r is True for r, _n in results):
        return "pass", ", ".join(n for r, n in results if r is True)
    if all(r is None for r, _n in results):
        return "info", "no runnable probe (" + ", ".join(n for _r, n in results) + ")"
    missing = "not found by " + ", ".join(n for r, n in results if r is False)
    since = dep.get("since_feature")
    if since and req is not None:
        sv = preq.vtuple((req["claude_code"]["features"].get(since) or {}).get("version"))
        a = preq.vtuple(actual)
        if sv and a and a < sv:
            return "info", f"{missing} (expected: claude {preq.vstr(a)} < {since} {preq.vstr(sv)})"
    if dep.get("optional"):
        return "info", f"{missing} (optional/opt-in here; presence upstream is unverified by machine). on_missing: {dep.get('on_missing', '')}"
    return "warn", f"{missing}. on_missing: {dep.get('on_missing', '')}"


def check_builtins(root, deps, exe, req=None, actual=None):
    probes = Probes(root, exe)
    rows = []
    for dep in deps["dependencies"]:
        results = [run_probe(probes, p) for p in dep["probes"]]
        level, detail = evaluate_dependency(dep, results, req, actual)
        rows.append((level, f"builtin {dep['id']}", f"[{dep['kind']}, {dep['decision']}] {detail}"))
    return rows


# ---------------------------------------------------------------- 出力

def render(rows, header):
    lines = [header]
    for level, name, detail in rows:
        lines.append(f"[{level.upper():<4}] {name:<32} {detail}")
    c = {lv: sum(1 for r in rows if r[0] == lv) for lv in LEVEL_ORDER}
    lines.append(f"host-canary: pass {c['pass']} / warn {c['warn']} / info {c['info']} / skip {c['skip']} (WARN でも exit 0。--strict で 1)")
    return "\n".join(lines)


# ---------------------------------------------------------------- selftest

def selftest():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    root = preq.find_root() or ROOT
    req = preq.load(root)
    cc = req["claude_code"]
    v_ci = cc.get("verified_on_ci")
    check("platform-requirements に verified_on_ci がある(版の形)", bool(v_ci) and re.fullmatch(r"\d+(\.\d+)+", str(v_ci)) is not None, str(v_ci))
    pin = ci_pinned_version(root)
    check("harness-ci.yml の npm 版固定を読める", pin is None or re.fullmatch(r"\d+\.\d+\.\d+", pin) is not None, str(pin))
    rows = compare_host(req, v_ci, v_ci)
    check("host: 実機 = verified_on_ci = pin なら ci-pin / host-version が pass",
          all(l == "pass" for l, n, _d in rows if n in ("ci-pin", "host-version")), str(rows))
    rows = compare_host(req, "9.9.9", v_ci)
    check("host: 実機 > verified_on_ci は INFO で Day-0 儀式を案内", any(l == "info" and n == "host-version" and "Day-0" in d for l, n, d in rows), str(rows))
    rows = compare_host(req, "0.0.1", v_ci)
    check("host: 実機 < verified_on_ci は WARN(後退)", any(l == "warn" and n == "host-version" for l, n, _d in rows), str(rows))
    rows = compare_host(req, v_ci, "0.0.2")
    check("host: pin が verified_on_ci と違えば WARN(JSON が正)", any(l == "warn" and n == "ci-pin" for l, n, _d in rows), str(rows))
    rows = compare_host(req, None, v_ci)
    check("host: claude 不在は INFO で比較を省く", any(l == "info" and n == "host-version" for l, n, _d in rows), str(rows))
    deps = load_deps(root)
    check("builtin-dependencies.json の構造が妥当", validate_manifest(deps) == [])
    bad = json.loads(json.dumps(deps))
    bad["dependencies"][0].pop("on_missing", None)
    bad["dependencies"].append({"id": deps["dependencies"][0]["id"], "kind": "x", "decision": "D0", "used_by": ["a"],
                                "probes": [{"type": "nope"}], "on_missing": "x"})
    probs = validate_manifest(bad)
    check("validate_manifest: on_missing 欠落・id 重複・未知の probe type を検出",
          any("on_missing" in p for p in probs) and any("重複" in p for p in probs) and any("probe type" in p for p in probs), str(probs))
    check("evaluate: 1 つでも True なら pass", evaluate_dependency({}, [(False, "a"), (True, "b")])[0] == "pass")
    check("evaluate: 実行できた probe が無ければ info", evaluate_dependency({}, [(None, "a"), (None, "b")])[0] == "info")
    check("evaluate: 全 False は warn で on_missing を添える",
          evaluate_dependency({"on_missing": "再評価"}, [(False, "a"), (None, "b")]) == ("warn", "not found by a. on_missing: 再評価"))
    feat = next((n for n, f in cc["features"].items() if f.get("version")), None)
    lv, _d = evaluate_dependency({"since_feature": feat, "on_missing": "x"}, [(False, "cli-help")], req, "0.0.1")
    check("evaluate: since_feature の導入版より古い実機では info(無くて当然)", lv == "info", lv)
    lv, _d = evaluate_dependency({"since_feature": feat, "on_missing": "x"}, [(False, "cli-help")], req, "9.9.9")
    check("evaluate: since_feature の導入版以上なのに無ければ warn", lv == "warn", lv)
    lv, _d = evaluate_dependency({"optional": True, "on_missing": "x"}, [(False, "plugin-list")])
    check("evaluate: optional(opt-in plugin)は info", lv == "info", lv)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "bin.dat")
        with open(p, "wb") as f:
            f.write(b"x" * 100 + b"security-review" + b"y" * 100)
        found = bytes_contain(p, [b"security-review", b"nope"], chunk=64)
        check("bytes_contain: チャンク境界をまたぐ文字列も見つける(64B チャンク)", found == {b"security-review"}, str(found))
        probes = Probes(td, None)
        r, n = run_probe(probes, {"type": "file-exists", "path": "bin.dat"})
        check("probe file-exists: 実在すれば True", r is True and n.startswith("file-exists"))
        r, _n = run_probe(probes, {"type": "cli-help", "pattern": "x"})
        check("probe cli-help: claude 不在は None(実行不能)", r is None)
        r, _n = run_probe(probes, {"type": "bundle-string", "pattern": "x"})
        check("probe bundle-string: 実行ファイル無しは None", r is None)
    txt = render([("pass", "a", "x"), ("warn", "b", "y"), ("info", "c", "z")], "h")
    check("render: 集計行を持ち WARN でも exit 0 と明記", "host-canary: pass 1 / warn 1 / info 1" in txt and "exit 0" in txt)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="ホスト版(A3-6)とビルトイン依存(A5-8)のカナリア。WARN/INFO のみ(--strict で WARN を exit 1)")
    ap.add_argument("--root", default=None)
    ap.add_argument("--host", action="store_true", help="ホスト版の比較だけ")
    ap.add_argument("--builtins", action="store_true", help="ビルトイン依存の確認だけ")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    root = os.path.abspath(args.root) if args.root else ROOT
    req_root = preq.find_root(root) or root
    try:
        req = preq.load(req_root)
    except preq.RequirementsError as e:
        print(f"[WARN] platform-requirements {e}")
        return 1 if args.strict else 0
    exe, actual = actual_claude_version()
    do_host = args.host or not args.builtins
    do_builtins = args.builtins or not args.host
    rows = []
    if do_host:
        rows.extend(compare_host(req, actual, ci_pinned_version(root)))
    if do_builtins:
        try:
            deps = load_deps(root)
            rows.extend(check_builtins(root, deps, exe, req, actual))
        except (OSError, ValueError) as e:
            rows.append(("warn", "builtin-dependencies", f"{DEPS_REL} を読めない: {e}"))
    header = (f"host-canary  root={root.replace(chr(92), '/')}  claude={actual or 'not found'}"
              f"  verified_on_ci={req['claude_code'].get('verified_on_ci')}  verified_on_dev={req['claude_code'].get('verified_on_dev')}")
    if args.json:
        print(json.dumps({"claude": actual, "results": [{"level": l, "name": n, "detail": d} for l, n, d in rows]},
                         ensure_ascii=False, indent=2))
    else:
        print(render(rows, header))
    return 1 if (args.strict and any(l == "warn" for l, _n, _d in rows)) else 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

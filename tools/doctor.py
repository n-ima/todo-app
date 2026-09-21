#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ハーネス doctor: 環境・配線・配布鮮度を pass / warn / fail の表で診断する(第3回監査 A3-3 / 再監査 2026-09-09 A6-24・A6-8・A6-15)。

構造の整合(frontmatter・アダプタ・3面照合・台帳)は tools/validate-harness.py が正で、doctor は
**環境側**(このマシン・このチェックアウトで実際に動くか)だけを見る:

  claude-version   claude --version を .github/harness/platform-requirements.json の min / required と比較
                   (min 未満 FAIL・required 未満 WARN・未導入 WARN)。未達の機能キーを列挙する
  python / pyyaml / node / jq / git   前提ツールの有無(python の Store スタブ・PyYAML は validate 用)
  bash             全フックの起動シェル。Windows は Git Bash が必須(WSL の bash が先に解決されると FAIL)
  hooks-wiring     .claude/settings.json の有無・イベント数・コマンドの先頭語(bash / python)が解決できるか・
                   settings.local.json による hooks / statusLine の上書きの有無
  script-encoding  .sh に CR が無い・.ps1 が BOM 付き(core.autocrlf の値も併記)
  statusline       statusLine.command の配線と実行テスト(モック JSON を流して 1 行返るか。logs/usage の
                   doctor-probe.* は消す)
  workspace-trust  Claude Code の信頼ダイアログ承認記録(~/.claude.json)。未承認だとプロジェクトの
                   .claude/settings.json(フック・deny・statusLine)は読まれない
  distribution     docs/00-overview/harness-origin.md の latest_decision と本体(path:)の DECISIONS.md の
                   最新 D 番号の差(N 世代古ければ WARN → /91)。本体リポジトリ自身では対象外
  usage-logs       .github/hooks/logs/usage/ の受領書の有無と .gitignore
  windows-8dot3    (Windows のみ)8.3 短縮名の生成状態(レジストリ + 実パスの短縮名プローブ。情報のみ)

使い方(プロジェクト/本体のルート、またはどこからでも --root で):
    python tools/doctor.py                 # 表を出す。FAIL があれば exit 1
    python tools/doctor.py --json          # 機械可読
    python tools/doctor.py --strict        # WARN も exit 1
    python tools/doctor.py --no-probe      # statusline の実行テストを省く(CI 等)
    python tools/doctor.py --write-origin [--harness <本体パス>] [--force]
                                           # harness-origin.md を作る(GitHub テンプレート / ZIP 経路の代替。
                                           #  sync / intake 経路では --apply が自動生成する)
    python tools/doctor.py --selftest      # 合成フィクスチャで自己テスト(claude は起動しない)

出力は cp932 コンソールでも壊れないよう ASCII 主体(状態・項目名・詳細は ASCII、hint のみ日本語)。
依存: 標準ライブラリ + tools/platform_requirements.py(同梱)。fail-open ではなく診断ツールなので
検査自体の例外は FAIL として表に出す(黙って通さない)。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)
import platform_requirements as preq  # noqa: E402

ORIGIN_REL = os.path.join("docs", "00-overview", "harness-origin.md")
SETTINGS_REL = os.path.join(".claude", "settings.json")
LOCAL_SETTINGS_REL = os.path.join(".claude", "settings.local.json")
SCRIPTS_REL = os.path.join(".github", "hooks", "scripts")
USAGE_LOGS_REL = os.path.join(".github", "hooks", "logs", "usage")
LEVEL_ORDER = ("fail", "warn", "pass", "info", "skip")
STALE_ENV = "HARNESS_STALE_GENERATIONS"
DEFAULT_STALE_GENERATIONS = 1
PROBE_SID = "doctor-probe"


class Result:
    __slots__ = ("name", "level", "detail", "hint")

    def __init__(self, name, level, detail, hint=""):
        assert level in LEVEL_ORDER, level
        self.name, self.level, self.detail, self.hint = name, level, detail, hint

    def as_dict(self):
        return {"name": self.name, "level": self.level, "detail": self.detail, "hint": self.hint}


# ---------------------------------------------------------------- helpers

def is_windows():
    return os.name == "nt"


def which(cmd):
    p = shutil.which(cmd)
    if p is None and is_windows():
        for ext in (".exe", ".cmd", ".bat"):
            p = shutil.which(cmd + ext)
            if p:
                break
    return p


def argv_for(exe):
    """Windows の npm 配布(claude.cmd)は cmd.exe 経由でしか起動できない(e2e-run.py と同じ)。"""
    if exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", exe]
    return [exe]


def run(cmd, timeout=30, cwd=None, stdin=None):
    """(rc, stdout, stderr)。起動失敗は rc=None。"""
    try:
        cp = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd, input=stdin)
        return cp.returncode, cp.stdout.decode("utf-8", "replace"), cp.stderr.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, "", str(e)


def read_json_loose(path):
    """// 行コメント付き JSON(.claude/settings.json / .vscode/settings.json)を読む。"""
    text = open(path, encoding="utf-8-sig").read()
    return json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE))


def norm_path(p):
    # ~/.claude.json のキーは Windows では区切りが混在しうる。posix では backslash が区切りにならず
    # abspath が相対名として cwd を前置してしまう(CI ubuntu の selftest で顕在化)ため、先に区切りを揃える
    return os.path.normcase(os.path.normpath(os.path.abspath(str(p).replace("\\", "/")))).replace("\\", "/").rstrip("/")


def memo_is_pristine(root):
    """requirements/memo.md がテンプレのまま(マーカーの後に実内容が無い or ファイル無し)。
    フック(inject-progress / route-request)の本体判定と同じ規則(D058 / H-11)。"""
    memo = os.path.join(root, "requirements", "memo.md")
    if not os.path.isfile(memo):
        return True
    text = open(memo, encoding="utf-8", errors="replace").read()
    marker = "（ここから記入）"
    if marker not in text:
        return False
    return text.split(marker, 1)[1].strip() == ""


def is_body(root):
    """ハーネス本体リポジトリ(clone / テンプレ複製 / ZIP 展開コピー)の判定。フックと同じ規則。"""
    return (os.path.isfile(os.path.join(root, "DECISIONS.md"))
            or os.path.isfile(os.path.join(root, ".github", "harness", "USAGE.md"))) and memo_is_pristine(root)


def max_decision(path):
    """DECISIONS.md(## Dnnn の最大)→ CHANGELOG.md(Dnnn 言及の最大)の順で最新 D 番号を返す。(番号, 出典名)。"""
    dec = os.path.join(path, "DECISIONS.md")
    if os.path.isfile(dec):
        nums = [int(n) for n in re.findall(r"^## D(\d+)", open(dec, encoding="utf-8", errors="replace").read(), re.M)]
        if nums:
            return max(nums), "DECISIONS.md"
    ch = os.path.join(path, "CHANGELOG.md")
    if os.path.isfile(ch):
        nums = [int(n) for n in re.findall(r"\bD(\d{3})\b", open(ch, encoding="utf-8", errors="replace").read())]
        if nums:
            return max(nums), "CHANGELOG.md"
    return None, None


def parse_origin(text):
    """harness-origin.md の <!-- HARNESS_ORIGIN ... --> ブロックを {キー: 値} で返す(欠けたキーは無い)。
    sync-harness.py / intake-app.py / doctor --write-origin が書く共通書式(path / version / synced は旧来、
    source_url / source_commit / archive_sha256 / synced_at / latest_decision は 2026-09-10 追加)。"""
    m = re.search(r"<!--\s*HARNESS_ORIGIN(.*?)-->", text, re.S)
    body = m.group(1) if m else text
    out = {}
    for line in body.splitlines():
        km = re.match(r"^\s*([A-Za-z_][\w-]*):\s*(.*?)\s*$", line)
        if km:
            out[km.group(1)] = km.group(2)
    return out


def read_origin(root):
    p = os.path.join(root, ORIGIN_REL)
    if not os.path.isfile(p):
        return None
    return parse_origin(open(p, encoding="utf-8", errors="replace").read())


def origin_decision_number(origin):
    for key in ("latest_decision", "version"):
        m = re.search(r"D(\d+)", origin.get(key) or "")
        if m:
            return int(m.group(1))
    return None


def git_info(harness):
    """本体の HEAD sha と origin URL(git が無い / リポジトリでなければ unknown)。"""
    git = which("git")
    sha = url = "unknown"
    if git and os.path.isdir(harness):
        rc, out, _ = run([git, "-C", harness, "rev-parse", "HEAD"], timeout=15)
        if rc == 0 and re.fullmatch(r"[0-9a-f]{7,40}", out.strip()):
            sha = out.strip()
        rc, out, _ = run([git, "-C", harness, "remote", "get-url", "origin"], timeout=15)
        if rc == 0 and out.strip():
            url = out.strip()
    return sha, url


ORIGIN_TEMPLATE = """<!-- HARNESS_ORIGIN
path: {path}
version: {version}
synced: {synced}
source_url: {source_url}
source_commit: {source_commit}
archive_sha256: {archive_sha256}
synced_at: {synced_at}
latest_decision: {latest_decision}
route: {route}
-->

# ハーネス本体の場所と配布鮮度(自動記録)

このプロジェクトのハーネスは `{path}` からコピー/同期された({version} まで・{synced}・経路 {route})。
「ハーネスを更新して」の依頼(/91-sync-from-harness)ではこのパスが既定の本体として使われ、
SessionStart フック(inject-progress)と `python tools/doctor.py` は `latest_decision` と本体の
DECISIONS.md の最新 D 番号の差で「/91 を先に実行」を案内する。`tools/sync-harness.py --apply` の
たびに自動更新されるため、手で編集しない(本体を移動した場合は次回 `--harness` で明示するか
`python tools/doctor.py --write-origin --harness <本体パス> --force` で書き直す)。
"""


def write_origin(root, harness=None, force=False, route="doctor", archive_sha256="n/a"):
    """harness-origin.md を作る(既存なら force が無い限り触らない)。(書いたか, メッセージ)。"""
    dst = os.path.join(root, ORIGIN_REL)
    if os.path.isfile(dst) and not force:
        return False, f"{ORIGIN_REL} exists (use --force to rewrite)"
    if harness:
        harness_abs = os.path.abspath(harness)
        if not os.path.isdir(harness_abs):
            return False, f"harness path not found: {harness}"
        latest, src = max_decision(harness_abs)
        sha, url = git_info(harness_abs)
        path_s = harness_abs.replace("\\", "/")
    else:
        # テンプレート経路(本体の開発記録 DECISIONS.md が残っている)なら手元の複製から最新 D 番号を取る。
        # ZIP 経路は DECISIONS.md が無い(export-ignore)ため unknown になる(--harness で本体を指せば埋まる)
        latest, src = max_decision(root)
        sha, url, path_s = "unknown", "unknown", "unknown"
    version = f"D{latest:03d}" if latest else "unknown"
    now = datetime.datetime.now().astimezone()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(ORIGIN_TEMPLATE.format(path=path_s, version=version, synced=now.date().isoformat(),
                                       source_url=url, source_commit=sha, archive_sha256=archive_sha256,
                                       synced_at=now.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                       latest_decision=version, route=route))
    return True, f"wrote {ORIGIN_REL} (latest_decision {version}" + (f" from {src}" if src else "") + f", path {path_s})"


# ---------------------------------------------------------------- checks

def claude_version_string():
    """(実行ファイル, 版文字列)。未導入は (None, None)。"""
    exe = which("claude")
    if not exe:
        return None, None
    rc, out, err = run(argv_for(exe) + ["--version"], timeout=60)
    text = (out or err or "").strip()
    return exe, text if rc == 0 or text else None


def evaluate_claude(req, version_text, found=True):
    """版文字列 → Result(claude は起動しない。selftest から直接呼ぶ)。"""
    cc = req["claude_code"]
    if not found:
        return Result("claude-version", "warn", "claude not found on PATH",
                      "Claude Code を使わない(Copilot のみ)なら無視してよい。使うなら PATH を確認")
    vt = preq.vtuple(version_text)
    if vt is None:
        return Result("claude-version", "warn", f"cannot parse version from {version_text!r}",
                      "claude --version の出力形式が変わった可能性。platform_requirements.vtuple を確認")
    level = preq.level_for(vt, req)
    below = preq.features_below(req, vt)
    gap = ("; unavailable: " + ", ".join(f"{n}({v})" for n, v in below[:8])
           + (" ..." if len(below) > 8 else "")) if below else ""
    if level == "fail":
        return Result("claude-version", "fail",
                      f"{preq.vstr(vt)} < min {cc['min']} (required {cc['required']}){gap}",
                      f"Claude Code を {cc['required']} 以上に更新する(最低版 {cc['min']} 未満では予算の強制停止が効かない)")
    if level == "warn":
        return Result("claude-version", "warn",
                      f"{preq.vstr(vt)} >= min {cc['min']} but < required {cc['required']}{gap}",
                      f"要求版 {cc['required']} 以上に更新する(deny のサブシェル評価・FORCE)")
    return Result("claude-version", "pass", f"{preq.vstr(vt)} >= required {cc['required']}{gap}")


def check_claude(req):
    exe, text = claude_version_string()
    if exe is None:
        return evaluate_claude(req, None, found=False)
    if text is None:
        return Result("claude-version", "warn", f"claude found at {exe} but --version failed", "claude --version を手で実行して確認")
    return evaluate_claude(req, text)


def claude_version_status(root=None):
    """validate-harness (n) 用: (level, message)。level は fail / warn / pass / missing。"""
    try:
        req = preq.load(root or preq.find_root())
    except preq.RequirementsError as e:
        return "missing", str(e)
    r = check_claude(req)
    if r.detail.startswith("claude not found"):
        return "missing", r.detail
    return r.level, r.detail


def check_python():
    v = sys.version_info
    exe = which("python")
    detail = f"{v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if v < (3, 8):
        return Result("python", "fail", detail + " < 3.8", "python 3.8 以上を導入する")
    if exe and "windowsapps" in exe.lower():
        return Result("python", "warn", detail + f"; PATH resolves 'python' to Store stub {exe}",
                      "run-python.sh は PATH の python を使う。Store のスタブより先に実体の python を PATH に置く")
    if exe:
        rc, out, _ = run([exe, "--version"], timeout=20)
        if rc != 0:
            return Result("python", "warn", detail + f"; 'python' on PATH ({exe}) does not run",
                          "フックは PATH の python を起動する。実行できる python を PATH の先頭に置く")
    else:
        return Result("python", "warn", detail + "; 'python' not on PATH (hooks use run-python.sh: python -> python3)",
                      "python3 だけの環境なら run-python.sh が python3 に落ちるので問題ない")
    return Result("python", "pass", detail)


def check_pyyaml():
    try:
        import yaml  # noqa: F401
        return Result("pyyaml", "pass", f"PyYAML {getattr(yaml, '__version__', '?')}")
    except Exception:  # noqa: BLE001
        return Result("pyyaml", "warn", "PyYAML not importable (validate-harness.py / generate-adapters.py need it)",
                      "pip install pyyaml(フックと doctor 自体は不要)")


def check_optional_tool(name, note):
    p = which(name)
    if p:
        return Result(name, "pass", p)
    return Result(name, "info", f"not found (optional: {note})")


def check_git():
    p = which("git")
    if not p:
        return Result("git", "fail", "git not found", "git を導入する(ConfigChange 第2防衛線・基準線・sync/intake が動かない)")
    rc, out, _ = run([p, "--version"], timeout=20)
    return Result("git", "pass", (out.strip() or p))


def check_bash():
    p = which("bash")
    if not p:
        return Result("bash", "fail", "bash not found (all hooks are wired as 'bash ...' and would silently fail-open)",
                      "Windows は Git for Windows を導入し Git Bash を PATH に置く")
    if is_windows() and "system32" in p.lower():
        return Result("bash", "fail", f"'bash' resolves to WSL launcher {p} (hooks would run inside WSL)",
                      "Git の usr\\bin を System32 より前に PATH へ置く(where bash で Git のものが先頭)")
    rc, out, _ = run([p, "--version"], timeout=20)
    first = (out.splitlines() or [""])[0].strip()
    if rc != 0:
        return Result("bash", "fail", f"{p} does not run", "bash --version を手で実行して確認")
    return Result("bash", "pass", f"{p} ({first})" if first else p)


def check_hook_wiring(root):
    path = os.path.join(root, SETTINGS_REL)
    if not os.path.isfile(path):
        return Result("hooks-wiring", "fail", f"{SETTINGS_REL} not found (Claude Code hooks / deny / statusLine not wired)",
                      "配布物の .claude/settings.json を置く(sync-harness / intake-app の --apply)")
    try:
        data = read_json_loose(path)
    except Exception as e:  # noqa: BLE001
        return Result("hooks-wiring", "fail", f"{SETTINGS_REL} is not valid JSON: {e}", "JSON を直す(validate も ERROR になる)")
    hooks = data.get("hooks") or {}
    commands = []
    for ev, groups in hooks.items():
        for g in groups or []:
            for h in (g or {}).get("hooks") or []:
                if h.get("command"):
                    commands.append(h["command"])
    sl = (data.get("statusLine") or {}).get("command")
    if sl:
        commands.append(sl)
    heads = sorted({shlex.split(c, posix=True)[0] for c in commands if c.strip()})
    unresolved = [h for h in heads if not which(h)]
    refs = sorted(set(re.findall(r"\.github/hooks/scripts/[\w.-]+", json.dumps(data))))
    missing = [r for r in refs if not os.path.isfile(os.path.join(root, r.replace("/", os.sep)))]
    local = os.path.join(root, LOCAL_SETTINGS_REL)
    override = ""
    if os.path.isfile(local):
        try:
            ld = read_json_loose(local)
            over = [k for k in ("hooks", "statusLine", "permissions") if k in ld]
            if over:
                override = f"; settings.local.json overrides {'/'.join(over)}"
        except Exception:  # noqa: BLE001
            override = "; settings.local.json is not valid JSON"
    detail = f"{len(hooks)} events, {len(commands)} commands, interpreters {heads}, {len(refs)} scripts referenced{override}"
    if unresolved:
        return Result("hooks-wiring", "fail", detail + f"; unresolved interpreter(s): {unresolved}",
                      "フックの先頭語(bash / python)が PATH に無い。上の bash / python 項目を直す")
    if missing:
        return Result("hooks-wiring", "warn", detail + f"; missing script(s): {missing}",
                      "配線先のスクリプトが無い(python tools/validate-harness.py が ERROR で列挙する)")
    if not hooks:
        return Result("hooks-wiring", "warn", detail, "hooks が空。配布物の settings.json と比べる(/91)")
    return Result("hooks-wiring", "pass", detail)


def check_script_encoding(root):
    sdir = os.path.join(root, SCRIPTS_REL)
    if not os.path.isdir(sdir):
        return Result("script-encoding", "fail", f"{SCRIPTS_REL} not found", "フックスクリプト一式が無い(sync-harness --apply)")
    crlf, nobom = [], []
    for name in sorted(os.listdir(sdir)):
        p = os.path.join(sdir, name)
        if name.endswith(".sh") and b"\r" in open(p, "rb").read():
            crlf.append(name)
        if name.endswith(".ps1") and not open(p, "rb").read(3).startswith(b"\xef\xbb\xbf"):
            nobom.append(name)
    autocrlf = "n/a"
    git = which("git")
    if git:
        rc, out, _ = run([git, "-C", root, "config", "--get", "core.autocrlf"], timeout=15)
        autocrlf = out.strip() or "unset"
    detail = f"core.autocrlf={autocrlf}"
    if crlf or nobom:
        return Result("script-encoding", "fail", detail + (f"; CR in .sh: {crlf}" if crlf else "")
                      + (f"; BOM missing in .ps1: {nobom}" if nobom else ""),
                      ".sh は LF(.gitattributes の eol=lf。git checkout をやり直す)、.ps1 は UTF-8 BOM 付きが必須")
    return Result("script-encoding", "pass", detail + "; .sh LF ok, .ps1 BOM ok")


def statusline_probe_payload(root, version):
    return json.dumps({"session_id": PROBE_SID, "cwd": root, "workspace": {"project_dir": root},
                       "model": {"id": "doctor", "display_name": "Doctor"}, "version": version or "0.0.0",
                       "cost": {"total_cost_usd": 0.0, "total_lines_added": 0, "total_lines_removed": 0},
                       "context_window": {"used_percentage": 0}, "effort": {"level": "high"}}).encode("utf-8")


def check_statusline(root, probe=True, version=None):
    path = os.path.join(root, SETTINGS_REL)
    cmd = None
    try:
        cmd = ((read_json_loose(path).get("statusLine") or {}).get("command")) if os.path.isfile(path) else None
    except Exception:  # noqa: BLE001
        cmd = None
    if not cmd:
        return Result("statusline", "warn", "statusLine.command not wired in .claude/settings.json (receipt host $ / ctx% stay n/a)",
                      "配布物の settings.json の statusLine を取り込む(/91)。差し替えは settings.local.json で")
    if "statusline.py" not in cmd:
        return Result("statusline", "info", f"custom statusLine: {cmd}", "ハーネスの statusline.py を使うなら --chain で既存表示を連結できる")
    if not probe:
        return Result("statusline", "pass", f"wired: {cmd} (probe skipped)")
    rc, out, err = run(shlex.split(cmd, posix=True), timeout=60, cwd=root, stdin=statusline_probe_payload(root, version))
    logs = os.path.join(root, USAGE_LOGS_REL)
    for leftover in (PROBE_SID + ".status.json", PROBE_SID + ".statusline.txt"):
        try:
            os.remove(os.path.join(logs, leftover))
        except OSError:
            pass
    first = (out.strip().splitlines() or [""])[0]
    if rc == 0 and " | " in first and "Traceback" not in out:
        return Result("statusline", "pass", f"probe ok: {first}")
    tail = (err.strip().splitlines() or [""])[-1][:160]
    return Result("statusline", "warn", f"probe failed (rc={rc}): {first[:120] or tail}",
                  "bash / python の解決と statusline.py --selftest を確認(表示は fail-open で前回値になる)")


def check_workspace_trust(root, claude_json=None):
    if claude_json is None:
        cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        claude_json = os.path.join(cfg, ".claude.json") if cfg else os.path.join(os.path.expanduser("~"), ".claude.json")
    if not os.path.isfile(claude_json):
        return Result("workspace-trust", "info", "no ~/.claude.json (Claude Code has not run on this machine, or custom config dir)")
    try:
        data = json.load(open(claude_json, encoding="utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        return Result("workspace-trust", "info", f"cannot read {os.path.basename(claude_json)}: {e}")
    target = norm_path(root)
    matched = [v for k, v in (data.get("projects") or {}).items() if isinstance(v, dict) and norm_path(k) == target]
    if not matched:
        return Result("workspace-trust", "info", "no trust record for this folder yet (first `claude` launch here asks to trust it)",
                      "信頼ダイアログを承認するまで .claude/settings.json のフック・deny・statusLine は読まれない")
    if any(v.get("hasTrustDialogAccepted") for v in matched):
        return Result("workspace-trust", "pass", "trust dialog accepted for this folder")
    # デスクトップアプリ(Code タブ)は承認フラグを false のまま記録するが、プロジェクトの settings.json は適用される
    # (3 実プロジェクトで実測: フラグ false でもフック判定ログ・受領書が残っている)。フックが動いた痕跡があれば pass
    logs_dir = os.path.join(root, ".github", "hooks", "logs")
    evidence = [n for n in ("hook-decisions.jsonl", "hook-decisions.log") if os.path.isfile(os.path.join(logs_dir, n))]
    if evidence:
        return Result("workspace-trust", "pass",
                      f"trust flag is false in {os.path.basename(claude_json)} but hooks have run here ({evidence[0]} exists; the desktop app records false)")
    return Result("workspace-trust", "warn",
                  "trust flag NOT accepted for this folder and no hook log yet (CLI: project .claude/settings.json is not applied until accepted)",
                  "claude を起動して「Do you trust the files in this folder?」を承認する(デスクトップアプリはフラグ false のまま適用される)")


def check_distribution(root, stale_threshold=None):
    if stale_threshold is None:
        try:
            stale_threshold = int(os.environ.get(STALE_ENV, DEFAULT_STALE_GENERATIONS))
        except ValueError:
            stale_threshold = DEFAULT_STALE_GENERATIONS
    if is_body(root):
        latest, src = max_decision(root)
        return Result("distribution", "info", "harness body repository (freshness check n/a)"
                      + (f"; latest decision D{latest:03d} from {src}" if latest else ""))
    origin = read_origin(root)
    if origin is None:
        return Result("distribution", "warn", f"{ORIGIN_REL} missing (template/ZIP route, or copied by hand)",
                      "python tools/doctor.py --write-origin --harness <本体パス> で作る(sync/intake 経路は --apply が自動生成)。"
                      "以後 SessionStart フックと /91 が本体との版差を使える")
    have = origin_decision_number(origin)
    path = origin.get("path") or ""
    if have is None:
        return Result("distribution", "warn", f"{ORIGIN_REL} has no latest_decision/version (path {path or 'unknown'})",
                      "python tools/doctor.py --write-origin --harness <本体パス> --force で書き直す")
    if not path or path == "unknown" or not os.path.isdir(path):
        return Result("distribution", "info", f"copy at D{have:03d}; harness path {path or 'unknown'} not reachable from this machine",
                      "本体に届く環境で python tools/sync-harness.py を実行するか --harness で本体を指す")
    if norm_path(path) == norm_path(root):
        return Result("distribution", "info", "origin path points to this folder (body itself)")
    latest, src = max_decision(path)
    if latest is None:
        return Result("distribution", "info", f"copy at D{have:03d}; harness at {path} has no DECISIONS.md/CHANGELOG.md (ZIP copy?)",
                      "D 番号で比較できない本体。clone の本体を --harness で指す")
    sha_note = ""
    sc = origin.get("source_commit")
    if sc and sc != "unknown":
        head, _ = git_info(path)
        if head != "unknown" and not head.startswith(sc[:7]) and not sc.startswith(head[:7]):
            sha_note = f"; harness HEAD {head[:12]} != synced {sc[:12]}"
    diff = latest - have
    if diff >= stale_threshold and diff > 0:
        return Result("distribution", "warn", f"copy at D{have:03d}, harness at D{latest:03d} ({src}): {diff} generation(s) behind{sha_note}",
                      "/91-sync-from-harness を先に実行する(python tools/sync-harness.py → --apply → 手動マージ → doctor)")
    return Result("distribution", "pass", f"copy at D{have:03d} = harness D{latest:03d} ({src}){sha_note}")


def check_usage_logs(root):
    logs = os.path.join(root, USAGE_LOGS_REL)
    gi = os.path.join(root, ".gitignore")
    ignored = os.path.isfile(gi) and ".github/hooks/logs/" in open(gi, encoding="utf-8", errors="replace").read()
    gi_note = "" if ignored else "; .gitignore lacks .github/hooks/logs/"
    if not os.path.isdir(logs):
        return Result("usage-logs", "info" if ignored else "warn", f"{USAGE_LOGS_REL} not created yet (first Stop hook creates it){gi_note}",
                      "" if ignored else ".gitignore に .github/hooks/logs/ を足す(受領書は session_id を含む個人データ。RC-4)")
    names = os.listdir(logs)
    receipts = sum(1 for n in names if n.endswith(".receipt.json") or n.endswith(".receipt.draft.json"))
    status = sum(1 for n in names if n.endswith(".status.json"))
    detail = f"{receipts} receipt(s), {status} status file(s){gi_note}"
    if not ignored:
        return Result("usage-logs", "warn", detail, ".gitignore に .github/hooks/logs/ を足す(RC-4)")
    return Result("usage-logs", "pass" if receipts else "info", detail + ("" if receipts else " (no receipts yet)"))


def check_windows_8dot3(root):
    if not is_windows():
        return Result("windows-8dot3", "skip", "not Windows")
    reg = "unreadable"
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem")
        val = winreg.QueryValueEx(k, "NtfsDisable8dot3NameCreation")[0]
        reg = {0: "0 (enabled on all volumes)", 1: "1 (disabled)", 2: "2 (per-volume)", 3: "3 (system volume only)"}.get(val, str(val))
    except Exception:  # noqa: BLE001
        pass
    probe = "n/a"
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(os.path.abspath(root), buf, 1024):
            short = buf.value
            probe = "short name generated for this path" if norm_path(short) != norm_path(root) else "no short name for this path"
    except Exception:  # noqa: BLE001
        pass
    return Result("windows-8dot3", "info", f"NtfsDisable8dot3NameCreation={reg}; {probe} (guards expand 8.3 names: H-10/RG-7)")


def run_all(root, req, probe=True, stale_threshold=None):
    results = []
    claude_res = check_claude(req)
    results.append(claude_res)
    version = re.search(r"\d+\.\d+\.\d+", claude_res.detail)
    results.append(check_python())
    results.append(check_pyyaml())
    results.append(check_git())
    results.append(check_bash())
    results.append(check_optional_tool("node", "sh guards parse JSON with node -> python"))
    results.append(check_optional_tool("jq", "sh guards prefer jq -> node -> python"))
    results.append(check_hook_wiring(root))
    results.append(check_script_encoding(root))
    results.append(check_statusline(root, probe=probe, version=version.group(0) if version else None))
    results.append(check_workspace_trust(root))
    results.append(check_distribution(root, stale_threshold))
    results.append(check_usage_logs(root))
    results.append(check_windows_8dot3(root))
    return results


# ---------------------------------------------------------------- output

def summarize(results):
    counts = {lv: 0 for lv in LEVEL_ORDER}
    for r in results:
        counts[r.level] += 1
    return counts


def render(results, root, req):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cc = req["claude_code"]
    lines = [f"harness doctor  root={root.replace(chr(92), '/')}  {now}  "
             f"(claude min {cc['min']} / required {cc['required']}; platform-requirements as_of {req.get('as_of')})"]
    for r in results:
        lines.append(f"[{r.level.upper():<4}] {r.name:<17} {r.detail}")
        if r.hint and r.level in ("fail", "warn"):
            lines.append(f"       hint: {r.hint}")
    c = summarize(results)
    lines.append(f"doctor: pass {c['pass']} / warn {c['warn']} / fail {c['fail']} / info {c['info']} / skip {c['skip']}")
    return "\n".join(lines)


def exit_code(results, strict=False):
    c = summarize(results)
    if c["fail"] or (strict and c["warn"]):
        return 1
    return 0


# ---------------------------------------------------------------- selftest

def selftest():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    root = preq.find_root()
    req = preq.load(root)
    cc = req["claude_code"]
    # 版判定(claude は起動しない)
    r = evaluate_claude(req, "2.1.201 (Claude Code)")
    check("claude: 2.1.201 は min 未満で fail、未達機能を列挙", r.level == "fail" and "budget_subagent_inclusive" in r.detail, r.detail)
    r = evaluate_claude(req, cc["min"] + " (Claude Code)")
    check("claude: min ちょうどは warn(required 未満)", r.level == "warn", r.detail)
    r = evaluate_claude(req, cc["required"])
    check("claude: required 以上は pass", r.level == "pass", r.detail)
    r = evaluate_claude(req, "garbage")
    check("claude: 解釈不能は warn", r.level == "warn", r.detail)
    r = evaluate_claude(req, None, found=False)
    check("claude: 未導入は warn(Copilot のみの利用を許す)", r.level == "warn" and "not found" in r.detail, r.detail)
    # 表の出力は ASCII 主体(状態・項目名・詳細に非 ASCII を含まない)
    txt = render([r, evaluate_claude(req, "2.1.201")], root, req)
    body_lines = [ln for ln in txt.splitlines() if ln.startswith("[")]
    check("render: 状態行は ASCII のみ", all(ord(ch) < 128 for ln in body_lines for ch in ln), txt)
    check("render: 要約行を持つ", "doctor: pass" in txt)
    with tempfile.TemporaryDirectory() as td:
        # 配布鮮度: 合成の本体(DECISIONS.md D003/D007)とプロジェクト(harness-origin.md)
        body = os.path.join(td, "body")
        os.makedirs(os.path.join(body, ".github", "harness"))
        open(os.path.join(body, "DECISIONS.md"), "w", encoding="utf-8").write("## D003: a\n\n## D007: b\n")
        open(os.path.join(body, ".github", "harness", "USAGE.md"), "w", encoding="utf-8").write("usage\n")
        proj = os.path.join(td, "proj")
        os.makedirs(os.path.join(proj, "docs", "00-overview"))
        os.makedirs(os.path.join(proj, "requirements"))
        open(os.path.join(proj, "requirements", "memo.md"), "w", encoding="utf-8").write("# memo\nCLI tool\n")
        open(os.path.join(proj, "docs", "00-overview", "progress.md"), "w", encoding="utf-8").write("# p\n")

        def write_o(latest, path=body, extra=""):
            with open(os.path.join(proj, ORIGIN_REL), "w", encoding="utf-8") as f:
                f.write(f"<!-- HARNESS_ORIGIN\npath: {path.replace(chr(92), '/')}\nversion: {latest}\nsynced: 2026-09-10\n"
                        f"latest_decision: {latest}\n{extra}-->\n\n# x\n")

        write_o("D003")
        r = check_distribution(proj, 1)
        check("distribution: 4 世代古ければ warn で /91 を案内", r.level == "warn" and "4 generation" in r.detail and "/91" in r.hint, r.detail)
        r = check_distribution(proj, 5)
        check("distribution: 閾値(5)未満の差は pass 扱い", r.level == "pass", r.detail)
        write_o("D007")
        r = check_distribution(proj, 1)
        check("distribution: 同じ D 番号は pass", r.level == "pass" and "D007 = harness D007" in r.detail, r.detail)
        write_o("D005", path=os.path.join(td, "nowhere"))
        r = check_distribution(proj, 1)
        check("distribution: 本体パスに到達できなければ info(fail-open)", r.level == "info" and "not reachable" in r.detail, r.detail)
        os.remove(os.path.join(proj, ORIGIN_REL))
        r = check_distribution(proj, 1)
        check("distribution: harness-origin.md 無しは warn(--write-origin を案内)", r.level == "warn" and "--write-origin" in r.hint, r.detail)
        r = check_distribution(body, 1)
        check("distribution: 本体自身は info(対象外)", r.level == "info" and "body" in r.detail, r.detail)
        # 旧形式(version: のみ)も読める
        with open(os.path.join(proj, ORIGIN_REL), "w", encoding="utf-8") as f:
            f.write(f"<!-- HARNESS_ORIGIN\npath: {body.replace(chr(92), '/')}\nversion: D007\nsynced: 2026-09-10\n-->\n")
        r = check_distribution(proj, 1)
        check("distribution: 旧形式(version: のみ)の origin も比較できる", r.level == "pass", r.detail)
        # CHANGELOG フォールバック
        body2 = os.path.join(td, "body2")
        os.makedirs(body2)
        open(os.path.join(body2, "CHANGELOG.md"), "w", encoding="utf-8").write("- x (D065, A3-10)\n- y (D081)\n")
        check("max_decision: DECISIONS.md 無しは CHANGELOG.md の最大 D 番号", max_decision(body2) == (81, "CHANGELOG.md"))
        # --write-origin
        os.remove(os.path.join(proj, ORIGIN_REL))
        wrote, msg = write_origin(proj, harness=body)
        text = open(os.path.join(proj, ORIGIN_REL), encoding="utf-8").read()
        o = parse_origin(text)
        check("write-origin: 本体を指して作ると latest_decision / source_commit / synced_at / route を持つ",
              wrote and o.get("latest_decision") == "D007" and o.get("source_commit") and o.get("synced_at")
              and o.get("route") == "doctor" and o.get("archive_sha256") == "n/a" and "path: " in text, msg)
        wrote2, msg2 = write_origin(proj, harness=body)
        check("write-origin: 既存は force 無しで上書きしない", not wrote2 and "exists" in msg2, msg2)
        wrote3, _ = write_origin(proj, harness=body, force=True)
        check("write-origin: --force で書き直す", wrote3)
        wrote4, msg4 = write_origin(proj, harness=os.path.join(td, "nowhere"), force=True)
        check("write-origin: 存在しない本体パスは拒否", not wrote4 and "not found" in msg4, msg4)
        tpl = os.path.join(td, "tpl")
        os.makedirs(os.path.join(tpl, "docs", "00-overview"))
        open(os.path.join(tpl, "DECISIONS.md"), "w", encoding="utf-8").write("## D042: t\n")
        wrote5, _ = write_origin(tpl)
        o5 = read_origin(tpl)
        check("write-origin: --harness 無し(テンプレ経路)は手元の DECISIONS.md から latest_decision を取る",
              wrote5 and o5.get("latest_decision") == "D042" and o5.get("path") == "unknown", str(o5))
        r = check_distribution(proj, 1)
        check("distribution: doctor が書いた origin を doctor が読める", r.level == "pass", r.detail)
        # 本体判定(memo プリスティン)
        tplcopy = os.path.join(td, "tplcopy")
        os.makedirs(os.path.join(tplcopy, "requirements"))
        open(os.path.join(tplcopy, "DECISIONS.md"), "w", encoding="utf-8").write("## D001\n")
        open(os.path.join(tplcopy, "requirements", "memo.md"), "w", encoding="utf-8").write("# memo\n\n（ここから記入）\n")
        check("is_body: DECISIONS.md + テンプレのままの memo は本体扱い", is_body(tplcopy))
        open(os.path.join(tplcopy, "requirements", "memo.md"), "a", encoding="utf-8").write("CLI を作りたい\n")
        check("is_body: 実メモが書かれたら本体ではない(H-11)", not is_body(tplcopy))
        # 信頼記録: 区切り・大文字小文字の違いを正規化して照合し、他プロジェクトの情報は出さない
        cj = os.path.join(td, "claude.json")
        key_a = proj.replace("/", "\\")
        json.dump({"projects": {key_a: {"hasTrustDialogAccepted": True},
                                proj.replace("\\", "/").lower(): {"hasTrustDialogAccepted": False},
                                os.path.join(td, "other"): {"hasTrustDialogAccepted": False}}},
                  open(cj, "w", encoding="utf-8"))
        r = check_workspace_trust(proj, cj)
        check("trust: 同一フォルダのいずれかの記録が承認済みなら pass", r.level == "pass", r.detail)
        json.dump({"projects": {proj.lower(): {"hasTrustDialogAccepted": False}}}, open(cj, "w", encoding="utf-8"))
        r = check_workspace_trust(proj, cj)
        check("trust: 記録があるが未承認は warn", r.level == "warn", r.detail)
        # フラグ false でもフック判定ログがあれば適用済みとみなす(デスクトップアプリの記録)
        _logs = os.path.join(proj, ".github", "hooks", "logs")
        os.makedirs(_logs, exist_ok=True)
        open(os.path.join(_logs, "hook-decisions.jsonl"), "w", encoding="utf-8").write('{"decision":"allow"}\n')
        r = check_workspace_trust(proj, cj)
        check("trust: 未承認でもフック判定ログがあれば pass(デスクトップアプリ)", r.level == "pass" and "hooks have run" in r.detail, r.detail)
        os.remove(os.path.join(_logs, "hook-decisions.jsonl"))
        r = check_workspace_trust(os.path.join(td, "unseen"), cj)
        check("trust: 記録が無いフォルダは info", r.level == "info" and "other" not in r.detail, r.detail)
        r = check_workspace_trust(proj, os.path.join(td, "missing.json"))
        check("trust: ~/.claude.json 無しは info", r.level == "info", r.detail)
        # 配線: settings.json 無し / 先頭語が解決できない / スクリプト欠落
        r = check_hook_wiring(proj)
        check("hooks-wiring: settings.json 無しは fail", r.level == "fail", r.detail)
        os.makedirs(os.path.join(proj, ".claude"))
        os.makedirs(os.path.join(proj, SCRIPTS_REL))
        with open(os.path.join(proj, SETTINGS_REL), "w", encoding="utf-8") as f:
            json.dump({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "no-such-interp-xyz .github/hooks/scripts/inject-progress.sh"}]}]}}, f)
        r = check_hook_wiring(proj)
        check("hooks-wiring: 先頭語が PATH に無ければ fail", r.level == "fail" and "unresolved" in r.detail, r.detail)
        with open(os.path.join(proj, SETTINGS_REL), "w", encoding="utf-8") as f:
            f.write('// comment\n' + json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": sys.executable.replace("\\", "/") + " .github/hooks/scripts/inject-progress.sh"}]}]}}))
        r = check_hook_wiring(proj)
        check("hooks-wiring: 参照スクリプトが無ければ warn(validate へ誘導)、// コメント付き JSON も読める",
              r.level == "warn" and "missing script" in r.detail, r.detail)
        open(os.path.join(proj, SCRIPTS_REL, "inject-progress.sh"), "w", newline="\n").write("#!/usr/bin/env bash\n")
        r = check_hook_wiring(proj)
        check("hooks-wiring: 解決できる先頭語 + 実在スクリプトは pass", r.level == "pass" and "1 events" in r.detail, r.detail)
        # 文字コード: CRLF の .sh / BOM 無し .ps1 は fail
        r = check_script_encoding(proj)
        check("script-encoding: LF の .sh のみは pass", r.level == "pass", r.detail)
        open(os.path.join(proj, SCRIPTS_REL, "bad.sh"), "wb").write(b"#!/usr/bin/env bash\r\necho x\r\n")
        open(os.path.join(proj, SCRIPTS_REL, "bad.ps1"), "wb").write("# no bom".encode("utf-8"))
        r = check_script_encoding(proj)
        check("script-encoding: CR 入り .sh と BOM 無し .ps1 を fail で列挙", r.level == "fail" and "bad.sh" in r.detail and "bad.ps1" in r.detail, r.detail)
        # statusline: 未配線は warn、配線があれば実行テスト(本リポジトリの statusline.py を使う)
        r = check_statusline(proj, probe=False)
        check("statusline: statusLine 未配線は warn", r.level == "warn", r.detail)
        real_sl = os.path.join(root, SCRIPTS_REL, "statusline.py")
        if os.path.isfile(real_sl):
            with open(os.path.join(proj, SETTINGS_REL), "w", encoding="utf-8") as f:
                json.dump({"statusLine": {"type": "command",
                                          "command": f'"{sys.executable}" "{real_sl}"'.replace("\\", "/")}}, f)
            os.makedirs(os.path.join(proj, USAGE_LOGS_REL), exist_ok=True)
            r = check_statusline(proj, probe=True, version="2.1.201")
            leftover = os.path.join(root, USAGE_LOGS_REL, PROBE_SID + ".status.json")
            # プローブの永続化先は statusline.py 自身の logs(本リポジトリ側)なので、そちらも掃除する
            for p in (leftover, leftover.replace(".status.json", ".statusline.txt")):
                try:
                    os.remove(p)
                except OSError:
                    pass
            check("statusline: モック JSON の実行テストが 1 行返して pass", r.level == "pass" and "probe ok" in r.detail, r.detail)
        # usage-logs
        r = check_usage_logs(proj)
        check("usage-logs: .gitignore 無し + 受領書無しは warn", r.level == "warn", r.detail)
        open(os.path.join(proj, ".gitignore"), "w", encoding="utf-8").write(".github/hooks/logs/\n")
        open(os.path.join(proj, USAGE_LOGS_REL, "s1.receipt.json"), "w", encoding="utf-8").write("{}")
        r = check_usage_logs(proj)
        check("usage-logs: ignore 済み + 受領書ありは pass", r.level == "pass" and "1 receipt" in r.detail, r.detail)
        # 集計と exit code
        rs = [Result("a", "pass", "x"), Result("b", "warn", "y", "h"), Result("c", "info", "z")]
        check("exit_code: warn だけなら 0、--strict なら 1", exit_code(rs) == 0 and exit_code(rs, strict=True) == 1)
        rs.append(Result("d", "fail", "w"))
        check("exit_code: fail があれば 1", exit_code(rs) == 1)
        j = json.dumps([x.as_dict() for x in rs])
        check("json: 出力は name/level/detail/hint を持つ", '"level": "fail"' in j and '"hint"' in j)
    check("claude_version_status: validate 用 API が level を返す",
          claude_version_status(root)[0] in ("fail", "warn", "pass", "missing"))
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="harness doctor: 環境・配線・配布鮮度の pass/warn/fail 診断")
    ap.add_argument("--root", default=None, help="プロジェクト/本体のルート(既定: このファイルの親の親)")
    ap.add_argument("--json", action="store_true", help="機械可読(JSON 配列)で出す")
    ap.add_argument("--strict", action="store_true", help="WARN も exit 1 にする")
    ap.add_argument("--no-probe", action="store_true", help="statusline の実行テストを省く")
    ap.add_argument("--stale-generations", type=int, default=None,
                    help=f"配布鮮度の警告閾値(D 番号の差。既定 {DEFAULT_STALE_GENERATIONS}。環境変数 {STALE_ENV})")
    ap.add_argument("--write-origin", action="store_true", help="docs/00-overview/harness-origin.md を作る")
    ap.add_argument("--harness", default=None, help="--write-origin で記録する本体のパス")
    ap.add_argument("--force", action="store_true", help="--write-origin で既存を書き直す")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    root = os.path.abspath(args.root) if args.root else os.path.dirname(TOOLS_DIR)
    if args.write_origin:
        wrote, msg = write_origin(root, harness=args.harness, force=args.force)
        print(("OK: " if wrote else "SKIP: ") + msg)
        return 0 if wrote else 1
    req_root = preq.find_root(root) or root
    try:
        req = preq.load(req_root)
    except preq.RequirementsError as e:
        print(f"[FAIL] platform-requirements {e}")
        return 1
    results = run_all(root, req, probe=not args.no_probe, stale_threshold=args.stale_generations)
    if args.json:
        print(json.dumps({"root": root, "as_of": req.get("as_of"), "results": [r.as_dict() for r in results],
                          "summary": summarize(results)}, ensure_ascii=False, indent=2))
    else:
        print(render(results, root, req))
    return exit_code(results, strict=args.strict)


if __name__ == "__main__":
    # 表(状態・項目名・詳細)は ASCII なので符号化に依存しない。日本語の hint だけは、TTY ならコンソールの
    # 符号化(cp932 等)のまま(errors=replace で落ちない)、パイプ/ファイル/CI なら他ツールと同じ UTF-8 で出す
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

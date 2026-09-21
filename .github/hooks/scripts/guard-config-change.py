#!/usr/bin/env python3
"""ConfigChange フック(Claude Code 専用): 設定・スキルの変更を保守モード外で block する第2防衛線。

背景(2026-09-09 再監査 RG-1/CC-10/SC-3):
  guard-harness-config-edit(PreToolUse)はツール入力のコマンド文字列・パスを見る第1防衛線で、
  `python tools/x.py` のようなスクリプト経由の間接書込は原理的に検知できない。Claude Code は
  設定ファイル(.claude/settings.json 等)とスキルファイル(.claude/skills/)の変更を ConfigChange
  イベントで通知し、`decision: block` でその変更をセッションへ反映させないことができる
  (CHANGELOG 2.1.49 で追加、2.1.140 に回帰修正。実機 2.1.201 で利用可=SC-3 の反証)。
  「どの経路で書かれたか」ではなく「書かれた結果」を見るため、第1防衛線のバイパスに依存しない。

判定(matcher: project_settings|local_settings|skills):
  - 保守モード(.claude/settings.json.locked が実在=tools/harness-maintenance.py --on)なら allow。
    人間が意図して設定を変える経路はこれだけ、という運用と一致させる。
  - project_settings(.claude/settings.json): 内容が git HEAD と異なれば block
    (JSON として等価なら空白差は無視。理由: 保守モード外の設定変更)。
  - local_settings(.claude/settings.local.json): gitignore 対象で HEAD が無いのが通常のため、
    追跡されている場合だけ HEAD と比較して block。未追跡なら allow(記録のみ)。
  - skills(.claude/skills/ 配下のファイル):
      * 中核2スキル(request-routing / gate-check)は HEAD と異なれば block
      * <skill>/hooks/hooks.json と .claude-plugin/plugin.json の出現は block(任意コマンド実行経路)
      * 権限系 frontmatter の**拡張**が HEAD との差分に現れたら block(G-3/SC-2/A7-G-3 の自己権限昇格。
        第7波 2026-09-17: キー集合を公式仕様(Claude Code skills リファレンス / VS Code agent-skills /
        agentskills.io)から列挙し、「新規キー」だけでなく「値の拡張」も見る。縮小・同値・既定値の明示は allow。
        付与系(allowed-tools / tools / permissions / permission-mode / hooks / context / agent / shell / mcp /
        mcp-servers)は値のトークン集合が増えたら拡張、真偽値は disable-model-invocation true→false と
        user-invocable false→true が拡張、制限系 disallowed-tools は削除・縮小が拡張。判定関数
        privileged_expansion() は guard-harness-config-edit sh/ps1 の同名規則と selftest で意味照合する)
      * それ以外の動的なスキル追加・変更は allow(スキルの動的追加はハーネスの中核機能。D048)
  - user_settings / policy_settings / 不明な source は allow(policy は仕様上 block 不能)。

fail-open: stdin が JSON でない・git が無い・HEAD に対象が無い・例外 → 無出力で exit 0。
出力は公式仕様どおり `{"decision":"block","reason":"..."}`(systemMessage/continue は無視される
ため出さない。block は利用者にもモデルにも表示されずデバッグログにだけ残る=判定は
判定ログ logs/hook-decisions.jsonl(_log.py)に記録する)。

配線(.claude/settings.json。run-python.sh 経由で python/python3 どちらでも起動):
    "ConfigChange": [{"matcher": "project_settings|local_settings|skills",
                      "hooks": [{"type": "command",
                                 "command": "bash .github/hooks/scripts/run-python.sh .github/hooks/scripts/guard-config-change.py",
                                 "timeout": 10}]}]

自己テスト: python .github/hooks/scripts/guard-config-change.py --selftest
(一時 git リポジトリを作って判定関数を直接検証する。git が無ければ SKIP)
"""
from __future__ import annotations

import datetime
import io
import json
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_REL = os.path.join(".claude", "settings.json.locked")
# 権限系 frontmatter キー(第7波 A7-G-3。guard-harness-config-edit sh/ps1 の privileged_fm と同じ集合)
GRANT_KEYS = ("allowed-tools", "tools", "permissions", "permission-mode", "permissionmode", "hooks", "context",
              "agent", "shell", "mcp", "mcp-servers", "mcpservers")
BOOL_EXPAND = {"disable-model-invocation": False, "user-invocable": True}  # key -> 拡張になる新しい値
RESTRICT_KEYS = ("disallowed-tools",)
ALL_KEYS = GRANT_KEYS + tuple(BOOL_EXPAND) + RESTRICT_KEYS
PRIVILEGED_FM = re.compile(r"(?im)^[ \t]*(" + "|".join(re.escape(k) for k in ALL_KEYS) + r")[ \t]*:")
_FM_LINE = re.compile(r"^[ \t]*([A-Za-z][\w-]*)[ \t]*:(.*)$")
CORE_SKILLS = ("request-routing", "gate-check")
HANDLED_SOURCES = ("project_settings", "local_settings", "skills")


def fm_values(text: str) -> dict:
    """テキスト中の権限系キー行(値 + 続く字下げ行 / リスト行)を key(小文字) -> 値ブロブ で返す。
    frontmatter 境界は厳密に見ない(本文中の同名行も拾う=保守側。同値なら allow になるので誤 ask は限定的)。"""
    lines = (text or "").splitlines()
    out: dict = {}
    i = 0
    while i < len(lines):
        m = _FM_LINE.match(lines[i])
        if m and m.group(1).lower() in ALL_KEYS:
            key = m.group(1).lower()
            blob = [m.group(2)]
            j = i + 1
            while j < len(lines) and lines[j].strip() != "---" and (lines[j].startswith((" ", "\t")) or lines[j].startswith("-")):
                blob.append(lines[j])
                j += 1
            out[key] = (out.get(key, "") + "\n" + "\n".join(blob)).strip("\n")
            i = j
        else:
            i += 1
    return out


def fm_tokens(blob: str) -> set:
    """値ブロブのトークン集合(空白・カンマ・括弧・引用符で分割、小文字化。YAML リストの `-` 記号は除く)。"""
    return {t.lower() for t in re.split(r"[\s,\[\]\"'`]+", blob or "") if t and t != "-"}


def fm_truthy(blob: str) -> bool:
    toks = [t for t in re.split(r"[\s,\[\]\"'`#]+", blob or "") if t]
    return bool(toks) and toks[0].lower() in ("true", "yes", "on", "1")


def privileged_expansion(old_text: str, new_text: str, full_replace: bool = True) -> list:
    """新内容が旧内容に対して権限を拡張する権限系キーの一覧(空なら縮小・同値・既定値のみ=allow)。
    full_replace=True は新内容がファイル全体(Write / ConfigChange の HEAD 比較)、False は置換断片(Edit の new_string。
    無いキーは「変更なし」とみなす)。"""
    old, new = fm_values(old_text), fm_values(new_text)
    exp = []
    for k in GRANT_KEYS:
        if k not in new:
            continue
        nt = fm_tokens(new[k])
        if k not in old:
            if nt:
                exp.append(k)
        elif not nt <= fm_tokens(old[k]):
            exp.append(k)
    for k, expanding_value in BOOL_EXPAND.items():
        if k not in new:
            continue
        new_v = fm_truthy(new[k])
        # 旧値が無ければホストの既定値(dmi=false / user-invocable=true)。既定値はどちらも「拡張後の値」と同じなので、
        # 既定値を明示するだけの書込は拡張にならない
        old_v = fm_truthy(old[k]) if k in old else expanding_value
        if new_v == expanding_value and old_v != expanding_value:
            exp.append(k)
    for k in RESTRICT_KEYS:
        if k in old:
            if k not in new:
                if full_replace:
                    exp.append(k)  # 制限キーの削除=拡張
            elif not fm_tokens(old[k]) <= fm_tokens(new[k]):
                exp.append(k)  # 制限の縮小=拡張
    return exp


# 判定ログの共通実装(_log.py → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _log as _hooklog
except Exception:  # noqa: BLE001
    _hooklog = None
_PAYLOAD = {}  # main() が解析したペイロード(session_id / hook_event_name / tool_use_id を判定ログに載せる)


def hook_log(decision: str, target: str) -> None:
    """判定ログ(書式・redaction・置き場の正は _log.py。失敗しても判定に影響させない)。"""
    if _hooklog is None:
        return
    try:
        _hooklog.hook_log(decision, target, payload=_PAYLOAD, script=os.path.basename(__file__))
    except Exception:  # noqa: BLE001
        pass


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def find_root(file_path: str | None, cwd: str | None) -> str | None:
    """対象ファイルから遡って `.claude/` を含むディレクトリ(=プロジェクトルート)を探す。
    見つからなければ cwd(ペイロード → プロセス)に `.claude/` があればそれ、無ければ None。"""
    if file_path:
        cur = os.path.dirname(os.path.abspath(file_path))
        for _ in range(64):
            if os.path.isdir(os.path.join(cur, ".claude")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    for c in (cwd, os.getcwd()):
        if c and os.path.isdir(os.path.join(c, ".claude")):
            return os.path.abspath(c)
    return None


def git_show_head(root: str, rel: str) -> bytes | None:
    """HEAD 版の内容。追跡されていない・git が無い・リポジトリでない → None。"""
    try:
        proc = subprocess.run(
            ["git", "-C", root, "show", "HEAD:" + _norm(rel)],
            capture_output=True, timeout=10)
        if proc.returncode != 0:
            return None
        return proc.stdout
    except Exception:
        return None


def _strip_json_comments(text: str) -> str:
    return re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)


def _as_text(b: bytes | None) -> str | None:
    if b is None:
        return None
    t = b.decode("utf-8-sig", errors="replace")
    return t.replace("\r\n", "\n").replace("\r", "\n").strip()


def content_differs(head: bytes | None, cur: bytes | None) -> bool:
    """HEAD と作業ファイルの差。JSON として等価なら差なし(空白・改行差を無視)。"""
    ht, ct = _as_text(head), _as_text(cur)
    if ht is None or ct is None:
        return (ht or "") != (ct or "")
    if ht == ct:
        return False
    try:
        return json.loads(_strip_json_comments(ht)) != json.loads(_strip_json_comments(ct))
    except Exception:
        return True


def read_file(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except Exception:
        return None


def decide(payload: dict, root: str | None) -> tuple[str, str]:
    """(decision, tag)。decision は 'allow' か 'block'。"""
    source = str(payload.get("source") or "")
    file_path = payload.get("file_path")
    if source not in HANDLED_SOURCES:
        return "allow", f"source:{source or '?'}"
    if root is None:
        return "allow", f"no-root:{source}"
    if os.path.exists(os.path.join(root, LOCK_REL)):
        return "allow", f"maintenance:{source}"

    if source in ("project_settings", "local_settings"):
        rel = os.path.join(".claude", "settings.json" if source == "project_settings" else "settings.local.json")
        target = os.path.join(root, rel)
        if file_path and os.path.isabs(str(file_path)):
            # 公式ペイロードの file_path を優先(ルート配下でなければ既定パスに戻す)
            fp = os.path.abspath(str(file_path))
            if _norm(fp).lower().startswith(_norm(root).lower() + "/"):
                target = fp
                rel = os.path.relpath(fp, root)
        head = git_show_head(root, rel)
        cur = read_file(target)
        if head is None:
            return "allow", f"untracked:{_norm(rel)}"
        if cur is None:
            return "allow", f"missing:{_norm(rel)}"
        if content_differs(head, cur):
            return "block", f"changed-outside-maintenance:{_norm(rel)}"
        return "allow", f"same-as-head:{_norm(rel)}"

    # skills
    if not file_path:
        return "allow", "skills:no-file_path"
    fp = os.path.abspath(str(file_path))
    rel = _norm(os.path.relpath(fp, root)) if _norm(fp).lower().startswith(_norm(root).lower() + "/") else _norm(str(file_path))
    rel_l = rel.lower()
    cur = read_file(fp)
    head = git_show_head(root, rel)
    if cur is None:
        return "allow", f"skills:removed:{rel}"
    if rel_l.endswith("/hooks/hooks.json") or rel_l.endswith(".claude-plugin/plugin.json"):
        return "block", f"skills:hooks-json:{rel}"
    m = re.search(r"(^|/)\.claude/skills/([^/]+)/", rel_l)
    skill = m.group(2) if m else ""
    if skill in CORE_SKILLS:
        if head is None or content_differs(head, cur):
            return "block", f"skills:core-changed:{rel}"
        return "allow", f"skills:core-same:{rel}"
    expanded = privileged_expansion(_as_text(head) or "", _as_text(cur) or "", full_replace=True)
    if expanded:
        return "block", f"skills:privileged-frontmatter({','.join(sorted(set(expanded)))}):{rel}"
    return "allow", f"skills:dynamic:{rel}"


def main(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    try:
        raw = stdin.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")
        payload = json.loads(raw.lstrip("﻿"))
        if not isinstance(payload, dict):
            return 0
        global _PAYLOAD
        _PAYLOAD = payload
        root = find_root(payload.get("file_path"), payload.get("cwd"))
        decision, tag = decide(payload, root)
        hook_log(decision, tag)
        if decision == "block":
            stdout.write(json.dumps({
                "decision": "block",
                "reason": "保守モード外(.claude/settings.json.locked 無し)の設定・スキル変更のため反映を拒否しました: " + tag,
            }, ensure_ascii=False) + "\n")
            stdout.flush()
    except Exception:
        # fail-open: 例外時は無出力で継続
        return 0
    return 0


# ---------------------------------------------------------------- selftest
def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def selftest() -> int:
    import shutil
    import stat
    import tempfile

    if shutil.which("git") is None:
        print("guard-config-change selftest: SKIP (git not found)")
        return 0
    failures: list[str] = []

    def check(name, got, want):
        if got == want:
            print(f"PASS: {name}")
        else:
            print(f"FAIL: {name}: expected {want}, got {got}")
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="gcc-selftest-")
    try:
        root = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(root, ".claude", "skills", "gate-check"))
        os.makedirs(os.path.join(root, ".claude", "skills", "plain"))
        settings = os.path.join(root, ".claude", "settings.json")
        with open(settings, "w", encoding="utf-8") as fh:
            json.dump({"permissions": {"deny": ["Edit(AGENTS.md)"]}}, fh, indent=2)
        with open(os.path.join(root, ".claude", "skills", "gate-check", "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: gate-check\ndescription: core\n---\nbody\n")
        with open(os.path.join(root, ".claude", "skills", "plain", "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: plain\ndescription: x\n---\nbody\n")
        _run(["git", "init", "-q"], root)
        _run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A"], root)
        r = _run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "init"], root)
        if r.returncode != 0:
            print("guard-config-change selftest: SKIP (git commit failed: %s)" % r.stderr.strip()[:200])
            return 0

        pl = {"hook_event_name": "ConfigChange", "source": "project_settings", "file_path": settings, "cwd": root}
        check("project_settings unchanged -> allow", decide(pl, find_root(settings, root))[0], "allow")
        # 空白だけの差(JSON 等価)は変更とみなさない
        with open(settings, "w", encoding="utf-8") as fh:
            fh.write('{"permissions":{"deny":["Edit(AGENTS.md)"]}}\n')
        check("project_settings whitespace-only diff -> allow", decide(pl, root)[0], "allow")
        # deny を剥がす変更 → block
        with open(settings, "w", encoding="utf-8") as fh:
            json.dump({"permissions": {"deny": []}}, fh)
        d, tag = decide(pl, root)
        check("project_settings deny removed -> block", d, "block")
        check("project_settings block tag names file", "changed-outside-maintenance" in tag, True)
        # 保守モード(.locked 実在)なら allow
        lock = os.path.join(root, LOCK_REL)
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("{}")
        check("project_settings changed but maintenance lock -> allow", decide(pl, root)[0], "allow")
        os.remove(lock)
        # file_path 無し(cwd から解決)でも判定できる
        pl2 = {"source": "project_settings", "cwd": root}
        check("project_settings without file_path (cwd) -> block", decide(pl2, find_root(None, root))[0], "block")
        # local_settings は未追跡 → allow
        pl3 = {"source": "local_settings", "file_path": os.path.join(root, ".claude", "settings.local.json"), "cwd": root}
        with open(pl3["file_path"], "w", encoding="utf-8") as fh:
            fh.write('{"permissions":{"allow":["Bash(*)"]}}')
        check("local_settings untracked -> allow", decide(pl3, root)[0], "allow")
        # skills: 権限系 frontmatter の新規追加 → block
        evil = os.path.join(root, ".claude", "skills", "evil", "SKILL.md")
        os.makedirs(os.path.dirname(evil))
        with open(evil, "w", encoding="utf-8") as fh:
            fh.write("---\nname: evil\nallowed-tools: Bash(*)\n---\nrun\n")
        pls = {"source": "skills", "file_path": evil, "cwd": root}
        d, tag = decide(pls, root)
        check("skills new allowed-tools frontmatter -> block", d, "block")
        check("skills block tag names key", "allowed-tools" in tag, True)
        with open(evil, "w", encoding="utf-8") as fh:
            fh.write("---\nname: evil\ndescription: x\nhooks:\n  PreToolUse: []\n---\nrun\n")
        check("skills new hooks: frontmatter -> block", decide(pls, root)[0], "block")
        # 権限系キー無しの動的スキル追加 → allow
        with open(evil, "w", encoding="utf-8") as fh:
            fh.write("---\nname: evil\ndescription: x\n---\nrun\n")
        check("skills plain new skill -> allow", decide(pls, root)[0], "allow")
        # 第7波(A7-G-3): 拡張だけ block、縮小・同値・既定値の明示は allow
        with open(evil, "w", encoding="utf-8") as fh:
            fh.write("---\nname: evil\ndescription: x\ndisable-model-invocation: true\nuser-invocable: false\ndisallowed-tools: Bash\n---\nrun\n")
        check("skills new skill with restrictions only (dmi true / ui false / disallowed-tools) -> allow", decide(pls, root)[0], "allow")
        with open(evil, "w", encoding="utf-8") as fh:
            fh.write("---\nname: evil\ndescription: x\ndisable-model-invocation: false\nuser-invocable: true\n---\nrun\n")
        check("skills new skill with default values (dmi false / ui true) -> allow", decide(pls, root)[0], "allow")
        with open(evil, "w", encoding="utf-8") as fh:
            fh.write("---\nname: evil\ndescription: x\ncontext: fork\nagent: reviewer\n---\nrun\n")
        d, tag = decide(pls, root)
        check("skills new context: fork + agent -> block", d, "block")
        check("skills block tag lists agent and context", "agent" in tag and "context" in tag, True)
        # 既存の非中核スキル(HEAD あり)に対する値の拡張 / 縮小
        granted = os.path.join(root, ".claude", "skills", "granted", "SKILL.md")
        os.makedirs(os.path.dirname(granted))
        with open(granted, "w", encoding="utf-8") as fh:
            fh.write("---\nname: granted\ndescription: g\nallowed-tools: Read Grep\ndisable-model-invocation: true\ndisallowed-tools: Bash\n---\nbody\n")
        # granted だけをコミットする(-A にすると途中で書き換えた settings.json が HEAD に入り、後続の main() 検査の前提が崩れる)
        _run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "add", os.path.join(".claude", "skills", "granted", "SKILL.md")], root)
        _run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "granted"], root)
        plg = {"source": "skills", "file_path": granted, "cwd": root}
        check("skills granted unchanged -> allow", decide(plg, root)[0], "allow")
        with open(granted, "w", encoding="utf-8") as fh:
            fh.write("---\nname: granted\ndescription: g\nallowed-tools: Read Grep Bash(git:*)\ndisable-model-invocation: true\ndisallowed-tools: Bash\n---\nbody\n")
        check("skills allowed-tools grows (Bash added) -> block", decide(plg, root)[0], "block")
        with open(granted, "w", encoding="utf-8") as fh:
            fh.write("---\nname: granted\ndescription: g\nallowed-tools: Read\ndisable-model-invocation: true\ndisallowed-tools: Bash\n---\nbody\n")
        check("skills allowed-tools shrinks (Grep removed) -> allow", decide(plg, root)[0], "allow")
        with open(granted, "w", encoding="utf-8") as fh:
            fh.write("---\nname: granted\ndescription: g\nallowed-tools: Read Grep\ndisable-model-invocation: false\ndisallowed-tools: Bash\n---\nbody\n")
        check("skills disable-model-invocation true -> false -> block", decide(plg, root)[0], "block")
        with open(granted, "w", encoding="utf-8") as fh:
            fh.write("---\nname: granted\ndescription: g\nallowed-tools: Read Grep\ndisable-model-invocation: true\n---\nbody\n")
        check("skills disallowed-tools removed -> block", decide(plg, root)[0], "block")
        with open(granted, "w", encoding="utf-8") as fh:
            fh.write("---\nname: granted\ndescription: g\nallowed-tools:\n  - Read\n  - Grep\ndisable-model-invocation: true\ndisallowed-tools: Bash Write\n---\nbody\n")
        check("skills same tools as YAML list + disallowed-tools grows -> allow", decide(plg, root)[0], "allow")
        check("privileged_expansion: Edit fragment lacking disallowed-tools is not a removal",
              privileged_expansion("disallowed-tools: Bash\n", "description: y\n", full_replace=False), [])
        check("privileged_expansion: user-invocable false -> true is expansion",
              privileged_expansion("user-invocable: false\n", "user-invocable: true\n"), ["user-invocable"])
        check("privileged_expansion: hooks block gains a new event -> expansion",
              privileged_expansion("hooks:\n  PostToolUse: []\n", "hooks:\n  PostToolUse: []\n  PreToolUse:\n    - matcher: Bash\n"), ["hooks"])
        # 既存の非中核スキルの本文変更 → allow
        plain = os.path.join(root, ".claude", "skills", "plain", "SKILL.md")
        with open(plain, "a", encoding="utf-8") as fh:
            fh.write("more\n")
        check("skills non-core body change -> allow", decide({"source": "skills", "file_path": plain, "cwd": root}, root)[0], "allow")
        # hooks/hooks.json の出現 → block
        hj = os.path.join(root, ".claude", "skills", "plain", "hooks", "hooks.json")
        os.makedirs(os.path.dirname(hj))
        with open(hj, "w", encoding="utf-8") as fh:
            fh.write("{}")
        check("skills hooks/hooks.json -> block", decide({"source": "skills", "file_path": hj, "cwd": root}, root)[0], "block")
        # 中核スキルの変更 → block、未変更 → allow
        core = os.path.join(root, ".claude", "skills", "gate-check", "SKILL.md")
        check("skills core unchanged -> allow", decide({"source": "skills", "file_path": core, "cwd": root}, root)[0], "allow")
        with open(core, "a", encoding="utf-8") as fh:
            fh.write("tampered\n")
        check("skills core changed -> block", decide({"source": "skills", "file_path": core, "cwd": root}, root)[0], "block")
        # 削除されたスキルファイル → allow
        check("skills removed file -> allow", decide({"source": "skills", "file_path": os.path.join(root, ".claude", "skills", "gone", "SKILL.md"), "cwd": root}, root)[0], "allow")
        # 対象外 source → allow
        check("policy_settings -> allow", decide({"source": "policy_settings", "cwd": root}, root)[0], "allow")
        # git リポジトリでない(HEAD 無し) → fail-open allow
        noroot = os.path.join(tmp, "nogit")
        os.makedirs(os.path.join(noroot, ".claude"))
        with open(os.path.join(noroot, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        check("project_settings outside git -> allow (fail-open)",
              decide({"source": "project_settings", "file_path": os.path.join(noroot, ".claude", "settings.json")}, noroot)[0], "allow")
        # main(): 壊れた JSON → 無出力 exit 0 / block 時は decision JSON
        out = io.StringIO()
        rc = main(io.BytesIO(b"{not json"), out)
        check("main broken JSON -> exit 0 no output", (rc, out.getvalue()), (0, ""))
        out = io.StringIO()
        rc = main(io.BytesIO(json.dumps(pl).encode("utf-8")), out)
        try:
            parsed = json.loads(out.getvalue())
        except Exception:
            parsed = None
        check("main block -> decision JSON", (rc, (parsed or {}).get("decision")), (0, "block"))
        with open(settings, "w", encoding="utf-8") as fh:
            json.dump({"permissions": {"deny": ["Edit(AGENTS.md)"]}}, fh, indent=2)
        out = io.StringIO()
        rc = main(io.BytesIO(json.dumps(pl).encode("utf-8")), out)
        check("main allow -> no output", (rc, out.getvalue()), (0, ""))
    finally:
        def _onerror(func, path, _exc):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass
        shutil.rmtree(tmp, onerror=_onerror)

    print("guard-config-change selftest: %s" % ("FAIL %d" % len(failures) if failures else "all passed"))
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""InstructionsLoaded フック: 常駐指示のロード実測を記録する(再監査 CC-9 / A5-5 の実測経路)。

「常駐指示は 200 行まで」という予算(A5-5)は、これまで AGENTS.md / CLAUDE.md の静的な
行数・バイト数(validate-harness.py の (h))でしか測れなかった。実際に何がいつ
ロードされたか(nested CLAUDE.md、.claude/rules/*.md、@include、圧縮後の再ロード)は
InstructionsLoaded でしか観測できない。本フックはロード 1 件ごとに

    .github/hooks/logs/instructions-loaded.jsonl

へ 1 行追記する(file_content 本文は記録しない。行数・バイト数だけ)。
集計は tools/validate-harness.py が「直近セッションの総ロード行数」を INFO 表示し、
200 行超なら WARN にする。

公式仕様(https://code.claude.com/docs/en/hooks、2026-09-10 確認):
- 入力: 共通欄 + `load_reason`(session_start / nested_traversal / path_glob_match /
  include / compact)+ `file_path`(絶対パス)+ `file_content`(全文)。
- 出力・exit code は無視される。block はできない。
- matcher は load_reason(完全一致または正規表現)。本ハーネスの配線は
  `session_start|compact|include`(常駐分・圧縮後の再ロード分・`@AGENTS.md` の展開分。
  遅延ロード分 nested_traversal / path_glob_match は対象外=常駐予算の実測が目的のため)。
  第3波(2026-09-17)の実測: 2.1.201 の 104 セッションで session_start に記録されたのは CLAUDE.md
  18 行だけで、`@AGENTS.md`(192 行)は `include` として来るため matcher から漏れていた。
  validate (i) は session_start と include を常駐として合算する。

設計方針: いかなる失敗でも無出力 exit 0 の fail-open。file_content が無い(将来の欄変更等)
場合は file_path を読んで数える(読めなければ null)。記録先は環境変数
HARNESS_HOOK_LOG_DIR で差し替え可(自己テスト用)。

配線(.claude/settings.json の hooks.InstructionsLoaded、matcher "session_start|compact|include"):
    bash .github/hooks/scripts/run-python.sh .github/hooks/scripts/log-instructions-loaded.py
自己テスト: python .github/hooks/scripts/log-instructions-loaded.py --selftest
"""
import datetime
import json
import os
import sys

LOG_NAME = "instructions-loaded.jsonl"


def read_stdin_utf8():
    raw = getattr(sys.stdin, "buffer", None)
    data = raw.read() if raw is not None else sys.stdin.read().encode("utf-8", "replace")
    return data.decode("utf-8", "replace").lstrip(chr(0xFEFF))


def log_dir():
    override = os.environ.get("HARNESS_HOOK_LOG_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")


def measure(content, file_path):
    """(行数, バイト数) を返す。content が無ければ file_path を読む。取れなければ (None, None)。"""
    text = content
    if not isinstance(text, str):
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            return None, None
    if text == "":
        return 0, 0
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    return lines, len(text.encode("utf-8"))


def relative_path(file_path, cwd):
    """cwd 配下なら cwd 相対の / 区切り、そうでなければそのまま返す。"""
    try:
        if file_path and cwd:
            rel = os.path.relpath(file_path, cwd)
            if not rel.startswith(".."):
                return rel.replace("\\", "/")
    except Exception:
        pass
    return file_path


def build_record(payload):
    cwd = payload.get("cwd") or os.getcwd()
    file_path = payload.get("file_path") or ""
    lines, nbytes = measure(payload.get("file_content"), file_path)
    return {
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "session_id": str(payload.get("session_id") or "unknown"),
        "load_reason": str(payload.get("load_reason") or "unknown"),
        "file_path": relative_path(file_path, cwd),
        "lines": lines,
        "bytes": nbytes,
    }


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        # Windows の stdin は既定でコードページ(CP932)復号になり、日本語を含む
        # file_content の行数・バイト数が狂う(実測: 3 行 17B が 2 行 26B)ため、
        # バイト列を UTF-8 で明示復号する
        payload = json.loads(read_stdin_utf8())
    except Exception:
        return
    if not isinstance(payload, dict) or not payload.get("file_path"):
        return
    record = build_record(payload)
    try:
        d = log_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, LOG_NAME), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- selftest
def selftest():
    import io
    import shutil
    import tempfile
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (f" ({detail})" if detail and not cond else ""))
        ok = ok and cond

    tmp = tempfile.mkdtemp(prefix="instructions-loaded-selftest-")
    old_env = os.environ.get("HARNESS_HOOK_LOG_DIR")
    old_stdin = sys.stdin
    try:
        logs = os.path.join(tmp, "logs")
        os.environ["HARNESS_HOOK_LOG_DIR"] = logs
        out = os.path.join(logs, LOG_NAME)

        sys.stdin = io.StringIO("")
        main()
        check("empty stdin: 何も記録しない", not os.path.exists(out))

        sys.stdin = io.StringIO(json.dumps({"session_id": "s1", "hook_event_name": "InstructionsLoaded"}))
        main()
        check("file_path 無し: 何も記録しない", not os.path.exists(out))

        content = "# 指示\n" + "行\n" * 9  # 10 行
        payload = {"session_id": "s1", "cwd": tmp, "hook_event_name": "InstructionsLoaded",
                   "load_reason": "session_start",
                   "file_path": os.path.join(tmp, "CLAUDE.md"), "file_content": content}
        sys.stdin = io.StringIO(json.dumps(payload))
        main()
        rec = {}
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                rec = json.loads(f.read().splitlines()[-1])
        check("record: 行数・バイト数を file_content から算出",
              rec.get("lines") == 10 and rec.get("bytes") == len(content.encode("utf-8")), str(rec))
        check("record: file_path は cwd 相対の / 区切り", rec.get("file_path") == "CLAUDE.md", str(rec.get("file_path")))
        check("record: 本文(file_content)は記録しない", "file_content" not in rec and "指示" not in json.dumps(rec, ensure_ascii=False))
        check("record: load_reason を保持", rec.get("load_reason") == "session_start")

        # file_content が無ければ file_path を読んで数える
        rules = os.path.join(tmp, ".claude", "rules")
        os.makedirs(rules)
        with open(os.path.join(rules, "a.md"), "w", encoding="utf-8") as f:
            f.write("a\nb\nc")  # 末尾改行なし = 3 行
        payload = {"session_id": "s1", "cwd": tmp, "load_reason": "compact",
                   "file_path": os.path.join(rules, "a.md")}
        sys.stdin = io.StringIO(json.dumps(payload))
        main()
        with open(out, encoding="utf-8") as f:
            recs = [json.loads(l) for l in f.read().splitlines()]
        check("record: file_content 無しは file_path を読む(末尾改行なし 3 行)",
              len(recs) == 2 and recs[-1]["lines"] == 3 and recs[-1]["file_path"] == ".claude/rules/a.md", str(recs[-1:]))

        # 読めないパスでも落ちず null で記録
        payload["file_path"] = os.path.join(tmp, "missing.md")
        sys.stdin = io.StringIO(json.dumps(payload))
        main()
        with open(out, encoding="utf-8") as f:
            last = json.loads(f.read().splitlines()[-1])
        check("record: 読めないファイルは lines/bytes=null", last["lines"] is None and last["bytes"] is None)
    finally:
        sys.stdin = old_stdin
        if old_env is None:
            os.environ.pop("HARNESS_HOOK_LOG_DIR", None)
        else:
            os.environ["HARNESS_HOOK_LOG_DIR"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

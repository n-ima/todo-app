#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code の Stop フックから呼ばれ、「アプリのコードを変更したのに docs/ に何も
記録していない」状態でターンを終えようとしたら1回だけブロックして記録を促す(D043決定6)。

設計方針:
- 教訓・台帳(change-requests.md)・tasks.md への記録は「気づいたら書く」任せでは
  実測で書かれなかった(改善の記録が一切残らない失敗の直接原因)。終了時に機械的な
  トリガを与える。モデルを介さないためコストはほぼゼロ。
- git status ではなくトランスクリプト解析で「このセッションでの編集」だけを見る
  (作業ツリーに元からある未コミット変更で毎ターン誤発火させないため)。
- stop_hook_active が真なら即継続(ブロック→応答→再Stopの無限ループ防止)。
- docs/00-overview/progress.md があるプロジェクトのみ対象(ハーネス本体は対象外)。
- 判定は保守的に: サブエージェント(task-worker)内の編集はメインのトランスクリプトに
  現れないため検知できない(誤発火しない側に倒れる)。docs/ 配下のどこかを1回でも
  編集していれば「記録あり」とみなす(実装フェーズの tasks.md 更新も記録に数える)。
- 解析はいかなる失敗でも継続(exit 0 + 出力なし)。Stop フックのブロックは
  {"decision": "block", "reason": "..."} のJSON出力で行う。
- ブロックは同一セッションで1回だけ: ブロック時に .github/hooks/logs/ に session_id を
  キーにしたマーカーファイルを残し、マーカーがあれば以降のStopでは再ブロックしない
  (一度弁明したセッションで毎ターン再ブロックされるのを防ぐ。マーカー操作の失敗は
  fail-open)。
- トランスクリプトの鮮度(CC-14/A6-18): 公式 hooks 文書は「transcript は非同期書込で
  Stop 時点では当ターンの最終メッセージを含まないことがある。当ターンの最終応答は
  `last_assistant_message` を使え」と明記する。ペイロードに `last_assistant_message` が
  ある場合はそれを一次入力とし、トランスクリプト末尾のアシスタント本文と一致しない
  (=当ターン未反映)ときは判定を保留してブロックしない(当ターンの docs 記録が未反映の
  誤ブロック、当ターンのアプリ編集が未反映の素通しの両方を「保留」に倒す。保留は
  マーカーを書かないので次の Stop で再判定される)。Stop 入力に uuid は無いため、
  突合は本文の一致で行う。フィールドが無い旧ホストでは従来どおり transcript のみで判定する。
- 自己テスト: python .github/hooks/scripts/remind-record.py --selftest
  (selftest.sh からも呼ばれる。CI では ubuntu ジョブの selftest.sh 経由)
"""
import datetime
import json
import os
import re
import sys

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# ハーネス管理領域(ここへの編集は「記録」または「ハーネス自身の作業」とみなす)。
# guard-phase-scope.sh の許可パターンと同じ考え方で、ルート相対の先頭セグメントで照合する
# (部分文字列一致では src/tools/ 等のアプリコードがハーネス扱いになり、アプリ内の
# frontend/docs/ 等が docs/ への記録と誤認されていた)。
HARNESS_PREFIXES = ("docs/", "requirements/", ".github/", ".claude/", ".agents/",
                    "tools/", ".vscode/", "temp/", "tmp/")
HARNESS_SUFFIX = ("readme.md", ".gitignore", ".gitattributes", "decisions.md", "memory.md")

# 判定ログの共通実装(_log.py → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _log as _hooklog
except Exception:  # noqa: BLE001
    _hooklog = None
_PAYLOAD = {}  # main() が解析したペイロード(session_id / hook_event_name / tool_use_id を判定ログに載せる)


# selftest がマーカー/ログの置き場を一時ディレクトリに差し替えるための上書き(本番は None)
_LOG_DIR_OVERRIDE = None


def log_dir():
    if _LOG_DIR_OVERRIDE:
        return _LOG_DIR_OVERRIDE
    # 記録先(マーカー・判定ログ)は HARNESS_HOOK_LOG_DIR で差し替え可(selftest が実 logs/ を汚さない。他の python フックと同じ)
    override = os.environ.get("HARNESS_HOOK_LOG_DIR")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")


def hook_log(decision, target):
    """判定ログ(ローカルのみ・gitignore 対象)。書式・redaction・置き場の正は _log.py。失敗しても判定に影響させない。"""
    if _hooklog is None:
        return
    try:
        _hooklog.hook_log(decision, target, payload=_PAYLOAD, script=os.path.basename(__file__),
                          log_dir_override=log_dir())
    except Exception:  # noqa: BLE001
        pass


def norm(path):
    return path.replace("\\", "/").lower()


def classify(paths, cwd):
    """(app_edits, docs_edits) を数える。リポジトリ外(スクラッチパッド等)は対象外。"""
    root = norm(os.path.abspath(cwd)).rstrip("/") + "/"
    app = docs = 0
    for p in paths:
        n = norm(os.path.abspath(os.path.join(cwd, p)) if not os.path.isabs(p) else p)
        if not n.startswith(root):
            continue
        rel = n[len(root):]
        if rel.startswith("docs/"):
            docs += 1
        elif (any(rel == s or rel.endswith("/" + s) for s in HARNESS_SUFFIX)
                or rel.startswith(HARNESS_PREFIXES)):
            # 固定名はパス区切り単位で照合する(素朴な endswith では
            # src/GameMemory.md が memory.md に誤マッチしてアプリ編集から漏れる)
            continue
        else:
            app += 1
    return app, docs


def scan_transcript(transcript):
    """(編集ツールの対象パス一覧, 末尾のアシスタント本文) を返す。失敗時は取れた分だけ。"""
    paths = []
    last_text = None
    with open(transcript, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            has_tool = '"tool_use"' in line
            is_assistant = '"assistant"' in line
            if not has_tool and not is_assistant:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            content = (entry.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            texts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use" and item.get("name") in EDIT_TOOLS:
                    inp = item.get("input") or {}
                    fp = inp.get("file_path") or inp.get("notebook_path") or ""
                    if fp:
                        paths.append(fp)
                elif item.get("type") == "text" and entry.get("type") == "assistant":
                    texts.append(item.get("text") or "")
            if texts and "".join(texts).strip():
                last_text = "\n".join(texts)
    return paths, last_text


def _squash(s):
    return " ".join((s or "").split())


def transcript_is_current(last_text, last_assistant_message):
    """トランスクリプト末尾のアシスタント本文が Stop ペイロードの最終応答と一致するか。
    一致 = 当ターンまで書き込まれている(判定してよい)。空・不一致 = 未反映(保留)。
    複数 text ブロックの連結/末尾ブロックのみ、どちらの形で来ても拾えるよう末尾一致も許す。"""
    a = _squash(last_text)
    b = _squash(last_assistant_message)
    if not a or not b:
        return False
    return a == b or a.endswith(b) or b.endswith(a)


def read_stdin_utf8():
    """stdin をバイト列で読み UTF-8 で明示復号する(Windows の python は既定で CP932 復号になり、日本語を含む
    ペイロードが化ける・落ちる=D074 の指摘)。先頭 BOM(PowerShell 5.1 のパイプ)は除去する。"""
    raw = getattr(sys.stdin, "buffer", None)
    data = raw.read() if raw is not None else sys.stdin.read().encode("utf-8", "replace")
    return data.decode("utf-8", "replace").lstrip(chr(0xFEFF))


def main():
    # WindowsのstdoutはcodepageがCP932になりうる。フック出力のJSONはUTF-8で返す
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        payload = json.loads(read_stdin_utf8())
    except Exception:
        return
    global _PAYLOAD
    _PAYLOAD = payload if isinstance(payload, dict) else {}
    if payload.get("stop_hook_active"):
        return
    # 一度ブロックしたセッションでは再ブロックしない(マーカーがあれば即継続)。
    # マーカー操作はいかなる失敗でも判定に影響させない(fail-open)。
    marker = None
    sid = payload.get("session_id") or ""
    if sid:
        try:
            marker = os.path.join(
                log_dir(),
                "remind-record-" + re.sub(r"[^A-Za-z0-9_-]", "_", sid) + ".blocked")
            if os.path.exists(marker):
                return
        except Exception:
            marker = None
    cwd = payload.get("cwd") or os.getcwd()
    if not os.path.exists(os.path.join(cwd, "docs", "00-overview", "progress.md")):
        return
    transcript = payload.get("transcript_path") or ""
    if not transcript or not os.path.exists(transcript):
        return

    try:
        paths, last_text = scan_transcript(transcript)
    except Exception:
        return

    # 鮮度ゲート(CC-14): 最終応答がトランスクリプトに未反映なら当ターンの編集・記録も
    # 欠けている可能性があるため判定を保留する(block しない・マーカーも書かない)。
    lam = payload.get("last_assistant_message")
    if lam is not None and not transcript_is_current(last_text, lam):
        hook_log("hold", "transcript-lag session=%s" % (sid or "unknown"))
        return

    app, docs = classify(paths, cwd)
    if app > 0 and docs == 0:
        if marker:
            try:
                os.makedirs(os.path.dirname(marker), exist_ok=True)
                with open(marker, "w", encoding="utf-8") as f:
                    f.write("blocked once\n")
            except Exception:
                pass
        hook_log("block", "app=%d docs=0 session=%s" % (app, sid or "unknown"))
        print(json.dumps({
            "decision": "block",
            "reason": ("このセッションでアプリのコードを変更しましたが、docs/ に記録がありません。"
                       "終了する前に該当する記録を残してください: 変更請求なら "
                       "docs/00-overview/change-requests.md の台帳更新、実装タスクなら "
                       "docs/03-implementation/tasks.md、確立した実行方法や受けた訂正は "
                       "docs/00-overview/learnings.md に1行。記録が本当に不要な場合は、"
                       "その理由を1行ユーザーに説明してから終了してください。")
        }, ensure_ascii=False))


# ---------------------------------------------------------------- selftest

def selftest():
    """一時ディレクトリにフィクスチャを作り、block/通過/ループ防止/鮮度ゲート/1回限りを検証する。"""
    global _LOG_DIR_OVERRIDE
    import io
    import shutil
    import tempfile
    ok = True
    try:  # CP932 コンソールでも日本語の PASS/FAIL 行で落ちない
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + ((" (%s)" % detail) if detail and not cond else ""))
        ok = ok and cond

    tmp = tempfile.mkdtemp(prefix="remind-record-selftest-")
    _LOG_DIR_OVERRIDE = os.path.join(tmp, "logs")
    try:
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, "docs", "00-overview"))
        with open(os.path.join(proj, "docs", "00-overview", "progress.md"), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\n-->\n")
        tpath = os.path.join(tmp, "t.jsonl")

        def entry(kind, **kw):
            if kind == "edit":
                return json.dumps({"type": "assistant", "uuid": kw.get("uuid", "u"), "message": {"content": [
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": kw["path"]}}]}}, ensure_ascii=False)
            return json.dumps({"type": "assistant", "uuid": kw.get("uuid", "u"), "message": {"content": [
                {"type": "text", "text": kw["text"]}]}}, ensure_ascii=False)

        def write_transcript(lines):
            with open(tpath, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

        def run(payload):
            payload = dict(payload)
            payload.setdefault("transcript_path", tpath)
            payload.setdefault("cwd", proj)
            old_in, old_out = sys.stdin, sys.stdout
            sys.stdin = io.StringIO(json.dumps(payload, ensure_ascii=False))
            sys.stdout = io.StringIO()
            try:
                main()
                return sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = old_in, old_out

        app = os.path.join(proj, "src", "main.py")
        doc = os.path.join(proj, "docs", "00-overview", "change-requests.md")

        # 1. 旧ホスト(last_assistant_message 無し): アプリ編集のみ -> block
        write_transcript([entry("edit", path=app), entry("text", text="実装しました")])
        out = run({"session_id": "s1"})
        check("旧ホスト(フィールド無し): アプリ編集のみ -> block", '"block"' in out, out[:80])
        # 2. 同一セッション2回目はマーカーにより再ブロックしない
        out = run({"session_id": "s1"})
        check("同一セッション2回目 -> 再ブロックしない(マーカー)", out == "", out[:80])
        # 3. docs 記録あり -> 通過
        write_transcript([entry("edit", path=app), entry("edit", path=doc), entry("text", text="記録しました")])
        out = run({"session_id": "s2"})
        check("アプリ編集 + docs 記録 -> 通過", out == "", out[:80])
        # 4. stop_hook_active -> 通過(ループ防止)
        write_transcript([entry("edit", path=app), entry("text", text="x")])
        out = run({"session_id": "s3", "stop_hook_active": True})
        check("stop_hook_active -> 通過", out == "", out[:80])
        # 5. 鮮度ゲート: 最終応答が transcript 末尾と不一致(当ターン未反映) -> 保留(block しない・マーカー無し)
        write_transcript([entry("edit", path=app), entry("text", text="前のターンの応答です")])
        out = run({"session_id": "s4", "last_assistant_message": "記録を追加して終了します"})
        marker = os.path.join(_LOG_DIR_OVERRIDE, "remind-record-s4.blocked")
        check("鮮度ゲート: 末尾不一致 -> 保留(block しない)", out == "", out[:80])
        check("鮮度ゲート: 保留ではマーカーを書かない", not os.path.exists(marker))
        # 6. 鮮度ゲート: 一致 -> 判定に進み block
        write_transcript([entry("edit", path=app), entry("text", text="実装しました。\n\n終了します")])
        out = run({"session_id": "s4", "last_assistant_message": "実装しました。\n\n終了します"})
        check("鮮度ゲート: 末尾一致 -> block", '"block"' in out, out[:80])
        check("block 後はマーカーが書かれる", os.path.exists(marker))
        # 7. 鮮度ゲート: 空の最終応答 -> 保留
        write_transcript([entry("edit", path=app), entry("text", text="x")])
        out = run({"session_id": "s5", "last_assistant_message": ""})
        check("鮮度ゲート: 空の最終応答 -> 保留", out == "", out[:80])
        # 8. 鮮度ゲート: 一致していれば docs 記録ありは通過(誤ブロックしない)
        write_transcript([entry("edit", path=app), entry("edit", path=doc), entry("text", text="台帳に記録しました")])
        out = run({"session_id": "s6", "last_assistant_message": "台帳に記録しました"})
        check("鮮度ゲート: 一致 + docs 記録あり -> 通過", out == "", out[:80])
        # 9. progress.md 無し -> 対象外
        proj2 = os.path.join(tmp, "proj2")
        os.makedirs(proj2)
        write_transcript([entry("edit", path=os.path.join(proj2, "src", "a.py")), entry("text", text="x")])
        out = run({"session_id": "s7", "cwd": proj2})
        check("progress.md 無し -> 対象外", out == "", out[:80])
        # 10. リポジトリ外の編集は数えない
        write_transcript([entry("edit", path=os.path.join(tmp, "elsewhere", "x.py")), entry("text", text="x")])
        out = run({"session_id": "s8"})
        check("リポジトリ外の編集 -> 通過", out == "", out[:80])
    finally:
        _LOG_DIR_OVERRIDE = None
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    main()

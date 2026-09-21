#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop フック用の有界自動継続番犬(watchdog)。**既定では未配線の opt-in**。

GATE_STATUS が in_progress のままターンが停止したとき、直近の応答が承認・質問待ちで
なければ「次の未完了タスクの続行かブロッカーの明示」を促す block を返し自動継続させる。
同一セッションでの継続は上限(既定3回。環境変数 WATCHDOG_CONTINUE_LIMIT で変更可)まで。

最新の Stop フック仕様への追随:
- 入力の stop_reason が max_tokens 系(トークン枯渇による停止)なら継続しない。
  枯渇は「完了」でも「承認待ち」でもなく、block しても同じ枯渇を繰り返すだけ
  (トークン枯渇を完了と誤認して block しない)。
- 入力に last_assistant_message(直近アシスタント応答)があればそれを承認・質問待ち
  判定に使い、無ければ従来どおりトランスクリプトから読む。どちらからも取れなければ
  待ち側に倒して継続しない(従来の保守的動作を維持)。
- Claude Code 本体にも Stop フック block の連続回数上限(環境変数
  CLAUDE_CODE_STOP_HOOK_BLOCK_CAP)がある。本スクリプトの WATCHDOG_CONTINUE_LIMIT は
  セッション累計の上限で、実効上限は両者の小さい方(main() 内コメント参照)。

配線方法(opt-in。gate-hooks.json には配線しない):
- 有効化する場合のみ、`.claude/settings.json` の hooks の Stop に、remind-record.py と
  同じ様式で `bash .github/hooks/scripts/run-python.sh
  .github/hooks/scripts/watchdog-continue.py` を追加する。

暴走対策(Claude Code Issue #55754 型の無限ループへの注意):
- Stop フックの block は「block→応答→再Stop→再block」の無限ループになりうる。
  本スクリプトは (1) stop_hook_active が真なら即 exit 0、(2) session_id ごとの
  カウンタ(logs/ 配下)で継続回数を上限に束縛、の二重の歯止めを持つ。
  **どちらの歯止めも外さないこと。**カウンタが書けない環境では継続させない
  (無限ループ防止を利便より優先)。
- 継続させた判断も見送った判断も、すべて判定ログ(_log.py → logs/hook-decisions.jsonl)に記録する。
- 解析はいかなる失敗でも継続(exit 0 + 出力なし)の fail-open。

事故的停止の記録(A2-1。目標達成評価への接続):
- 継続上限に達して in_progress のまま止まった(limit-reached)、またはトークン枯渇(stop_reason max_tokens)で
  in_progress のまま止まったセッションは `logs/watchdog-stop-<session_id>.json` に
  {recorded_at, hook_event_name, session_id, kind(watchdog_limit | max_tokens), reason, cwd, head_sha, gate_status,
   transcript_path} を残す(mark-abnormal-stop.py の abnormal-stop-<sid>.json と同じ骨格。1 セッション 1 ファイル)。
- 読み手は _log.py の iter_stop_records / stop_records_for_sessions。tools/golden-eval.py はこの記録と
  セッション開始時の baseline を突き合わせ「事故的停止で終わったセッション中の done 宣言」を達成扱いにしない(NG)、
  tools/e2e-run.py は走行の session_id に記録があれば verdict を DNF_ABNORMAL_STOP にする。
- 自己テスト: python .github/hooks/scripts/watchdog-continue.py --selftest
"""
import datetime
import json
import os
import re
import subprocess
import sys

DEFAULT_LIMIT = 3
# 直近の応答がこれらを含むなら「承認・質問待ち」とみなし継続させない(保守的判定)
WAIT_MARKERS = ("?", "？", "承認", "確認してください", "よろしいですか",
                "よろしいでしょうか", "どうしますか", "選択してください", "教えてください")


# 判定ログの共通実装(_log.py → logs/hook-decisions.jsonl。A6-20)。無ければ無記録で動く(fail-open)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _log as _hooklog
except Exception:  # noqa: BLE001
    _hooklog = None
_PAYLOAD = {}  # main() が解析したペイロード(session_id / hook_event_name / tool_use_id を判定ログに載せる)


def log_dir():
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


def gate_block(cwd):
    """progress.md の GATE_STATUS ブロック(文字列。無ければ None)。事故的停止の記録のスナップショット用。"""
    try:
        with open(os.path.join(cwd, "docs", "00-overview", "progress.md"), encoding="utf-8", errors="replace") as f:
            m = re.search(r"<!--\s*GATE_STATUS.*?(?:-->|\Z)", f.read(), re.S)
        return m.group(0).strip() if m else None
    except Exception:  # noqa: BLE001
        return None


def head_sha(cwd):
    try:
        proc = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, timeout=5,
                              encoding="utf-8", errors="replace")
        sha = proc.stdout.strip() if proc.returncode == 0 else ""
        return sha if re.fullmatch(r"[0-9a-f]{7,64}", sha) else None
    except Exception:  # noqa: BLE001
        return None


def write_stop_record(kind, reason, cwd, session_raw, session_safe):
    """事故的停止の記録(A2-1): logs/watchdog-stop-<session>.json。書けなくても判定に影響させない(fail-open)。"""
    try:
        d = log_dir()
        os.makedirs(d, exist_ok=True)
        rec = {"recorded_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
               "hook_event_name": str(_PAYLOAD.get("hook_event_name") or "Stop"),
               "session_id": session_raw, "kind": kind, "reason": reason, "cwd": cwd,
               "head_sha": head_sha(cwd), "gate_status": gate_block(cwd),
               "transcript_path": _PAYLOAD.get("transcript_path")}
        with open(os.path.join(d, "watchdog-stop-%s.json" % session_safe), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except Exception:  # noqa: BLE001
        pass


def gate_in_progress(cwd):
    """progress.md の GATE_STATUS に in_progress があるか(読めなければ False=継続しない)。"""
    # GATE_STATUS ブロックを抽出してから判定する(全文照合だと本文の言及、例えば
    # 教訓や注記の中の in_progress でも継続してしまう。docstring との乖離の修正。第2回監査)
    try:
        path = os.path.join(cwd, "docs", "00-overview", "progress.md")
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        m = re.search(r"<!-- GATE_STATUS.*?-->", text, re.S)
        if not m:
            return False
        return "in_progress" in m.group(0)
    except Exception:
        return False


def last_assistant_text(transcript):
    """トランスクリプトから直近のアシスタント応答テキストを取り出す(取れなければ None)。"""
    if not transcript or not os.path.exists(transcript):
        return None
    text = None
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or '"assistant"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") != "assistant":
                    continue
                content = (entry.get("message") or {}).get("content")
                if isinstance(content, str) and content:
                    text = content
                elif isinstance(content, list):
                    parts = [i.get("text") or "" for i in content
                             if isinstance(i, dict) and i.get("type") == "text"]
                    if any(parts):
                        text = "\n".join(parts)
    except Exception:
        return None
    return text


def waiting_for_user(text):
    """承認・質問待ちの疑いがあれば True。判定不能(None)も待ち側に倒し継続させない。"""
    if text is None:
        return True
    tail = text[-400:]
    return any(m in tail for m in WAIT_MARKERS)


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
        hook_log("allow", "stop_hook_active")
        return
    # stop_reason が max_tokens 系ならトークン枯渇による停止。「完了」でも「承認待ち」
    # でもないので継続しない(block しても同じ枯渇を繰り返すだけで、枯渇を完了と
    # 誤認したまま空転する。最新 Stop フック仕様のフィールドで、無い環境では素通り)
    stop_reason = str(payload.get("stop_reason") or "")
    cwd = payload.get("cwd") or os.getcwd()
    session_raw = str(payload.get("session_id") or "unknown")
    session = re.sub(r"[^A-Za-z0-9_.-]", "_", session_raw)
    if "max_token" in stop_reason.lower():
        hook_log("allow", "stop-reason-max-tokens %s" % stop_reason)
        # トークン枯渇で in_progress のまま止まった=事故的停止として記録する(達成扱いにしない。A2-1)
        if gate_in_progress(cwd):
            write_stop_record("max_tokens", "stop_reason=%s" % stop_reason, cwd, session_raw, session)
        return
    if not gate_in_progress(cwd):
        return
    # 上限の関係: WATCHDOG_CONTINUE_LIMIT は本スクリプト自身のセッション累計上限。
    # これとは別に Claude Code 本体が Stop フック block の連続回数を環境変数
    # CLAUDE_CODE_STOP_HOOK_BLOCK_CAP で束縛する(本体側が先に効けば block は
    # そこで打ち切られる)。実効上限は両者の小さい方。本スクリプトはこの環境変数を
    # 読まない(読む主体は Claude Code 本体。二重の歯止めとして独立に保つ)
    try:
        limit = int(os.environ.get("WATCHDOG_CONTINUE_LIMIT", DEFAULT_LIMIT))
    except Exception:
        limit = DEFAULT_LIMIT

    # 最新仕様では入力に last_assistant_message が直接入る。あればそれを判定に使い、
    # 無ければ従来どおりトランスクリプトから読む(どちらも取れなければ
    # waiting_for_user(None)=True で待ち側に倒れる。従来の保守的動作を維持)
    last_text = payload.get("last_assistant_message")
    if not (isinstance(last_text, str) and last_text.strip()):
        last_text = last_assistant_text(payload.get("transcript_path") or "")
    if waiting_for_user(last_text):
        hook_log("allow", "waiting-for-user session=%s" % session)
        return

    counter = os.path.join(log_dir(), "watchdog-continue-%s.count" % session)
    count = 0
    try:
        with open(counter, encoding="utf-8") as f:
            count = int(f.read().strip() or 0)
    except Exception:
        count = 0
    if count >= limit:
        hook_log("allow", "limit-reached %d/%d session=%s" % (count, limit, session))
        # 上限に達して in_progress のまま止まる=事故的停止として記録する(達成扱いにしない。A2-1)
        write_stop_record("watchdog_limit", "limit-reached %d/%d" % (count, limit), cwd, session_raw, session)
        return
    try:
        os.makedirs(log_dir(), exist_ok=True)
        with open(counter, "w", encoding="utf-8") as f:
            f.write(str(count + 1))
    except Exception:
        hook_log("allow", "counter-write-failed session=%s" % session)
        return
    hook_log("block", "continue %d/%d session=%s" % (count + 1, limit, session))
    print(json.dumps({
        "decision": "block",
        "reason": ("GATE_STATUS が in_progress のまま停止しています。"
                   "tasks.md の次の未完了タスクを続行するか、ブロッカーを明示してください。")
    }, ensure_ascii=False))


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

    def run(payload_text):
        old_stdin, old_stdout = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(payload_text)
        sys.stdout = io.StringIO()
        try:
            main()
            return sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout

    tmp = tempfile.mkdtemp(prefix="watchdog-selftest-")
    old_env = os.environ.get("HARNESS_HOOK_LOG_DIR")
    try:
        logs = os.path.join(tmp, "logs")
        os.environ["HARNESS_HOOK_LOG_DIR"] = logs
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, "docs", "00-overview"))
        with open(os.path.join(proj, "docs", "00-overview", "progress.md"), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\nimplementation: in_progress\n-->\n")
        check("empty stdin: 無出力・記録なし", run("") == "" and not os.path.exists(logs))
        base = {"session_id": "w/1", "cwd": proj, "hook_event_name": "Stop", "last_assistant_message": "次のタスクに進みます。"}
        out = run(json.dumps(base))
        check("in_progress・待ちでない → block(継続 1/3)、停止記録は書かない", '"block"' in out
              and not os.path.exists(os.path.join(logs, "watchdog-stop-w_1.json")), out)
        with open(os.path.join(logs, "watchdog-continue-w_1.count"), "w", encoding="utf-8") as f:
            f.write("3")
        out = run(json.dumps(base))
        rec_path = os.path.join(logs, "watchdog-stop-w_1.json")
        rec = json.load(open(rec_path, encoding="utf-8")) if os.path.exists(rec_path) else {}
        check("上限到達 → 無出力・watchdog-stop-<sid>.json(kind watchdog_limit・GATE スナップショット・session_id は生の値)",
              out == "" and rec.get("kind") == "watchdog_limit" and rec.get("reason") == "limit-reached 3/3"
              and rec.get("session_id") == "w/1" and "implementation: in_progress" in (rec.get("gate_status") or ""), str(rec))
        out = run(json.dumps(dict(base, session_id="w2", stop_reason="max_tokens")))
        rec2_path = os.path.join(logs, "watchdog-stop-w2.json")
        rec2 = json.load(open(rec2_path, encoding="utf-8")) if os.path.exists(rec2_path) else {}
        check("stop_reason max_tokens・in_progress → 無出力・kind max_tokens の記録", out == "" and rec2.get("kind") == "max_tokens"
              and "max_tokens" in rec2.get("reason", ""), str(rec2))
        with open(os.path.join(proj, "docs", "00-overview", "progress.md"), "w", encoding="utf-8") as f:
            f.write("<!-- GATE_STATUS\nrequirements: done\nimplementation: done\n-->\n")
        out = run(json.dumps(dict(base, session_id="w3", stop_reason="max_tokens")))
        check("max_tokens でも in_progress が無ければ記録しない", out == "" and not os.path.exists(os.path.join(logs, "watchdog-stop-w3.json")))
        out = run(json.dumps(dict(base, session_id="w4", stop_hook_active=True)))
        check("stop_hook_active → 即 exit・記録なし", out == "" and not os.path.exists(os.path.join(logs, "watchdog-stop-w4.json")))
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import _log as _reader  # noqa: E402
        kinds = sorted(r["kind"] for r in _reader.iter_stop_records(logs))
        check("_log.iter_stop_records が watchdog の記録を読む(読み手との契約)", kinds == ["max_tokens", "watchdog_limit"], str(kinds))
    finally:
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
    main()

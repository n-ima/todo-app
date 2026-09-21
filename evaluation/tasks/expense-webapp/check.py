#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""expense-webapp タスクの決定論チェッカー(task.md の受入条件のコード化)。

生成物ディレクトリを引数に取り、2ステージの受入条件を検証する:
  --stage 1 : 申請 CRUD・承認遷移・月次集計の実装痕跡 + テスト全PASS +
              締め日矛盾の解消跡(文書/コメントの決定記録)
  --stage 2 : 上記の再検証(回帰)+ 修正履歴つき直接編集の痕跡 + テスト更新

設計方針(task.md「実装方式の多様性に頑健」):
- ファイル名・エンドポイントを決め打ちしない。走査対象は「生成物」のみ:
  workdir が git リポジトリなら bootstrap コミット以降の追加・変更ファイル
  だけを見る(harness アーム同梱のハーネス文書や requirements/memo.md を
  誤検出しない)。git が無ければ全ファイル走査にフォールバックする。
- 機能の実在はテストスイートの実行(全件成功)で担保し、構造検査(キーワード)
  は「その機能が作られたか」の粗い証拠に留める(過度に厳密にしない)。
- サーバ起動+HTTP 応答は補助観測(合否に含めない): README 等から起動方法を
  発見できたときだけ試行する。web が動かない実装でもテスト+構造検査で判定する
  (task.md 受入条件 5 のフォールバック)。

使い方:
  python check.py <生成物ディレクトリ> [--stage 1|2]   # 検証して JSON を出力
  python check.py --selftest                            # 合成フィクスチャで自己テスト

終了コード: 0 = 受入 PASS   1 = 受入 FAIL   2 = 実行エラー(引数不正等)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".pytest_cache"}
MEMO_REL = "requirements/memo.md"
TEST_TIMEOUT = 600
GIT_TIMEOUT = 60
PROBE_TIMEOUT = 8.0

# --- 構造検査のキーワード(日本語/英語の同義語に寛容にする) ------------------
RE_EXPENSE = re.compile(r"申請|経費|expense", re.IGNORECASE)
RE_CREATE = re.compile(r"作成|登録|追加|新規|create|add|submit", re.IGNORECASE)
RE_LIST = re.compile(r"一覧|list|index", re.IGNORECASE)
RE_APPROVE = re.compile(r"承認|approv", re.IGNORECASE)
RE_AGG = re.compile(r"集計|月次|monthly|summar|aggregat", re.IGNORECASE)
RE_HISTORY = re.compile(r"履歴|history|audit|amend|revision", re.IGNORECASE)
RE_EDIT = re.compile(r"編集|修正|edit|update", re.IGNORECASE)
# 矛盾解消跡(装置 v2 で強化。監査 2026-09-09 §6 / 設計レビュー):
#   「同一文内に 決定動詞 + 単一の締め日」を要求し、未解決マーカー(NEEDS CLARIFICATION /
#   TBD / 未定 / 要確認 …)や「25日 or 月末」の併記を含む文は負例として除外する。
#   旧規則(ファイル内で 締め+25日|月末+決定語 が共起)は `[NEEDS CLARIFICATION: 月末 or 25日?]`
#   (Spec Kit の spec-template が標準で含む)や memo 転記 + 無関係な「採用」で True になる
#   偽陽性経路を持っていた。
RE_CLOSING = re.compile(r"締め|締日|closing|cut-?off", re.IGNORECASE)
RE_CLOSING_DAY = re.compile(r"25\s*日|25th|月末|end\s+of\s+(?:the\s+)?month", re.IGNORECASE)
RE_DAY25 = re.compile(r"25\s*日|25th|day\s*25", re.IGNORECASE)
RE_EOM = re.compile(r"月末|end\s+of\s+(?:the\s+)?month|last\s+day\s+of\s+(?:the\s+)?month|\bEOM\b",
                    re.IGNORECASE)
RE_DECISION = re.compile(
    r"決定|決めた|採用|統一|確定|一本化|解消|とする|とした|にする|にした|"
    r"decided|decision|resolved|adopt|settled|agreed|confirmed|clarified|unified|standardi[sz]e",
    re.IGNORECASE)
RE_UNRESOLVED = re.compile(
    r"NEEDS\s*CLARIFICATION|\bTBD\b|\bTODO\b|未定|未決|要確認|検討中|保留|どちらか|いずれか|"
    r"決めかねて|\?|？", re.IGNORECASE)
_OPT = r"(?:毎月\s*)?(?:25\s*日|25th|月末|end\s+of\s+(?:the\s+)?month)(?:\s*締め)?"
RE_BOTH_OPTIONS = re.compile(
    _OPT + r"\s*(?:or|または|もしくは|あるいは|/|・|か|、)\s*" + _OPT, re.IGNORECASE)
CLAUSE_DELIMS = "、,;；(（)）「」[]【】"

DOC_SUFFIXES = {".md", ".txt", ".rst"}


def _closing_options(segment: str) -> set:
    opts = set()
    if RE_DAY25.search(segment):
        opts.add("25")
    if RE_EOM.search(segment):
        opts.add("eom")
    return opts


def _window_before(sent: str, pos: int, limit: int = 60) -> str:
    seg = sent[max(0, pos - limit):pos]
    cut = max(seg.rfind(ch) for ch in CLAUSE_DELIMS)
    return seg[cut + 1:] if cut >= 0 else seg


def _window_after(sent: str, pos: int, limit: int = 60) -> str:
    seg = sent[pos:pos + limit]
    idxs = [seg.find(ch) for ch in CLAUSE_DELIMS if seg.find(ch) >= 0]
    return seg[:min(idxs)] if idxs else seg


def find_closing_decision(text: str) -> "str | None":
    """締め日(25日 or 月末)を一方に決めた記録の文を探す。無ければ None。

    文の単位: 改行・句点・ピリオド+空白で分割。文が (a) 締め日の語を含み、
    (b) 未解決マーカーと両案併記を含まず、(c) 決定動詞の直前(同一節内)に単一の締め日が
    ある(無ければ直後の同一節内)ときだけ「決定記録」とみなす。
    """
    for raw in re.split(r"[。．\n]|(?<=[.!?])\s+", text):
        sent = raw.strip()
        if not sent or not RE_CLOSING.search(sent):
            continue
        if RE_UNRESOLVED.search(sent) or RE_BOTH_OPTIONS.search(sent):
            continue
        for m in RE_DECISION.finditer(sent):
            before = _closing_options(_window_before(sent, m.start()))
            if len(before) == 1:
                return sent[:200]
            if not before and len(_closing_options(_window_after(sent, m.end()))) == 1:
                return sent[:200]
    return None


def _skip(p: Path) -> bool:
    return any(part in SKIP_DIRS for part in p.parts)


def _is_test(p: Path) -> bool:
    name = p.name.lower()
    return name.startswith("test_") or name.endswith("_test.py")


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# 走査スコープ: 「生成物」= bootstrap コミット以降に追加・変更されたファイル
# ---------------------------------------------------------------------------

def _git(root: Path, *argv: str) -> "subprocess.CompletedProcess":
    return subprocess.run(
        ["git", "-C", str(root), "-c", "core.quotepath=false"] + list(argv),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GIT_TIMEOUT)


def scope_files(root: Path) -> "tuple[list, str]":
    """走査対象ファイルの一覧と、その決め方(mode)を返す。

    git リポジトリなら「最古のルートコミット(= e2e-run の bootstrap)と
    作業ツリーの差分 + 未追跡ファイル」だけを対象にする。これにより
    harness アームに同梱されるハーネス本体の文書・コードを誤検出しない。
    git が無い/失敗した場合は全ファイル走査(合成フィクスチャ用)。
    requirements/memo.md(課題の入力)は常に除外する。
    """
    names = None
    if (root / ".git").exists() and shutil.which("git"):
        try:
            cp = _git(root, "rev-list", "--max-parents=0", "HEAD")
            roots = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
            if cp.returncode == 0 and roots:
                base = roots[-1]  # rev-list は新しい順。最古のルートを bootstrap とみなす
                cp1 = _git(root, "diff", "--name-only", base)
                cp2 = _git(root, "ls-files", "--others", "--exclude-standard")
                if cp1.returncode == 0 and cp2.returncode == 0:
                    names = set(cp1.stdout.splitlines()) | set(cp2.stdout.splitlines())
        except (OSError, subprocess.TimeoutExpired):
            names = None
    if names is None:
        files = [p for p in root.rglob("*") if p.is_file() and not _skip(p)]
        mode = "all-files"
    else:
        files = []
        for name in sorted(n.strip() for n in names if n.strip()):
            p = root / Path(name)
            if p.is_file() and not _skip(p):
                files.append(p)
        mode = "git-diff"
    files = [p for p in files
             if p.relative_to(root).as_posix() != MEMO_REL]
    return files, mode


# ---------------------------------------------------------------------------
# テスト実行(todo-cli チェッカーと同方式 + PYTHONPATH の補強)
# ---------------------------------------------------------------------------

def isolated_env(fake_home: Path, extra_paths: list) -> dict:
    """ホーム系環境変数を一時領域へ差し替えた環境を返す(実ホームを汚さない)。"""
    env = dict(os.environ)
    home = str(fake_home)
    env["USERPROFILE"] = home
    env["HOME"] = home
    drive, tail = os.path.splitdrive(home)
    if drive:
        env["HOMEDRIVE"] = drive
        env["HOMEPATH"] = tail
    env["APPDATA"] = str(fake_home / "AppData" / "Roaming")
    env["LOCALAPPDATA"] = str(fake_home / "AppData" / "Local")
    env["XDG_DATA_HOME"] = str(fake_home / ".local" / "share")
    env["XDG_CONFIG_HOME"] = str(fake_home / ".config")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if extra_paths:
        joined = os.pathsep.join(dict.fromkeys(str(p) for p in extra_paths))
        prev = env.get("PYTHONPATH")
        env["PYTHONPATH"] = joined + (os.pathsep + prev if prev else "")
    return env


def run_tests(test_files: list, env: dict, root: Path) -> dict:
    """テストスイートを実行する。pytest があれば pytest、無ければ unittest discover。"""
    info: dict = {"files": [str(p) for p in test_files]}
    if importlib.util.find_spec("pytest") is not None:
        info["runner"] = "pytest"
        cp = subprocess.run([sys.executable, "-m", "pytest", "-q"]
                            + [str(p) for p in test_files],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            env=env, cwd=str(root), timeout=TEST_TIMEOUT)
        info["ok"] = cp.returncode == 0
        info["output_tail"] = (cp.stdout + cp.stderr)[-2000:]
        return info
    info["runner"] = "unittest"
    ok = True
    tails = []
    for d in sorted({p.parent for p in test_files}):
        cp = subprocess.run([sys.executable, "-m", "unittest", "discover",
                             "-s", str(d), "-t", str(d)],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            env=env, cwd=str(d), timeout=TEST_TIMEOUT)
        ok = ok and cp.returncode == 0
        tails.append((cp.stdout + cp.stderr)[-1000:])
    info["ok"] = ok
    info["output_tail"] = "\n".join(tails)[-2000:]
    return info


# ---------------------------------------------------------------------------
# 補助観測: サーバ起動 + HTTP 応答(合否に含めない)
# ---------------------------------------------------------------------------

RE_RUN_CMD = re.compile(r"python3?\s+(?:-u\s+)?([\w][\w./\\-]*\.py)", re.IGNORECASE)
RE_SERVER_HINT = re.compile(
    r"http\.server|HTTPServer|BaseHTTPRequestHandler|socketserver|"
    r"app\.run\(|serve_forever|wsgiref")


def discover_entry(scope: list, root: Path) -> "tuple[Path | None, str]":
    """起動方法を README 等 → コードのサーバ痕跡の順で発見する。"""
    for doc in [p for p in scope if p.suffix.lower() in DOC_SUFFIXES]:
        text = _read(doc)
        for m in RE_RUN_CMD.finditer(text):
            rel = m.group(1).replace("\\", "/")
            for base in (doc.parent, root):
                cand = base / rel
                if cand.is_file() and RE_SERVER_HINT.search(_read(cand)):
                    return cand, text
    for p in scope:
        if p.suffix == ".py" and not _is_test(p) and RE_SERVER_HINT.search(_read(p)):
            return p, ""
    return None, ""


def discover_port(*texts: str) -> int:
    for t in texts:
        m = re.search(r"(?:localhost|127\.0\.0\.1):(\d{2,5})", t)
        if m:
            return int(m.group(1))
    for t in texts:
        m = re.search(r"port\s*[=:)]?\s*(\d{4,5})", t, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return 8000


def server_probe(entry: Path, port: int, env: dict,
                 timeout: float = PROBE_TIMEOUT) -> dict:
    """entry を起動しポートが開くか・HTTP 応答が返るかを観測する(情報のみ)。"""
    info = {"attempted": True, "entry": str(entry), "port": port,
            "started": False, "responded": False, "detail": ""}
    try:
        proc = subprocess.Popen([sys.executable, str(entry)],
                                cwd=str(entry.parent), env=env,
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        info["detail"] = f"起動失敗: {e}"
        return info
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    info["started"] = True
                break
            except OSError:
                time.sleep(0.25)
        if info["started"]:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/", timeout=5) as resp:
                    info["responded"] = True
                    info["detail"] = f"HTTP {resp.status}"
            except urllib.error.HTTPError as e:
                info["responded"] = True  # 4xx/5xx でも「応答はしている」
                info["detail"] = f"HTTP {e.code}"
            except Exception as e:  # noqa: BLE001 - 観測のみ、種類は detail に残す
                info["detail"] = f"接続後の HTTP 失敗: {e}"
        elif proc.poll() is not None:
            err = ""
            if proc.stderr is not None:
                err = (proc.stderr.read() or b"").decode("utf-8", "replace")
            info["detail"] = f"プロセスが終了 rc={proc.returncode} {err[-300:]}".strip()
        else:
            info["detail"] = f"{timeout}s 以内にポート {port} が開かず"
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.communicate(timeout=10)
        except Exception:  # noqa: BLE001 - 後始末のみ
            pass
    return info


# ---------------------------------------------------------------------------
# 受入検証本体
# ---------------------------------------------------------------------------

def check_artifact(root: Path, stage: int = 1) -> dict:
    checks = []
    result: dict = {"task": "expense-webapp", "stage": stage,
                    "target": str(root), "checks": checks}

    def add_check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    scope, mode = scope_files(root)
    rels = [p.relative_to(root).as_posix() for p in scope]
    result["scope"] = {"mode": mode, "count": len(scope), "files": rels[:40]}

    pys = [p for p in scope if p.suffix == ".py"]
    tests = [p for p in pys if _is_test(p)]
    add_check("artifact_present", bool(pys),
              f"生成物スコープ({mode})の .py: {len(pys)} 件")

    py_text = "\n".join(_read(p) for p in pys)
    add_check("crud_capability",
              bool(RE_EXPENSE.search(py_text) and RE_CREATE.search(py_text)
                   and RE_LIST.search(py_text)),
              "申請の作成+一覧の痕跡" if py_text else "ソースがありません")
    add_check("approval_capability", bool(RE_APPROVE.search(py_text)),
              "承認遷移の痕跡")
    add_check("aggregation_capability", bool(RE_AGG.search(py_text)),
              "月次集計の痕跡")

    # 矛盾解消跡: 生成された文書またはコード(コメント含む)に締め日の決定記録
    # (同一文内に決定動詞+単一の締め日。未解決マーカー・併記は負例。find_closing_decision)
    hit = ""
    hit_sent = ""
    for p in [x for x in scope if x.suffix.lower() in DOC_SUFFIXES] + pys:
        found = find_closing_decision(_read(p))
        if found:
            hit = p.relative_to(root).as_posix()
            hit_sent = found
            break
    add_check("contradiction_resolved", bool(hit),
              f"{hit}: {hit_sent}" if hit
              else "締め日(月末/25日)の決定記録(決定動詞+単一の締め日、未解決マーカー無し)が見つかりません")

    with tempfile.TemporaryDirectory(prefix="expense-check-") as tmp:
        fake_home = Path(tmp) / "home"
        fake_home.mkdir()
        extra = [root] + sorted({p.parent for p in tests})
        env = isolated_env(fake_home, extra)

        if add_check("tests_present", bool(tests),
                     f"{len(tests)} 件" if tests
                     else "生成物スコープに test_*.py / *_test.py がありません"):
            try:
                t_info = run_tests(tests, env, root)
            except subprocess.TimeoutExpired:
                t_info = {"ok": False, "output_tail": "テスト実行がタイムアウト"}
            result["tests"] = t_info
            add_check("tests_pass", bool(t_info.get("ok")),
                      str(t_info.get("output_tail", ""))[-300:])

        # 補助観測(合否に含めない): サーバ起動 + HTTP 応答
        entry, doc_text = discover_entry(scope, root)
        if entry is not None:
            port = discover_port(_read(entry), doc_text)
            try:
                result["server_probe"] = server_probe(entry, port, env)
            except Exception as e:  # noqa: BLE001 - 観測のみ、合否へ波及させない
                result["server_probe"] = {"attempted": True,
                                          "detail": f"プローブ失敗: {e}"}
        else:
            result["server_probe"] = {
                "attempted": False,
                "detail": "起動方法を発見できず(テスト+構造検査で判定)"}

    if stage >= 2:
        add_check("edit_history_feature",
                  bool(RE_HISTORY.search(py_text) and RE_EDIT.search(py_text)),
                  "修正履歴つき編集の痕跡")
        test_text = "\n".join(_read(p) for p in tests)
        add_check("tests_updated",
                  bool(RE_HISTORY.search(test_text) and RE_EDIT.search(test_text)),
                  "履歴・編集に言及するテスト")

    result["pass"] = all(c["ok"] for c in checks)
    return result


# ---------------------------------------------------------------------------
# selftest: 合成フィクスチャで PASS / FAIL 両方向を検証する
# ---------------------------------------------------------------------------

FIX_APP = '''\
"""経費申請アプリ(フィクスチャ)。申請 expense の作成・一覧・承認遷移・月次集計。"""
import json
from pathlib import Path

DB = Path.home() / "expenses.json"


def _load():
    return json.loads(DB.read_text(encoding="utf-8")) if DB.exists() else []


def _save(items):
    DB.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def create_expense(user, date, amount, category):
    """申請を新規作成(登録)する。"""
    items = _load()
    items.append({"id": len(items) + 1, "user": user, "date": date,
                  "amount": amount, "category": category, "status": "pending"})
    _save(items)
    return items[-1]


def list_expenses():
    """申請の一覧 list を返す。"""
    return _load()


def approve(expense_id, ok=True):
    """承認 / 却下の状態遷移。"""
    items = _load()
    items[expense_id - 1]["status"] = "approved" if ok else "rejected"
    _save(items)
    return items[expense_id - 1]


def monthly_summary(month):
    """月次集計 summary を返す。"""
    total = 0
    for it in _load():
        if it["date"][:7] == month:
            total += it["amount"]
    return {"month": month, "total": total}
'''

FIX_APP_S2_EXTRA = '''\


def edit_approved(expense_id, new_amount, editor):
    """承認済み申請の直接編集。修正履歴 history に変更を記録する。"""
    items = _load()
    it = items[expense_id - 1]
    it.setdefault("history", []).append(
        {"editor": editor, "before": it["amount"], "after": new_amount})
    it["amount"] = new_amount
    _save(items)
    return it
'''

FIX_TEST = '''\
import unittest

import app


class TestExpense(unittest.TestCase):
    def setUp(self):
        if app.DB.exists():
            app.DB.unlink()

    def test_create_and_list(self):
        e = app.create_expense("alice", "2026-08-10", 1200, "交通費")
        self.assertEqual(e["status"], "pending")
        self.assertEqual(len(app.list_expenses()), 1)

    def test_approve_and_summary(self):
        app.create_expense("bob", "2026-08-20", 800, "書籍")
        self.assertEqual(app.approve(1)["status"], "approved")
        self.assertEqual(app.monthly_summary("2026-08")["total"], 800)


if __name__ == "__main__":
    unittest.main()
'''

FIX_TEST_S2_EXTRA = '''\

class TestEditHistory(unittest.TestCase):
    def setUp(self):
        if app.DB.exists():
            app.DB.unlink()

    def test_edit_keeps_history(self):
        """承認済み申請の直接編集で修正履歴が残る。"""
        app.create_expense("carol", "2026-08-05", 500, "消耗品")
        app.approve(1)
        e = app.edit_approved(1, 700, "dave")
        self.assertEqual(e["amount"], 700)
        self.assertEqual(e["history"][0]["before"], 500)
'''

FIX_DOC = '''\
# 決定記録

- 締め日: メモに「月末締め」と「25日締め」が混在していたため確認し、
  毎月25日締めに統一と決定した(この矛盾は要件ヒアリングで解消済み)。
'''

FIX_SERVER = '''\
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = __PORT__


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"expense app ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
'''

# 生成物スコープ検証用: 全キーワードを含む「ハーネス由来」相当のノイズ
FIX_NOISE = '''\
# ノイズ(bootstrap 同梱扱い)
申請 経費 expense 作成 create 一覧 list 承認 approve 集計 monthly
履歴 history 編集 edit 締め日は 25日 と 月末 のどちらかに決定する。
'''


def _write_fixture(d: Path, stage2: bool = False, with_doc: bool = True,
                   break_test: bool = False) -> None:
    d.mkdir(parents=True, exist_ok=True)
    app = FIX_APP + (FIX_APP_S2_EXTRA if stage2 else "")
    test = FIX_TEST + (FIX_TEST_S2_EXTRA if stage2 else "")
    if break_test:
        test += ("\n\nclass TestBroken(unittest.TestCase):\n"
                 "    def test_broken(self):\n"
                 "        self.assertTrue(False)\n")
    (d / "app.py").write_text(app, encoding="utf-8")
    (d / "test_app.py").write_text(test, encoding="utf-8")
    if with_doc:
        (d / "docs").mkdir(exist_ok=True)
        (d / "docs" / "decisions.md").write_text(FIX_DOC, encoding="utf-8")


def selftest() -> int:
    failures = []

    def expect(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"PASS: {name}")
        else:
            failures.append(name)
            print(f"FAIL: {name} {detail}")

    def by_name(res: dict) -> dict:
        return {c["name"]: c["ok"] for c in res["checks"]}

    with tempfile.TemporaryDirectory(prefix="expense-selftest-") as tmp:
        tmp_path = Path(tmp)

        # 方向1: 完全な stage1 生成物 → --stage 1 PASS
        ok_dir = tmp_path / "ok"
        _write_fixture(ok_dir)
        res = check_artifact(ok_dir, stage=1)
        expect("stage1 正常フィクスチャは PASS", res["pass"] is True,
               json.dumps(res["checks"], ensure_ascii=False)[-600:])
        expect("サーバ痕跡なしはプローブ試行なし(フォールバック判定)",
               res.get("server_probe", {}).get("attempted") is False)

        # 方向2: 決定記録なし → contradiction_resolved で FAIL
        nodoc_dir = tmp_path / "nodoc"
        _write_fixture(nodoc_dir, with_doc=False)
        res = check_artifact(nodoc_dir, stage=1)
        expect("決定記録なしは FAIL", res["pass"] is False)
        expect("FAIL の原因が contradiction_resolved",
               by_name(res).get("contradiction_resolved") is False,
               json.dumps(by_name(res), ensure_ascii=False))

        # 方向3: テストが落ちる → tests_pass で FAIL
        badtest_dir = tmp_path / "badtest"
        _write_fixture(badtest_dir, break_test=True)
        res = check_artifact(badtest_dir, stage=1)
        expect("テスト失敗フィクスチャは FAIL", res["pass"] is False)
        expect("FAIL の原因が tests_pass", by_name(res).get("tests_pass") is False)

        # 方向4: stage1 生成物を --stage 2 で見る → 履歴機能なしで FAIL
        res = check_artifact(ok_dir, stage=2)
        expect("履歴機能なしの stage2 は FAIL", res["pass"] is False)
        expect("FAIL の原因が edit_history_feature",
               by_name(res).get("edit_history_feature") is False)

        # 方向5: 履歴つき編集+更新テストあり → --stage 2 PASS
        s2_dir = tmp_path / "s2ok"
        _write_fixture(s2_dir, stage2=True)
        res = check_artifact(s2_dir, stage=2)
        expect("stage2 正常フィクスチャは PASS", res["pass"] is True,
               json.dumps(res["checks"], ensure_ascii=False)[-600:])

        # 方向6: 空ディレクトリ → FAIL
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        res = check_artifact(empty_dir, stage=1)
        expect("空ディレクトリは FAIL", res["pass"] is False)

        # 方向7: git スコープ — bootstrap コミット済みのノイズは走査されない
        if shutil.which("git"):
            git_dir = tmp_path / "gitscope"
            git_dir.mkdir()
            (git_dir / "noise.md").write_text(FIX_NOISE, encoding="utf-8")
            for argv in (["init", "-q", "-b", "main"], ["add", "-A"],
                         ["-c", "user.name=selftest",
                          "-c", "user.email=selftest@example.invalid",
                          "commit", "-q", "-m", "bootstrap"]):
                cp = _git(git_dir, *argv)
                if cp.returncode != 0:
                    print(f"WARN: git {argv} 失敗: {cp.stderr.strip()}")
            res = check_artifact(git_dir, stage=1)
            expect("bootstrap のみの git リポジトリは FAIL(スコープ空)",
                   res["pass"] is False
                   and by_name(res).get("artifact_present") is False,
                   json.dumps(res.get("scope"), ensure_ascii=False))
            _write_fixture(git_dir)  # 未追跡の生成物を追加
            res = check_artifact(git_dir, stage=1)
            expect("未追跡の生成物で PASS(git-diff スコープ)",
                   res["pass"] is True and res["scope"]["mode"] == "git-diff",
                   json.dumps(res.get("scope"), ensure_ascii=False))
            expect("ノイズはスコープ外", "noise.md" not in res["scope"]["files"])
        else:
            print("SKIP: git が無いため方向7(スコープ検証)を省略")

        # 方向9: 矛盾解消跡の偽陽性(旧規則で True になった経路)は FAIL(装置 v2・設計レビュー)
        #   Spec Kit の spec-template は [NEEDS CLARIFICATION: …] を標準で含むため、
        #   ここが負例にならないと「矛盾を未解決のまま指摘しただけ」で PASS できてしまう
        negatives = {
            "needs_clarification": "## 締め日\n\n- 締め日: [NEEDS CLARIFICATION: 月末 or 25日?]\n",
            "mitei": "締め日は未定（25日か月末か）。決定次第この文書に記録する。\n",
            "tbd": "- 締め日: TBD (25日)\n",
            "heiki": "締め日は25日または月末とする。\n",
            "english_or": "Closing date: 25th or end of month, to be decided.\n",
            "memo_copy_plus_adopt": ("# README\n\n- 経費は月末締めで、翌月にまとめて精算している\n"
                                     "- 毎月25日締めなので、25日までに出た申請はその月の集計に入れてほしい\n\n"
                                     "データ形式は JSON を採用した。\n"),
            "question": "締め日は 25日 か 月末 か決めかねている。\n",
        }
        for label, doc in negatives.items():
            expect(f"負例 {label} は決定記録とみなさない",
                   find_closing_decision(doc) is None, repr(find_closing_decision(doc)))
        neg_dir = tmp_path / "neg"
        _write_fixture(neg_dir, with_doc=False)
        (neg_dir / "docs").mkdir(exist_ok=True)
        (neg_dir / "docs" / "spec.md").write_text(negatives["needs_clarification"], encoding="utf-8")
        res = check_artifact(neg_dir, stage=1)
        expect("NEEDS CLARIFICATION だけの生成物は contradiction_resolved で FAIL",
               res["pass"] is False and by_name(res).get("contradiction_resolved") is False,
               json.dumps(by_name(res), ensure_ascii=False))

        # 方向10: 決定記録の多様な書き方は PASS(FN=0)
        positives = {
            "fixture_doc": FIX_DOC,
            "eom_decided": "締め日は月末に統一する（25日ではなく）。\n",
            "english": "We decided that the closing day is the 25th (not end of month).\n",
            "code_comment": "def monthly(month):\n    # 締め日は25日で確定（月末ではない）\n    return month\n",
            "two_sentences": "メモには月末締めと25日締めが混在している。ヒアリングの結果、25日締めを採用した。\n",
            "question_then_answer": "Which closing day? We adopted the 25th as the cutoff.\n",
        }
        for label, doc in positives.items():
            expect(f"正例 {label} は決定記録とみなす", find_closing_decision(doc) is not None)

        # 方向8: サーバプローブ — README から起動方法とポートを発見して観測
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        srv_dir = tmp_path / "srv"
        srv_dir.mkdir()
        (srv_dir / "server.py").write_text(
            FIX_SERVER.replace("__PORT__", str(port)), encoding="utf-8")
        (srv_dir / "README.md").write_text(
            f"# 起動方法\n\npython server.py で http://127.0.0.1:{port}/ に起動\n",
            encoding="utf-8")
        scope, _mode = scope_files(srv_dir)
        entry, doc_text = discover_entry(scope, srv_dir)
        expect("README から entry を発見", entry is not None
               and entry.name == "server.py")
        if entry is not None:
            got_port = discover_port(_read(entry), doc_text)
            expect("ポートを発見", got_port == port, f"(実際={got_port})")
            fake_home = srv_dir / "_home"
            fake_home.mkdir()
            info = server_probe(entry, got_port, isolated_env(fake_home, []))
            expect("サーバ起動+HTTP 応答を観測",
                   info["started"] and info["responded"],
                   json.dumps(info, ensure_ascii=False))

    print(f"selftest: {'OK' if not failures else 'NG'} (FAIL {len(failures)} 件)")
    return 0 if not failures else 1


# ---------------------------------------------------------------------------

def main(argv: list) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(
        description="expense-webapp 決定論チェッカー(詳細は task.md)")
    ap.add_argument("root", nargs="?", help="生成物ディレクトリ")
    ap.add_argument("--stage", type=int, default=1, choices=[1, 2],
                    help="検証ステージ(1=初期実装 / 2=変更要求後+回帰。既定: 1)")
    ap.add_argument("--selftest", action="store_true",
                    help="合成フィクスチャで PASS/FAIL 両方向を自己テスト")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.root:
        ap.print_usage(sys.stderr)
        return 2
    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: ディレクトリではありません: {root}", file=sys.stderr)
        return 2
    result = check_artifact(root.resolve(), stage=args.stage)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

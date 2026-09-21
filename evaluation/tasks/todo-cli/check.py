#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""todo-cli タスクの決定論チェッカー(task.md の受入条件のコード化)。

生成物ディレクトリを引数に取り、todo.py を探して add / list / done / delete を
実際に実行し受入条件を検証する。データの分離のため USERPROFILE / HOME 等を
一時ディレクトリへ差し替えて実行する(実ユーザーのホームを汚さない・
前回実行のデータに影響されない)。テストスイート(test_*.py / *_test.py)が
あれば実行して合否に含める。結果は JSON で標準出力に返す。

使い方:
  python check.py <生成物ディレクトリ>   # 検証して JSON を出力
  python check.py --selftest             # 合成フィクスチャで PASS/FAIL 両方向を自己テスト

終了コード: 0 = 受入 PASS   1 = 受入 FAIL   2 = 実行エラー(引数不正等)
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".github", ".claude"}
TITLE_A = "牛乳を買う"
TITLE_B = "レポートを書く"
CMD_TIMEOUT = 60
TEST_TIMEOUT = 600


def find_todo_py(root: Path) -> "Path | None":
    candidates = [p for p in root.rglob("todo.py")
                  if not any(part in SKIP_DIRS for part in p.parts)]
    if not candidates:
        return None
    # 複数あれば最も浅いものを採用(ルート直下 > 深い場所)
    return min(candidates, key=lambda p: len(p.parts))


def isolated_env(fake_home: Path) -> dict:
    """ホーム系の環境変数を一時ディレクトリへ差し替えた環境を返す。"""
    env = dict(os.environ)
    home = str(fake_home)
    env["USERPROFILE"] = home                       # Windows: expanduser の第一候補
    env["HOME"] = home                              # POSIX / 一部ライブラリ
    drive, tail = os.path.splitdrive(home)
    if drive:                                       # Windows: HOMEDRIVE+HOMEPATH 経路
        env["HOMEDRIVE"] = drive
        env["HOMEPATH"] = tail
    env["APPDATA"] = str(fake_home / "AppData" / "Roaming")
    env["LOCALAPPDATA"] = str(fake_home / "AppData" / "Local")
    env["XDG_DATA_HOME"] = str(fake_home / ".local" / "share")
    env["XDG_CONFIG_HOME"] = str(fake_home / ".config")
    # 子プロセスの入出力を UTF-8 に固定(CP932 コンソールでの誤復号を防ぐ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_todo(todo_py: Path, args: list, env: dict, cwd: Path) -> "subprocess.CompletedProcess":
    return subprocess.run([sys.executable, str(todo_py)] + args,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env=env, cwd=str(cwd), timeout=CMD_TIMEOUT)


def line_with(text: str, needle: str) -> str:
    for line in text.splitlines():
        if needle in line:
            return line
    return ""


def find_test_files(root: Path) -> list:
    out = []
    for p in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        name = p.name.lower()
        if name.startswith("test_") or name.endswith("_test.py"):
            out.append(p)
    return sorted(out)


def run_tests(test_files: list, env: dict) -> dict:
    """テストスイートを実行する。pytest が入っていれば pytest、無ければ unittest discover。"""
    info: dict = {"files": [str(p) for p in test_files]}
    if importlib.util.find_spec("pytest") is not None:
        info["runner"] = "pytest"
        cp = subprocess.run([sys.executable, "-m", "pytest", "-q"]
                            + [str(p) for p in test_files],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            env=env, timeout=TEST_TIMEOUT)
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


def check_artifact(root: Path) -> dict:
    checks = []
    result: dict = {"task": "todo-cli", "target": str(root), "checks": checks}

    def add_check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    todo_py = find_todo_py(root)
    if not add_check("todo_py_found", todo_py is not None,
                     str(todo_py) if todo_py else "todo.py が見つかりません"):
        result["pass"] = False
        return result
    result["todo_py"] = str(todo_py)

    with tempfile.TemporaryDirectory(prefix="todo-check-") as tmp:
        fake_home = Path(tmp) / "home"
        workdir = Path(tmp) / "cwd"
        fake_home.mkdir()
        workdir.mkdir()
        env = isolated_env(fake_home)
        try:
            # 1. add ×2
            cp1 = run_todo(todo_py, ["add", TITLE_A], env, workdir)
            cp2 = run_todo(todo_py, ["add", TITLE_B], env, workdir)
            add_check("add_exits_zero", cp1.returncode == 0 and cp2.returncode == 0,
                      f"rc={cp1.returncode},{cp2.returncode} "
                      f"stderr={ (cp1.stderr + cp2.stderr)[-300:] }")
            # 2. list に両タイトル
            cp = run_todo(todo_py, ["list"], env, workdir)
            list_before = cp.stdout
            add_check("list_shows_both",
                      cp.returncode == 0 and TITLE_A in list_before
                      and TITLE_B in list_before,
                      f"rc={cp.returncode} stdout={list_before[-300:]}")
            # 3. done 1 → 1番目の表示行が変わる(完了の印)
            cp = run_todo(todo_py, ["done", "1"], env, workdir)
            done_rc_ok = cp.returncode == 0
            cp = run_todo(todo_py, ["list"], env, workdir)
            list_after_done = cp.stdout
            before_line = line_with(list_before, TITLE_A)
            after_line = line_with(list_after_done, TITLE_A)
            add_check("done_reflected",
                      done_rc_ok and after_line != "" and after_line != before_line,
                      f"before={before_line!r} after={after_line!r}")
            # 4. delete 2 → 2番目のタイトルが消え、1番目は残る
            cp = run_todo(todo_py, ["delete", "2"], env, workdir)
            del_rc_ok = cp.returncode == 0
            cp = run_todo(todo_py, ["list"], env, workdir)
            list_after_del = cp.stdout
            add_check("delete_reflected",
                      del_rc_ok and TITLE_B not in list_after_del
                      and TITLE_A in list_after_del,
                      f"stdout={list_after_del[-300:]}")
        except subprocess.TimeoutExpired as e:
            add_check("no_timeout", False, f"コマンドがタイムアウト: {e.cmd}")
        # 補助観測(合否に含めない): データがホーム配下に保存されたか
        stored = [str(p.relative_to(fake_home)) for p in fake_home.rglob("*")
                  if p.is_file()]
        result["storage_in_home"] = {"observed": bool(stored), "files": stored[:10]}

        # 5. テストスイート
        test_files = find_test_files(root)
        if add_check("tests_present", bool(test_files),
                     f"{len(test_files)} 件" if test_files
                     else "test_*.py / *_test.py が見つかりません"):
            try:
                tests = run_tests(test_files, env)
            except subprocess.TimeoutExpired:
                tests = {"ok": False, "output_tail": "テスト実行がタイムアウト"}
            result["tests"] = tests
            add_check("tests_pass", bool(tests.get("ok")),
                      str(tests.get("output_tail", ""))[-300:])

    result["pass"] = all(c["ok"] for c in checks)
    return result


# ---------------------------------------------------------------------------
# selftest: 最小の合成フィクスチャで PASS / FAIL 両方向を検証する
# ---------------------------------------------------------------------------

FIXTURE_OK = '''\
import json, sys
from pathlib import Path
DB = Path.home() / ".e2e-todo.json"
def load():
    return json.loads(DB.read_text(encoding="utf-8")) if DB.exists() else []
def save(items):
    DB.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
def main(argv):
    items = load()
    cmd = argv[0] if argv else "list"
    if cmd == "add":
        items.append({"title": argv[1], "done": False}); save(items); print("added")
    elif cmd == "list":
        for i, it in enumerate(items, 1):
            print(f"{i}. {'[x]' if it['done'] else '[ ]'} {it['title']}")
    elif cmd == "done":
        items[int(argv[1]) - 1]["done"] = True; save(items); print("done")
    elif cmd == "delete":
        items.pop(int(argv[1]) - 1); save(items); print("deleted")
    else:
        print("usage: todo.py add|list|done|delete", file=sys.stderr); return 2
    return 0
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

FIXTURE_TEST = '''\
import unittest
class TestTodoSmoke(unittest.TestCase):
    def test_smoke(self):
        self.assertTrue(True)
if __name__ == "__main__":
    unittest.main()
'''


def selftest() -> int:
    failures = []

    def expect(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"PASS: {name}")
        else:
            failures.append(name)
            print(f"FAIL: {name} {detail}")

    with tempfile.TemporaryDirectory(prefix="todo-check-selftest-") as tmp:
        # 方向1: 正しい実装 + テストあり → PASS
        ok_dir = Path(tmp) / "ok"
        ok_dir.mkdir()
        (ok_dir / "todo.py").write_text(FIXTURE_OK, encoding="utf-8")
        (ok_dir / "test_todo.py").write_text(FIXTURE_TEST, encoding="utf-8")
        res = check_artifact(ok_dir)
        expect("正常フィクスチャは PASS", res["pass"] is True,
               json.dumps(res.get("checks"), ensure_ascii=False)[-500:])
        expect("ホーム差し替えで保存を観測",
               res.get("storage_in_home", {}).get("observed") is True)

        # 方向2: done が保存されない実装 → done_reflected で FAIL
        broken_dir = Path(tmp) / "broken"
        broken_dir.mkdir()
        broken = FIXTURE_OK.replace(
            'items[int(argv[1]) - 1]["done"] = True; save(items); print("done")',
            'print("done")')
        (broken_dir / "todo.py").write_text(broken, encoding="utf-8")
        (broken_dir / "test_todo.py").write_text(FIXTURE_TEST, encoding="utf-8")
        res = check_artifact(broken_dir)
        bad = {c["name"]: c["ok"] for c in res["checks"]}
        expect("done 破壊フィクスチャは FAIL", res["pass"] is False)
        expect("FAIL の原因が done_reflected", bad.get("done_reflected") is False,
               json.dumps(bad, ensure_ascii=False))

        # 方向3: テスト無し → tests_present で FAIL
        notest_dir = Path(tmp) / "notest"
        notest_dir.mkdir()
        (notest_dir / "todo.py").write_text(FIXTURE_OK, encoding="utf-8")
        res = check_artifact(notest_dir)
        bad = {c["name"]: c["ok"] for c in res["checks"]}
        expect("テスト無しフィクスチャは FAIL", res["pass"] is False)
        expect("FAIL の原因が tests_present", bad.get("tests_present") is False)

        # 方向4: todo.py 不在 → 明確な FAIL
        empty_dir = Path(tmp) / "empty"
        empty_dir.mkdir()
        res = check_artifact(empty_dir)
        expect("todo.py 不在は FAIL", res["pass"] is False)

    print(f"selftest: {'OK' if not failures else 'NG'} (FAIL {len(failures)} 件)")
    return 0 if not failures else 1


def main(argv: list) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    if len(argv) == 1 and argv[0] == "--selftest":
        return selftest()
    if len(argv) != 1:
        print("usage: python check.py <生成物ディレクトリ> | --selftest",
              file=sys.stderr)
        return 2
    root = Path(argv[0])
    if not root.is_dir():
        print(f"ERROR: ディレクトリではありません: {root}", file=sys.stderr)
        return 2
    result = check_artifact(root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

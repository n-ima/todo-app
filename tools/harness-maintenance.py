#!/usr/bin/env python3
"""ハーネス本体リポジトリの保守モード切替（.claude/settings.json のガード一時解除）。

このハーネスは、プロンプトインジェクションによる自己権限昇格を防ぐため、
ハーネス自身の設定ファイル（.github/agents/, .github/hooks/, .claude/commands/,
AGENTS.md, CLAUDE.md 等）への編集を **二重に** ブロックしている。

  1. .claude/settings.json の permissions.deny（ツール層）
  2. .github/hooks/scripts/guard-harness-config-edit.sh（PreToolUse フック層）

そのため「deny 行を外すだけ」ではフック層が残って編集できない。本ツールは
両方をまとめて退避／復元する。**人間が自分のターミナルで実行すること**を前提とし、
エージェント経由の実行は確認入力で失敗する（エージェント自身にガードを外させないため）。

使い方（すべて dry-run が既定。実行には --apply が要る）:
    python tools/harness-maintenance.py                 # 現在の状態を表示
    python tools/harness-maintenance.py --on            # 解除内容の確認（変更しない）
    python tools/harness-maintenance.py --on --apply    # 保守モードへ
    python tools/harness-maintenance.py --off --apply   # 通常モードへ戻す

保守作業が終わったら **必ず --off --apply で戻す**。戻し忘れると、以後のセッションで
ハーネス設定が無防備なまま残る。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETTINGS = REPO / ".claude" / "settings.json"
BACKUP = REPO / ".claude" / "settings.json.locked"

# 保守モードで無効化する PreToolUse フック（スクリプト名で照合）
DISABLED_HOOKS = ("guard-harness-config-edit", "guard-template-edit")

CONFIRM_ON = "MAINTENANCE ON"
CONFIRM_OFF = "MAINTENANCE OFF"


def fail(msg: str) -> None:
    print(f"エラー: {msg}", file=sys.stderr)
    sys.exit(1)


def load_settings() -> dict:
    if not SETTINGS.exists():
        fail(f"{SETTINGS} が見つかりません（ハーネスのルートで実行してください）。")
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"{SETTINGS} が壊れています: {e}")
        raise  # 到達しない（fail が exit する）


def is_maintenance() -> bool:
    return BACKUP.exists()


def strip_guards(settings: dict) -> tuple[dict, list[str], list[str]]:
    """deny リストと保護フックを取り除いた設定を返す（元の設定は変更しない）。"""
    out = json.loads(json.dumps(settings))

    removed_deny = list(out.get("permissions", {}).get("deny", []))
    if removed_deny:
        out["permissions"]["deny"] = []

    removed_hooks: list[str] = []
    for group in out.get("hooks", {}).get("PreToolUse", []):
        kept = []
        for hook in group.get("hooks", []):
            cmd = hook.get("command", "")
            if any(name in cmd for name in DISABLED_HOOKS):
                removed_hooks.append(cmd)
            else:
                kept.append(hook)
        group["hooks"] = kept
    # フックが空になったグループは残しても無害なので構造は保つ（--off で丸ごと戻すため）

    return out, removed_deny, removed_hooks


def confirm(expected: str) -> None:
    """人間が自分のターミナルで実行していることの確認。

    stdin が対話端末(TTY)であることを要求する。これで `echo ... |` のパイプ供給や
    `< answer.txt` のリダイレクト供給を弾く。エージェントが直接実行した場合は
    (環境により stdin が TTY 扱いでも)入力する人間がいないため入力待ちのまま
    タイムアウトし、成立しない。実行環境の権限分類器も通常この種のコマンドを
    ブロックする(三重の防壁)。
    注意: Git Bash 単体(mintty)では python が TTY を認識できないことがある。
    その場合は PowerShell か VS Code の統合ターミナルで実行する。
    """
    if not sys.stdin.isatty():
        fail(
            "stdin が対話端末ではありません。このツールは人間が自分のターミナル"
            "（PowerShell / VS Code統合ターミナル）で実行するものです"
            "（エージェント経由・パイプ経由では実行できません）。"
        )
    print(f'\n続行するには {expected!r} と入力してください: ', end="")
    try:
        answer = input().strip()
    except EOFError:
        fail("確認入力を読めませんでした。中止します。")
        return
    if answer != expected:
        fail("確認入力が一致しませんでした。中止します。")


def cmd_status() -> None:
    if is_maintenance():
        print("現在: 【保守モード】ハーネス設定へのガードが解除されています。")
        print(f"  退避元: {BACKUP.relative_to(REPO)}")
        print("  作業が終わったら: python tools/harness-maintenance.py --off --apply")
    else:
        print("現在: 通常モード（ハーネス設定はツール層＋フック層の二重ガードで保護）。")
        print("  保守作業を始める: python tools/harness-maintenance.py --on --apply")


def cmd_on(apply: bool) -> None:
    if is_maintenance():
        print("すでに保守モードです。何もしません。")
        return

    settings = load_settings()
    stripped, removed_deny, removed_hooks = strip_guards(settings)

    print("保守モードで一時的に無効化するガード:")
    print(f"  permissions.deny: {len(removed_deny)} 件")
    for entry in removed_deny:
        print(f"    - {entry}")
    print(f"  PreToolUse フック: {len(removed_hooks)} 件")
    for cmd in removed_hooks:
        print(f"    - {cmd}")
    print("\n無効化しないもの（保守中も有効）: guard-secret-leak / guard-dangerous-git /"
          " PostToolUse / SessionStart / Stop / SessionEnd")

    if not apply:
        print("\n[dry-run] 変更していません。実行するには --apply を付けてください。")
        return

    confirm(CONFIRM_ON)
    shutil.copy2(SETTINGS, BACKUP)
    SETTINGS.write_text(json.dumps(stripped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n保守モードにしました（元の設定は {BACKUP.relative_to(REPO)} に退避）。")
    print("次にやること: 新しいチャットを開き、最初に保守作業の指示を入力してください")
    print("（いま開いているチャットには反映されないことがあるため、続きは必ず新しいチャットで）。")
    print("作業が終わったら必ず: python tools/harness-maintenance.py --off --apply")


def cmd_off(apply: bool) -> None:
    if not is_maintenance():
        print("すでに通常モードです。何もしません。")
        return

    print(f"{BACKUP.relative_to(REPO)} から {SETTINGS.relative_to(REPO)} を復元します"
          "（保守モード中に settings.json へ加えた変更は失われます）。")

    if not apply:
        print("\n[dry-run] 変更していません。実行するには --apply を付けてください。")
        return

    # 保守中に settings.json 自体を意図的に変更した場合の取りこぼしを防ぐ
    current = SETTINGS.read_text(encoding="utf-8") if SETTINGS.exists() else ""
    stripped_backup, _, _ = strip_guards(json.loads(BACKUP.read_text(encoding="utf-8")))
    expected = json.dumps(stripped_backup, ensure_ascii=False, indent=2) + "\n"
    if current != expected:
        print("\n注意: 保守モード中に settings.json が変更されています。")
        print("      その変更を残したい場合は中止し、手動で退避ファイルへ反映してください。")
        confirm(CONFIRM_OFF)

    shutil.copy2(BACKUP, SETTINGS)
    BACKUP.unlink()
    print("通常モードに戻しました。")
    print("次にやること: いま開いているチャットは閉じ、以後の作業は新しいチャットで指示してください")
    print("（復元されたガードが有効なのは新しいチャットからです）。")


def main() -> None:
    # Windows の既定コードページでは日本語が化けるため UTF-8 に揃える（他ツールと同じ扱い）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="ハーネス設定のガードを一時解除／復元する（人間が実行するツール）",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--on", action="store_true", help="保守モードにする（ガード解除）")
    group.add_argument("--off", action="store_true", help="通常モードに戻す（ガード復元）")
    parser.add_argument("--apply", action="store_true", help="実際に変更する（既定は dry-run）")
    args = parser.parse_args()

    if args.on:
        cmd_on(args.apply)
    elif args.off:
        cmd_off(args.apply)
    else:
        cmd_status()


if __name__ == "__main__":
    main()

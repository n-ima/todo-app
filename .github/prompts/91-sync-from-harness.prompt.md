---
agent: orchestrator
description: 'ハーネス本体で適用済みの改善を、このプロジェクトのハーネスコピーへ逆同期する。ファイルコピーは人間がtools/sync-harness.pyで実行し、エージェントはレポート読解・要レビューの手動マージ・docsの完了処理を担う'
---

`.github/skills/harness-sync/SKILL.md` の手順に従って、ハーネス本体からの
逆同期を進めてください。

1. 本体リポジトリのパスは `docs/00-overview/harness-origin.md`（HARNESS_ORIGIN）の
   自動記録を既定として使う（確認だけ取る。記録が無いときのみユーザーに聞く）。
2. dry-run（`python tools/sync-harness.py`。origin記録が無ければ `--harness <本体のパス>`）
   の結果レポート `docs/00-overview/harness-sync-report.md` を読み、
   追加/更新/要レビューを要約して提示する
   （実行がブロックされる環境ではコマンドをユーザーに提示して実行してもらう）。
3. `--apply` は必ずユーザーに実行してもらう（エージェントは実行しない）。
4. 要レビューのファイルは両側の差分を読み、汎用部分だけを取り込む編集案を提示・適用する。
5. 検証（selftest.sh・validate-harness.py・`python tools/doctor.py` の distribution 行が
   PASS）の結果を確認し、スキルの完了処理
   （progress.md の同期マーカー更新・learnings.md の暫定行の解消）まで行う。

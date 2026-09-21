---
agent: implement
description: 'tasks.md の未完了タスクをすべて、ノンストップで自動実装する（1タスクずつの承認は求めない）'
---

前提: `docs/03-implementation/tasks.md` が作成・承認済みであること
（未作成なら先に `/05-implementation-plan` を実行する）。

implement エージェントの進め方のうち、ステップ2以降（自動実装区間）を実行してください
（手順の正は implement.agent.md。あなたはコーディネーターとして実装コードを自分では書かず、
未完了タスクを先頭から順に、1タスクにつき1回 `task-worker` サブエージェントに委譲する）。
タスクごとの承認は求めず（AGENTS.md「自律性」）、ループ制御・エスカレーション・セッション分割の
提案は implement.agent.md の定義に従い、全タスク完了後はテストフェーズへ自動継続してください。

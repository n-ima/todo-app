---
description: 'このハーネスの使い方を案内する。次に何をすればよいか分からないとき、どのプロンプト/エージェントを使うべきか迷ったときに実行する'
---

このコマンドは薄いアダプタです。振る舞いの正は参照先にあります。

1. `.github/agents/orchestrator.agent.md` を読み、その役割定義に従ってこの会話のロールを設定してください。
2. その上で `.github/prompts/98-harness-help.prompt.md` の本文の指示を実行してください。
3. 役割定義の中の `runSubagent` は、Claude Code では **Agent ツール(旧称 Task)**で
   `.claude/agents/` の同名サブエージェント(reviewer / task-worker / spec-critic)を
   呼ぶことに読み替えてください。ハンドオフボタンは存在しないため、フェーズ移行の案内は
   「新しいセッションで /<コマンド名> を実行」の形にしてください。

---
description: '既存のコードベースにハーネスを導入する。実装済みコードからas-is要件・アーキテクチャをdocsへ逆起こしし、整合検証を経て差分駆動の改修サイクルに接続する'
---

このコマンドは薄いアダプタです。振る舞いの正は参照先にあります。

1. `.github/agents/change.agent.md` を読み、その役割定義に従ってこの会話のロールを設定してください。
2. その上で `.github/prompts/11-brownfield-intake.prompt.md` の本文の指示を実行してください。
3. 役割定義の中の `runSubagent` は、Claude Code では **Agent ツール(旧称 Task)**で
   `.claude/agents/` の同名サブエージェント(reviewer / task-worker / spec-critic)を
   呼ぶことに読み替えてください。ハンドオフボタンは存在しないため、フェーズ移行の案内は
   「新しいセッションで /<コマンド名> を実行」の形にしてください。

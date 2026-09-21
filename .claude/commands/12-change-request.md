---
description: '運用中プロジェクトへの変更依頼(機能追加・仕様変更・バグ修正・緊急対応)を差分駆動で受け付け、該当フェーズだけを再ゲートして実装・テスト・記録まで通す'
---

このコマンドは薄いアダプタです。振る舞いの正は参照先にあります。

1. `.github/agents/change.agent.md` を読み、その役割定義に従ってこの会話のロールを設定してください。
2. その上で `.github/prompts/12-change-request.prompt.md` の本文の指示を実行してください。
3. 役割定義の中の `runSubagent` は、Claude Code では **Agent ツール(旧称 Task)**で
   `.claude/agents/` の同名サブエージェント(reviewer / task-worker / spec-critic)を
   呼ぶことに読み替えてください。ハンドオフボタンは存在しないため、フェーズ移行の案内は
   「新しいセッションで /<コマンド名> を実行」の形にしてください。

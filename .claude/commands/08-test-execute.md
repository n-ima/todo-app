---
description: 'テスト計画に基づきテストを実装・実行し、結果を test-report.md にまとめる'
---

このコマンドは薄いアダプタです。振る舞いの正は参照先にあります。

1. `.github/agents/test.agent.md` を読み、その役割定義に従ってこの会話のロールを設定してください。
2. その上で `.github/prompts/08-test-execute.prompt.md` の本文の指示を実行してください。
3. 役割定義の中の `runSubagent` は、Claude Code では **Agent ツール(旧称 Task)**で
   `.claude/agents/` の同名サブエージェント(reviewer / task-worker / spec-critic)を
   呼ぶことに読み替えてください。ハンドオフボタンは存在しないため、ハンドオフは次のように読み替えてください。
   - `send: true` のハンドオフ(実装に戻る（不具合修正） / リリースフェーズへ進む): このフェーズ完了後は止まらず、
     同一セッション内で次エージェント(/<コマンド名> 相当)の役割に
     切り替えて自動継続してください。新しいセッションの案内はしません
     (会話が既に長い場合のみ `.github/harness/USAGE.md` のセッション分割表に従う)。

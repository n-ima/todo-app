---
description: 'environment.mdと環境固有Skillに基づき、リリースを「計画→独立検証→承認→実行」で進める。準備は自動、外部反映（push・タグ・デプロイ・公開）はユーザーの exact-action 承認後にのみ実行し、人手必須の作業だけをユーザーに委ねる'
---

このコマンドは薄いアダプタです。振る舞いの正は参照先にあります。

1. `.github/agents/release.agent.md` を読み、その役割定義に従ってこの会話のロールを設定してください。
2. その上で `.github/prompts/09-release-checklist.prompt.md` の本文の指示を実行してください。
3. 役割定義の中の `runSubagent` は、Claude Code では **Agent ツール(旧称 Task)**で
   `.claude/agents/` の同名サブエージェント(reviewer / task-worker / spec-critic)を
   呼ぶことに読み替えてください。ハンドオフボタンは存在しないため、フェーズ移行の案内は
   「新しいセッションで /<コマンド名> を実行」の形にしてください。

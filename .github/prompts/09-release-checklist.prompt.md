---
agent: release
description: 'environment.mdと環境固有Skillに基づき、リリースを「計画→独立検証→承認→実行」で進める。準備は自動、外部反映（push・タグ・デプロイ・公開）はユーザーの exact-action 承認後にのみ実行し、人手必須の作業だけをユーザーに委ねる'
---

前提: `docs/04-test/test-report.md` が作成済みであること。

入口の最初のステップとして GATE_STATUS の release が `not_started` なら `in_progress` にする（`.github/harness/STATE-MACHINE.md` §2。release.agent.md 手順 1）。
release エージェントの進め方（ステップ1〜9。手順・判断基準・安全手順の正は release.agent.md。D087）を
実行してください。入力は `docs/01-requirements/environment.md` と `.github/skills/deploy-<environment>/SKILL.md`
（無ければ `skill-authoring` スキルで作るが、作成した Skill は同一セッションでは実行しない）、出力は
`docs/05-release/release-checklist.md`（外部反映の action packet を含む）と `CHANGELOG.md`。
止まる条件: 外部反映（push・タグ・デプロイ・公開）は分類に関わらず、`reviewer` の独立検証（ステップ5）と
`pending_approval` での packet 提示（ステップ6）を経てユーザーの exact-action 承認後にのみ一度だけ実行する
（AGENTS.md「必ず止まる条件」2）。外部に反映しない準備（ビルド・dry-run・ローカル検証）に確認は求めず、
`environment.md` の「人手」作業は操作内容を提示してユーザーに委ねる。完了後は全フェーズ完了の更新確認と
`/10-retrospective` の提案（ステップ8〜9）まで行ってください。

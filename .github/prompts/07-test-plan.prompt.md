---
agent: test
description: '要件・設計からテスト計画（単体/結合/E2E/非機能）を作成する'
---

前提: 実装フェーズ（`docs/03-implementation/tasks.md` の全タスク）が完了していること。

入口の最初のステップとして GATE_STATUS の test が `not_started` なら `in_progress` にする（`.github/harness/STATE-MACHINE.md` §2）。
test エージェントの進め方のうち、ステップ1（着手前の読み取り調査 → `test-plan.md` の作成。
分類・要件IDとの対応づけ・含める項目の正は test.agent.md と `test-case-design` スキル）を実行してください。
出力は `docs/04-test/test-plan.md`。自動実行区間なので作成後に確認は求めず、そのまま
`/08-test-execute` の区間（テストコードの実装・実行）へ自動継続します（AGENTS.md「自律性」）。

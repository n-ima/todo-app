---
agent: test
description: 'テスト計画に基づきテストを実装・実行し、結果を test-report.md にまとめる'
---

前提: `docs/04-test/test-plan.md` が作成済みであること（未作成なら先に `/07-test-plan`）。

test エージェントの進め方のうち、テストコードの実装・実行から再開してください
（失敗の切り分け・ブラウザ確認・`test-report.md` の作成を含む。手順の正は test.agent.md に定義）。
`reviewer` の独立レビューと、結果に応じた実装への差し戻し／リリースフェーズへの自動継続は
test.agent.md の定義（ハンドオフ）に従ってください。

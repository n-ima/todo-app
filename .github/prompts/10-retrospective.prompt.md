---
agent: orchestrator
description: '開発全体を振り返り、教訓の整理・ハーネス本体への改善提案・新規Skillの還流判断を行う'
---

`.github/skills/harness-retrospective/SKILL.md` の手順に従って振り返りを実施してください。

1. `docs/06-retrospective/retrospective_template.md` から `retrospective.md`（連番可）を作成する。
2. `learnings.md`・テストレポート・実装メモ・このセッションでの訂正履歴・教訓候補の下書き
   （`.github/hooks/logs/learnings-draft.local.md`。`learnings.md` への転記はこの手順か人の明示操作だけ）を情報源に、
   摩擦のあった出来事を事実ベースで洗い出し、「ハーネス改善 / プロジェクト固有 / 一過性」に分類する。
3. ハーネス改善に分類した項目は、対象ファイル・現状の問題・提案・根拠の4点セットで記述する。
4. このプロジェクトで新規作成したSkillの還流（本体テンプレートへの取り込み）可否を判断する。
5. 結果をユーザーに提示し、ハーネス本体リポジトリへの還流を案内する。

リリース直後でなくても、開発の途中で「ハーネスの挙動に違和感がある」と感じたタイミングで
実行してよい（その場合は対象範囲を明記する）。

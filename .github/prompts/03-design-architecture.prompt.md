---
agent: design
description: '要件定義書をもとに技術スタック候補を提示し、選定後にアーキテクチャ設計書を作成する'
---

前提: `docs/01-requirements/requirements.md` がゲート承認済みであること。
未承認なら design.agent.md の定義に従い要件定義に差し戻してください。

design エージェントの進め方のうち、着手前の前提チェックとステップ1〜4
（要件の読み込み → 技術スタックの提案・選定 → `architecture.md` の作成 → ADR の記録。
含めるべき内容・判断基準は design.agent.md に定義）を実行してください。
アーキテクチャが固まったら、詳細設計以降は `/04-design-detailed` で続行します。

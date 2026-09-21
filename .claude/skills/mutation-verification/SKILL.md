---
name: mutation-verification
description: 実装をわざと壊してテストが落ちることを実測する mutation 検証の手順。execFileSync でのテスト実行方法、置換が1箇所であることの検査、生き残り(テストが落ちない変異)の切り分け順序、等価変異の典型カタログを含む。テストの穴を実装フェーズ中に検出したいとき、task-worker やテストエージェントが使う。既定では必須にしない(コスト増のためプロジェクトの重要度に応じて選択する)。
---

このスキルの本文の正は `.github/skills/mutation-verification/SKILL.md` です。
それを読み、その手順・チェックリストに従ってください(このファイルはClaude Codeが
`.claude/skills/` しか探索しないために置いてある薄いポインタです)。

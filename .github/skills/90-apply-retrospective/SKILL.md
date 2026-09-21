---
name: 90-apply-retrospective
description: 'ハーネス本体リポジトリ専用。振り返り(retrospective)の改善提案表を読み込み、本体へ適用してDECISIONS.mdに記録する'
user-invocable: true
disable-model-invocation: false
metadata:
  harness-entry: copilot
  generated-from: .github/prompts/90-apply-retrospective.prompt.md
---

<!-- generated from .github/prompts/90-apply-retrospective.prompt.md + .github/agents/harness-maintainer.agent.md by tools/generate-adapters.py (Copilot Agent Host 向け入口スキル。手で編集しない。再生成: python tools/generate-adapters.py) -->

このスキルは Copilot Agent Host 向けの薄い入口アダプタです。振る舞いの正は参照先にあり、
このファイル自体は振る舞いを持ちません(Local ハーネスでは同名の prompt file、Claude Code では
同名のスラッシュコマンドが同じ正を参照します。再監査 2026-09-09 CP-2 / A6-17)。

1. `.github/agents/harness-maintainer.agent.md` を読み、その役割定義(役割・手順・`tools` / `agents` の範囲・`handoffs`)に
   従ってこの会話のロールを設定してください。Agent Host では prompt files の `agent:` バインド
   (D008)が効かないため、役割はこの本文の指示で設定します(指示層=劣化モード。
   エージェントピッカーで `harness-maintainer` を選んでから実行すると `tools` / `agents` / `handoffs` が
   機械的にも効きます)。
2. その上で `.github/prompts/90-apply-retrospective.prompt.md` の本文の指示を実行してください。
3. 役割定義の中の `runSubagent` は、そのまま `.github/agents/` の同名サブエージェント
   (reviewer / task-worker / spec-critic)を呼びます。ハンドオフボタンが出ない場合、フェーズ移行の案内は
   「新しいチャットで /<スキル名> を実行」の形にしてください。

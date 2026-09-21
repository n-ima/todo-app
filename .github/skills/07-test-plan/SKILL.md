---
name: 07-test-plan
description: '要件・設計からテスト計画（単体/結合/E2E/非機能）を作成する'
user-invocable: true
disable-model-invocation: false
metadata:
  harness-entry: copilot
  generated-from: .github/prompts/07-test-plan.prompt.md
---

<!-- generated from .github/prompts/07-test-plan.prompt.md + .github/agents/test.agent.md by tools/generate-adapters.py (Copilot Agent Host 向け入口スキル。手で編集しない。再生成: python tools/generate-adapters.py) -->

このスキルは Copilot Agent Host 向けの薄い入口アダプタです。振る舞いの正は参照先にあり、
このファイル自体は振る舞いを持ちません(Local ハーネスでは同名の prompt file、Claude Code では
同名のスラッシュコマンドが同じ正を参照します。再監査 2026-09-09 CP-2 / A6-17)。

1. `.github/agents/test.agent.md` を読み、その役割定義(役割・手順・`tools` / `agents` の範囲・`handoffs`)に
   従ってこの会話のロールを設定してください。Agent Host では prompt files の `agent:` バインド
   (D008)が効かないため、役割はこの本文の指示で設定します(指示層=劣化モード。
   エージェントピッカーで `test` を選んでから実行すると `tools` / `agents` / `handoffs` が
   機械的にも効きます)。
2. その上で `.github/prompts/07-test-plan.prompt.md` の本文の指示を実行してください。
3. 役割定義の中の `runSubagent` は、そのまま `.github/agents/` の同名サブエージェント
   (reviewer / task-worker / spec-critic)を呼びます。ハンドオフボタンが出ない場合、ハンドオフは次のように読み替えてください。
   - `send: true` のハンドオフ(実装に戻る（不具合修正） / リリースフェーズへ進む): このフェーズ完了後は止まらず、
     同一チャット内で次エージェント(/<スキル名> 相当)の役割に
     切り替えて自動継続してください。新しいチャットの案内はしません
     (会話が既に長い場合のみ `.github/harness/USAGE.md` のセッション分割表に従う)。

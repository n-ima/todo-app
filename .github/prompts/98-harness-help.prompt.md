---
agent: orchestrator
description: 'このハーネスの使い方を案内する。次に何をすればよいか分からないとき、どのプロンプト/エージェントを使うべきか迷ったときに実行する'
---

1. `docs/00-overview/progress.md` の GATE_STATUS から現在の状況を把握する。
2. `.github/skills/harness-guide/SKILL.md` の入口表に従い、ユーザーの状況に合った
   入口を**1つだけ**案内する（「何を / どのチャットで / なぜ」の3点で短く）。
3. ハーネス設定自体の変更相談には「人間が編集し、本体リポジトリの DECISIONS.md に
   記録する」運用を案内する。
4. 「今のチャットを続けてよいか」の相談には `.github/harness/USAGE.md`「セッション分割表」の
   該当行を 1 行で示す（要約は harness-guide スキル「セッション分割の要点」）。

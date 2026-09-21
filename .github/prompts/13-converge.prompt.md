---
agent: change
description: '運用中(または任意時点)の docs⇔実態の乖離を棚卸しし、分類・起票して収束させる収束監査を実行する'
---

`.github/skills/converge/SKILL.md` の手順に従って、収束監査を実行してください。

- 検出（gate-check の横断整合監査 + `tools/trace-check.py` / `tools/golden-eval.py`）→
  分類（docs が正 / 実装が正 / どちらも古い）→ append-only の起票
  （`tasks.md` または CR 台帳）→ `progress.md` の申し送りへの結果要約、の順に進める。
- このプロンプト自体では修正しない。対処はタスク（`/06`）または変更請求（`/12`）の
  通常フローに乗せる。
- 乖離が残っていれば、消化後にもう一度 `/13-converge` を実行するよう案内して終了する
  （1回の実行で全部直そうとしない）。

---
agent: orchestrator
description: '各フェーズの進捗とゲート承認状況をダッシュボード表示し、progress.md を更新する'
---

1. `docs/00-overview/progress.md` と、`docs/01-requirements/` 〜 `docs/05-release/` の
   各成果物の有無・内容を確認する。**progress.md が無くても作成しない**
   （振り分けは orchestrator.agent.md の brownfield 判定に従い、`/00-start-project` か
   `/11-brownfield-intake` を案内する。ここで作ると brownfield 検知の後勝ち上書きになる）。
2. 各フェーズを「未着手 / 進行中 / ゲート承認待ち / 完了」で判定し、表にする。
3. `progress.md` の内容が実態とずれていれば、更新案を提示し、ユーザー承認後に反映する。
4. 費用欄: `python tools/session-receipt.py --session latest --summary` を実行して表示する
   （`.github/hooks/logs/usage` の受領書があればその 3 行。無ければ
   `python tools/effort-report.py --summary` の集計。数値は受領書が正、無ければ effort-report。
   自分で推定・補完しない。`host n/a` は statusline 未配線を意味する）。
5. 次に着手すべきプロンプトを1つ提案する。
6. ユーザーが整合チェック・監査を求めた場合は、gate-check スキルの「横断整合監査」を
   実行し、成果物間の食い違いを表で報告する（修正はしない）。

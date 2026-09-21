---
agent: harness-maintainer
description: 'ハーネス本体リポジトリ専用。振り返り(retrospective)の改善提案表を読み込み、本体へ適用してDECISIONS.mdに記録する'
---

`.github/skills/harness-apply-retrospective/SKILL.md` の手順に従って、
振り返りの改善提案をこのハーネス本体に適用してください。

1. 本体リポジトリで実行されているかの前提確認から始める
   （個別プロジェクトのセッションなら中止して正規フローを案内する）。
2. 対象の振り返りファイルのパスが指定されていなければユーザーに確認する。
3. スキルの手順どおり、仕分け → 適用（保護対象は検証済みの適用スクリプトを用意し、
   人間に保守モード `python tools/harness-maintenance.py --on --apply` で
   実行してもらう）→ アダプタ再生成・整合検証 → DECISIONS.md 記録 →
   保守モード復元（`--off --apply`）の確認 → コミット提案まで行う。

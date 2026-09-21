# 独立レビュー記録（review-log）

`reviewer` サブエージェントの実施記録。実装フェーズのチェックポイント（完了タスク10個ごと・
全タスク完了時。implement.agent.md）、テスト完了後・リリース前（test.agent.md）、
変更請求の完了前（change.agent.md）、リリースの承認前（release-checklist と action packet の
独立検証。release.agent.md 手順5）のレビューを、**呼び出し元エージェントが1回1エントリで
末尾に追記する**（`reviewer` は読み取り専用のため、エントリ本文は `reviewer` の返答を
そのまま転記する。既存エントリは書き換えない）。

implementation / test の `done` 条件（gate-check スキル）は、このファイルの日時付きエントリ
（見出しの `YYYY-MM-DD HH:MM`。最新エントリが当該フェーズの最新の完了証拠と同日以降）
または `security-review-report.md` の存在で判定される（`warn-gate-tamper` フック・
`golden-eval` が機械検査する）。

## エントリ形式（`reviewer.agent.md`「記録」節と同一）

### YYYY-MM-DD HH:MM / <implementation|test|change|release> / 対象: <TASK-xxx〜TASK-yyy または CR-nnn または release vX.Y.Z> / verdict: <BLOCKER|MAJOR|MINOR|承認>

- 呼び出し元: <implement|test|change|release>
- 対象コミット: <ハッシュ範囲、または working tree>
- 根拠（file:line）:
  - <path:line> — <指摘または確認内容>（<CRITICAL|HIGH|MEDIUM|LOW|INFO>）
- 対応: <差し戻すタスクID / 追加する修正タスク / 対応不要>

## エントリ

（ここから下に、新しいエントリを末尾へ追記する）

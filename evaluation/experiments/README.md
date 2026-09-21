# evaluation/experiments/ — 系列の事前登録

A/B 系列（S1〜S5）の**課題・アーム順・n・予算・判定規則を走らせる前に固定する**ファイル群。
`tools/e2e-run.py --plan <file>` はここを正として走行条件を組み立て（CLI 引数で違う値を
与えるとエラー）、結果 JSON の `experiment.plan_path` / `plan_sha256` に記録する。
`tools/eval-report.py --validate` は (1) 系列内の全結果の `plan_sha256` が現在のファイルの
sha256 と一致すること、(2) ファイルの登録日時（最終コミット日時。未コミットなら mtime）が
系列の初回走行より前であること、を検査する。**結果を見てから予算や n を動かさない**ための
機械的な歯止めであり、変える必要が出たら新しい系列 ID で登録し直す。

## ファイルの状態

| ファイル | 実験 | 課題 | アーム | 予算 | 状態 |
|---|---|---|---|---|---|
| `S1-expense-capability.json` | capability | expense-webapp | harness / bare | B_cap $120（発話上限＝合計上限） | draft（パイロット 1+1 走行で B_cap を確定してから registered） |
| `S2-expense-efficiency.json` | efficiency | expense-webapp | harness / bare | ラダー $12 / $24 / $48（`--budget-rung 0..2`） | draft |
| `S3-todo-capability.json` | capability | todo-cli | harness / bare | B_cap $60 | draft |
| `S4-todo-efficiency.json` | efficiency | todo-cli | harness / bare | ラダー $4 / $12 | draft |
| `S5-expense-speckit.json` | capability | expense-webapp | harness / external:speckit | B_cap $120、`allowed_tools_extra` を両アームに適用 | draft（`arms/speckit.json` の smoke が前提） |

`status` は `draft`（値がパイロット待ち）→ `registered`（このファイルをコミットした時点が
登録日時）。`draft` のまま本番系列を走らせない。

## スキーマ（schema_version 1）

```json
{
  "schema_version": 1,
  "series_id": "S1-expense-capability",
  "status": "draft | registered",
  "registered_on": "YYYY-MM-DD",
  "experiment": "capability | efficiency",
  "task": "expense-webapp",
  "arms": ["harness", "bare"],            // run_index を ABAB 順に割り当てる
  "order": "ABAB",
  "n_per_arm": 5,
  "model": "claude-opus-5",               // フル ID(エイリアス不可)
  "effort": "high",
  "budgets": {"total_usd": 120.0, "call_usd": 120.0},   // capability
  "budget_ladder_usd": [12, 24, 48], "stages": 2, "call_budget_usd": null,   // efficiency(各段は series_id/B<usd>)
  "max_turns_per_call": 60,
  "fresh_policy": "fresh-on-command",
  "allowed_tools_extra": [],              // 同一系列の全アームに同じ値を与える
  "max_nudges": 2,
  "min_cli": "2.1.251",
  "validity": {...},                      // 妥当性条件(装置の validity() と一致させる)
  "escalation": {...},                    // budget_used_ratio > 0.7 のときの B_cap x1.5(最大 2 段)
  "decision_rules": {...},                // 主指標・検定・差を主張する条件・n の延長・補充規則・費用は範囲
  "pilot": {...},
  "notes": "..."
}
```

## 統計の事前登録（全系列共通。`evaluation/README.md` が正）

- n=5/アーム。Fisher 正確検定（両側）: 5/5 vs 0/5 → p=0.0079、4/5 vs 0/5 → p=0.048、
  3/5 vs 0/5 → p=0.17（差を主張しない）。Wilson 95% 区間を併記。
- 分離しなければ n=8 まで延長（ABAB 順）。DNF/INVALID で有効 n < 5 なら ABAB 順で最大 +3 走行を補充。
- 費用は範囲（min / median / max）。アーム間の費用比は作らない。
- 能力実験で `budget_used_ratio > 0.7` が 1 本でも出たら系列を INVALID とし B_cap ×1.5 で
  再走行（最大 2 段）。破棄した系列の結果も `results/` に残す。

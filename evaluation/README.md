# evaluation/ — ハーネスの実測評価基盤（評価装置 v2）

## 目的

「世界最高」の定義を**ここで測定した性能で優位を証明できる状態**に置く
（知名度・公開・コミュニティは評価軸から除外）。そのために、同一課題・同一条件の
A/B 実測でハーネスの効果を測る:

- **harness アーム**: ハーネス適用プロジェクト（`git archive HEAD`）で D049 実証済みの
  発話系列（/00 → 承認 → … → テスト・リリース）を投入
- **bare アーム**: ハーネス無しの空ディレクトリ + 同じ memo で「メモのアプリを実装し
  テストまで」を同数・同予算の発話で依頼
- **external アーム**（`arms/<name>.json`）: 他ハーネス（Spec Kit v1.0.5 など）を pin して
  同じ条件で走らせる（Copilot 経路は本装置の対象外。別提案）

判定はエージェントの自己申告ではなく、課題ごとの決定論チェッカー
（`tasks/<task>/check.py`）が生成物を実際に実行して行う。

2026-09-09 の再監査（`audits/external-reaudit-2026-09-09.md` §2.2 EV-1〜3・§6）で、
装置 v1 の結果 10 本には **verdict が無く**、予算超過走行（$45.47 > 上限 $40）が
check PASS として数えられ、発話単位の予算打切りが harness の全 PASS 走行で起きていたのに
記録されず、model が結果に残らず（transcript から `claude-fable-5` と復元）、ユーザー
スコープの設定が両アームに漏れ込み、CLI 2.1.201（予算強制が main-only）で採取されていた
ことが判明した。本ディレクトリはその指摘を反映した**装置 v2**の定義である。

## 用語（`.github/harness/GLOSSARY.md` が正。結果 JSON の `verdict` と一致させる）

| 語 | 意味 |
|---|---|
| `PASS` / `FAIL` | 台本を送り終え、check.py の最終判定が通った / 通らなかった |
| `DNF_BUDGET_TOTAL` | 合計上限で停止、または **`cost > total_budget`**（打切りフラグに依存しない） |
| `DNF_BUDGET_CALL` | 発話単位の上限（`--max-budget-usd`）で 1 件でも打切られた（**能力実験のみ DNF 扱い**。効率実験では件数を報告） |
| `DNF_QUOTA` | クレジット枯渇・利用上限（`out of usage credits` 等） |
| `DNF_TIMEOUT` | `--call-timeout` 到達 |
| `DNF_ERROR` | API エラー（`api_error_status`）・実行エラー（`error_during_execution`） |
| `DNF_TURNS` | `--max-turns` 到達（`error_max_turns`） |
| `DNF_ABNORMAL_STOP` | 走行中に workdir のフックが事故的停止を記録した（StopFailure の `abnormal-stop-*.json`、watchdog の `watchdog-stop-*.json`。結果 JSON の `abnormal_stops[]` に転記。A2-1） |
| `INVALID_APPARATUS` | 装置側の不備（setup 失敗・出力の解釈不能・モデル拒否・共有 allowedTools 外の要求で deny され PASS 未到達 など）。比較表に載せない |
| `valid_for_comparison` / `invalid_reasons[]` | 事前登録した妥当性条件の機械検査結果とその理由 |
| 能力実験 / 効率実験 | 下記「実験の分離」 |
| `condition_hash` | 固定条件 C の要約ハッシュ。同一表に載せるのは hash 一致の走行だけ |

判定の優先順: `INVALID_APPARATUS`（装置） > `DNF_QUOTA` > `DNF_TIMEOUT` > `DNF_ERROR` >
`DNF_TURNS` > `DNF_ABNORMAL_STOP` > `DNF_BUDGET_TOTAL` > `DNF_BUDGET_CALL`（能力のみ） > check。
判定は `subtype` 単独に依存しない（2.1.201 で Fable 5.1 は API 400 でも `subtype: success`）:
`is_error` + `api_error_status` + `errors[]` + 応答文言の複合で分類する（`classify_call`）。

## 実験の分離と妥当性条件（機械検査。`tools/e2e-run.py` の `validity()` が正）

### 共通固定条件 C（両実験・全アーム同一。`conditions` に記録し `condition_hash` で同一性を判定）

| 項目 | 値 |
|---|---|
| モデル | `--model` フル ID 必須（例 `claude-opus-5`。エイリアス不可）。サブエージェントは env `CLAUDE_CODE_SUBAGENT_MODEL=<同 ID>` + `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`（CLI ≥ 2.1.257 のとき） |
| effort | `--effort` 必須（既定系列は high。sweep は別系列） |
| CLI 版 | **≥ 2.1.251**（Fable 5.1 対応。未満は exit 2）。推奨 **≥ 2.1.257**（FORCE）。2.1.217 未満は予算強制が main-only。系列内で版が変われば INVALID |
| ユーザースコープ隔離 | 系列専用の `CLAUDE_CONFIG_DIR`（資格情報のみ複製。skills/plugins/settings を持たない）+ `--strict-mcp-config` + `--setting-sources project,local`。`--setting-sources` だけではユーザースコープの skills/plugins/MCP を遮断できない（実プローブ: user skills 16 本・MCP 6 本がロード） |
| モデル切替の抑止 | `--settings <series>/settings.json` で `switchModelsOnFlag: false`（安全分類器によるフォールバックを止める）。フォールバックが起きれば `model_fallback_cost_ratio` として記録し比較から外す |
| 起動と記録 | 各 call を `--output-format stream-json --verbose` で起動し、`system/init` の version・model・skills・agents・mcp_servers・plugins を保存。プロジェクト由来でない要素の指紋を `condition_hash` に含め、実ユーザー設定と重なる要素があれば `user_scope_unisolated` |
| 権限 | `--permission-mode acceptEdits`、`--allowedTools` 3 種 + `allowed_tools_extra`（**同一系列の全アームに同じ値**。Spec Kit の `Bash(bash .specify/*)` も harness/bare に与える） |
| ターン | `--max-turns 60`/発話（固定） |
| セッション | `fresh_policy ∈ {continue, fresh-on-command}` を系列で固定。新規は `--session-id <uuid4>`、継続は `--resume <id>`。`--continue` は使わない |
| 課題 | `task.md` と `check.py` の sha256 |
| 課金経路 | `conditions.auth_mode ∈ {api_key, oauth}`（`init.apiKeySource` から判定）。oauth は費用が「定価換算」であり、Fable 5.1 は usage credits を同意なしに消費して `DNF_QUOTA` が再発しうる。系列内で混在したら INVALID |
| 費用推定の係数 | CLI ≥ 2.1.239 の推定はデータ常駐ワークスペースで 1.1x を含む。`--data-residency-premium` で宣言し `conditions.data_residency_premium_1_1x` に記録 |
| 環境変数 | `CLAUDE_CODE_EFFORT_LEVEL` / `CLAUDE_CODE_SUBAGENT_MODEL` 等 13 変数を起動前に掃除（`ENV_SCRUB`） |

### 能力実験（capability。非拘束・同額上限）

- 予算: 両アーム `--total-budget-usd B_cap`、発話上限 = 合計上限（発話単位の拘束を消す）。
  各 call の `--max-budget-usd` は **`min(call_budget, total_budget − 消費済み)`** を動的に
  付与し、合計が上限を超えられないようにする。
- 妥当性条件（全て真のときだけ `valid_for_comparison=true`）:
  1. `verdict ∈ {PASS, FAIL}`（DNF / INVALID を含まない）
  2. `totals.n_truncated_calls == 0` かつ `totals.n_max_turns_calls == 0`
  3. `totals.budget_used_ratio = cost / total_budget ≤ 0.7`（1 本でも超えたら系列を INVALID とし B_cap ×1.5 で再走行。最大 2 段。破棄した系列も `results/` に残す）
  4. `conditions.budget_enforcement == subagent-inclusive`（CLI ≥ 2.1.217）
  5. 主モデル（`modelUsage` で費用最大）が `model_requested` と一致し、補助モデルの費用比 < 1%
  6. 系列内の `condition_hash` が単一、ユーザースコープ漏れ無し、`auth_mode` 単一
  7. 退避物（git bundle・transcript zip の sha256）がある（再検証可能性）
  8. `permission_denials` があり PASS 未到達なら INVALID_APPARATUS（全アーム同一規則）
- 主指標: pass@1 率（Wilson 95%）、Fisher 正確検定（両側）、pass^k（k=n）。
  副指標: 費用範囲・トークン種別（`modelUsage` 合算。`usage` は打切り call で 0 になるため使わない）・ターン・時間・生成物（git-diff スコープ・全言語）。

### 効率実験（efficiency。拘束・同額上限）

- 予算: 両アーム同額の**事前登録ラダー** `B ∈ {$12, $24, $48}`（expense-webapp）/ `{$4, $12}`
  （todo-cli）。発話上限は `B / ステージ数`。結果を見てから B を動かさない。
- 妥当性条件: C が一致し、両アームの `total_budget_usd` が等しい。DNF は成果なので verdict は
  何でもよいが `INVALID_APPARATUS` は除外。
- 主指標: 予算内 PASS 率、DNF 率、PASS 走行の費用分布（cost-to-pass）。B ごとに別表。

### 対称性（アーム間の公平）

- **督促**（`# expect:` 未達時の `wait_for_state`）は `kind=nudge` として `calls[]` に記録し
  予算に算入する。bare 系アームには同一 series/run_index の harness 結果と**同数**の継続発話を
  各ステージ末尾に与える（`--nudges` で明示も可）。督促上限（`--max-nudges`、既定 2）に達しても
  台本は続行し、verdict は check.py が決める（未達の記録は `state_trace[]`）。
- `# expect:` はライトパス/フルパスの両経路で成り立つ**到達状態のみ**を書く（`task.md` 参照）。
- `permission_denials` は全アーム共通に記録し、INVALID 化の規則もアーム種別に依らない。

## 実行方法（ハーネス本体ルートで。実走行には claude CLI ≥ 2.1.251 が必要）

```
python tools/e2e-run.py --selftest                                   # 装置の自己検証(claude を起動しない)
python tools/e2e-run.py --plan evaluation/experiments/S1-expense-capability.json --run-index 1 --dry-run
python tools/e2e-run.py --plan evaluation/experiments/S1-expense-capability.json --next   # ABAB 順で次の走行
python tools/e2e-run.py --plan evaluation/experiments/S2-expense-efficiency.json --budget-rung 0 --next
python tools/e2e-run.py --arm harness --task todo-cli --experiment capability --series-id pilot \
    --model claude-opus-5 --effort high --total-budget-usd 60 --fresh-policy fresh-on-command   # 事前登録なしのパイロット
python tools/eval-report.py --validate           # 系列の妥当性(条件単一・予算同額・事前登録の sha256/登録日時)
python tools/eval-report.py --report             # evaluation/REPORT.md を生成(--check で鮮度検査)
python evaluation/tasks/todo-cli/check.py --selftest
python evaluation/tasks/expense-webapp/check.py --selftest           # 矛盾解消跡の負例(NEEDS CLARIFICATION 等)を含む
python tools/eval-report.py --selftest                               # Fisher/Wilson の既知値・backfill 規則
```

`--plan` は事前登録 JSON を正とし、CLI 引数で違う値を与えるとエラーになる。走行は
`evaluation/runs/<series>/<結果名>/`（gitignore。workdir・stream-json・git bundle・隔離
`CLAUDE_CONFIG_DIR`）に置かれ、結果 JSON は `results/<task>-<arm>-<timestamp>.json`、
応答全文・`system/init`・check 出力・GATE_STATUS 最終値は `results/trajectories/<結果名>/`、
transcript zip は `results/trajectories/<結果名>.zip`（gitignore。sha256 は JSON と
`MANIFEST.json` に記録）に退避される。

## 指標の定義（結果 JSON schema_version 2）

| 指標 | 出どころ |
|---|---|
| verdict / valid_for_comparison / invalid_reasons | 上記の判定・妥当性条件 |
| コスト（USD） | `totals.cost_usd`（各 call の `total_cost_usd` 合計。CLI の定価推定。`cost_basis` を併記） |
| 予算使用率・打切り | `totals.budget_used_ratio`、`n_truncated_calls`、`n_max_turns_calls`、`max_call_cost_ratio` |
| トークン | `totals.tokens{input, output, cache_read, cache_creation}`（`modelUsage` 合算）、`cost_by_model` |
| ターン・時間 | `totals.num_turns`、`totals.duration_s` |
| 督促 | `totals.n_nudges`、`nudges_by_stage`、`state_trace[]` |
| 生成物 | `artifact_stats`（bootstrap コミットからの git-diff スコープ。ファイル数・拡張子別・テスト数・追加/削除行。全言語） |
| 条件 | `conditions`（C の全項目 + `init_fingerprint`）、`experiment.condition_hash`、`experiment.plan_sha256` |
| 退避 | `retention`（git bundle の sha256・check 出力・GATE 最終値）、`trajectory`（transcript zip の sha256・session_id） |

## n と統計（事前登録。`tools/eval-report.py` が計算する）

- **n=5/アーム**（合計 10 走行/課題/実験）。完全分離（5/5 vs 0/5）で Fisher 両側 p=0.0079、
  4/5 vs 0/5 で p=0.048。**3/5 vs 0/5 は p=0.17 で差を主張しない**。pass^5 合格の Wilson 95%
  下限は 0.57。分離しなければ n=8 まで ABAB 順で延長（8/8 vs 0/8 で p=0.00016）。
- DNF/INVALID で有効 n < 5 になった場合の補充規則（ABAB 順で最大 +3 走行）も事前登録に書く。
- n=2 の 2/2 vs 0/2 は p=0.33 であり、装置 v1 の「2/2 vs 0/2」は統計的主張にならない。
- 同一アーム内の verdict 混在率を `flake_rate` として併記する。

## 費用の報告形式（比を作らない）

費用は **範囲（min / median / max）** で報告する。アーム間の費用比（「約7.7倍」「約14倍」
「4.9倍」「46%減」型）や PASS 費用 ÷ FAIL 費用は意味を持たないため作らない。
許される文型: 「B=$120 の能力実験（Opus 5 high, n=5）で harness は 5/5 PASS [0.57, 1.0]、
bare は 0/5 [0, 0.43]、p=0.008。harness の PASS 費用は $38〜$52、bare の FAIL 費用は $11〜$17」。
cost-per-PASS（系列総支出 ÷ PASS 数）はアーム内の絶対値のみ。`COMPARISON.md` の実測表は
`REPORT.md`（生成物）からしか値を取らない。

## 記録方針: 結果は negative でも記録する（削除しない）

- harness が bare に**負けた結果も `results/` に残す**。都合の良い結果だけを残すと測定基盤
  そのものの信頼が失われ、「実測が正」の原則（AGENTS.md）に反する。
- **比較から除外できるのは (a) 測定装置側の欠陥（`INVALID_APPARATUS`）か (b) 事前登録済みの
  妥当性条件のどちらかだけ**で、いずれの場合も `results/` から削除せず `invalid_reasons[]` に
  理由を機械記録する。除外の判断を結果を見てから追加してはならない（追加するなら次の系列の
  事前登録として）。
- 装置 v1（`schema_version` 1）の 10 走行は `tools/eval-report.py --backfill` で verdict /
  `valid_for_comparison=false` / `invalid_reasons` / `recovered_model` を付与した**参考値**で、
  workdir が消失（Temp の自動クリーンアップ 2026-09-04/06）しているため再検証できない。
  比較表には載せない（`REPORT.md` の別表に verdict の再判定つきで残す）。

## 課題

| 課題 | 性格 | 検証する仮説 |
|---|---|---|
| todo-cli | 小規模・明確要件・単一機能 | セレモニーコスト（小タスクでの harness のオーバーヘッド） |
| expense-webapp | 曖昧要件・複数機能・変更要求・2ステージ | 価値仮説: 曖昧さと回帰リスクの下で成功率・保守性が上がる |

expense-webapp の memo は意図的に曖昧で、締め日ルールに矛盾を1つ含む（「月末締め」と
「25日締め」の混在）。`check.py` の `contradiction_resolved` は**同一文内に決定動詞 + 単一の
締め日**を要求し、`[NEEDS CLARIFICATION: 月末 or 25日?]`（Spec Kit の spec-template が標準で
含む）・未定・TBD・「25日または月末」併記を負例として除外する（selftest に FP/FN の両方向）。
bare にも Spec Kit にも合格経路はある（どちらの締め日でも「決定の記録」があればよい）。

## 実施順序（監査 §6。費用は API 課金で $900〜1,300 + パイロット $100 + 再走行 $300〜450/回）

1. transcript 退避と backfill（実施済み: `results/trajectories/MANIFEST.json`、10 本に verdict 付与）
2. 装置 v2 + `eval-report` + `check.py` 強化 → selftest 緑 → CI 追加
3. 開発機を Claude Code 2.1.265 系へ更新し、**パイロット 1+1 走行**（harness/bare）で非打切り時の
   `max_call_cost` と `budget_used_ratio` を実測して B_cap を確定（事前登録の `status: draft` →
   `registered` はこのとき）
4. S1 expense-webapp 能力実験（Opus 5 high、B_cap=$120、n=5、ABAB）
5. S2 expense-webapp 効率ラダー（$12/$24/$48、n=5）
6. S3/S4 todo-cli（能力 B_cap=$60、効率 $4/$12）
7. S5 Spec Kit v1.0.5 アーム（能力のみ、n=5。`arms/speckit.json` の smoke が通ってから）。
   OMC は `--plugin-dir <pinned checkout>` で S6 候補（FORCE により tier は無効化される）
8. `REPORT.md` を生成し、COMPARISON の実測表は生成物からのみ転記

## 構成

```
evaluation/
  README.md                 … 本ファイル(目的・用語・実験の分離・妥当性条件・統計・記録方針)
  REPORT.md                 … 生成物(tools/eval-report.py --report。手編集禁止)
  arms/<name>.json          … アーム仕様(harness / bare / speckit。依存ゼロの JSON)
  experiments/<series>.json … 事前登録(課題・アーム順・n・予算・判定規則。sha256 を結果に記録)
  tasks/<task>/task.md      … 課題定義(memo 全文・受入条件・発話系列・# expect: 注釈)
  tasks/<task>/check.py     … 決定論チェッカー(生成物を実行して受入条件を検証)
  results/*.json            … 結果 JSON(negative も記録。v1 は backfill 済みの参考値)
  results/trajectories/     … MANIFEST.json(zip の sha256・model 件数)と <結果名>/ の小さな退避物
  results/manual/*.json     … 手動 E2E(Copilot 経路。.github/harness/COPILOT-E2E.md のチェックリスト結果を機械可読化。
                               schema manual-e2e/1。REPORT.md「手動 E2E」節に集計。A/B 比較・統計には使わない。A5-8)
  manual/copilot-e2e_template.json … 手動 E2E 結果の記入テンプレ(コピーして results/manual/ に置く)
  runs/<series>/            … 走行の作業領域(gitignore。workdir・bundle・stream・隔離 CONFIG_DIR)
```

注意: `evaluation/` と `tools/e2e-run.py`・`tools/eval-report.py` はハーネス本体専用の
測定装置であり、配布物（git archive / ZIP / intake 経路）には含めない（`.gitattributes` の
export-ignore・`sync-harness.py` の EXCLUDE_FILES・`intake-app.py` の TEMPLATE_EXCLUDE_REL の
三重台帳。三者一致は両ツールの selftest が検査する）。

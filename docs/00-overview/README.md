# docs/ について

このフォルダは、このプロジェクトの「要件定義 → 設計 → 実装 → テスト → リリース」の
成果物を段階的に保存する場所です。フェーズごとにサブフォルダが分かれています。

| フォルダ | フェーズ | 主な成果物 |
|---|---|---|
| `00-overview/` | 横断 | progress.md（GATE_STATUS と直下の GATE_COUNTERS。語彙・遷移・復旧の正は `.github/harness/STATE-MACHINE.md`、検査は `python tools/gate_status.py check`）, learnings.md, notepad.md（未確定の途中状態）, change-requests.md（運用中の変更請求台帳）, intake-report.md（brownfield 取り込み時）。教訓候補の自動起草はここではなく `.github/hooks/logs/learnings-draft.local.md`（ローカル・git 管理外・30 日で回転）に置かれ、learnings.md への転記は人か `/10-retrospective` の明示操作だけ（旧 learnings-pending.md は廃止。残っていれば棚卸しして削除する） |
| `01-requirements/` | 要件定義 | requirements.md, nfr.md, glossary.md, environment.md（デプロイ環境・自動化可否。設計・リリースの前提） |
| `02-design/` | 設計 | architecture.md, adr/（ADR 連番）, detailed-design/, interfaces/（ICD。大規模分割時）, ui/（HTML モックアップ・design-tokens.md。画面がある場合） |
| `03-implementation/` | 実装 | tasks.md |
| `04-test/` | テスト | test-plan.md, test-report.md, review-log.md（独立レビュー記録）, security-review-report.md（リリース前セキュリティレビュー） |
| `05-release/` | リリース | release-checklist.md, CHANGELOG.md |
| `06-retrospective/` | 振り返り | retrospective.md（ハーネス改善提案を含む）, effort-report.md / baselines.json（トークン集計の生成物） |

各フォルダの `*_template.md` はひな形です。実際の成果物は同じフォルダに
テンプレートをコピーして（例: `requirements_template.md` → `requirements.md`）作成します。

進捗状況は [progress.md](progress.md)（`progress_template.md` から作成）で管理します。
`/99-status` プロンプトを実行すると自動で確認・更新できます。

このフォルダにはもう1つ、`learnings.md`（`learnings_template.md` から作成）が置かれます。
開発中に得た教訓を1行ずつ蓄積するファイルで、SessionStartフックにより
以後のすべてのセッションへ自動注入されます。

`harness-origin.md` は**ハーネス本体の場所と配布鮮度の自動記録**です（`path` / `source_url` /
`source_commit` / `archive_sha256` / `synced_at` / `latest_decision` / `route`）。`tools/sync-harness.py`
と `tools/intake-app.py` の `--apply` が書き、GitHub テンプレート・ZIP 経路では生成されないため
`python tools/doctor.py --write-origin --harness <本体パス>` で作ります（`/00-start-project` の初期化で
案内。手で編集しない）。SessionStart フック（inject-progress）は `latest_decision` と本体の DECISIONS.md の
最新 D 番号を比べ、古ければ「`/91-sync-from-harness` を先に実行」を 1 行注入し、`python tools/doctor.py`
の `distribution` 行が同じ判定を表で出します。

トークン・費用の計測データ（会話ごとの受領書）はここではなく `.github/hooks/logs/usage/`
（ローカル・git 管理外）に置かれ、集計だけが `../06-retrospective/effort-report.md` に
`python tools/effort-report.py` で生成されます（旧 `effort-log.csv` は廃止・`--migrate` で取り込み）。

## トレーサビリティ ID 体系（文書間で共通。変えない）

| ID | 何の ID か | 定義する文書 | 参照する文書 |
|---|---|---|---|
| `US-nnn` | ユーザーストーリー（機能要求） | requirements.md §5 | architecture.md 要件対応表 / tasks.md「対応要件」/ test-plan.md「対応要件ID」 |
| `FR-nnn` | 個別の機能要求文（EARS。US の下に付ける場合） | requirements.md | 同上 |
| `NFR-nnn` | 非機能要件 | nfr.md の ID 列 | architecture.md §6 / test-plan.md 非機能テストの「対応要件ID」 |
| `A-nnn` | 前提・リスク | requirements.md §7 | 設計・変更請求の差し戻し理由 |
| `IF-nnn` | インターフェース契約（ICD） | 02-design/interfaces/ | サブシステムの requirements / architecture |
| `ADR nnnn` | 設計判断 | 02-design/adr/nnnn-*.md | architecture.md |
| `TASK-nnn` | 実装タスク | tasks.md | review-log.md / change-requests.md |
| `UT- / IT- / E2E- / NFT-nnn` | テストケース（単体 / 結合 / E2E / 非機能） | test-plan.md | test-report.md / change-requests.md |
| `CR-nnn` | 変更請求 | change-requests.md | review-log.md |

`python tools/trace-check.py <ルート>` が `US-|FR-|NFR-` の参照整合（orphan / dangling）を機械検査する。

使い方の全体像は [リポジトリルートのREADME](../../README.md) を参照してください。

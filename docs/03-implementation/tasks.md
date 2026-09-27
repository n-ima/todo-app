# 実装タスクリスト

設計（`docs/02-design/`）を実装可能な粒度に分解したもの。依存関係の順に並べる。
1タスク = 概ね1コミットで完結する規模を目安にする。

**完了条件（done契約）**: 各タスクには着手前に「完了条件」を必ず定義する。
完了条件 = **機械的検証**（実行するテスト・ビルド・lint等のコマンド）+
**実行時確認**（起動・画面操作・API応答など、動くことの証拠。該当する場合のみ）。
`[x]` はこの完了条件を実行して満たした証拠をもって付ける。「実装し終えた」という
自己申告だけで付けない（完了を自然言語の主張ではなく検証に束縛する）。
証拠はタスク行の直下に **同じ書込で** 次の1行形式で書く（3点セット。gate-check スキル）:
`  - 証拠: `<再実行可能なコマンド>` → <出力の要約> (YYYY-MM-DD HH:MM)`
（例: `  - 証拠: `npm test` → 28 passed (2026-09-10 14:02)`）。`[x]` を増やす書込に
この3点（コマンド・出力要約・日時）が無いと `guard-done-evidence` フックが書込自体を拒否する
（エージェントのツール呼出のみ。人間の直接編集は対象外。A2-4b）。

**人手必須タスク**（`.github/workflows/` への配置などエージェントが書き込めない作業）は
`- [ ] 👤 TASK-xxx: <内容>` のように `👤` を付けて区別する。この印のタスクは
ドラフトの出力では完了にならず、**実体の配置・実行の確認をもって** `[x]` にする
（人手必須タスクが残っている間は実装フェーズを完了にしない）。

**試行記録（失敗時のみ）**: タスクが完了条件を満たせなかったときは、そのタスク行の直下に
`  - 試行: <n>回 / 失敗署名: <同一失敗の要約> / 次戦略: retry|replan` の1行を
記録・更新する（成功したタスクには書かない）。会話の記憶ではなくこのファイルが正であり、
セッションを跨いでも試行回数の上限判定（実装エージェントのループ制御）が維持される。

**依存（任意）**: 先行タスクがあるときは、タスク行の括弧内に `依存: TASK-xxx` を
書いてよい（複数あればカンマ区切り。省略時は「リスト順 = 依存順」とみなす）。
依存の無いタスク同士は、**ユーザーが明示要求した場合に限り** git worktree 分離で
並列実装してよい（既定は直列。並列時は 1 worktree 1 ライター・マージは直列）。

共通の前提: 実装規約は `.github/skills/dotnet-conventions/SKILL.md`。テストは `tests/TodoApp.Tests`（xUnit）に置き、
下記の「完了条件」の `dotnet test` はフィルタ無しで全件が通ること（既存テストを壊さない）を含む。
略記: `BUILD` = `dotnet build TodoApp.sln -warnaserror`、`TEST` = `dotnet test tests/TodoApp.Tests`。
既存コード: なし（2026-09-27 に確認。ハーネスと docs のみ）。開発機に .NET 10 SDK が未導入（`dotnet` コマンドが見つからない）。

## 基盤・共通部分

- [x] 👤 TASK-000: 開発機に .NET 10 SDK を導入する（`winget install Microsoft.DotNet.SDK.10`。E2E 用に Chrome か Edge も）（対応設計: architecture.md §10「開発環境」/ 完了条件: `dotnet --list-sdks` に 10.x が出る）
  - 証拠: `dotnet --list-sdks` → 10.0.401 [C:\Program Files\dotnet\sdk] (2026-09-27 10:05)
- [x] TASK-001: ソリューションの骨組み（`TodoApp.sln`・`src/TodoApp`・`tests/TodoApp.Tests`・`Directory.Build.props`（net10.0・Nullable・TreatWarningsAsErrors・AnalysisLevel latest-recommended）・`Directory.Packages.props`（DD-01 §1 の NuGet を最新安定版で固定）・`.gitignore` 追記・空の Web ホストとスモークテスト 1 件）（依存: TASK-000 / 対応要件: NFR-007 / 対応設計: DD-01 §1 / 完了条件: `BUILD` が警告 0 で成功・`TEST` が 1 件以上 passed）
  - 証拠: `dotnet build TodoApp.sln -warnaserror` → 0 個の警告 0 エラー / `dotnet test tests/TodoApp.Tests` → 合計 1 成功 1 失敗 0 (2026-09-27 13:46)
- [ ] 👤 TASK-002: CI 設定 `.github/workflows/ci.yml`（build-test・secrets(gitleaks)・package の 3 ジョブ。Action はコミットハッシュ固定）。エージェントはドラフトを `docs/03-implementation/ci.yml.draft` に出力し、ユーザーが `.github/workflows/ci.yml` へ配置する（依存: TASK-001 / 対応要件: NFR-012・A-017 / 対応設計: DD-10 §7 / 完了条件: ファイルが配置され、最初の push 後に build-test と secrets が緑（push はユーザー承認のうえ））
  - 状況: ドラフト出力済み（docs/03-implementation/ci.yml.draft）。ユーザーの配置と push 待ち
- [x] TASK-003: 共通基盤 Infrastructure: `IClock`（Tokyo Standard Time の暦日）・`SystemClock`・テスト用 `FakeClock`・`ErrorIds`（DD-12 の全 ID と文言）・`AppErrorException`・設定クラス（DD-01 §7 の全キー）と `appsettings.json`・`appsettings.local.json` の読み込み（依存: TASK-001 / 対応要件: NFR-015 / 対応設計: DD-01 §3・§5・§7・DD-12 / 完了条件: `TEST` で IClock の日付境界（UTC 14:59/15:00）・ErrorIds と DD-12 の件数一致の単体テストが通る）
  - 証拠: `dotnet build TodoApp.sln -warnaserror` → 0 エラー・0 個の警告 / `dotnet test tests/TodoApp.Tests` → 合計 8 件・成功 8・失敗 0（IClock の UTC 14:59:59/15:00 境界・ErrorIds と DD-12 の 46 件一致を含む） (2026-09-27 14:23)
- [x] TASK-004: DB 接続と Migrator: `Db`（接続ごとの PRAGMA・`WriteAsync` の BEGIN IMMEDIATE・SQLITE_BUSY→`E-DB-BUSY`）・`migrations/0001_init.sql`（DD-02 §2 と同一）・`Migrator`（schema_version・1 ファイル 1 トランザクション・`-- requires: foreign_keys=off`・ExpectedSchemaVersion）（依存: TASK-003 / 対応要件: US-015・NFR-010 / 対応設計: DD-01 §4・DD-02 / 完了条件: `TEST` で一時ファイル DB への適用・再適用で何もしない・2 ファイル目失敗時のロールバック・初期 3 状態の結合テストが通る）
  - 証拠: `dotnet build TodoApp.sln -warnaserror` → 0 警告 0 エラー ／ `dotnet test tests/TodoApp.Tests` → 合計 17・成功 17・失敗 0（MigratorTests 9 件: 0001 と DD-02 の同一性・一時 DB への適用と初期 3 状態・再適用で何もしない・2 ファイル目失敗のロールバック・foreign_keys=off・E-DB-BUSY） (2026-09-27 14:29)
- [x] TASK-005: CLI の枠組みと DB 系サブコマンド: `Program.cs` の CLI 振り分け・`--data`・サービス稼働中の拒否（終了コード 40）・`migrate --init/--plan/--apply`・`check-schema`・起動時の版不一致で `E-SYS-SCHEMA-MISMATCH` 終了（依存: TASK-004 / 対応要件: US-019・US-022 / 対応設計: DD-10 §1・DD-02 §1 / 完了条件: `TEST` で各サブコマンドの終了コード（0/3/4/5/6/10）の結合テストが通る・実行時確認 `dotnet run --project src/TodoApp -- migrate --init --data <tmp>` → 0）
  - 証拠: `dotnet test tests/TodoApp.Tests` → 合計 28・失敗 0・成功 28（CliTests で終了コード 0/1/3/4/5/6/10/40 を確認） (2026-09-27 14:34)
  - 証拠: `dotnet build TodoApp.sln -warnaserror` → 0 個の警告・0 エラー (2026-09-27 14:34)
  - 証拠: `dotnet run --project src/TodoApp -- migrate --init --data <tmp>/d` → 終了コード 0（0001_init.sql 適用・版 1） (2026-09-27 14:34)
- [x] TASK-006: Web ホストの共通設定: Kestrel 443/80（証明書は設定から。開発時は開発証明書）・80→443 転送（ループバックの `/healthz` は除外）・セキュリティヘッダー（CSP 等）・`Cache-Control: no-store`・例外ハンドラ（`E-SYS-UNEXPECTED`＋相関 ID）・JSON エラー応答の形・Serilog（日次ファイル・マスク）・`/healthz`・Windows サービス対応（依存: TASK-005 / 対応要件: NFR-006・NFR-007・NFR-008・FR-008 / 対応設計: DD-01 §5・§6・§8 / 完了条件: `TEST` で WebApplicationFactory によるヘッダー・`/healthz` 200・想定外例外の 500 画面に相関 ID・スタックトレースを出さないことの結合テストが通る）
  - 試行: 1回 / 失敗署名: レビュー BLOCKER H1 — appsettings.Production.json:5 の証明書パスが不正な JSON エスケープ（本番起動で FormatException）。全 appsettings*.json を読み込むテストが無い / 次戦略: replan（パス修正＋構成読込テスト追加）
  - 証拠: `dotnet build TodoApp.sln -warnaserror` → 0 個の警告 0 エラー / `dotnet test tests/TodoApp.Tests` → 合計 91 成功 91 失敗 0（WebHostTests の既存結合テストに加え AppSettingsTests: src/TodoApp の全 appsettings*.json が AddJsonFile で読込可・Production の証明書パスが C:\TodoApp\certs\server.pfx） (2026-09-27 15:53)
- [x] TASK-007: 共通レイアウトと静的資産: `_Layout.cshtml`（ヘッダー・ナビ・csrf/autosave の meta・フラッシュ・エラー要約）・`wwwroot/css/app.css`（design-tokens.md の CSS 変数・768px のレスポンシブ）・`confirm.js`（`<dialog>` の共通処理）・`/error` 画面（依存: TASK-006 / 対応要件: NFR-011・NFR-014 / 対応設計: DD-11 §1・§4・§5・ui/design-tokens.md / 完了条件: `BUILD`・`TEST` のレンダリング結合テスト（meta の出力・インラインスクリプトが無いこと）が通る）
  - 証拠: `dotnet build TodoApp.sln -warnaserror` → 0 個の警告 0 エラー / `dotnet test tests/TodoApp.Tests` → 合計 46 成功 46 失敗 0（LayoutTests: csrf-token/autosave-seconds の meta 出力・インラインの script/style/on* 無し・/error 画面に文言/エラー ID/相関 ID/一覧へ戻る・POST の例外も 500 画面・app.css のトークンと 767px メディアクエリ・confirm.js 配信。既存 WebHostTests も通過） (2026-09-27 15:07)

- [ ] TASK-008: レビュー M1: 初期パスワードの利用者で想定外例外が /error ではなく /account/password への 302 になる問題。`ForcePasswordChange` の除外に `/error` を加え、DD-03 §3 の例外リストに `/error` を追記（依存: TASK-103 / 対応要件: US-002・NFR-008 / 対応設計: DD-03 §3・DD-01 §5 / 完了条件: `TEST` で must_change 利用者の想定外例外が 500・相関 ID 表示になる結合テストが通る）
- [ ] TASK-009: レビュー M2: `AppErrorException` を `/error` で DD-12 の ID・文言・ステータスに変換（画面と `/api` の JSON。E-DB-BUSY は 503）（依存: TASK-008 / 対応要件: NFR-008 / 対応設計: DD-12・DD-01 §5・ADR 0002 / 完了条件: `TEST` でログイン中の SQLITE_BUSY が 503 E-DB-BUSY になる（画面・API とも）結合テストが通る）
- [ ] TASK-010: レビュー M3: 起動失敗の扱い（イベントログへの出力が効くよう Serilog の構成を直す・トップレベルで例外を捕捉してログ＋イベントログ＋終了コード 1）（依存: TASK-009 / 対応要件: NFR-008 / 対応設計: DD-01 §8・DD-02 §1 / 完了条件: `TEST` で起動失敗（版不一致・構成不正）時に終了コード 1 でログが残る結合テストが通る）
- [ ] TASK-011: レビュー M4(a)・LOW: `global.json` に `sdk.version`（10.0.401・rollForward latestFeature）・CLI の Migrator 生成を try 内へ・`migrate --init` 失敗時に作った todo.db を削除・適用途中の IOException を 5 に・`Auth:PasswordMinLength` の使用・CLI の Console 入出力を UTF-8 に・Login の暗黙 Required を日本語の E-AUTH-FAILED に（依存: TASK-010 / 対応要件: US-019・US-001 / 対応設計: DD-10 §1・DD-03 §2 / 完了条件: `TEST` で各点の回帰テストが通る）

## コア機能

- [x] TASK-101: 利用者リポジトリと認証: `UserRepository`・`AuthService.LoginAsync`（ダミーハッシュ・5 回で 15 分ロック・無効化を漏らさない・Rehash）・Cookie 認証（毎要求の ShouldRenew・除外パス・OnValidatePrincipal の stamp/is_active 検査）・`/login`・`POST /logout`・未ログイン転送（API は 401）・`returnUrl` の IsLocalUrl・Antiforgery 全適用と `E-CSRF`（依存: TASK-007 / 対応要件: US-001・NFR-005・NFR-007 / 対応設計: DD-03 §1・§2・DD-01 §6 / 完了条件: `TEST` で US-001 の受け入れ条件（成功・失敗・5 回目ロック・6 回目 E-AUTH-LOCKED・15 分後解除・8 時間無操作で失効・無効化の即時失効・外部 returnUrl 無視・CSRF 無しの POST 拒否）の結合テストが通る）
  - 証拠: `dotnet build TodoApp.sln -warnaserror` → 0 エラー・0 個の警告 / `dotnet test tests/TodoApp.Tests` → 合計 70 件・成功 70・失敗 0（AuthTests 24 件: 成功・失敗の非区別・5 回目ロック・6 回目 E-AUTH-LOCKED・15 分後解除・8 時間無操作で失効と毎要求延長・無効化/stamp 更新の即時失効・外部 returnUrl 無視・CSRF 無しのフォーム/API POST 拒否を含む） (2026-09-27 15:17)
- [x] TASK-102: CLI `create-admin`・`has-admin`（依存: TASK-101 / 対応要件: US-019 / 対応設計: DD-10 §1 / 完了条件: `TEST` で終了コード 0/11/12/14 とパスワードが標準入力から読まれることの結合テストが通る）
  - 証拠: `dotnet build TodoApp.sln -warnaserror` → 警告 0・エラー 0 / `dotnet test tests/TodoApp.Tests` → 合計 79・成功 79・失敗 0（CliTests に create-admin の 0/11/12・標準入力 1 行目のパスワード・has-admin の 0/14 を追加） (2026-09-27 15:34)
- [x] TASK-103: パスワード変更と強制転送: `/account/password`（E-PWD-CURRENT/LENGTH/CONFIRM/SAME の順序・8 文字ちょうど可）・成功時の stamp 更新と Cookie 再発行・`must_change_password` の強制転送（API は 403 `E-AUTH-MUST-CHANGE`）（依存: TASK-101 / 対応要件: US-002 / 対応設計: DD-03 §3 / 完了条件: `TEST` で US-002 の受け入れ条件と「他端末のセッションが失効し本人は継続」の結合テストが通る）
  - 証拠: `dotnet build TodoApp.sln -warnaserror` → 0 個の警告・0 エラー / `dotnet test tests/TodoApp.Tests` → 合計 89・成功 89・失敗 0（PasswordChangeTests: 強制転送 302/403 E-AUTH-MUST-CHANGE・E-PWD-* の順序・8/128 文字可・他端末のセッション失効と本人継続） (2026-09-27 15:43)
- [ ] TASK-104: 利用者管理 `/admin/users`: 登録・変更・無効化/有効化（本人のロック解放）・パスワード再設定・最後の管理者の保護・管理画面の 403 `E-PERM-ADMIN-ONLY`・「（無効）表示名」の表示ヘルパ（依存: TASK-103 / 対応要件: US-003 / 対応設計: DD-03 §4 / 完了条件: `TEST` で US-003 の受け入れ条件（重複・形式・最後の管理者の降格と無効化の拒否・無効化で即ログアウト・一般利用者の 403）の結合テストが通る）
- [ ] TASK-105: 状態定義 `/admin/workflow`: `WorkflowService`（追加・名前変更・上へ/下へ・完了扱いの付け外し・削除の 3 拒否条件。ゴミ箱を含めて数える）（依存: TASK-104 / 対応要件: US-015・FR-007 / 対応設計: DD-08 §1 / 完了条件: `TEST` で US-015 の受け入れ条件と E-WF-* 各エラーの結合テストが通る）
- [ ] TASK-106: プロジェクト: `ProjectService`（作成・名前変更・削除（配下の他人ロックで拒否））・`/admin/projects`・一覧の「＋プロジェクト」用の作成エンドポイント（依存: TASK-105 / 対応要件: US-004・US-008 / 対応設計: DD-04 §8 / 完了条件: `TEST` で `E-PROJECT-NAME`・重複名の許容・削除で配下の行が変わらないこと・一般利用者の 403 の結合テストが通る）
- [ ] TASK-107: `TaskPermission` とタスク作成: `TaskRepository`・`CurrentUser`・`TaskPermission`・入力検証（DD-04 §2 の全項目。メモの数え方・`title_norm`）・`TaskService.Create`（初期状態・sort_order・project_id・作成者記録）・割り当て通知（作成時）・新規作成画面 2 種（依存: TASK-106 / 対応要件: US-004・FR-001・FR-002・FR-005 / 対応設計: DD-04 §1〜§3・DD-08 §2 / 完了条件: `TEST` で TaskPermission の真理値表・検証の境界値（タイトル 200/201・メモ 100000/100001・同日の開始日と期限）・段数無制限・親がゴミ箱なら E-TASK-PARENT-GONE・自分への割り当てで通知しないことのテストが通る）
- [ ] TASK-108: 一覧（ツリー）: `TaskListService.Build`（1 回の SQL・メモリ上のツリー・M/V/D・完了の非表示と薄い親・プロジェクトの出し方・並び順）・段の展開規則（3 段・500 行・3,000 行上限と段の縮退の注記）・`/` 画面と `_TaskRows` 部分ビュー・`GET /api/tasks/{id}/children`・`tree.js`・0 件表示（依存: TASK-107 / 対応要件: US-005・NFR-001 / 対応設計: DD-06 §2〜§4 / 完了条件: `TEST` で 3 段目は見え 4 段目は描かれない・完了の親が D になる・3,000 行超で段が縮退する・子の遅延取得の結合テストが通る・実行時確認: 開発起動でブラウザから一覧が表示できる）
- [ ] TASK-109: 絞り込み・検索・期限の強調: `TaskFilter`（DD-06 §1 のクエリ。不正値は既定値）・`NormalizeForSearch` の検索・`status` に完了扱いを含むときの扱い・`_DueBadge`（超過・間近。完了は除外）・「期限超過 n 件」（全体で数える・クリックで `/?overdue=1`）・絞り込みバーとナビのクエリ引き継ぎ（依存: TASK-108 / 対応要件: US-012・US-013 / 対応設計: DD-06 §1・§5・§6・DD-11 §1 / 完了条件: `TEST` で全角/半角・大小文字の同一視・ひらがな/カタカナの区別・期限の境界（昨日/今日/明日/明後日を FakeClock で）・期限超過件数が絞り込みに依存しないことのテストが通る）
- [ ] TASK-110: タスク詳細画面（閲覧）: 項目・パンくず・子タスク一覧・作成者/更新者・ロック状態の帯・操作の出し分け（DD-11 §3。無効ボタンと FR-001 の文言）・変更履歴セクション・ID 表示とリンクのコピー（メモはこの時点ではプレーン表示で可）（依存: TASK-109 / 対応要件: US-006・US-017・FR-001 / 対応設計: DD-11 §2・§3・DD-08 §3 / 完了条件: `TEST` で権限なし・他人のロック中・本人のロック中・管理者の各状態のボタン出し分けと履歴 0 件表示の結合テストが通る）
- [ ] TASK-111: 編集ロックと下書きの API: `LockService`（取得・競合 409 `E-LOCK-HELD`・本人の再取得・30 分の遅延評価・下書きと conflict の返却）・`DELETE /lock`（キャンセル）・`PUT /draft`（結果型でコミット後 409 `E-LOCK-LOST`）・`DELETE /draft`・ログアウト/無効化での解放の接続（依存: TASK-110 / 対応要件: US-010 / 対応設計: DD-05 §1〜§4・DD-01 §5 / 完了条件: `TEST` で 29 分 59 秒は有効・30 分で無効・同時取得で片方だけ成功・E-LOCK-LOST 後に drafts に送信内容が残ること・conflict の判定の結合テストが通る）
- [ ] TASK-112: 保存: `TaskService.SaveAsync`（ロック確認を権限より先・`E-TASK-CHILDREN-OPEN`（全段で数える）・履歴 status/assignee・割り当て通知・ロックと下書きの削除）・`POST /tasks/{id}/save`（依存: TASK-111 / 対応要件: US-006・US-016・US-017・FR-003・FR-007 / 対応設計: DD-04 §4・DD-08 §2・§3 / 完了条件: `TEST` で US-006 の受け入れ条件・強制解除後の保存が下書きに残る・親の値を子から計算しない・任意の状態遷移が可能なことの結合テストが通る）
- [ ] TASK-113: 詳細画面の編集モード `edit.js`: 「編集する」でロック取得→編集欄・下書き復元/破棄/衝突のダイアログ（無効値の置き換えと注記）・60 秒ごとの自動保存（dirty のときだけ）・保存/キャンセル・完了警告の確認ダイアログ・E-LOCK-LOST で読み取り専用化・401 でログインへ（依存: TASK-112 / 対応要件: US-006・US-010 / 対応設計: DD-05 §3・DD-11 §3 / 完了条件: `BUILD`・`TEST` が通る・実行時確認: 開発起動で 2 つのブラウザセッションから編集ロックの競合と下書き復元ができる（手順と結果を証拠に書く））
- [ ] TASK-114: メモの Markdown: `MarkdownService`（DD-07 のパイプライン・`disabled` のチェックボックス・画像を出さない・リンク規則（task:/http(s)/その他）・task: の存在確認を 1 回の SQL）・詳細画面での描画・`POST /api/markdown/preview`（活動に数えない・上限検査）・`preview.js`（500ms デバウンス・タブと 1200px 以上の 2 分割）・残り文字数表示（依存: TASK-113 / 対応要件: US-009・FR-005・NFR-007 / 対応設計: DD-07 / 完了条件: `TEST` で `<script>` がエスケープされる・`javascript:` がリンクにならない・削除済みタスクへのリンクが「（削除されたタスク）」・表が表にならない・チェックボックスに disabled の単体テストが通る）
- [ ] TASK-115: 移動・上へ/下へ: `POST /tasks/{id}/move`（子孫集合・E-PERM-SUBTREE・E-TASK-CYCLE・E-TASK-PARENT-GONE・E-LOCK-HELD-SUBTREE・プロジェクト変更時に削除済みを含む全子孫の project_id 更新）・移動ダイアログ（自分と子孫を選べない）・`POST /tasks/{id}/reorder`（手動順のときだけ表示）（依存: TASK-114 / 対応要件: US-007 / 対応設計: DD-04 §5・§6・DD-11 §3 / 完了条件: `TEST` で US-007 の受け入れ条件（自分の子孫の下へ移動を拒否・配下に他人の担当があれば一般利用者は拒否し管理者は可・端での上へは何もしない・ゴミ箱の子孫も project_id が変わる）の結合テストが通る）
- [ ] TASK-116: 削除・ゴミ箱・復元: `TrashService`（削除で子孫に trash_root_id・本人のロック削除・下書きは残す）・`/trash`（根だけ・一般は自分の削除分・管理者は全件とプロジェクト・完全削除予定日）・復元（E-TRASH-NOT-FOUND・E-TRASH-PARENT-MISSING・プロジェクトの復元は管理者）・削除確認ダイアログ（依存: TASK-115 / 対応要件: US-008・FR-004 / 対応設計: DD-04 §7・§8 / 完了条件: `TEST` で US-008 の受け入れ条件（子孫ごと戻る・先に単独削除された子孫は別の根のまま・親がゴミ箱なら復元拒否・他人の削除分を一般利用者は戻せない）の結合テストが通る）
- [ ] TASK-117: 強制解除: `POST /api/tasks/{id}/lock/force-release`（管理者のみ・無ければ 200・履歴 `lock_force_released`・ログ）と詳細画面のボタン（一般利用者には出さない）（依存: TASK-116 / 対応要件: US-011 / 対応設計: DD-05 §6 / 完了条件: `TEST` で US-011 の受け入れ条件（一般利用者 403・解除後に元の保持者の保存が E-LOCK-LOST で下書きに残る・履歴の表示文言）の結合テストが通る）

## 周辺機能

- [ ] TASK-201: 通知: `NotificationService`（ヘッダーの未読件数と 99+・`/notifications` の 200 件・`open` で既読と遷移（他人の ID は 404・削除済みは一覧へ戻して `E-NOTIFY-TASK-DELETED`）・「すべて既読にする」）（依存: TASK-117 / 対応要件: US-016・FR-008 / 対応設計: DD-08 §2 / 完了条件: `TEST` で US-016 の受け入れ条件（割り当て通知の文言・自分自身は除外・既読で件数が減る・削除済みタスクの通知）の結合テストが通る）
- [ ] TASK-202: カレンダー: `CalendarService`（月 6 週・週表示・日曜始まり・帯と点・週の境目で分割「（続き）」・貪欲法の段配置・月は 3 段まで＋「他 n 件」とその日の一覧ダイアログ・期限なし除外・期限の強調）・`/calendar`（前後・今日・クエリ引き継ぎ・書き込み操作なし・スマートフォンは週表示既定）（依存: TASK-201 / 対応要件: US-014・FR-006 / 対応設計: DD-06 §7 / 完了条件: `TEST` で 1 日 4 件で「他 1 件」・週をまたぐ帯の分割・開始日だけのタスクを出さない・完了の非表示の単体テストが通る）
- [ ] TASK-203: CSV 書き出し: `ExportService`（M を前順・列・UTF-8 BOM・CRLF・RFC 4180・CSV インジェクション対策・（無効）付き担当者・ファイル名）・`GET /export.csv`（依存: TASK-202 / 対応要件: US-018・NFR-006 / 対応設計: DD-06 §8 / 完了条件: `TEST` で BOM・`"` と改行を含むタイトルのエスケープ・`=1+1` 先頭に `'`・未ログインで 401/転送の結合テストが通る）
- [ ] TASK-204: 秘密情報と暗号化: `SecretStore`（DPAPI LocalMachine・entropy 別）・`BackupCrypto`（DD-09 §4 の形式・チャンクと AAD・`E-RESTORE-DECRYPT`）・CLI `init-secrets`・`import-key`・`set-cert-password`・`set-share-credential`（依存: TASK-203 / 対応要件: US-020・NFR-006 / 対応設計: DD-09 §4・§5・DD-10 §1 / 完了条件: `TEST` で暗号化→復号の往復（0 バイト・1 チャンク境界・複数チャンク）・チャンクの並べ替え/切り詰め/鍵違いで失敗・`init-secrets` の 0/13・`import-key` の 15 のテストが通る）
- [ ] TASK-205: バックアップと復旧の CLI: `BackupService.RunAsync`（backup_runs の kind/destination・オンラインバックアップ・quick_check・暗号化・一時名→改名・7 世代・一時ファイルの finally 削除・共有フォルダ接続は抽象化してテストで差し替え）・CLI `backup`・`list-backups`・`restore`（30/31/32・before-restore への改名・WAL・`--key-stdin` 成功時の鍵保存）（依存: TASK-204 / 対応要件: US-020・NFR-004・NFR-009 / 対応設計: DD-09 §3・DD-10 §1・§4 手順 6 / 完了条件: `TEST` で書き込み中のバックアップ→restore で内容が一致・8 回目で最古が消える・失敗時に既存世代が残る・pre-update は数えない・restore の各終了コードの結合テストが通る）
- [ ] TASK-206: 日次処理: `DailyJobService`（起動時と 5 分周期・SemaphoreSlim・`daily.lock`・daily_runs・30 分ごとの再試行（kind=daily のみ）・手順 1〜6（期限切れロック掃除・ゴミ箱の完全削除（31 日目・古い順・根ごと 1 トランザクション・プロジェクトは深い順）・期限の通知の INSERT OR IGNORE・90 日超のログ削除））（依存: TASK-205 / 対応要件: US-008・US-016・US-019・US-020・FR-004・NFR-008 / 対応設計: DD-09 §1・§2・DD-04 §7・DD-08 §2 / 完了条件: `TEST` で FakeClock による「同じ日に 2 回目は何もしない」・30 日目は残り 31 日目に消える・明日→今日の通知が重ならない・期限変更で再通知・daily.lock 保持中は何もしないの結合テストが通る）
- [ ] TASK-207: 管理画面 `/admin/system` とバックアップ警告帯: 日次の最終成功・直近の試行とエラー文言・直近の更新前バックアップ・共有フォルダ・日次処理の完了日・アプリと DB の版・連続失敗 n>=3 の帯（管理者のみ・稼働日で数える）（依存: TASK-206 / 対応要件: US-020 / 対応設計: DD-09 §6 / 完了条件: `TEST` で 2 日失敗は帯なし・3 日で帯・行の無い日を数えない・一般利用者には出ない・pre-update の成功で警告が消えないことの結合テストが通る）
- [ ] TASK-208: `Install-TodoApp.ps1`（通常/`-Recover`・14 手順を成果物で個別判定・既存バックアップ検知の確認・UTF-8 BOM）と Pester テスト（外部コマンドは Mock）（依存: TASK-207 / 対応要件: US-019・NFR-004 / 対応設計: DD-10 §2 / 完了条件: `pwsh`/`powershell` で `Invoke-Pester tests/scripts` が通る（前提不足の列挙・チェックサム不一致で中止・install.completed で中止・再実行で続きから・-Recover で起動しない）。実機での ACL 付与の確認はリリース時の人手確認として docs/05-release へ申し送る）
- [ ] TASK-209: `Update-TodoApp.ps1`（daily.lock 待ち 30 分・停止・更新前バックアップ（届かなければローカル＋確認）・migrate --plan/承認/--apply の分岐・current 切替・/healthz・スキーマ変更有無で分かれるロールバック・古い版と pre-update の整理）と Pester テスト（依存: TASK-208 / 対応要件: US-022・NFR-003 / 対応設計: DD-10 §3 / 完了条件: `Invoke-Pester tests/scripts` で承認 N で旧版起動・apply 4 で旧版起動・5 で起動しない・起動失敗時の自動戻し（スキーマ変更なしのみ）のテストが通る）
- [ ] TASK-210: `Restore-TodoApp.ps1`・`New-TodoAppCertificate.ps1` と Pester テスト（依存: TASK-209 / 対応要件: US-020・NFR-004 / 対応設計: DD-10 §4・§5 / 完了条件: `Invoke-Pester tests/scripts` で y 以外で中止・復号失敗→鍵入力で再試行・31/32 の中止文言・証明書スクリプトの引数検証のテストが通る）

## 仕上げ（ドキュメント・クリーンアップ等）

- [ ] TASK-901: 配布物の組み立て: `dotnet publish -r win-x64 --self-contained` の設定・migrations と scripts と version.txt の同梱・ZIP と `.sha256` を作るスクリプト（CI の package ジョブと共通）（依存: TASK-210 / 対応要件: US-019・US-022 / 対応設計: DD-10 §6 / 完了条件: 開発機でパッケージスクリプトを実行し ZIP の中身が DD-10 §6 と一致・SHA-256 ファイルが検証できる・展開した exe で `migrate --init` → 0）
- [ ] TASK-902: E2E（Playwright for .NET）の骨組みと主要動線のスモーク（ログイン→初回パスワード変更→プロジェクト作成→タスク作成→編集・保存→一覧→カレンダー）。本格的な E2E ケースはテストフェーズで test-plan.md に対応づけて足す（依存: TASK-901 / 対応要件: NFR-011 / 対応設計: architecture.md §1 テスト / 完了条件: `dotnet test tests/TodoApp.E2E` がローカルで passed）
- [ ] TASK-903: 開発者向け README（ビルド・テスト・開発起動・CLI の使い方・E2E の前提）と、実装で確定した値（パッケージの版・設定の実値）を docs へ反映（依存: TASK-902 / 対応要件: ― / 対応設計: DD-01 / 完了条件: `BUILD`・`TEST` が通り、README の手順どおりに開発起動して `/healthz` が 200）

---
実装メモ（判断に迷った点、後で見直すべき点など）はこのファイル末尾に追記してよい。

- US-021（ドラッグ＆ドロップ）は Could のため初版のタスクに含めない（DD-06 §9 は後から足す場合の形）。US-012 の（Could）メモ本文検索・US-018 の（Could）メモ列は TASK-109・TASK-203 で実装コストが小さければ含め、含めなかった場合はここに記録する。
- 負荷試験（15,000 件・同時 10 人。NFR-001）はテストフェーズで行う。

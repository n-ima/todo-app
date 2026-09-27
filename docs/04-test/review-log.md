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

### 2026-09-27 16:10 / implementation / 対象: TASK-000〜TASK-007, TASK-101〜TASK-103 / verdict: BLOCKER

- 呼び出し元: implement
- 対象コミット: e959a4c..eb947b7（reviewer は git を実行できないため src/・tests/・docs/03-implementation/ の作業ツリーを直接読んだ）
- 根拠（file:line）:
  - src/TodoApp/appsettings.Production.json:5 — `"C:\TodoApp\certs\server.pfx"` が不正な JSON エスケープで、本番起動時の構成読み込みで失敗する。どのテストでも検出されない（HIGH）
  - src/TodoApp/Infrastructure/AuthSetup.cs:126-146 / src/TodoApp/Infrastructure/WebHostSetup.cs:59,74 / src/TodoApp/Pages/Error.cshtml.cs:36-49 — must_change の利用者では例外の再実行先 /error が 302 になり、E-SYS-UNEXPECTED・相関 ID・ErrorModel のログが失われる（MEDIUM）
  - src/TodoApp/Pages/Error.cshtml.cs:17,33 / src/TodoApp/Data/Db.cs:53-56 — AppErrorException（E-DB-BUSY 503）が 500 の E-SYS-UNEXPECTED になる。担当タスクが無い（MEDIUM）
  - src/TodoApp/Program.cs:11-37 / src/TodoApp/Infrastructure/WebHostSetup.cs:39 — AddSerilog がプロバイダーを置き換えるためイベントログに書かれず、起動失敗時に終了コード 1 にならない（DD-01 §8・DD-02 §1）（MEDIUM）
  - docs/03-implementation/ci.yml.draft:22-27 / global.json:1-5 / tests/TodoApp.Tests/CliTests.cs:118-130 — sdk.version が無く setup-dotnet が失敗する見込み。ubuntu では他プロセス検出テストが落ちる見込み（要確認）（MEDIUM）
  - src/TodoApp/Cli/CliRunner.cs:59 / src/TodoApp/Data/Migrator.cs:23-30 — Migrator の生成が try の外にあり、未処理例外になる（LOW）
  - src/TodoApp/Cli/CliRunner.cs:145-162 — init が失敗すると空の todo.db が残り、再実行が 10 になる（LOW）
  - src/TodoApp/Cli/CliRunner.cs:212 — 途中で IOException が起きると 5 ではなく 1 になる（LOW）
  - src/TodoApp/Services/AuthService.cs:100 / src/TodoApp/Cli/CliRunner.cs:267 — Auth:PasswordMinLength が使われていない（LOW）
  - src/TodoApp/Program.cs:6-9 — CLI の出力が UTF-8 になっていない（DD-10 §1）（LOW）
  - src/TodoApp/Pages/Login.cshtml.cs:17-20 / src/TodoApp/Pages/Shared/_Layout.cshtml:59-69 — 暗黙の Required で英語のエラー文言が出る（LOW）
  - src/TodoApp/Cli/CliRunner.cs:253-257 — ログイン ID 形式違反の 1 は DD-10 の表の範囲内。DD-10 §1 と TASK-208 に扱いを明記すること（LOW）
  - src/TodoApp/Cli/StartupSchemaCheck.cs:20 / src/TodoApp/Infrastructure/ErrorIds.cs:59 / src/TodoApp/Cli/CliRunner.cs:274 / src/TodoApp/Infrastructure/AuthSetup.cs:149-163 — healthz ごとのプール破棄・文言の備考混入・UtcNow の直接使用・/api 以外の Minimal API の CSRF 対象外（INFO）
  - src/TodoApp/Pages/Login.cshtml.cs:54 / src/TodoApp/Infrastructure/AuthSetup.cs:33-54,82-100 / src/TodoApp/Services/AuthService.cs:27-120 / src/TodoApp/Data/Migrator.cs:61-112 — returnUrl・Cookie・セッション延長・ログインとパスワード変更の順序・Migrator のトランザクションは設計どおり（INFO）
- 対応: H1 → TASK-006 を差し戻して修正（構成読込テスト追加）。M1 → TASK-008、M2 → TASK-009、M3 → TASK-010、M4(a) と LOW → TASK-011 を追加。M4(b)（ubuntu ランナーで Windows 前提テストが落ちる）は DD-10 §7 の変更を伴うためユーザー判断へ。INFO の CSRF・403 の注意は notepad に引き継ぎ

### 2026-09-27 16:05 / implementation / 対象: TASK-006（H1 再レビュー） / verdict: 承認

- 呼び出し元: implement
- 対象コミット: working tree（eb947b7 以降の未コミット変更）
- 根拠（file:line）:
  - src/TodoApp/appsettings.Production.json:5 — 証明書パスが `"C:\TodoApp\certs\server.pfx"` になり、JSON エスケープとして正しい。H1 は解消（INFO）
  - tests/TodoApp.Tests/AppSettingsTests.cs:19-25 — `src/TodoApp` の全 `appsettings*.json` を `optional:false` で読み込む。3 件未満なら失敗する（INFO）
  - tests/TodoApp.Tests/AppSettingsTests.cs:28-34 — 証明書パスの完全一致を確認。assert の緩和・skip なし（INFO）
  - src/TodoApp/appsettings.json:10 / src/TodoApp/appsettings.Development.json:1-7 — ほかの構成ファイルに不正エスケープは無い（INFO）
  - docs/03-implementation/tasks.md:54-55 — 失敗の記録と証拠が修正内容と合う（INFO）
  - 未確認: reviewer はコマンドを実行できないため build/test は未実行（コーディネーターの証拠で代替）
- 対応: 対応不要（H1 はクローズ。M1〜M4 は TASK-008〜011 で対応）

### 2026-09-27 18:53 / implementation / 対象: TASK-008〜TASK-011, TASK-104〜TASK-109 / verdict: MAJOR

- 呼び出し元: implement
- 対象コミット: 410628e..d8e857a（reviewer は git を実行できないため src/・tests/・docs/03-implementation/tasks.md・関連設計書の作業ツリーを直接読んだ）
- 根拠（file:line）:
  - src/TodoApp/Services/TaskListService.cs:58-69 / src/TodoApp/Data/Db.cs:41-57 — 読むだけの処理（一覧 `/`・`/api/tasks/{id}/children`・`memo=1` の検索）まで `Db.WriteAsync`（BEGIN IMMEDIATE）で実行し、メモの NFKC 正規化と照合もロック保持中に行う。同時閲覧で直列化し、保存・自動保存が busy_timeout 5 秒を超えて E-DB-BUSY になりうる（NFR-001・DD-06 §2・DD-01 §4）。同じ使い方が UserAdminService.cs:15-20・WorkflowService.cs:10-15・ProjectService.cs:11-16・TaskService.cs:83-92 にもある（MEDIUM）
  - src/TodoApp/Services/ProjectService.cs:9 / src/TodoApp/Services/TaskService.cs:26,75 — ロック有効時間 30 分とメモ上限 100,000 が直書きで、`Lock:IdleMinutes`・`Memo:MaxLength`（DD-01 §7）を読まない（LOW）
  - src/TodoApp/Cli/CliRunner.cs:327-334,76 — `appsettings.local.json` が壊れていると `create-admin` が InvalidDataException で未処理例外になる（終了コード 1 にならない）。`GetValue<int>` はキーが無いと 0 を返し最小長検査が効かない（LOW）
  - src/TodoApp/Pages/Admin/Users.cshtml:54-61 / Users.cshtml.cs:52-57 / Workflow.cshtml.cs:55-60 / Projects.cshtml.cs:49-54 — 入力エラーの再表示で入力値が消え、フィールド横の文言も無い（DD-01 §5）（LOW）
  - docs/02-design/detailed-design/DD-10-cli-scripts.md:16 / docs/03-implementation/tasks.md:107 — 前回 LOW「create-admin の形式違反は終了コード 1」の DD-10 §1・TASK-208 への明記が未対応で追跡タスクも無い（LOW）
  - docs/03-implementation/tasks.md:84,86 / src/TodoApp/wwwroot/js/tree.js:1-67 — TASK-108 の実行時確認は curl だけで、tree.js の開閉・遅延挿入が一度も動かされていない（LOW）
  - src/TodoApp/Services/TaskFilter.cs:42-43 — `<summary>` の重複（INFO）
  - src/TodoApp/Pages/Admin/Users.cshtml:8 — 管理小ナビに /admin/projects へのリンクが無い（INFO）
  - src/TodoApp/Pages/Shared/_TaskRows.cshtml:6 — D 行は期限の行帯を出さない。DD-06 §4 の範囲内（INFO）
  - src/TodoApp/Services/UserAdminService.cs:61 — 無効化時のロック解放は TASK-111 で追跡済み（INFO）
  - 前回 M1（AuthSetup.cs:136-143・DD-03:34・PasswordChangeTests.cs:138-155）・M2（Error.cshtml.cs:36-58・AppErrorResponseTests.cs）・M3（WebHostRunner.cs:19-94・StartupFailureTests.cs）・M4(a)（global.json・ci.yml.draft）・LOW 7 件（CliRunner.cs ほか・各回帰テスト）はすべて解消（INFO）
  - TaskListService・TaskFilter・DueBadge（DD-06 §1〜§6）、ProjectRepository のロック境界（DD-05 §1）と削除（DD-04 §8）、TaskService の作成（DD-04 §2・§3）、UserAdminService の最後の管理者保護（DD-03 §4）、WorkflowService の削除判定（DD-08 §1）は設計どおり（INFO）
  - セキュリティ: 管理画面の二重防御・SQL の全パラメータ化・Html.Raw 不使用・tree.js の innerHTML は同一オリジンのエンコード済み部分ビューのみ・＋プロジェクトの戻り先は固定。問題なし（INFO）
  - tests/TodoApp.Tests/SmokeTests.cs:14-15 — 404 確認先を `/no-such-page` に変えただけで弱体化ではない。Skip・TODO・プレースホルダ無し（INFO）
  - 未確認: build/test の実行（tasks.md の証拠で代替）、コミットの分け方、UserAdminTests・WorkflowTests の本文
- 対応: MEDIUM → TASK-012（`Db.ReadAsync` の追加と読み取り処理の移行・正規化と照合をトランザクション外へ）を追加し TASK-110 より前に消化。LOW のうち CLI 設定読込・admin 画面の入力保持・DD-10 §1/TASK-208 の終了コード明記 → TASK-013 を追加。Lock:IdleMinutes → TASK-111、Memo:MaxLength → TASK-112、tree.js の実動確認 → TASK-902 の E2E へ（notepad に引き継ぎ）。INFO は対応不要

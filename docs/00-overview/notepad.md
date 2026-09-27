# notepad（未確定の途中状態。確定したら docs 本体へ移して行を消す）

## 実装フェーズの後続タスクへの引き継ぎ（2026-09-27）

- [TASK-204] 証明書パスワードは未供給。SecretStore 実装時に `Kestrel:Endpoints:Https:Certificate:Password` へ流し込む（それまで Production 起動は失敗する）
- [TASK-111] ログアウト・無効化時の編集ロック解放（`LockService.ReleaseAllOf`）が未接続
- [TASK-201 / TASK-207] _Layout に通知ベルの件数・管理者のバックアップ警告帯は未実装
- [テスト共通] Web テストは `TestWebApp`（`CreateAsync(configureServices)`・`CreateUserAsync`・`LoginAsync`・`CreateLoggedInClientAsync` 等）と直列 collection を使う。Razor は日本語を HTML エンコードするので本文比較は `WebUtility.HtmlDecode` 後に行う。E-DB-BUSY のテストは約 30 秒かかる
- [TASK-903 / 実機確認] イベントログのソース `TodoApp` への書込みは実機未確認。複数 Web ホスト同時起動でログファイルがロックされる現象の原因は未確定（テストは直列化と削除リトライで回避）
- [TASK-112 / TASK-115] Antiforgery 検証は `/api/*` とフォーム POST だけが対象（AuthSetup.cs）。`/tasks/{id}/save`・`move`・`reorder` を Minimal API で作るなら CSRF 検証の対象に含めること
- [TASK-104] `OnRedirectToAccessDenied` が未設定。403 `E-PERM-ADMIN-ONLY` はこのタスクで配線する
- [TASK-208] `create-admin` のログイン ID 形式違反は終了コード 1（「その他」）。Install の手順 8 はスクリプト側で同じ正規表現の事前検証を入れ、DD-10 §1 にこの扱いを明記する
- [ユーザー判断待ち] CI の build-test は DD-10 §7 で ubuntu-latest だが、Windows 前提のテスト（他プロセスによる todo.db オープン検出・後続の DPAPI）は ubuntu で落ちる見込み（レビュー M4(b)）
- [INFO] ErrorIds の E-AUTH-REQUIRED の文言に DD-12 の備考「（画面はログインへ転送）」が混入・CliRunner.cs の `DateTimeOffset.UtcNow` 直接使用・/healthz ごとの `ClearAllPools()`

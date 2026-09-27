# notepad（未確定の途中状態。確定したら docs 本体へ移して行を消す）

## 実装フェーズの後続タスクへの引き継ぎ（2026-09-27）

- [TASK-204] 証明書パスワードは未供給。SecretStore 実装時に `Kestrel:Endpoints:Https:Certificate:Password` へ流し込む（それまで Production 起動は失敗する）
- [TASK-111] ログアウト・無効化時の編集ロック解放（`LockService.ReleaseAllOf`）が未接続（無効化は `UserAdminService.SetActiveAsync` の同じトランザクション内に追加する）
- [TASK-201 / TASK-207] _Layout に通知ベルの件数・管理者のバックアップ警告帯は未実装
- [テスト共通] Web テストは `TestWebApp`（`CreateAsync(configureServices)`・`CreateUserAsync`・`LoginAsync`・`CreateLoggedInClientAsync` 等）と直列 collection を使う。Razor は日本語を HTML エンコードするので本文比較は `WebUtility.HtmlDecode` 後に行う。E-DB-BUSY のテストは約 30 秒かかる
- [TASK-903 / 実機確認] イベントログのソース `TodoApp` への書込みは実機未確認。複数 Web ホスト同時起動でログファイルがロックされる現象の原因は未確定（テストは直列化と削除リトライで回避）
- [TASK-112 / TASK-115] Antiforgery 検証は `/api/*` とフォーム POST だけが対象（AuthSetup.cs）。`/tasks/{id}/save`・`move`・`reorder` を Minimal API で作るなら CSRF 検証の対象に含めること
- [TASK-208] `create-admin` のログイン ID 形式違反は終了コード 1（「その他」）。Install の手順 8 はスクリプト側で同じ正規表現の事前検証を入れ、DD-10 §1 にこの扱いを明記する
- [INFO] ErrorIds の E-AUTH-REQUIRED の文言に DD-12 の備考「（画面はログインへ転送）」が混入・CliRunner.cs の `DateTimeOffset.UtcNow` 直接使用・/healthz ごとの `ClearAllPools()`
- [TASK-115] 一覧の手動順（sort=manual）での「上へ」「下へ」ボタン（DD-06 §4）は未設置（reorder API と同時に `_TaskRows` へ足す）。`_DueBadge` は `(DateOnly?, DueState)` を渡す部分ビューで、詳細・カレンダー（TASK-110・TASK-202）でも使う
- [テスト/MINOR] E-PWD-LENGTH の文言は「8 文字以上」固定で、`Auth:PasswordMinLength` を変えても追従しない（TASK-011）
- [TASK-111] ロック有効時間は `Lock:IdleMinutes` から読む判定を 1 か所にまとめ、ProjectService（現在 30 分直書き）もそれを使う（レビュー 18:53 LOW）
- [TASK-112] メモ上限は `Memo:MaxLength` から読む（TaskService の 100,000 直書きを置き換え。レビュー 18:53 LOW）
- [TASK-902] tree.js の開閉・子の遅延挿入を E2E で実際に動かす（TASK-108 の実行時確認は curl のみ。レビュー 18:53 LOW）

# notepad（未確定の途中状態。確定したら docs 本体へ移して行を消す）

## 実装フェーズの後続タスクへの引き継ぎ（2026-09-27）

- [TASK-204] 証明書パスワードは未供給。SecretStore 実装時に `Kestrel:Endpoints:Https:Certificate:Password` へ流し込む（それまで Production 起動は失敗する）
- [TASK-103] `must_change_password` の強制転送は未実装（ログイン直後の `/account/password` 転送のみある）
- [TASK-111] ログアウト・無効化時の編集ロック解放（`LockService.ReleaseAllOf`）が未接続
- [TASK-201 / TASK-207] _Layout に通知ベルの件数・管理者のバックアップ警告帯は未実装
- [テスト共通] Web テストは `TestWebApp`（`CreateAsync(configureServices)`・`CreateUserAsync`・`LoginAsync`・`CreateLoggedInClientAsync` 等）と直列 collection を使う。Razor は日本語を HTML エンコードするので本文比較は `WebUtility.HtmlDecode` 後に行う。E-DB-BUSY のテストは約 30 秒かかる
- [TASK-903 / 実機確認] イベントログのソース `TodoApp` への書込みは実機未確認。複数 Web ホスト同時起動でログファイルがロックされる現象の原因は未確定（テストは直列化と削除リトライで回避）

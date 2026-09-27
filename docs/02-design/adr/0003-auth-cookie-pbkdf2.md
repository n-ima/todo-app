# ADR 0003: 認証は Cookie セッション＋PBKDF2、CSRF・XSS は標準機能と Markdown の HTML 無効化で防ぐ

- 日付: 2026-09-26
- ステータス: 承認済み

## コンテキスト

- アプリ自身が管理する ID・パスワード。ソルト付きの低速ハッシュ、5 回失敗で 15 分ロック、8 時間無操作で失効、初回ログインでパスワード変更を強制（NFR-005・US-001・US-002）。
- 状態を変える全リクエストに CSRF 対策、メモのプレビューで HTML を実行させない（NFR-007）。外部の認証基盤は使えない（社外に出さない。NFR-006）。

## 検討した選択肢

| 選択肢 | メリット | デメリット |
|---|---|---|
| A. ASP.NET Core の Cookie 認証 + `PasswordHasher<T>`（PBKDF2-HMAC-SHA512・10 万回以上・ソルト 128bit）を単体で使う | 必要な機能だけを持つ。テーブルを自分で設計できる（利用者の無効化・初期パスワードフラグ・失敗回数） | ロックアウト等のロジックを自分で書く（数十行） |
| B. ASP.NET Core Identity 一式 | ロックアウト等が揃う | EF Core 前提でテーブルが多く、ADR 0002 の素の SQL 方針と合わない。メール確認などの不要機能が付く |
| C. Argon2id（外部ライブラリ） | 耐 GPU 性が高い | 外部依存が増える。10 人規模の社内 LAN（悪意の利用者を想定しない。Q-02-1）では PBKDF2 で十分 |

## 決定

A を採用する。

- セッション: 暗号化された認証 Cookie（`HttpOnly`・`Secure`・`SameSite=Strict`）。スライディング有効期限 8 時間（NFR-005）。ロックが残るのを避けるため、ログアウトはロックの即時解放と同じトランザクションで行う（US-010）。
- 利用者の無効化・ロール変更・パスワード再設定の直後に効かせるため、Cookie に `security_stamp` を入れ、リクエストごとに DB の値と照合する（10 人規模なので毎回照合してよい）。
- ロックアウト: `users.failed_count` と `locked_until` を持ち、5 回連続失敗で 15 分（暫定値 A-007。設定ファイルで変えられる）。存在しない ID でも同じ文言・同じ処理時間で失敗させる（US-001）。
- CSRF: Razor Pages の標準の Antiforgery を全フォーム・全 JSON API（`fetch` は `RequestVerificationToken` ヘッダー）に必須化する。
- XSS: Razor の自動エスケープ。Markdown は Markdig の `DisableHtml()` を使い、生 HTML を文字列として出す（US-009）。リンクの URL は `https:`・`http:`・`task:`（タスクへのリンク）以外を捨てる（`javascript:` を防ぐ）。加えて `Content-Security-Policy: default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'` を付け、インラインスクリプトを使わない。
- 認可: 全ページ・全 API で `[Authorize]` を既定にし、ロールと §5.0 の編集権限はサービス層の `TaskPermission` で判定する（画面のボタンの無効化は表示のためだけ。FR-001）。

## 結果・影響

- 捨てたもの: SSO・多要素認証（要件に無い）。
- パスワードの暫定値（A-007）は `appsettings.json` の設定値にし、社内規程に合わせて変えられるようにする。

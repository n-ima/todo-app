# DD-01 ソリューション構成と共通規約

## 1. ソリューション構成

```
TodoApp.sln
src/TodoApp/                     … Web アプリ本体＋CLI（1 つの exe）
  Program.cs                     … 引数が CLI サブコマンドなら Cli へ、無ければ Web ホスト
  Cli/                           … migrate / create-admin / backup / restore / list-backups / init-secrets / set-share-credential
  Pages/                         … Razor Pages（画面。DD-11）
  Api/                           … Minimal API のエンドポイント定義（/api/*。JSON）
  Services/                      … TaskService・TaskPermission・LockService・TrashService・ProjectService
                                   UserService・AuthService・WorkflowService・NotificationService
                                   HistoryService・TaskListService・CalendarService・ExportService・MarkdownService
  Data/                          … Db（接続生成）・各 Repository（Dapper）・Migrator
  Jobs/                          … DailyJobService・BackupService・BackupCrypto・SecretStore
  Infrastructure/                … IClock・AppErrorException・ErrorIds・ミドルウェア・設定クラス
  migrations/NNNN_name.sql       … 出力ディレクトリへコピー（CopyToOutputDirectory）
  wwwroot/css/app.css            … design-tokens.md の CSS 変数
  wwwroot/js/*.js                … tree.js・edit.js・preview.js・confirm.js（ES Modules）
scripts/                         … Install / Update / Restore / New-TodoAppCertificate（.ps1）
tests/TodoApp.Tests/             … xUnit 単体・結合（実 SQLite 一時ファイル・WebApplicationFactory）
tests/TodoApp.E2E/               … Playwright for .NET
tests/scripts/                   … Pester
```

- 対象フレームワーク `net10.0`、`<Nullable>enable`、`<TreatWarningsAsErrors>true`、`<AnalysisLevel>latest-recommended`（A-017 の代替の静的解析）。
- NuGet: Markdig・Dapper・Microsoft.Data.Sqlite・Serilog.AspNetCore・Serilog.Sinks.File・Microsoft.Extensions.Hosting.WindowsServices。テストは xUnit・Microsoft.AspNetCore.Mvc.Testing・Microsoft.Playwright。版は実装時の最新安定版を `Directory.Packages.props` で固定する。

## 2. 層と依存

- Pages / Api → Services → Data（一方向）。Pages・Api は入力の形式検証と HTTP への変換だけを行い、業務規則・認可は Services に置く。
- **認可はサービスの公開メソッドの先頭で `TaskPermission` またはロール検査を呼ぶ**（FR-001。画面側の無効表示は表示用）。
- サービスのメソッドは第 1 引数に `CurrentUser`（`Id`・`Role`・`DisplayName`）を受け取る。

## 3. 時刻と日付（NFR-015・US-013）

```csharp
public interface IClock {
    DateTimeOffset UtcNow { get; }
    DateOnly Today { get; }   // TimeZoneInfo "Tokyo Standard Time" の暦日
}
```

- DB の日時列は UTC の ISO 8601（`yyyy-MM-ddTHH:mm:ss.fffZ`）文字列。日付列（開始日・期限・`run_date` 等）は `yyyy-MM-dd`。
- 画面の表示は日本時間の `YYYY/MM/DD`、日時は `YYYY/MM/DD HH:mm`。入力は `<input type="date">`（`yyyy-MM-dd`）。
- `DateTime.Now`・`DateTime.Today` を直接使わない（テストで差し替えられなくなるため。レビュー観点）。

## 4. DB アクセス

- 接続ごとに `PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;`（`journal_mode=WAL` は migrate で 1 回設定）。
- 書き込みは `BEGIN IMMEDIATE` のトランザクション（`Db.WriteAsync(Func<IDbConnection, IDbTransaction, Task<T>>)`）で行い、読み取り→判定→書き込みの間に他の書き込みを挟ませない（ロック取得の競合・移動の検査を正しくするため）。
- SQL は Repository に置き、パラメータ化のみ。文字列連結・補間で SQL を組み立てない（NFR-007。IN 句は Dapper の配列展開を使う）。
- `SQLITE_BUSY` が `busy_timeout` を超えたら `E-DB-BUSY`（DD-12）。

## 5. エラーの表し方

- 業務エラーは `throw new AppErrorException(ErrorIds.X, args...)`。文言は `ErrorIds` の表（DD-12）から引く。例外は `Db.WriteAsync` のトランザクションをロールバックする。
- **例外（コミットしてから失敗を返すもの）**: `E-LOCK-LOST`（下書きを残す。DD-05 §3）。サービスは例外を投げずに結果型 `SaveResult.LockLost` を返し、トランザクションをコミットした後で API 層が 409 に変換する。ロールバックで消えてはいけない書き込みを伴う失敗は、この形だけを使う。
- JSON API の失敗応答: HTTP ステータス（DD-12）＋本文
  ```json
  { "error": { "id": "E-LOCK-HELD", "message": "山田 花子さんが編集中です（10:42 から）", "data": { "holder": "山田 花子", "since": "2026-09-28T01:42:00.000Z" } } }
  ```
- 画面（フォーム POST）の失敗: 同じ画面を再表示し、入力値を保持してページ上部とフィールド横に文言を出す。文言の末尾にエラー ID を小さく併記する（問い合わせ用）。
- 想定外の例外: 例外ハンドラが `E-SYS-UNEXPECTED` の画面を返し、相関 ID（`HttpContext.TraceIdentifier`）を画面とログに出す。スタックトレースは画面に出さない。

## 6. HTTP の共通設定（NFR-006・NFR-007・FR-008）

| 項目 | 値 |
|---|---|
| 待ち受け | `https://*:443`（証明書は `certs/server.pfx`。パスワードは `data/secrets/cert.bin`＝DPAPI）・`http://*:80` |
| HTTP→HTTPS | 80 番への要求は 301 で `https://<Host>/...` へ。ただし**ループバックからの `/healthz` は転送せず 80 番で応答**（更新スクリプトの確認用。PowerShell 5.1 は証明書検証の省略が難しいため） |
| HSTS | 付けない（自己署名・社内 LAN。証明書の信頼設定前の端末で締め出さないため） |
| セキュリティヘッダー | `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer` |
| 認証 | 全ページ・全 API に `RequireAuthorization` を既定適用。例外は `/login`・`/healthz`・静的ファイル |
| Antiforgery | 全 POST/PUT/DELETE。フォームは hidden、`fetch` は `RequestVerificationToken` ヘッダー（レイアウトの `<meta name="csrf-token">` から読む） |
| キャッシュ | 認証後のページ・API は `Cache-Control: no-store` |

外部への HTTP 送信（HttpClient）は実装しない。レビュー観点に含める（FR-008）。

## 7. 設定値（`appsettings.json`。暫定値は A-007）

| キー | 既定値 | 根拠 |
|---|---|---|
| `Auth:MaxFailedAttempts` | 5 | NFR-005 |
| `Auth:LockoutMinutes` | 15 | NFR-005 |
| `Auth:SessionIdleHours` | 8 | NFR-005 |
| `Auth:PasswordMinLength` | 8 | NFR-005 |
| `Lock:IdleMinutes` | 30 | US-010 |
| `Lock:AutosaveSeconds` | 60 | US-010・A-007（画面へ埋め込んで JS が使う） |
| `Memo:MaxLength` | 100000 | FR-005 |
| `Trash:RetentionDays` | 30 | US-008 |
| `Backup:Generations` | 7 | US-020 |
| `Backup:RetryMinutes` | 30 | US-020 |
| `Backup:WarnAfterFailedDays` | 3 | US-020 |
| `Backup:SharePath` | （Install で設定） | US-020 |
| `Daily:CheckIntervalMinutes` | 5 | ADR 0005 |
| `Log:RetentionDays` | 90 | NFR-008 |
| `Calendar:MonthMaxPerDay` | 3 | architecture.md §9 |
| `Paths:Data` | `C:\TodoApp\data` | ADR 0006 |

利用者に固有の値（共有フォルダのパス）は `C:\TodoApp\data\appsettings.local.json` に置き、版の入れ替えで消えないようにする（`current` 配下の `appsettings.json` は配布物の既定値）。

## 8. ログ（NFR-008）

- Serilog の日次ファイル `data/logs/app-YYYYMMDD.log`（日本時間の日付）。保持の削除は日次処理が行う（DD-09）。
- 出力するもの: 起動・停止、日次処理の各手順の開始/結果、バックアップの成否（エラー ID）、未処理例外（相関 ID）、ログイン失敗（ログイン ID と送信元 IP。パスワードは出さない）、ロック強制解除。
- 出力しないもの: パスワード・Cookie・Antiforgery トークン・バックアップ鍵・共有フォルダのパスワード・メモ本文。リクエストログにクエリ文字列を含めるのは `/tasks` 系だけ（検索語は業務データだが機密度が低いため可）。
- 起動失敗（Host 構築・マイグレーション版不一致・ポート使用中・証明書読み込み失敗）は Windows イベントログ（ソース `TodoApp`）にも書き、終了コード 1。

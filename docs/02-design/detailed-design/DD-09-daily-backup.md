# DD-09 日次処理・バックアップ・ログの保持

方針は [ADR 0005](../adr/0005-daily-job-and-backup.md)。

## 1. 日次処理の契機（US-019）

`DailyJobService : BackgroundService`。起動直後に 1 回、以後 `Daily:CheckIntervalMinutes`（5 分）ごとに `TickAsync()` を呼ぶ（`PeriodicTimer`。休止・スリープから復帰した後の最初の周期でも呼ばれる）。

```
TickAsync()
  today = clock.Today
  if daily_runs に today の行が無い:
      RunDailyAsync(today)                      … 1 日 1 回。止まっていた日数は問わない
  else if today に kind='daily' で成功したバックアップが無い
       and today の kind='daily' の最後の試行の started_at から 30 分以上経過:
      RunBackupOnlyAsync(today)                 … 当日中の再試行（US-020）
```

- 同じプロセス内での重複実行は `SemaphoreSlim(1)` で防ぐ（前回の Tick が続いていればスキップ）。
- **`daily.lock`**: `DailyJobService` だけが取る（`BackupService` と CLI は取らない。更新スクリプトがロックを保持したまま CLI の `backup` を呼んでも待ち合わせにならないように）。日次処理とバックアップの再試行の間、`data/daily.lock` を `FileShare.None`・`FileOptions.DeleteOnClose` で開いて保持する。開けなければ（更新スクリプトが保持中）その Tick は何もしない。更新スクリプトとの協調は DD-10 §3。
- 例外は Tick の中で捕まえてログに書き、サービスを落とさない（ADR 0001）。

## 2. 日次処理の手順（`RunDailyAsync`）

| 順 | 手順 | 内容 | 失敗時 |
|---|---|---|---|
| 1 | バックアップ | §3（kind='daily'）。当日すでに kind='daily' で成功していればスキップ | `backup_runs` に失敗を記録して次へ |
| 2 | 期限切れロックの掃除 | `DELETE FROM task_locks WHERE last_activity_at <= now - 30分` | ログに書いて次へ |
| 3 | ゴミ箱の完全削除 | DD-04 §7 | 根ごとにスキップしてログ |
| 4 | 期限の通知 | DD-08 §2 | ログに書いて次へ |
| 5 | 古いログの削除 | `data/logs/app-YYYYMMDD.log` のうちファイル名の日付 < today − 90 日 を削除 | ログに書いて次へ |
| 6 | 完了の記録 | `INSERT INTO daily_runs(run_date, completed_at) VALUES (today, now)` | ― |

- 手順 2〜5 は冪等に書き、失敗しても翌日に同じ処理で追いつく。手順 6 は手順 1〜5 の成否に関係なく記録する（同じ日の 2 回目の起動で 2〜5 を再実行しない＝US-019。バックアップだけは §1 の再試行で拾う）。

## 3. バックアップ（US-020・NFR-009）

```
BackupService.RunAsync(today, kind = 'daily', destinationDir = 共有フォルダ, rotate = true)
 1. INSERT backup_runs(run_date=today, kind, destination=destinationDir, started_at=now)
 2. スナップショット: SqliteConnection.BackupDatabase で data/tmp/snap-<ts>.db へ（WAL 下でも書き込みを止めない）
 3. snap に PRAGMA quick_check。'ok' でなければ E-BACKUP-SNAPSHOT
 4. 暗号化: §4 の形式で data/tmp/todo-<yyyyMMdd-HHmmss>.db.enc へ（鍵が読めなければ E-BACKUP-SECRET）
 5. 共有フォルダへ接続（WNetAddConnection2。資格情報は §5）。失敗は E-BACKUP-SHARE
 6. <share>\todo-<ts>.db.enc.tmp へコピー → 同じフォルダ内で todo-<ts>.db.enc へ改名
 7. rotate なら世代管理: <share> 直下の todo-*.db.enc を名前順に並べ、新しい 7 件を残して古いものを削除（削除の失敗はログだけ）
 8. UPDATE backup_runs SET finished_at, ok=1, file_name
 9. data/tmp の一時ファイルを削除（成功・失敗とも finally で）
 失敗: UPDATE backup_runs SET finished_at, ok=0, error_id。既存の世代は消さない
```

- `<ts>` は日本時間 `yyyyMMdd-HHmmss`。名前順＝時刻順になる。
- 更新スクリプトの直前バックアップは CLI `backup --to <share>\pre-update --no-rotate --kind pre-update` で同じ処理を使い、世代管理をしない。`kind` が `daily` 以外の記録は、当日の再試行・3 稼働日の警告・「最後に成功した日時」の判定に使わない（管理画面では別の行「直近の更新前バックアップ」として表示する）（`pre-update\` は `todo-*.db.enc` の列挙対象外。NFR-009）。`pre-update\` の古いファイルは Update が 5 件を超えた分を削除する。

## 4. 暗号化ファイルの形式（`BackupCrypto`）

| 位置 | 長さ | 内容 |
|---|---|---|
| ヘッダー | 8 | マジック `TODOBK01`（ASCII） |
|  | 1 | 形式の版 `0x01` |
|  | 4 | チャンクの平文サイズ（1,048,576。リトルエンディアン） |
|  | 8 | 平文の全長（リトルエンディアン） |
|  | 16 | ファイル ID（乱数） |
| チャンク（繰り返し） | 12 | nonce（乱数） |
|  | 4 | 暗号文の長さ |
|  | n | 暗号文（AES-256-GCM） |
|  | 16 | 認証タグ |

- AAD = ヘッダー全体（37 バイト）＋チャンク番号（8 バイト）＋最終チャンクなら `0x01` でなければ `0x00`。並べ替え・切り詰め・別ファイルとの差し替えを検出する。
- 復号: どれかのチャンクのタグ検証に失敗 → `E-RESTORE-DECRYPT`（鍵違いまたは破損。区別しない）。復号後の長さが全長と違う → 同じエラー。
- 鍵: 32 バイトの乱数（`RandomNumberGenerator`）。

## 5. 秘密情報の保管（`SecretStore`）

| ファイル（`data/secrets/`） | 中身 | 保護 |
|---|---|---|
| `backup.key` | バックアップ鍵 32 バイト | DPAPI `LocalMachine`（entropy `TodoApp.BackupKey.v1`） |
| `share.bin` | 共有フォルダの `{ "user": "...", "password": "..." }` | DPAPI `LocalMachine`（entropy `TodoApp.Share.v1`） |
| `cert.bin` | サーバー証明書 PFX のパスワード | DPAPI `LocalMachine`（entropy `TodoApp.Cert.v1`） |

- `data` フォルダの ACL: 継承を切り、`NT SERVICE\TodoApp` と `BUILTIN\Administrators` と `SYSTEM` だけにフルコントロール（Install が設定。NFR-006 (2)）。
- 共有フォルダのパスは `data/appsettings.local.json` の `Backup:SharePath`（秘密ではない）。
- 鍵の控え: `init-secrets` が Base64 で 1 回だけ標準出力に出す（ログには出さない）。人がパスワード管理ツールに保管する（Q-10-7）。

## 6. 状況表示と警告（US-020）

- `/admin/system`: 最後に成功した日次バックアップ（`kind='daily'`）の日時とファイル名、直近の日次の試行の日時・成否・エラー ID と文言、直近の更新前バックアップの日時と出力先、共有フォルダのパス、最後に日次処理を完了した日、アプリの版、DB の版。
- 警告: `backup_runs` の `kind='daily'` の行を `run_date` で集約し、新しい日付から順に「その日に成功が 1 件も無い」日が連続した数を n とする。`n >= 3` なら管理者のヘッダー直下に「バックアップが n 日失敗しています。管理画面で状況を確認してください」の帯（`/admin/system` へのリンク）。一般利用者には出さない。サーバーが止まっていた日は `backup_runs` に行が無いので数えない（稼働日で数える）。

## 7. ログの保持（NFR-008）

手順 5 で削除する。Serilog の `retainedFileCountLimit` は `null`（削除は日次処理に一本化し、日付基準の 90 日を守るため）。

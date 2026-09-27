# DD-10 CLI サブコマンド・運用スクリプト・CI

方針は [ADR 0006](../adr/0006-distribution-service-update-tls.md)。サーバー上の操作はすべて人が実行する（エージェントは実行しない。environment.md）。

## 1. CLI（`TodoApp.exe <サブコマンド>`）

共通: `--data <dir>`（既定 `C:\TodoApp\data`）。秘密の値は引数ではなく標準入力で受け取る（プロセス一覧に残さないため）。出力は UTF-8。
`migrate --apply`・`backup`・`restore`・`import-key` は、サービス `TodoApp` が実行中（または `todo.db` を他プロセスが開いている）なら終了コード 40 で拒否する（稼働中のデータ置き換えを防ぐ）。

| サブコマンド | 動作 | 終了コード |
|---|---|---|
| `migrate --init` | `todo.db` が無ければ作成し、WAL を設定し、全マイグレーションを適用 | 0 成功 / 10 既に存在 / 1 失敗 |
| `migrate --plan` | 未適用ファイルの名前と SQL 全文を表示 | 0 未適用あり / 3 未適用なし / 1 失敗 |
| `migrate --apply` | 未適用を番号順に 1 ファイル 1 トランザクションで適用（DD-02 §1） | 0 成功 / 4 最初のファイルで失敗（DB は変更なし）/ 5 途中まで適用して失敗（取り消せない）/ 1 その他 |
| `check-schema` | DB の版と期待する版を表示 | 0 一致 / 6 不一致 |
| `create-admin --login-id <id> --display-name <名前>` | 標準入力 1 行目をパスワードとして管理者を作成（`must_change_password=1`） | 0 / 11 重複 / 12 パスワード規則違反 / 1 |
| `has-admin` | 有効な管理者がいるか | 0 いる / 14 いない / 1 |
| `import-key` | 標準入力 1 行目の Base64 を検証（32 バイト）して `backup.key` に保存（既存は上書き） | 0 / 15 形式違反 / 1 |
| `init-secrets` | `backup.key` を生成し、Base64 を 1 回だけ表示。既にあれば何もしない | 0 生成 / 13 既存 / 1 |
| `set-share-credential --share <UNC>` | 標準入力 1 行目をユーザー名、2 行目をパスワードとして `share.bin` に保存し、`appsettings.local.json` に `SharePath` を書く。接続を試して結果を表示 | 0 接続成功 / 20 保存したが接続失敗 / 1 |
| `set-cert-password` | 標準入力 1 行目を `cert.bin` に保存 | 0 / 1 |
| `backup --to <dir> [--no-rotate] [--kind pre-update]` | DD-09 §3 を 1 回実行（サービス停止中に使う）。`backup_runs.kind` は `--kind`（既定 `manual`）。`daily.lock` は取らない | 0 / 1 |
| `list-backups --from <dir>` | `todo-*.db.enc` を新しい順に番号・日時・サイズで表示（`pre-update` も `--from` で指定可） | 0 / 1 |
| `restore --from <file> [--key-stdin]` | §4 の復号・検査・置き換え（サービス停止中） | 0 成功 / 30 復号失敗（鍵違い・破損）/ 31 整合性検査失敗 / 32 DB の版が新しすぎる / 1 |
| （共通） | サービス稼働中の拒否 | 40 |

## 2. `Install-TodoApp.ps1`（US-019）

```
.\Install-TodoApp.ps1 -Package <ZIP のパス> -CertificatePath <server.pfx> -BackupShare \\server\share [-Recover]
```

- **通常モード**: 新規導入。
- **復旧モード `-Recover`**（機材喪失後の新サーバー。US-020・NFR-004 (2)）: 新しい鍵を作らず、手順 9 で鍵の控え（Base64）の入力を求めて `import-key` で `backup.key` に取り込む。手順 8（管理者作成）と手順 13（起動）を行わず、最後に `Restore-TodoApp.ps1` の実行を案内して終わる（空の DB のバックアップが共有フォルダの世代を押し出さないため）。
- 通常モードで共有フォルダに既に `todo-*.db.enc` があれば、手順 9 の前に「既存のバックアップがあります。機材の入れ替えなら -Recover で実行し直してください。新しい鍵で続けますか (y/N)」と確認し、N なら中止する（控えの鍵を新しい鍵で上書きする事故を防ぐ）。

各手順は**成果物の有無で個別に判定**し、中断後の再実行で続きから進む（US-019「再実行で続きからやり直せる」）。

| 順 | 手順 | 失敗時・再実行時 |
|---|---|---|
| 1 | 前提の確認: Windows 11（ビルド 22000 以上）・PowerShell 5.1 以上・管理者として実行・`C:` の空き 1 GB 以上・TCP 443/80 が未使用（再実行で自サービスが使っている場合は除く）・証明書ファイルが存在 | 足りない項目をすべて列挙して中止（US-019） |
| 2 | ZIP の SHA-256 を、同じフォルダの `<ZIP>.sha256` と照合 | 不一致・`.sha256` が無い → 中止（再取得を案内） |
| 3 | `C:\TodoApp\data\install.completed` の確認 | ある → 「既に導入済みです。更新は更新スクリプトを使ってください」で中止（US-019。既存データに触れない）。無ければ以降の各手順を成果物で判定して進める（`todo.db` だけがあるのは前回の導入が途中で止まった状態） |
| 4 | `releases\<版>\` へ展開、`current` ジャンクション作成、`data`・`certs` フォルダ作成 | 中止（再実行でやり直せる） |
| 5 | サービス登録（**起動しない**）: `sc.exe create TodoApp binPath= "C:\TodoApp\current\TodoApp.exe" start= delayed-auto obj= "NT SERVICE\TodoApp"`、`sc.exe failure TodoApp reset= 86400 actions= restart/60000/restart/60000//`、イベントログのソース `TodoApp` を登録。既にあれば設定を上書き | 中止 |
| 6 | `data` の ACL（DD-09 §5）。サービス SID は `sc.exe showsid TodoApp` で得た SID 文字列で付与する（仮想アカウント名の解決に依存しない。サービス登録後に行う） | 中止 |
| 7 | 証明書を `certs\server.pfx` へコピー、パスワードを入力させ `set-cert-password` | 中止 |
| 8 | `todo.db` が無ければ `migrate --init`。有効な管理者が 0 人なら（`has-admin` の終了コードで判定）管理者のログイン ID・表示名・初期パスワード（`Read-Host -AsSecureString` を 2 回）→ `create-admin`（復旧モードでは行わない） | 規則違反なら再入力 |
| 9 | 鍵: 通常モードは `init-secrets`（13＝既存なら飛ばす）→ 鍵の Base64 を表示し「パスワード管理ツールに保管しましたか (y/N)」に y が入るまで進まない。復旧モードは控えの入力 → `import-key` | ― |
| 10 | 共有フォルダのユーザー名・パスワード → `set-share-credential`（`share.bin` があれば「設定し直しますか (y/N)」） | 接続失敗（20）は警告を表示して続行（退避先が未起動の可能性。A-022） |
| 11 | ファイアウォール: `New-NetFirewallRule -DisplayName "TodoApp HTTPS/HTTP" -Direction Inbound -Protocol TCP -LocalPort 80,443 -Profile Private -Action Allow`（同名があれば作り直し） | 中止 |
| 12 | `install.completed` を作成 | ― |
| 13 | （通常モード）サービス起動 → `http://127.0.0.1/healthz` を最大 60 秒待つ。（復旧モード）起動せず、`Restore-TodoApp.ps1` の実行を案内 | 失敗 → ログの場所を表示して終了（導入済みの印はあるので、原因を直して `Start-Service TodoApp`） |
| 14 | 人手の作業を表示: ネットワークプロファイルを「プライベート」に、高速スタートアップ・スリープの無効化、アクティブ時間の設定（A-019・A-023） | ― |

- 依存パッケージは配布物に同梱済みで、スクリプトはネットワークから何も取得しない（ADR 0001）。US-019 の「依存パッケージの取得中にネットワークが切れた」は、人がブラウザで配布物を取得する段階へ移り、手順 2 のチェックサム照合で検出して中止する（architecture.md §9 の決定。README の対応表にも記載）。
- 手順 6 の SID 付与は Windows 11 実機での確認が必要（実装フェーズの Install タスクの完了条件に「実機で ACL が付くこと」を入れる）。

## 3. `Update-TodoApp.ps1`（US-022）

```
.\Update-TodoApp.ps1 -Package <ZIP のパス>
```

```
 1. 前提確認（管理者・ZIP の SHA-256・install.completed）。新版が現在の版と同じなら中止
 2. data\daily.lock を FileShare.None で開けるまで 5 秒間隔で待つ（最大 30 分。日次処理の完了待ち）。開けたら保持したまま次へ
      30 分で開けなければ「日次処理が終わりません。ログを確認してください」で中止（何も変更していない）
 3. サービス停止（最大 60 秒待つ）
 4. TodoApp.exe（現行版）backup --to <share>\pre-update --no-rotate --kind pre-update
      共有フォルダに届かなければ data\pre-update\ へ出力し、その場所を表示（更新は続けてよいか y/N を確認）
 5. 新版を releases\<新版>\ へ展開
 6. 新版の exe で migrate --plan
      3（未適用なし）→ 8 へ
      0 → SQL を表示し「このスキーマ変更を適用しますか (y/N)」
          N → 旧版のまま起動して「更新を中止しました」で終了（US-022）
          y → migrate --apply
               0 → スキーマ変更あり として 8 へ
               4 → 旧版で起動し「スキーマ変更に失敗し、変更は取り消されました」で終了
               5/1 → 起動せず、更新前バックアップの場所と Restore-TodoApp.ps1 の使い方を表示して終了
 8. current を新版へ切り替え、daily.lock を閉じ、サービス起動 → /healthz を最大 60 秒待つ
 9. 失敗時:
      スキーマ変更なし → current を旧版へ戻して起動し、「新版が起動しなかったため旧版に戻しました」（Q-09-7）
      スキーマ変更あり → 自動で戻さない。停止したまま、更新前バックアップの場所と戻し方を表示（US-022）
10. 成功 → releases のうち current と直前の版以外を削除。pre-update の 5 件超を削除
```

- 計画停止の目安（手順 3〜8）は数分で、30 分以内（NFR-003）。
- 旧版での起動（手順 6 の N・4）は `current` を変えずにサービスを起動するだけ。

## 4. `Restore-TodoApp.ps1`（US-020）

```
.\Restore-TodoApp.ps1 [-From <フォルダ>]   … 既定は SharePath。pre-update も選べる
```

```
 1. 前提確認（管理者・install.completed）
 2. list-backups で番号付き一覧 → 番号を入力
 3. 「現在のデータは選んだ時点（YYYY/MM/DD HH:mm）の内容で置き換わります。よろしいですか (y/N)」→ y 以外は中止
 4. サービス停止
 5. restore --from <file>
      30（復号失敗）→ 「鍵の控え（Base64）を入力してください」→ restore --from <file> --key-stdin で再試行
                       再試行も 30 → 「鍵が違うか、ファイルが壊れています」で中止（現在のデータは変えない）。サービスを元どおり起動
      31 → 「バックアップが壊れています」で中止（同上）
      32 → 「このバックアップは新しい版で作られています。先に更新スクリプトで同じ版にしてください」で中止
 6. restore が行うこと（CLI 内部）:
      復号 → data\tmp\restore-<ts>.db → PRAGMA integrity_check = 'ok' を確認 → schema_version を確認
      → 現在の todo.db を todo.db.before-restore-<ts>、todo.db-wal・todo.db-shm があれば todo.db.before-restore-<ts>-wal・-shm に改名
        （サービス停止後なので通常は WAL が既に取り込まれ、-wal は無いか空）→ 一時ファイルを todo.db に移動 → PRAGMA journal_mode=WAL を実行
      → --key-stdin で成功した場合は、その鍵を backup.key として保存（控えの鍵をそのまま使い続けるため）
 7. 戻した DB の版が古ければ migrate --plan → SQL を表示し承認 → migrate --apply（拒否なら起動せずに理由と戻し方を表示）
 8. サービス起動 → /healthz → 「復旧しました。元のデータは todo.db.before-restore-<ts> にあります」
```

- 機材喪失時: 新サーバーで `Install-TodoApp.ps1 -Recover`（控えの鍵を取り込み、サービスは起動しない。§2）→ `Restore-TodoApp.ps1`（共有フォルダの最新世代を選ぶ）で元の最新バックアップに戻る。Restore の手順 4 のサービス停止は「既に停止」なら何もしない。

## 5. `New-TodoAppCertificate.ps1`（人が実行する補助。environment.md）

```
.\New-TodoAppCertificate.ps1 -HostName todo.example.local -IpAddress 192.168.1.10 [-RootPfx .\todo-root-ca.pfx]
```

- `-RootPfx` が無ければルート CA を作る: `New-SelfSignedCertificate -Type Custom -KeyUsage CertSign,CRLSign -TextExtension @("2.5.29.19={critical}{text}ca=1&pathlength=0") -NotAfter (10 年後) -Subject "CN=TodoApp Root CA"`。`todo-root-ca.pfx`（パスワード付き。サーバーに残さず保管）と `todo-root-ca.cer`（端末へ配る）を出力。
- サーバー証明書: `-Signer <ルート> -DnsName <HostName> -TextExtension @("2.5.29.17={text}DNS=<HostName>&IPAddress=<IP>","2.5.29.37={text}1.3.6.1.5.5.7.3.1") -NotAfter (397 日後)` → `server.pfx`。
- 証明書の更新（397 日ごと）: 同じスクリプトを `-RootPfx` 付きで実行してサーバー証明書だけを作り直す → `certs\server.pfx` を差し替える → `TodoApp.exe set-cert-password`（新しい PFX のパスワード）→ `Restart-Service TodoApp`。端末の信頼設定はやり直さない（ルート CA が同じため）。この手順はリリース文書（`docs/05-release/`）にも載せる。
- 最後に有効期限の日付を表示し、`docs/05-release/` に記録するよう案内する（environment.md）。iOS は「証明書信頼設定」で手動で有効化が必要な旨を表示する。

## 6. 配布物の構成（ZIP）

```
TodoApp-<版>-win-x64.zip
  TodoApp.exe ほか self-contained 一式・appsettings.json・migrations\・wwwroot\
  scripts\Install-TodoApp.ps1・Update-TodoApp.ps1・Restore-TodoApp.ps1・New-TodoAppCertificate.ps1
  version.txt
TodoApp-<版>-win-x64.zip.sha256   … Release に別ファイルとして添付
```

スクリプトは UTF-8（BOM 付き）で保存する（PowerShell 5.1 が日本語を正しく読むため）。

## 7. CI（`.github/workflows/ci.yml`。実装フェーズの最初のタスクで作る）

| ジョブ | 契機 | ランナー | 内容 |
|---|---|---|---|
| build-test | push・pull_request | windows-latest（2026-09-27 変更。Windows 前提のテスト（他プロセスの todo.db オープン検出・DPAPI）が ubuntu では動かないため。A-024 の消費最小化は secrets を ubuntu に残すことで図る） | `dotnet build -warnaserror` → `dotnet test tests/TodoApp.Tests`（E2E は除外） |
| secrets | push・pull_request | ubuntu-latest | gitleaks（版を固定した Action。A-017 の代替） |
| package | タグ `v*` | windows-latest | `dotnet publish -c Release -r win-x64 --self-contained true` → Pester（`tests/scripts`）→ ZIP と SHA-256 → Release に添付 |

- E2E（Playwright）は開発機で実行する（Actions の消費を抑える。A-024）。
- 使う Action は版（コミットハッシュ）を固定する。

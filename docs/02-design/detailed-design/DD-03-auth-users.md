# DD-03 認証・パスワード・利用者管理

方針は [ADR 0003](../adr/0003-auth-cookie-pbkdf2.md)。

## 1. 認証 Cookie とセッション

- スキーム: Cookie 認証。Cookie 名 `TodoApp.Auth`、`HttpOnly`・`Secure`・`SameSite=Strict`、`ExpireTimeSpan = 8 時間`、`SlidingExpiration = true`。Cookie の有効期限は付けない（ブラウザのセッション Cookie）。
- 標準の `SlidingExpiration` は残り時間が半分を切ったときしか更新しないため、`OnValidatePrincipal` で**認証済みの要求のたびに** `context.ShouldRenew = true` にする（「最後の操作から 8 時間」を正確に守る。US-001）。ただし `/healthz`・静的ファイル・`/api/markdown/preview` は更新しない（操作に数えない）。
- Claims: `sub`（users.id）・`name`（表示名）・`role`・`stamp`（security_stamp）・`mcp`（must_change_password）。
- `OnValidatePrincipal`: 毎リクエストで users を読み、`is_active = 0` または `stamp` 不一致なら `RejectPrincipal()`＋サインアウト（無効化・ロール変更・パスワード再設定の即時反映）。ロール・表示名・`must_change_password` は DB の値で Claims を差し替える。
- 未ログインで要ログインの URL を開いたら `/login?returnUrl=<元の相対パス>` へ 302（API は 401 `E-AUTH-REQUIRED`）。`returnUrl` は `Url.IsLocalUrl` を満たすものだけ使う（オープンリダイレクト対策）。
- 8 時間無操作後の次の操作でログイン画面へ（US-001）。API の場合は 401 を返し、JS はログイン画面へ遷移させる。

## 2. ログイン（US-001）

入力: `loginId`（1〜50 文字）、`password`（1〜128 文字）。

```
AuthService.LoginAsync(loginId, password, remoteIp) -> LoginResult
 1. user = users WHERE login_id = loginId（NOCASE）
 2. user が無い → ダミーのハッシュで VerifyHashedPassword を 1 回実行（処理時間をそろえる）→ E-AUTH-FAILED
 3. locked_until > now → E-AUTH-LOCKED（パスワードの正否を判定しない）
 4. 検証失敗 → failed_count += 1。failed_count >= MaxFailedAttempts なら locked_until = now + 15 分、failed_count = 0 → E-AUTH-FAILED（5 回目の失敗の時点でロック。6 回目から E-AUTH-LOCKED）
 5. is_active = 0 → E-AUTH-FAILED（無効化の有無も漏らさない）
 6. 成功 → failed_count = 0、locked_until = NULL。検証結果が SuccessRehashNeeded ならハッシュを更新
 7. サインイン（Cookie を新規発行＝セッション固定対策）。must_change_password = 1 なら /account/password へ、でなければ returnUrl か / へ
```

- 失敗時の文言は ID とパスワードのどちらが誤りかを区別しない。ロック時の文言も含め DD-12 を正とする。
- ログアウト `POST /logout`: `LockService.ReleaseAllOf(userId)`（ロックだけ消し下書きは残す。US-010）→ サインアウト → `/login`。

## 3. パスワード変更（US-002）

- 強制転送: `must_change_password = 1` の利用者は、`/account/password`・`/logout`・`/error`（例外ハンドラの再実行先。転送すると想定外例外が 500 画面にならない）・静的ファイル以外への要求をすべて `/account/password` へ 302（API は 403 `E-AUTH-MUST-CHANGE`）。
- 入力: `currentPassword`・`newPassword`・`newPasswordConfirm`。
- 検証（順に評価し最初の違反を返す）:
  1. 現在のパスワードが正しい（誤り → `E-PWD-CURRENT`）
  2. 新しいパスワードが 8 文字以上 128 文字以下（`E-PWD-LENGTH`。8 文字ちょうどは可）
  3. 確認欄と一致（`E-PWD-CONFIRM`）
  4. 新しいパスワードが現在のパスワードと同じでない（`E-PWD-SAME`。初回変更時は「初期パスワードと同じ」に当たる）
- 成功: ハッシュ更新・`must_change_password = 0`・`security_stamp` を新しくし、同じ要求内で Cookie を再発行（本人はログインのまま、他の端末のセッションは失効）→ `/` へ。
- ハッシュは `PasswordHasher<UserRecord>`（.NET 10 の既定: PBKDF2-HMAC-SHA512・10 万回以上）。

## 4. 利用者管理（US-003。`/admin/users`、管理者のみ）

| 操作 | 入力 | 規則 | エラー |
|---|---|---|---|
| 登録 | ログイン ID・表示名・ロール・初期パスワード | ログイン ID は英数字と `._-`（`^[A-Za-z0-9._-]{1,50}$`）。初期パスワードは §3 の長さ規則。`must_change_password=1`、`security_stamp` は新しい GUID | `E-USER-DUPLICATE`・`E-USER-LOGINID-FORMAT`・`E-PWD-LENGTH`・`E-VALIDATION` |
| 変更 | 表示名・ロール | ロールを member にするとき、他に有効な管理者がいなければ拒否。変更したら `security_stamp` 更新 | `E-USER-LAST-ADMIN` |
| 無効化 / 有効化 | ― | 無効化するとき、対象が有効な管理者で他に有効な管理者がいなければ拒否。無効化で `security_stamp` 更新、その利用者のロックを解放（下書きは残す） | `E-USER-LAST-ADMIN` |
| パスワード再設定 | 新しい初期パスワード | `must_change_password=1`・`failed_count=0`・`locked_until=NULL`・`security_stamp` 更新 | `E-PWD-LENGTH` |

- 「他に有効な管理者がいない」の判定: `SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1 AND id <> :target` が 0。書き込みトランザクション内で数える。
- 利用者は削除しない（履歴・作成者の参照を保つ）。
- 無効化された担当者の表示: 表示名の前に「（無効）」を付ける（一覧・詳細・カレンダー・CSV・履歴以外のすべて。履歴はスナップショットのまま）。担当者の選択肢には有効な利用者だけを出し、現在の担当者が無効なら「（無効）表示名」を選択済みの 1 項目として残す。
- 管理画面の認可: `/admin/*` は `RequireRole("admin")` のポリシー。一般利用者のアクセスは 403 画面（`E-PERM-ADMIN-ONLY`）。

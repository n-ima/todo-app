# DD-04 タスク操作・権限・ゴミ箱・プロジェクト

## 1. 権限判定 `TaskPermission`（requirements.md §5.0・FR-001）

```csharp
public sealed class TaskPermission {
    // 自分が担当 / 担当者未設定 / 管理者
    public bool CanEdit(CurrentUser u, TaskRow t) => u.IsAdmin || t.AssigneeId is null || t.AssigneeId == u.Id;
    // 移動・削除: 対象を編集でき、かつ（一般利用者は）配下の全タスクも編集できる（A-013）
    public IReadOnlyList<TaskRow> SubtreeBlockers(CurrentUser u, IEnumerable<TaskRow> subtree)
        => u.IsAdmin ? [] : subtree.Where(t => !CanEdit(u, t)).ToList();
    public void EnsureEdit(CurrentUser u, TaskRow t) { if (!CanEdit(u, t)) throw new AppErrorException(ErrorIds.PermDenied); }
    public bool CanRestore(CurrentUser u, int deletedBy) => u.IsAdmin || deletedBy == u.Id;
}
```

| 操作 | 判定 |
|---|---|
| 閲覧 | ログイン済みなら全件 |
| 作成（任意の親の下） | ログイン済み（A-002）。親が有効であること |
| 保存・ロック取得・自動保存・上へ/下へ | `CanEdit`（保存前の値で判定。担当者を他人に変えて保存するのは可） |
| 移動・削除 | `CanEdit(対象)` かつ `SubtreeBlockers` が空。移動先の親の権限は問わない（US-007） |
| 復元 | `CanRestore` |
| 強制解除・プロジェクト管理・利用者管理・状態定義 | `IsAdmin` |

画面は同じ `TaskPermission` の結果でボタンを無効化し、`title` 属性と横の注記に FR-001 の文言を出す（DD-11）。

## 2. 入力の検証（作成・保存・自動保存で共通）

| 項目 | 規則 | エラー |
|---|---|---|
| タイトル | 前後の空白を除いて 1〜200 文字。改行を含まない | 空 → `E-TASK-TITLE-REQUIRED`、長すぎ → `E-TASK-TITLE-LENGTH` |
| 状態 | 存在する `statuses.id` | `E-VALIDATION` |
| 担当者 | NULL、または有効な利用者。無効な利用者は「現在の値のまま」のときだけ許す | `E-TASK-ASSIGNEE-INACTIVE` |
| 開始日・期限 | NULL または `yyyy-MM-dd` の実在日 | `E-VALIDATION` |
| 期限と開始日 | 両方あるとき `start_date <= due_date`（同日は可） | `E-TASK-DATE-ORDER`（FR-002） |
| 優先度 | 1（高）・2（中）・3（低） | `E-VALIDATION` |
| メモ | 100,000 文字以下。改行を `\n` に正規化したうえで `string.Length`（UTF-16 コード単位）で数える（ブラウザの `maxlength` と同じ数え方にそろえるため） | `E-MEMO-TOO-LONG`（FR-005） |

`title_norm` は保存のたびに `NormalizeForSearch(title)`＝`title.Normalize(NormalizationForm.FormKC).ToLowerInvariant()` で作り直す（DD-06 §5）。

## 3. 作成（US-004）

- `POST /projects/{projectId}/tasks`・`POST /tasks/{parentId}/children`（フォーム。入力: タイトル・担当者・開始日・期限・優先度）。
- 手順（1 トランザクション）:
  1. 親（プロジェクト、または親タスクとその所属プロジェクト）が有効であること（ゴミ箱・削除済み → `E-TASK-PARENT-GONE`）。
  2. §2 の検証。
  3. `status_id` = `statuses` の `sort_order` 最小の行、`priority` 既定 2、`sort_order` = 同じ親の兄弟の最大 +1（兄弟なしなら 1）、`project_id` = 親の所属、`created_by/updated_by` = 本人、`created_at/updated_at` = now。
  4. 担当者が作成者以外なら割り当て通知（DD-08 §2）。
- 成功したら作成したタスクの詳細画面へ 302。段数の上限は設けない（深さの検査をしない）。

## 4. 保存（US-006・FR-003・FR-007）

`POST /tasks/{id}/save`（詳細画面の JS から JSON で送る。本文: §2 の全項目＋`confirmChildren: bool`）。

```
TaskService.SaveAsync(user, id, input) （BEGIN IMMEDIATE）
 1. task = 有効なタスク（無ければ E-TASK-NOT-FOUND）
 2. 有効なロックが本人のものでなければ、input を本人の下書きとして UPSERT し、コミットして SaveResult.LockLost を返す（→ 409 E-LOCK-LOST。DD-01 §5）
    権限の判定より先に行う（強制解除の後に担当者が付け替えられていても、入力内容を本人の下書きに残すため。下書きは本人にしか見えないので権限上の問題は無い）
 3. EnsureEdit(user, task)
 4. §2 の検証
 5. 状態が「完了扱いでない → 完了扱い」に変わり、かつ有効な子孫に完了扱いでないものが n 件（全段を数える）あり、confirmChildren = false
      → E-TASK-CHILDREN-OPEN（data: { count: n }）。画面は確認ダイアログを出し、了承なら confirmChildren = true で再送
 6. UPDATE tasks（全項目・title_norm・updated_by/at）
 7. 状態が変わったら task_history(kind='status', before=旧状態名, after=新状態名)
    担当者が変わったら task_history(kind='assignee', before=旧表示名 or NULL, after=新表示名 or NULL)
 8. 新しい担当者が NULL でなく、変更があり、actor と異なれば割り当て通知（DD-08 §2）
 9. task_locks と本人の drafts を削除
 → 200 { redirect: "/tasks/{id}" }
```

- 親の状態・期限を子から計算する処理は持たない（FR-003）。状態は定義済みのどれへでも変えられ、遷移の検査をしない（FR-007）。

## 5. 移動（US-007）

`POST /tasks/{id}/move`（本文: `destProjectId`・`destParentTaskId`（NULL ならプロジェクト直下））。`destParentTaskId` があるときは移動先のプロジェクトを親の `project_id` から導出し、`destProjectId` と食い違えば `E-VALIDATION`。移動先はダイアログのツリーから選ぶ（DD-11）。

```
 1. task と有効な子孫集合 S（自分を含む。DD-02 §3 の再帰 CTE）
 2. EnsureEdit(user, task)。SubtreeBlockers(user, S) が空でなければ E-PERM-SUBTREE（data: 編集できないタスクのタイトル一覧）
 3. destParentTaskId ∈ S → E-TASK-CYCLE
 4. 移動先の親（プロジェクト・親タスク）が有効 → でなければ E-TASK-PARENT-GONE
 5. S の中に他人の有効なロックがある → E-LOCK-HELD-SUBTREE（data: 保持者名とタスク名）
 6. 現在と同じ親なら何もしない（200）
 7. task.parent_task_id = destParentTaskId、task.sort_order = 移動先の兄弟の最大 +1、task.updated_by/at を更新
    プロジェクトが変わる場合は、削除済みを含む全子孫（`deleted_at` で絞らない再帰 CTE。DD-02 §3 の「全子孫」）の project_id を移動先のプロジェクトに更新（ゴミ箱にある子孫も復元時に元の親の下へ戻るため）
```

## 6. 上へ・下へ（US-007・A-004）

`POST /tasks/{id}/reorder`（本文: `direction` = `up` | `down`）。`CanEdit(対象)` で判定。同じ親の有効な兄弟を `sort_order, id` の順に並べ、隣と `sort_order` を入れ替える。端なら何もしない（200）。内容の変更ではないため、ロックの有無は問わず、`updated_at` も変えない。一覧の並び順が「手動順」のときだけボタンを出す（DD-06）。

## 7. 削除・ゴミ箱・復元（US-008・FR-004）

### 削除 `POST /tasks/{id}/delete`

```
 1. task と有効な子孫集合 S
 2. EnsureEdit(user, task)、SubtreeBlockers → E-PERM-SUBTREE
 3. S に他人の有効なロック → E-LOCK-HELD-SUBTREE
 4. S の全行に deleted_at = now、deleted_by = user、trash_root_id = task.id
 5. S の本人のロックを削除（下書きは残す）
 → 200 { redirect: "/" }
```

既にゴミ箱にある子孫（先に単独で削除されたもの）は S に含まれず、元の `trash_root_id` のまま残る。

### ゴミ箱 `GET /trash`

- 行 = 根（`deleted_at IS NOT NULL AND trash_root_id = id`）。一般利用者は `deleted_by = 自分` のものだけ、管理者は全件。管理者には削除されたプロジェクトも同じ表に「プロジェクト」として出す。
- 列: タイトル・元の場所（プロジェクト名 > 親タスク名）・含まれる子タスク数・削除した人・削除日・完全削除予定日（削除日＋30 日の翌日）・「元に戻す」。
- 削除されたプロジェクト配下のタスクの根は、プロジェクトを戻すまで出さない（戻す操作が成立しないため）。

### 復元 `POST /trash/{rootId}/restore`

```
 1. root = tasks WHERE id = rootId AND trash_root_id = rootId（無ければ E-TRASH-NOT-FOUND＝完全削除済み）
 2. CanRestore(user, root.deleted_by) でなければ E-PERM-DENIED
 3. root.parent_task_id が NULL でなく、その親が deleted_at IS NOT NULL → E-TRASH-PARENT-MISSING
    所属プロジェクトが削除済み → E-TRASH-PARENT-MISSING
 4. trash_root_id = rootId の全行の deleted_at / deleted_by / trash_root_id を NULL に戻す
 5. root.sort_order = 兄弟の最大 +1（元の位置は保証しない）
```

### 完全削除（日次処理の手順 (3)。DD-09）

- 対象: 根のうち `Today >= 削除日（日本時間の暦日）+ 31 日` のもの（削除日を 0 日目として 30 日目までは戻せる）。境界の UTC 時刻 `cutoff` = （Today − 30 日）の日本時間 0:00 を UTC にしたもの。
- 根を `deleted_at` の古い順に処理する（単独で先に削除された子孫は親より古い削除日時を持つので、必ず先に消える）。1 根 = 1 トランザクションで `DELETE FROM tasks WHERE trash_root_id = :root AND deleted_at < :cutoff`（付随データは CASCADE。対象の列挙後に「復元→再削除」が挟まっても、新しい削除日時の行は消さない）。外部キー違反になった根はスキップしてログに残し、翌日に再試行する。
- 削除されたプロジェクト（`projects.deleted_at < cutoff`）: 1 トランザクションで、そのプロジェクトの全タスク（ゴミ箱のものを含む）を深い順（子から親。再帰 CTE で深さを求めて降順）に `DELETE` → `DELETE FROM projects WHERE id = :id`。
- 利用者向けの完全削除 API・「ゴミ箱を空にする」は作らない（FR-004）。

## 8. プロジェクト（US-004・US-008。管理者のみ）

| 操作 | 画面 | 規則 |
|---|---|---|
| 作成 | 一覧の「＋プロジェクト」・`/admin/projects` | 名前 1〜100 文字（前後の空白を除く。違反は `E-PROJECT-NAME`）。`sort_order` = 最大 +1。重複名は許す |
| 名前変更 | `/admin/projects` | 同上 |
| 削除 | `/admin/projects`（確認ダイアログ） | 配下に他人の有効なロックがあれば `E-LOCK-HELD-SUBTREE`。`projects.deleted_at/deleted_by` を付ける。配下のタスクの行は変えない |
| 復元 | `/trash` | 管理者のみ |

一覧でのプロジェクトの並びは `sort_order`（作成順）。

# DD-08 ワークフロー（状態）・通知・変更履歴

## 1. 状態の定義（US-015・FR-007）

`/admin/workflow`（管理者のみ）。`WorkflowService` の操作はすべて 1 トランザクション。

| 操作 | 規則 | エラー |
|---|---|---|
| 追加 | 名前 1〜20 文字（前後の空白を除く）・重複不可。末尾（`sort_order` 最大 +1）に追加。完了扱いは既定オフ | `E-WF-NAME-DUPLICATE`・`E-VALIDATION` |
| 名前変更 | 同上 | 同上 |
| 上へ・下へ | 隣と `sort_order` を入れ替え | ― |
| 完了扱いの付け外し | 外した結果、完了扱いが 0 件になるなら拒否 | `E-WF-NEED-DONE` |
| 削除 | 状態が 1 つだけなら拒否。使っているタスク（ゴミ箱を含む全行）が n 件あれば拒否。削除すると完了扱いが 0 件になるなら拒否 | `E-WF-LAST-STATUS`・`E-WF-IN-USE`（data: n）・`E-WF-NEED-DONE` |

- ゴミ箱のタスクも数える（復元したときに状態が無くならないため。文言の n にはゴミ箱の件数も含むと注記する）。
- 定義はアプリ全体で 1 つ。変更は次の画面表示から全画面の選択肢・表示順・完了扱いの判定（一覧の非表示・期限の強調・親の完了警告）に反映される（キャッシュしない）。
- 新規タスクの初期状態は `sort_order` が最小の状態（US-004）。
- 定義画面が未実装の段階でも、初期データの 3 状態で動く（US-015 の注記）。
- 遷移の順序制約は持たない（FR-007）。

## 2. 通知（US-016・FR-008）

### 割り当て通知

- 契機: 作成（DD-04 §3）または保存（DD-04 §4）で、担当者が NULL 以外の値 A に**変わった**とき。A = 操作者なら作らない。
- 行: `kind='assigned'`・`user_id=A`・`message="{操作者の表示名}さんが『{タイトル}』の担当をあなたにしました"`・`due_date_for=NULL`。同じトランザクションで作る。

### 期限の通知（日次処理の手順 (4)。A-020 (a)）

```sql
INSERT OR IGNORE INTO notifications (user_id, task_id, kind, message, due_date_for, created_at)
SELECT t.assignee_id, t.id, 'due',
       CASE WHEN t.due_date = :tomorrow THEN '『' || t.title || '』の期限は明日です'
            ELSE '『' || t.title || '』の期限は今日です' END,
       t.due_date, :now
FROM tasks t
JOIN projects p ON p.id = t.project_id
JOIN statuses s ON s.id = t.status_id
JOIN users u ON u.id = t.assignee_id
WHERE t.deleted_at IS NULL AND p.deleted_at IS NULL
  AND s.is_done = 0 AND u.is_active = 1
  AND t.due_date IN (:today, :tomorrow);
```

- 一意索引 `ux_notifications_due(user_id, task_id, due_date_for)` により、前日に「明日です」を出したタスクに当日「今日です」は出ない。期限を変えると `due_date_for` が変わるので新しい期限で再び出る。
- 期限が過去・担当者なし・完了扱い・無効な利用者のタスクには出ない。保存の瞬間には作らない（次の日次処理で作る）。

### 表示と既読

- ヘッダーの未読件数: レイアウトの描画ごとに `SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL`。1 件以上ならベルに件数バッジ（99 超は「99+」）。
- `GET /notifications`: 新しい順に最大 200 件（未読は太字）。通知は削除しない（件数が多い場合も 200 件だけ表示）。
- `POST /notifications/{id}/open`（本人の通知のみ。他人の ID は 404）: `read_at` を付ける → 対象タスクが有効なら `/tasks/{taskId}` へ 302。ゴミ箱・削除済みプロジェクト配下なら `/notifications` へ戻して「このタスクは削除されています」を出す（完全削除済みの通知は CASCADE で既に消えている）。
- 「すべて既読にする」ボタン（`POST /notifications/read-all`）を置く。
- 外部への送信（メール・チャット）は実装しない（FR-008）。

## 3. 変更履歴（US-017・US-011）

- 記録: DD-04 §4（`status`・`assignee`）と DD-05 §6（`lock_force_released`）の 3 種類だけ。タイトル・期限・メモ等は記録しない。
- 表示: 詳細画面の「変更履歴」セクション（折りたたみ）。`at` の新しい順に「日時・操作者・内容」。

| kind | 表示 |
|---|---|
| status | 「状態: 未着手 → 進行中」 |
| assignee | 「担当者: （なし） → 山田 花子」 |
| lock_force_released | 「ロックを強制解除（編集中だった人: 山田 花子）」 |

- 0 件なら「変更履歴はありません」。操作者名は現在の表示名（無効なら「（無効）」付き）。変更前・変更後の値は記録時のスナップショット。

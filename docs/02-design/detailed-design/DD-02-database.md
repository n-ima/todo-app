# DD-02 データベース

SQLite（WAL）。方針は [ADR 0002](../adr/0002-sqlite-plain-sql-migrations.md)。ここに初版の DDL 全文を置き、`src/TodoApp/migrations/0001_init.sql` はこれと同一にする。

## 1. マイグレーションの規則

- ファイル名 `NNNN_<英小文字_区切り>.sql`（4 桁連番）。**一度リリースしたファイルは変更しない**（変更は新しい番号で足す）。
- `Migrator` は `schema_version` の最大値より大きいファイルを番号順に、**1 ファイル = 1 トランザクション**で適用し、成功したら同じトランザクションで `schema_version` に行を足す。
- アプリが期待する版 `ExpectedSchemaVersion` は、配布物に含まれる最大番号。起動時に DB の版と一致しなければ `E-SYS-SCHEMA-MISMATCH` でイベントログに書いて終了する（自動適用しない）。
- `PRAGMA foreign_keys` はトランザクション内で変えられないため、テーブル再作成を伴う変更では、ファイル先頭のコメント `-- requires: foreign_keys=off` を `Migrator` が読み、トランザクションの外で OFF にしてから適用し、最後に `PRAGMA foreign_key_check` が空であることを確かめてから ON に戻す（空でなければ失敗としてロールバック）。
- 初期データ（状態 3 件）は `0001_init.sql` に含める（`migrate --init` と同じ経路にする）。

## 2. DDL（0001_init.sql）

```sql
CREATE TABLE schema_version (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE users (
  id                   INTEGER PRIMARY KEY,
  login_id             TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (length(login_id) BETWEEN 1 AND 50),
  display_name         TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 50),
  role                 TEXT NOT NULL CHECK (role IN ('admin','member')),
  password_hash        TEXT NOT NULL,
  must_change_password INTEGER NOT NULL DEFAULT 1 CHECK (must_change_password IN (0,1)),
  is_active            INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  failed_count         INTEGER NOT NULL DEFAULT 0,
  locked_until         TEXT NULL,
  security_stamp       TEXT NOT NULL,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);

CREATE TABLE projects (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  sort_order INTEGER NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT NULL,
  deleted_by INTEGER NULL REFERENCES users(id)
);

CREATE TABLE statuses (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 20),
  sort_order INTEGER NOT NULL,
  is_done    INTEGER NOT NULL DEFAULT 0 CHECK (is_done IN (0,1))
);

CREATE TABLE tasks (
  id             INTEGER PRIMARY KEY,
  project_id     INTEGER NOT NULL REFERENCES projects(id),
  parent_task_id INTEGER NULL REFERENCES tasks(id),
  title          TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  title_norm     TEXT NOT NULL,
  status_id      INTEGER NOT NULL REFERENCES statuses(id),
  assignee_id    INTEGER NULL REFERENCES users(id),
  start_date     TEXT NULL,
  due_date       TEXT NULL,
  priority       INTEGER NOT NULL DEFAULT 2 CHECK (priority IN (1,2,3)),
  memo           TEXT NOT NULL DEFAULT '',
  sort_order     INTEGER NOT NULL,
  created_by     INTEGER NOT NULL REFERENCES users(id),
  created_at     TEXT NOT NULL,
  updated_by     INTEGER NOT NULL REFERENCES users(id),
  updated_at     TEXT NOT NULL,
  deleted_at     TEXT NULL,
  deleted_by     INTEGER NULL REFERENCES users(id),
  trash_root_id  INTEGER NULL,
  CHECK (start_date IS NULL OR due_date IS NULL OR start_date <= due_date)
);
CREATE INDEX ix_tasks_parent   ON tasks(parent_task_id);
CREATE INDEX ix_tasks_project  ON tasks(project_id);
CREATE INDEX ix_tasks_assignee ON tasks(assignee_id);
CREATE INDEX ix_tasks_due      ON tasks(due_date);
CREATE INDEX ix_tasks_deleted  ON tasks(deleted_at);
CREATE INDEX ix_tasks_trash    ON tasks(trash_root_id);

CREATE TABLE task_locks (
  task_id          INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
  user_id          INTEGER NOT NULL REFERENCES users(id),
  acquired_at      TEXT NOT NULL,
  last_activity_at TEXT NOT NULL
);

CREATE TABLE drafts (
  task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  user_id         INTEGER NOT NULL REFERENCES users(id),
  payload_json    TEXT NOT NULL,
  base_updated_at TEXT NOT NULL,
  saved_at        TEXT NOT NULL,
  PRIMARY KEY (task_id, user_id)
);

CREATE TABLE task_history (
  id           INTEGER PRIMARY KEY,
  task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('status','assignee','lock_force_released')),
  actor_id     INTEGER NOT NULL REFERENCES users(id),
  before_value TEXT NULL,
  after_value  TEXT NULL,
  at           TEXT NOT NULL
);
CREATE INDEX ix_history_task ON task_history(task_id, at);

CREATE TABLE notifications (
  id           INTEGER PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users(id),
  task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('assigned','due')),
  message      TEXT NOT NULL,
  due_date_for TEXT NULL,
  created_at   TEXT NOT NULL,
  read_at      TEXT NULL
);
CREATE INDEX ix_notifications_user ON notifications(user_id, read_at);
CREATE UNIQUE INDEX ux_notifications_due ON notifications(user_id, task_id, due_date_for) WHERE kind = 'due';

CREATE TABLE daily_runs (
  run_date     TEXT PRIMARY KEY,
  completed_at TEXT NOT NULL
);

CREATE TABLE backup_runs (
  id          INTEGER PRIMARY KEY,
  run_date    TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'daily' CHECK (kind IN ('daily','pre-update','manual')),
  destination TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT NULL,
  ok          INTEGER NULL CHECK (ok IN (0,1)),
  error_id    TEXT NULL,
  file_name   TEXT NULL
);
CREATE INDEX ix_backup_runs_date ON backup_runs(kind, run_date);

INSERT INTO statuses (name, sort_order, is_done) VALUES
  ('未着手', 1, 0), ('進行中', 2, 0), ('完了', 3, 1);
```

補足:

- `tasks.memo` の長さ上限（100,000 文字）は DB の CHECK に入れず、サービスで検証する（設定値で変えられるようにするため。FR-005）。
- `task_history.before_value / after_value` は表示用の文字列スナップショット（状態名・担当者の表示名。`lock_force_released` は `before_value` に元のロック保持者の表示名、`after_value` は NULL）。後で状態名や表示名が変わっても当時の値を残す。
- `tasks.trash_root_id` は削除操作の根のタスク ID。有効なタスクでは NULL。
- プロジェクトの削除は `projects.deleted_at` だけを付け、配下のタスクの行は変えない（DD-04 §8）。
- `ON DELETE CASCADE` は完全削除（DD-09）で付随データ（ロック・下書き・履歴・通知）を一緒に消すためだけに使う。`tasks.parent_task_id` には付けない（誤って子孫を連鎖削除しないため）。

## 3. 主要な問い合わせ

| 用途 | SQL の要点 |
|---|---|
| 一覧の読み込み（DD-06） | `SELECT t.id, t.project_id, t.parent_task_id, t.title, t.title_norm, t.status_id, t.assignee_id, t.start_date, t.due_date, t.priority, t.sort_order, t.updated_at FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.deleted_at IS NULL AND p.deleted_at IS NULL`（メモを読まない） |
| 子孫の列挙（移動・削除・復元） | `WITH RECURSIVE d(id) AS (SELECT :id UNION ALL SELECT t.id FROM tasks t JOIN d ON t.parent_task_id = d.id WHERE t.deleted_at IS NULL) SELECT id FROM d` |
| 全子孫（削除済みを含む。移動時の project_id 更新・プロジェクトの完全削除） | 上の CTE から `WHERE t.deleted_at IS NULL` を外し、深さ列 `depth` を持たせる |
| 祖先の列挙 | 使わない（移動の循環検出は、移動先が子孫集合に含まれるかで判定） |
| 期限超過件数（DD-06 §6） | 一覧の読み込み結果からメモリ上で数える（別 SQL を投げない） |

## 4. 容量の見積もり（NFR-010）

15,000 タスク × メモ平均 2,000 文字（UTF-8 で約 6 KB）≒ 90 MB が上限の目安。バックアップの一時ファイルと暗号化後のファイルも同程度。サーバーの空き容量の前提として Install が `C:` の空き 1 GB 以上を確認する（DD-10）。

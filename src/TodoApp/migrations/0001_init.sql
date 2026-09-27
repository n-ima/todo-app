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

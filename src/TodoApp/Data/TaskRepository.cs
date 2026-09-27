using System.Data;
using Dapper;

namespace TodoApp.Data;

/// <summary>tasks の 1 行（権限判定・親の検査に使う列）。</summary>
public sealed class TaskRow
{
    public long Id { get; init; }
    public long ProjectId { get; init; }
    public long? ParentTaskId { get; init; }
    public string Title { get; init; } = "";
    public long? AssigneeId { get; init; }
}

/// <summary>tasks に書き込む値（DD-04 §3）。</summary>
public sealed record NewTaskRecord(
    long ProjectId,
    long? ParentTaskId,
    string Title,
    string TitleNorm,
    long StatusId,
    long? AssigneeId,
    string? StartDate,
    string? DueDate,
    int Priority,
    long CreatedBy,
    string Now);

/// <summary>担当者の選択肢。</summary>
public sealed class UserOption
{
    public long Id { get; init; }
    public string DisplayName { get; init; } = "";
}

/// <summary>一覧の 1 行（DD-02 §3 の一覧 SQL。メモは読まない）。</summary>
public sealed class TaskListRecord
{
    public long Id { get; init; }
    public long ProjectId { get; init; }
    public long? ParentTaskId { get; init; }
    public string Title { get; init; } = "";
    public string TitleNorm { get; init; } = "";
    public long StatusId { get; init; }
    public long? AssigneeId { get; init; }
    public string? StartDate { get; init; }
    public string? DueDate { get; init; }
    public int Priority { get; init; }
    public long SortOrder { get; init; }
    public string UpdatedAt { get; init; } = "";
}

/// <summary>担当者の表示に使う利用者（無効も含む）。</summary>
public sealed class UserNameRecord
{
    public long Id { get; init; }
    public string DisplayName { get; init; } = "";
    public bool IsActive { get; init; }
}

/// <summary>tasks と作成時に参照する表の SQL（パラメータ化のみ。DD-01 §4）。</summary>
public static class TaskRepository
{
    /// <summary>有効なタスク全件（DD-02 §3・DD-06 §2）。</summary>
    public static async Task<IReadOnlyList<TaskListRecord>> ListActiveForListAsync(IDbConnection connection, IDbTransaction transaction) =>
        (await connection.QueryAsync<TaskListRecord>(
            """
            SELECT t.id AS Id, t.project_id AS ProjectId, t.parent_task_id AS ParentTaskId, t.title AS Title, t.title_norm AS TitleNorm,
                   t.status_id AS StatusId, t.assignee_id AS AssigneeId, t.start_date AS StartDate, t.due_date AS DueDate,
                   t.priority AS Priority, t.sort_order AS SortOrder, t.updated_at AS UpdatedAt
            FROM tasks t JOIN projects p ON p.id = t.project_id
            WHERE t.deleted_at IS NULL AND p.deleted_at IS NULL
            """,
            transaction: transaction).ConfigureAwait(false)).AsList();

    /// <summary>（Could）メモ本文検索用に有効なタスクのメモを読む（DD-06 §5。一覧 SQL とは別に読む）。</summary>
    public static async Task<IReadOnlyList<(long Id, string Memo)>> ListActiveMemosAsync(IDbConnection connection, IDbTransaction transaction) =>
        (await connection.QueryAsync<(long, string)>(
            "SELECT id, memo FROM tasks WHERE deleted_at IS NULL",
            transaction: transaction).ConfigureAwait(false)).AsList();

    public static async Task<IReadOnlyList<UserNameRecord>> ListUserNamesAsync(IDbConnection connection, IDbTransaction transaction) =>
        (await connection.QueryAsync<UserNameRecord>(
            "SELECT id AS Id, display_name AS DisplayName, is_active AS IsActive FROM users",
            transaction: transaction).ConfigureAwait(false)).AsList();

    private const string TaskColumns =
        "t.id AS Id, t.project_id AS ProjectId, t.parent_task_id AS ParentTaskId, t.title AS Title, t.assignee_id AS AssigneeId";

    /// <summary>有効なタスク（ゴミ箱になく、所属プロジェクトも削除されていない）。</summary>
    public static Task<TaskRow?> FindActiveAsync(IDbConnection connection, IDbTransaction transaction, long id) =>
        connection.QuerySingleOrDefaultAsync<TaskRow?>(
            $"SELECT {TaskColumns} FROM tasks t JOIN projects p ON p.id = t.project_id " +
            "WHERE t.id = @id AND t.deleted_at IS NULL AND p.deleted_at IS NULL",
            new { id },
            transaction);

    public static Task<string?> FindActiveProjectNameAsync(IDbConnection connection, IDbTransaction transaction, long id) =>
        connection.QuerySingleOrDefaultAsync<string?>(
            "SELECT name FROM projects WHERE id = @id AND deleted_at IS NULL", new { id }, transaction);

    /// <summary>状態の初期値＝sort_order 最小の状態。</summary>
    public static Task<long> FirstStatusIdAsync(IDbConnection connection, IDbTransaction transaction) =>
        connection.ExecuteScalarAsync<long>("SELECT id FROM statuses ORDER BY sort_order, id LIMIT 1", transaction: transaction);

    /// <summary>担当者の有効/無効。存在しなければ null。</summary>
    public static Task<bool?> FindUserActiveAsync(IDbConnection connection, IDbTransaction transaction, long id) =>
        connection.QuerySingleOrDefaultAsync<bool?>("SELECT is_active FROM users WHERE id = @id", new { id }, transaction);

    public static async Task<IReadOnlyList<UserOption>> ListActiveUsersAsync(IDbConnection connection, IDbTransaction transaction) =>
        (await connection.QueryAsync<UserOption>(
            "SELECT id AS Id, display_name AS DisplayName FROM users WHERE is_active = 1 ORDER BY display_name, id",
            transaction: transaction).ConfigureAwait(false)).AsList();

    /// <summary>sort_order は同じ親の兄弟（ゴミ箱のものも含む）の最大 +1。兄弟なしなら 1。</summary>
    public static Task<long> InsertAsync(IDbConnection connection, IDbTransaction transaction, NewTaskRecord t) =>
        connection.ExecuteScalarAsync<long>(
            """
            INSERT INTO tasks (project_id, parent_task_id, title, title_norm, status_id, assignee_id, start_date, due_date, priority,
                               sort_order, created_by, created_at, updated_by, updated_at)
            SELECT @ProjectId, @ParentTaskId, @Title, @TitleNorm, @StatusId, @AssigneeId, @StartDate, @DueDate, @Priority,
                   COALESCE(MAX(sort_order), 0) + 1, @CreatedBy, @Now, @CreatedBy, @Now
            FROM tasks
            WHERE (@ParentTaskId IS NULL AND parent_task_id IS NULL AND project_id = @ProjectId)
               OR (@ParentTaskId IS NOT NULL AND parent_task_id = @ParentTaskId);
            SELECT last_insert_rowid();
            """,
            t,
            transaction);

    /// <summary>割り当て通知（DD-08 §2）。</summary>
    public static Task InsertAssignedNotificationAsync(
        IDbConnection connection, IDbTransaction transaction, long userId, long taskId, string message, string now) =>
        connection.ExecuteAsync(
            "INSERT INTO notifications (user_id, task_id, kind, message, due_date_for, created_at) VALUES (@userId, @taskId, 'assigned', @message, NULL, @now)",
            new { userId, taskId, message, now },
            transaction);
}

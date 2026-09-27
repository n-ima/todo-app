using System.Data;
using Dapper;

namespace TodoApp.Data;

/// <summary>projects の 1 行（DD-02）。</summary>
public sealed class ProjectRecord
{
    public long Id { get; init; }
    public string Name { get; init; } = "";
    public long SortOrder { get; init; }
}

/// <summary>他人が持つ有効なロック（E-LOCK-HELD-SUBTREE の文言用）。</summary>
public sealed class HeldLock
{
    public string HolderName { get; init; } = "";
    public string Title { get; init; } = "";
}

/// <summary>projects の SQL（パラメータ化のみ。DD-01 §4）。</summary>
public static class ProjectRepository
{
    /// <summary>削除済み（deleted_at あり）を除いて sort_order（作成順）で返す（DD-04 §8）。</summary>
    public static async Task<IReadOnlyList<ProjectRecord>> ListActiveAsync(IDbConnection connection, IDbTransaction transaction) =>
        (await connection.QueryAsync<ProjectRecord>(
            "SELECT id AS Id, name AS Name, sort_order AS SortOrder FROM projects WHERE deleted_at IS NULL ORDER BY sort_order, id",
            transaction: transaction).ConfigureAwait(false)).AsList();

    public static Task<long> CountActiveAsync(IDbConnection connection, IDbTransaction transaction, long id) =>
        connection.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM projects WHERE id = @id AND deleted_at IS NULL", new { id }, transaction);

    /// <summary>sort_order は削除済みも含めた最大 +1（復元時に並びが重ならないため）。</summary>
    public static Task<long> InsertAsync(IDbConnection connection, IDbTransaction transaction, string name, long createdBy, string now) =>
        connection.ExecuteScalarAsync<long>(
            "INSERT INTO projects (name, sort_order, created_by, created_at, updated_at) SELECT @name, COALESCE(MAX(sort_order), 0) + 1, @createdBy, @now, @now FROM projects; SELECT last_insert_rowid();",
            new { name, createdBy, now },
            transaction);

    public static Task RenameAsync(IDbConnection connection, IDbTransaction transaction, long id, string name, string now) =>
        connection.ExecuteAsync("UPDATE projects SET name = @name, updated_at = @now WHERE id = @id", new { id, name, now }, transaction);

    public static Task MarkDeletedAsync(IDbConnection connection, IDbTransaction transaction, long id, long deletedBy, string now) =>
        connection.ExecuteAsync("UPDATE projects SET deleted_at = @now, deleted_by = @deletedBy WHERE id = @id", new { id, deletedBy, now }, transaction);

    /// <summary>配下のタスクに付いた他人の有効なロック（last_activity_at が境界より後。DD-05 の定義）を 1 件返す。</summary>
    public static Task<HeldLock?> FindOthersLockAsync(IDbConnection connection, IDbTransaction transaction, long projectId, long actorId, string validAfter) =>
        connection.QueryFirstOrDefaultAsync<HeldLock?>(
            """
            SELECT u.display_name AS HolderName, t.title AS Title
            FROM task_locks l JOIN tasks t ON t.id = l.task_id JOIN users u ON u.id = l.user_id
            WHERE t.project_id = @projectId AND l.user_id <> @actorId AND l.last_activity_at > @validAfter
            ORDER BY t.id LIMIT 1
            """,
            new { projectId, actorId, validAfter },
            transaction);
}

using System.Data;
using Dapper;

namespace TodoApp.Data;

/// <summary>statuses の 1 行（DD-02）。</summary>
public sealed class StatusRecord
{
    public long Id { get; init; }
    public string Name { get; init; } = "";
    public long SortOrder { get; init; }
    public bool IsDone { get; init; }
}

/// <summary>statuses の SQL（パラメータ化のみ。DD-01 §4）。</summary>
public static class StatusRepository
{
    public static async Task<IReadOnlyList<StatusRecord>> ListAsync(IDbConnection connection, IDbTransaction transaction) =>
        (await connection.QueryAsync<StatusRecord>(
            "SELECT id AS Id, name AS Name, sort_order AS SortOrder, is_done AS IsDone FROM statuses ORDER BY sort_order, id",
            transaction: transaction).ConfigureAwait(false)).AsList();

    public static Task<long> CountByNameAsync(IDbConnection connection, IDbTransaction transaction, string name, long excludeId) =>
        connection.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM statuses WHERE name = @name AND id <> @excludeId", new { name, excludeId }, transaction);

    public static Task<long> InsertAsync(IDbConnection connection, IDbTransaction transaction, string name) =>
        connection.ExecuteScalarAsync<long>(
            "INSERT INTO statuses (name, sort_order, is_done) SELECT @name, COALESCE(MAX(sort_order), 0) + 1, 0 FROM statuses; SELECT last_insert_rowid();",
            new { name },
            transaction);

    public static Task RenameAsync(IDbConnection connection, IDbTransaction transaction, long id, string name) =>
        connection.ExecuteAsync("UPDATE statuses SET name = @name WHERE id = @id", new { id, name }, transaction);

    public static Task SetSortOrderAsync(IDbConnection connection, IDbTransaction transaction, long id, long sortOrder) =>
        connection.ExecuteAsync("UPDATE statuses SET sort_order = @sortOrder WHERE id = @id", new { id, sortOrder }, transaction);

    public static Task SetDoneAsync(IDbConnection connection, IDbTransaction transaction, long id, bool isDone) =>
        connection.ExecuteAsync("UPDATE statuses SET is_done = @isDone WHERE id = @id", new { id, isDone }, transaction);

    /// <summary>ゴミ箱（deleted_at あり）も含めた全行で数える（復元時に状態が無くならないため。DD-08 §1）。</summary>
    public static Task<long> CountTasksAsync(IDbConnection connection, IDbTransaction transaction, long id) =>
        connection.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM tasks WHERE status_id = @id", new { id }, transaction);

    public static Task DeleteAsync(IDbConnection connection, IDbTransaction transaction, long id) =>
        connection.ExecuteAsync("DELETE FROM statuses WHERE id = @id", new { id }, transaction);
}

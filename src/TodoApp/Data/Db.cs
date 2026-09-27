using System.Data;
using Dapper;
using Microsoft.Data.Sqlite;
using TodoApp.Infrastructure;

namespace TodoApp.Data;

/// <summary>SQLite 接続の生成と書き込みトランザクション（DD-01 §4）。</summary>
public sealed class Db
{
    private const int SqliteBusy = 5;

    public Db(string databasePath)
    {
        DatabasePath = databasePath;
        ConnectionString = new SqliteConnectionStringBuilder { DataSource = databasePath }.ToString();
    }

    public string DatabasePath { get; }

    public string ConnectionString { get; }

    /// <summary>接続ごとに foreign_keys=ON・busy_timeout=5000 を設定して開く。</summary>
    public async Task<SqliteConnection> OpenAsync()
    {
        var connection = new SqliteConnection(ConnectionString);
        try
        {
            await connection.OpenAsync().ConfigureAwait(false);
            await connection.ExecuteAsync("PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;").ConfigureAwait(false);
            return connection;
        }
        catch
        {
            await connection.DisposeAsync().ConfigureAwait(false);
            throw;
        }
    }

    /// <summary>BEGIN IMMEDIATE のトランザクションで実行する。例外ならロールバック。SQLITE_BUSY は E-DB-BUSY。</summary>
    public async Task<T> WriteAsync<T>(Func<IDbConnection, IDbTransaction, Task<T>> work)
    {
        ArgumentNullException.ThrowIfNull(work);
        try
        {
            await using var connection = await OpenAsync().ConfigureAwait(false);
            // deferred:false で BEGIN IMMEDIATE（読み取り→判定→書き込みの間に他の書き込みを挟ませない）
            await using var transaction = connection.BeginTransaction(IsolationLevel.Serializable, deferred: false);
            var result = await work(connection, transaction).ConfigureAwait(false);
            await transaction.CommitAsync().ConfigureAwait(false);
            return result;
        }
        catch (SqliteException ex) when (ex.SqliteErrorCode == SqliteBusy)
        {
            throw new AppErrorException(ErrorIds.DbBusy);
        }
    }
}

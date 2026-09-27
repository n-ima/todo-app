using System.Globalization;
using Microsoft.Data.Sqlite;
using TodoApp.Data;
using TodoApp.Infrastructure;

namespace TodoApp.Cli;

/// <summary>Web 起動時の DB 版の確認（DD-02 §1）。</summary>
public static class StartupSchemaCheck
{
    /// <summary>版が一致すれば null、不一致なら E-SYS-SCHEMA-MISMATCH の文言。</summary>
    public static async Task<string?> GetMismatchMessageAsync(string dataDirectory, string migrationsDirectory)
    {
        var databasePath = Path.Combine(dataDirectory, CliRunner.DatabaseFileName);
        var migrator = new Migrator(new Db(databasePath), new SystemClock(), migrationsDirectory);
        var current = await CliRunner.GetDatabaseVersionAsync(migrator, databasePath).ConfigureAwait(false);
        SqliteConnection.ClearAllPools();
        return current == migrator.ExpectedSchemaVersion
            ? null
            : string.Format(CultureInfo.InvariantCulture, ErrorIds.Messages[ErrorIds.SysSchemaMismatch], current, migrator.ExpectedSchemaVersion);
    }
}

using System.Globalization;
using Dapper;
using Microsoft.Data.Sqlite;
using TodoApp.Data;
using TodoApp.Infrastructure;
using Xunit;

namespace TodoApp.Tests;

public sealed class MigratorTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "todoapp-test-" + Guid.NewGuid().ToString("N"));
    private readonly FakeClock _clock = new(DateTimeOffset.Parse("2026-09-27T01:02:03.456Z", CultureInfo.InvariantCulture));

    public MigratorTests() => Directory.CreateDirectory(_dir);

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        Directory.Delete(_dir, recursive: true);
    }

    private Db NewDb() => new(Path.Combine(_dir, "todo.db"));

    private string MigrationsWith(string name, string sql)
    {
        var dir = Path.Combine(_dir, "migrations");
        Directory.CreateDirectory(dir);
        File.Copy(Path.Combine(Migrator.DefaultMigrationsDirectory, "0001_init.sql"), Path.Combine(dir, "0001_init.sql"));
        File.WriteAllText(Path.Combine(dir, name), sql);
        return dir;
    }

    [Fact]
    public void 同梱の0001_init_DD02のDDLと同一()
    {
        var doc = File.ReadAllText(Path.Combine(InfrastructureTests.FindRepoRoot(), "docs", "02-design", "detailed-design", "DD-02-database.md"))
            .ReplaceLineEndings("\n");
        var start = doc.IndexOf("```sql\n", StringComparison.Ordinal) + "```sql\n".Length;
        var ddl = doc[start..doc.IndexOf("```", start, StringComparison.Ordinal)];
        var file = File.ReadAllText(Path.Combine(Migrator.DefaultMigrationsDirectory, "0001_init.sql")).ReplaceLineEndings("\n");
        Assert.Equal(ddl, file);
    }

    [Fact]
    public async Task Apply_空のDB_全適用され初期3状態とWALと版が入る()
    {
        var db = NewDb();
        var migrator = new Migrator(db, _clock, Migrator.DefaultMigrationsDirectory);

        var applied = await migrator.ApplyAsync();

        Assert.Equal([1], applied.Select(f => f.Version));
        Assert.Equal(migrator.ExpectedSchemaVersion, await migrator.GetCurrentVersionAsync());
        await using var c = await db.OpenAsync();
        var statuses = (await c.QueryAsync<(string Name, long SortOrder, long IsDone)>(
            "SELECT name, sort_order, is_done FROM statuses ORDER BY sort_order")).ToList();
        Assert.Equal([("未着手", 1L, 0L), ("進行中", 2L, 0L), ("完了", 3L, 1L)], statuses);
        Assert.Equal("wal", await c.ExecuteScalarAsync<string>("PRAGMA journal_mode"));
        Assert.Equal("2026-09-27T01:02:03.456Z", await c.ExecuteScalarAsync<string>("SELECT applied_at FROM schema_version"));
    }

    [Fact]
    public async Task Apply_再適用_何もしない()
    {
        var db = NewDb();
        var migrator = new Migrator(db, _clock, Migrator.DefaultMigrationsDirectory);
        await migrator.ApplyAsync();

        var applied = await migrator.ApplyAsync();

        Assert.Empty(applied);
        await using var c = await db.OpenAsync();
        Assert.Equal(1, await c.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM schema_version"));
        Assert.Equal(3, await c.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM statuses"));
    }

    [Fact]
    public async Task Apply_2ファイル目が失敗_そのファイル分だけロールバック()
    {
        var db = NewDb();
        var dir = MigrationsWith("0002_bad.sql", "CREATE TABLE extra (id INTEGER PRIMARY KEY);\nINSERT INTO no_such_table VALUES (1);\n");
        var migrator = new Migrator(db, _clock, dir);

        await Assert.ThrowsAsync<SqliteException>(migrator.ApplyAsync);

        Assert.Equal(2, migrator.ExpectedSchemaVersion);
        Assert.Equal(1, await migrator.GetCurrentVersionAsync());
        await using var c = await db.OpenAsync();
        Assert.Equal(0, await c.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM sqlite_master WHERE name = 'extra'"));
    }

    [Fact]
    public async Task Apply_foreign_keys_offで違反あり_ロールバックしてONに戻す()
    {
        var db = NewDb();
        var dir = MigrationsWith("0002_rebuild.sql",
            "-- requires: foreign_keys=off\nINSERT INTO projects (name, sort_order, created_by, created_at, updated_at) VALUES ('p', 1, 999, 'x', 'x');\n");
        var migrator = new Migrator(db, _clock, dir);

        await Assert.ThrowsAsync<InvalidOperationException>(migrator.ApplyAsync);

        Assert.Equal(1, await migrator.GetCurrentVersionAsync());
        await using var c = await db.OpenAsync();
        Assert.Equal(0, await c.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM projects"));
    }

    [Fact]
    public async Task Apply_foreign_keys_offで違反なし_適用される()
    {
        var db = NewDb();
        var dir = MigrationsWith("0002_rebuild.sql",
            "-- requires: foreign_keys=off\nCREATE TABLE statuses_new AS SELECT * FROM statuses;\nDROP TABLE statuses_new;\n");
        var migrator = new Migrator(db, _clock, dir);

        Assert.Equal(2, (await migrator.ApplyAsync()).Count);
        Assert.Equal(2, await migrator.GetCurrentVersionAsync());
    }

    [Fact]
    public async Task Open_接続ごとにforeign_keysとbusy_timeoutが設定される()
    {
        var db = NewDb();
        await new Migrator(db, _clock, Migrator.DefaultMigrationsDirectory).ApplyAsync();
        await using var c = await db.OpenAsync();
        Assert.Equal(1, await c.ExecuteScalarAsync<long>("PRAGMA foreign_keys"));
        Assert.Equal(5000, await c.ExecuteScalarAsync<long>("PRAGMA busy_timeout"));
    }

    [Fact]
    public async Task WriteAsync_例外_ロールバックされる()
    {
        var db = NewDb();
        await new Migrator(db, _clock, Migrator.DefaultMigrationsDirectory).ApplyAsync();

        await Assert.ThrowsAsync<AppErrorException>(() => db.WriteAsync<int>(async (c, t) =>
        {
            await c.ExecuteAsync("INSERT INTO daily_runs VALUES ('2026-09-27', 'x')", transaction: t);
            throw new AppErrorException(ErrorIds.Validation, "x");
        }));

        await using var conn = await db.OpenAsync();
        Assert.Equal(0, await conn.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM daily_runs"));
    }

    [Fact]
    public async Task WriteAsync_他の書き込みトランザクション中_E_DB_BUSY()
    {
        var db = NewDb();
        await new Migrator(db, _clock, Migrator.DefaultMigrationsDirectory).ApplyAsync();
        await using var holder = await db.OpenAsync();
        await holder.ExecuteAsync("BEGIN IMMEDIATE;");

        // busy_timeout（5 秒）を超えるまで待ってから失敗する
        var ex = await Assert.ThrowsAsync<AppErrorException>(() => db.WriteAsync((c, t) => Task.FromResult(0)));

        Assert.Equal(ErrorIds.DbBusy, ex.ErrorId);
        await holder.ExecuteAsync("ROLLBACK;");
    }
}

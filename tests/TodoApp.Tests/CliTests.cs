using Microsoft.Data.Sqlite;
using TodoApp.Cli;
using TodoApp.Data;
using Xunit;

namespace TodoApp.Tests;

public sealed class CliTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "todoapp-cli-" + Guid.NewGuid().ToString("N"));
    private readonly StringWriter _out = new();
    private readonly StringWriter _err = new();

    public CliTests() => Directory.CreateDirectory(_dir);

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        Directory.Delete(_dir, recursive: true);
        _out.Dispose();
        _err.Dispose();
    }

    private string DataDir => Path.Combine(_dir, "data");

    private Task<int> RunWith(string migrations, params string[] args) =>
        CliRunner.RunAsync([.. args, "--data", DataDir], _out, _err, migrations, () => false);

    private Task<int> Run(params string[] args) => RunWith(Migrator.DefaultMigrationsDirectory, args);

    private string MigrationsWith(params (string Name, string Sql)[] extra)
    {
        var dir = Path.Combine(_dir, "migrations");
        Directory.CreateDirectory(dir);
        File.Copy(Path.Combine(Migrator.DefaultMigrationsDirectory, "0001_init.sql"), Path.Combine(dir, "0001_init.sql"), overwrite: true);
        foreach (var (name, sql) in extra)
        {
            File.WriteAllText(Path.Combine(dir, name), sql);
        }

        return dir;
    }

    [Fact]
    public async Task Init_DBなし_0で作成され版が一致する()
    {
        Assert.Equal(0, await Run("migrate", "--init"));
        Assert.True(File.Exists(Path.Combine(DataDir, "todo.db")));
        Assert.Equal(0, await Run("check-schema"));
    }

    [Fact]
    public async Task Init_既に存在_10()
    {
        await Run("migrate", "--init");
        Assert.Equal(10, await Run("migrate", "--init"));
    }

    [Fact]
    public async Task Plan_未適用あり_0でSQLを表示_未適用なし_3()
    {
        await Run("migrate", "--init");
        var dir = MigrationsWith(("0002_extra.sql", "CREATE TABLE extra (id INTEGER PRIMARY KEY);\n"));

        Assert.Equal(0, await RunWith(dir, "migrate", "--plan"));
        Assert.Contains("0002_extra.sql", _out.ToString(), StringComparison.Ordinal);
        Assert.Contains("CREATE TABLE extra", _out.ToString(), StringComparison.Ordinal);
        Assert.Equal(3, await Run("migrate", "--plan"));
    }

    [Fact]
    public async Task CheckSchema_版が古い_6_Applyで0になり一致()
    {
        await Run("migrate", "--init");
        var dir = MigrationsWith(("0002_extra.sql", "CREATE TABLE extra (id INTEGER PRIMARY KEY);\n"));

        Assert.Equal(6, await RunWith(dir, "check-schema"));
        Assert.Equal(0, await RunWith(dir, "migrate", "--apply"));
        Assert.Equal(0, await RunWith(dir, "check-schema"));
    }

    [Fact]
    public async Task Apply_最初のファイルで失敗_4で版は変わらない()
    {
        await Run("migrate", "--init");
        var dir = MigrationsWith(("0002_bad.sql", "INSERT INTO no_such_table VALUES (1);\n"));

        Assert.Equal(4, await RunWith(dir, "migrate", "--apply"));
        Assert.Equal(6, await RunWith(dir, "check-schema"));
        Assert.Contains("データベースの版: 1", _out.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task Apply_途中で失敗_5()
    {
        await Run("migrate", "--init");
        var dir = MigrationsWith(
            ("0002_ok.sql", "CREATE TABLE extra (id INTEGER PRIMARY KEY);\n"),
            ("0003_bad.sql", "INSERT INTO no_such_table VALUES (1);\n"));

        Assert.Equal(5, await RunWith(dir, "migrate", "--apply"));
        await RunWith(dir, "check-schema");
        Assert.Contains("データベースの版: 2", _out.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task Apply_サービス稼働中_40()
    {
        await Run("migrate", "--init");
        var code = await CliRunner.RunAsync(["migrate", "--apply", "--data", DataDir], _out, _err, Migrator.DefaultMigrationsDirectory, () => true);
        Assert.Equal(40, code);
        Assert.Contains("E-CLI-SERVICE-RUNNING", _err.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task Apply_他がtodo_dbを開いている_40()
    {
        await Run("migrate", "--init");
        await using var holder = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = Path.Combine(DataDir, "todo.db"),
            Pooling = false,
        }.ToString());
        await holder.OpenAsync(TestContext.Current.CancellationToken);

        Assert.Equal(40, await Run("migrate", "--apply"));
    }

    [Fact]
    public async Task Plan_DBなし_1で空ファイルを作らない()
    {
        Assert.Equal(1, await Run("migrate", "--plan"));
        Assert.False(File.Exists(Path.Combine(DataDir, "todo.db")));
    }

    [Fact]
    public async Task 不明な引数_1()
    {
        Assert.Equal(1, await Run("migrate", "--bogus"));
    }

    [Fact]
    public async Task StartupSchemaCheck_一致ならnull_DBなしならE_SYS_SCHEMA_MISMATCHの文言()
    {
        Assert.Equal(
            "データベースの版（0）がアプリの版（1）と一致しません。更新スクリプトを使ってください",
            await StartupSchemaCheck.GetMismatchMessageAsync(DataDir, Migrator.DefaultMigrationsDirectory));
        Assert.False(File.Exists(Path.Combine(DataDir, "todo.db")));

        await Run("migrate", "--init");
        Assert.Null(await StartupSchemaCheck.GetMismatchMessageAsync(DataDir, Migrator.DefaultMigrationsDirectory));
    }
}

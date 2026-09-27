using Microsoft.AspNetCore.Identity;
using Microsoft.Data.Sqlite;
using TodoApp.Cli;
using TodoApp.Data;
using TodoApp.Services;
using Xunit;

namespace TodoApp.Tests;

public sealed class CliTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "todoapp-cli-" + Guid.NewGuid().ToString("N"));
    private TextReader _in = TextReader.Null;
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
        CliRunner.RunAsync([.. args, "--data", DataDir], _in, _out, _err, migrations, () => false);

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
        var code = await CliRunner.RunAsync(["migrate", "--apply", "--data", DataDir], TextReader.Null, _out, _err, Migrator.DefaultMigrationsDirectory, () => true);
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

    private Task<int> CreateAdmin(string loginId, string stdin, string displayName = "管理者")
    {
        _in = new StringReader(stdin);
        return Run("create-admin", "--login-id", loginId, "--display-name", displayName);
    }

    private async Task<T> QueryAsync<T>(string sql)
    {
        await using var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = Path.Combine(DataDir, "todo.db") }.ToString());
        await connection.OpenAsync(TestContext.Current.CancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        return (T)(await command.ExecuteScalarAsync(TestContext.Current.CancellationToken))!;
    }

    [Fact]
    public async Task HasAdmin_いない14_CreateAdminで0になり_いる0()
    {
        await Run("migrate", "--init");
        Assert.Equal(14, await Run("has-admin"));

        Assert.Equal(0, await CreateAdmin("admin", "password-1\nsecond-line\n"));
        Assert.Equal(0, await Run("has-admin"));
    }

    [Fact]
    public async Task CreateAdmin_標準入力1行目がパスワードでmust_change_password_1の管理者()
    {
        await Run("migrate", "--init");
        Assert.Equal(0, await CreateAdmin("admin", "password-1\nignored\n", "管理 太郎"));

        Assert.Equal("admin|管理 太郎|1|1", await QueryAsync<string>(
            "SELECT role || '|' || display_name || '|' || must_change_password || '|' || is_active FROM users WHERE login_id = 'admin'"));
        var hash = await QueryAsync<string>("SELECT password_hash FROM users WHERE login_id = 'admin'");
        Assert.NotEqual(PasswordVerificationResult.Failed, AuthService.PasswordHasher.VerifyHashedPassword(new UserRecord(), hash, "password-1"));
        Assert.Equal(PasswordVerificationResult.Failed, AuthService.PasswordHasher.VerifyHashedPassword(new UserRecord(), hash, "ignored"));
    }

    [Fact]
    public async Task CreateAdmin_重複_大文字小文字違いも11()
    {
        await Run("migrate", "--init");
        Assert.Equal(0, await CreateAdmin("admin", "password-1\n"));
        Assert.Equal(11, await CreateAdmin("ADMIN", "password-2\n"));
        Assert.Contains("E-USER-DUPLICATE", _err.ToString(), StringComparison.Ordinal);
        Assert.Equal(1L, await QueryAsync<long>("SELECT COUNT(*) FROM users"));
    }

    [Theory]
    [InlineData("1234567\n")]
    [InlineData("")]
    public async Task CreateAdmin_パスワード短い_空_12で作成しない(string stdin)
    {
        await Run("migrate", "--init");
        Assert.Equal(12, await CreateAdmin("admin", stdin));
        Assert.Contains("E-PWD-LENGTH", _err.ToString(), StringComparison.Ordinal);
        Assert.Equal(14, await Run("has-admin"));
    }

    [Fact]
    public async Task CreateAdmin_パスワード8文字ちょうどと128文字は可_129文字は12()
    {
        await Run("migrate", "--init");
        Assert.Equal(0, await CreateAdmin("a8", "12345678\n"));
        Assert.Equal(0, await CreateAdmin("a128", new string('x', 128) + "\n"));
        Assert.Equal(12, await CreateAdmin("a129", new string('x', 129) + "\n"));
    }

    [Fact]
    public async Task CreateAdmin_ログインID形式違反_1()
    {
        await Run("migrate", "--init");
        Assert.Equal(1, await CreateAdmin("bad id", "password-1\n"));
        Assert.Contains("E-USER-LOGINID-FORMAT", _err.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task CreateAdmin_引数不足_1()
    {
        await Run("migrate", "--init");
        Assert.Equal(1, await Run("create-admin", "--login-id", "admin"));
    }

    [Fact]
    public async Task HasAdmin_無効な管理者と一般利用者だけなら14()
    {
        await Run("migrate", "--init");
        await CreateAdmin("admin", "password-1\n");
        await CreateAdmin("member1", "password-1\n");
        await using (var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = Path.Combine(DataDir, "todo.db") }.ToString()))
        {
            await connection.OpenAsync(TestContext.Current.CancellationToken);
            await using var command = connection.CreateCommand();
            command.CommandText = "UPDATE users SET is_active = 0 WHERE login_id = 'admin'; UPDATE users SET role = 'member' WHERE login_id = 'member1'";
            await command.ExecuteNonQueryAsync(TestContext.Current.CancellationToken);
        }

        Assert.Equal(14, await Run("has-admin"));
    }

    [Fact]
    public async Task Migrationsフォルダーなし_未処理例外でなく1()
    {
        Assert.Equal(1, await RunWith(Path.Combine(_dir, "no-such-migrations"), "migrate", "--init"));
        Assert.False(File.Exists(Path.Combine(DataDir, "todo.db")));
    }

    [Fact]
    public async Task Init_適用失敗_todo_dbを残さず再実行できる()
    {
        var bad = MigrationsWith(("0002_bad.sql", "INSERT INTO no_such_table VALUES (1);\n"));

        Assert.Equal(1, await RunWith(bad, "migrate", "--init"));
        Assert.False(File.Exists(Path.Combine(DataDir, "todo.db")));
        Assert.Equal(0, await Run("migrate", "--init"));
    }

    [Fact]
    public async Task Apply_途中でファイルを読めないIOException_5()
    {
        await Run("migrate", "--init");
        var dir = MigrationsWith(
            ("0002_ok.sql", "CREATE TABLE extra (id INTEGER PRIMARY KEY);\n"),
            ("0003_locked.sql", "CREATE TABLE extra2 (id INTEGER PRIMARY KEY);\n"));
        using (new FileStream(Path.Combine(dir, "0003_locked.sql"), FileMode.Open, FileAccess.ReadWrite, FileShare.None))
        {
            Assert.Equal(5, await RunWith(dir, "migrate", "--apply"));
        }

        await RunWith(dir, "check-schema");
        Assert.Contains("データベースの版: 2", _out.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task CreateAdmin_PasswordMinLengthの設定値を使う()
    {
        await Run("migrate", "--init");
        await File.WriteAllTextAsync(Path.Combine(DataDir, "appsettings.local.json"), """{ "Auth": { "PasswordMinLength": 10 } }""", TestContext.Current.CancellationToken);

        Assert.Equal(12, await CreateAdmin("a9", "123456789\n"));
        Assert.Equal(0, await CreateAdmin("a10", "1234567890\n"));
    }

    [Fact]
    public async Task 実行ファイル_CLIの出力はUTF8()
    {
        using var process = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo("dotnet")
        {
            ArgumentList = { Path.Combine(AppContext.BaseDirectory, "TodoApp.dll"), "check-schema", "--data", DataDir },
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8,
            UseShellExecute = false,
            CreateNoWindow = true,
        })!;
        var stderr = await process.StandardError.ReadToEndAsync(TestContext.Current.CancellationToken);
        await process.WaitForExitAsync(TestContext.Current.CancellationToken);

        Assert.Equal(1, process.ExitCode);
        Assert.Contains("がありません。先に migrate --init を実行してください", stderr, StringComparison.Ordinal);
    }

    [Fact]
    public void GlobalJson_SDKの版とrollForwardを固定している()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (!File.Exists(Path.Combine(dir!.FullName, "global.json")))
        {
            dir = dir.Parent;
        }

        using var json = System.Text.Json.JsonDocument.Parse(File.ReadAllText(Path.Combine(dir.FullName, "global.json")));
        var sdk = json.RootElement.GetProperty("sdk");
        Assert.Equal("10.0.401", sdk.GetProperty("version").GetString());
        Assert.Equal("latestFeature", sdk.GetProperty("rollForward").GetString());
    }
}

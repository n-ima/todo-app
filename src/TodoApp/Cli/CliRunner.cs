using System.ServiceProcess;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Data.Sqlite;
using TodoApp.Data;
using TodoApp.Infrastructure;

namespace TodoApp.Cli;

/// <summary>CLI の終了コード（DD-10 §1）。</summary>
public static class ExitCodes
{
    public const int Success = 0;
    public const int Failure = 1;
    public const int NoPending = 3;
    public const int ApplyFailedFirst = 4;
    public const int ApplyFailedPartial = 5;
    public const int SchemaMismatch = 6;
    public const int AlreadyExists = 10;
    public const int DuplicateLoginId = 11;
    public const int PasswordRuleViolation = 12;
    public const int NoAdmin = 14;
    public const int ServiceRunning = 40;
}

/// <summary>CLI サブコマンドの振り分け（DD-10 §1）。</summary>
public static class CliRunner
{
    public const string DefaultDataDirectory = @"C:\TodoApp\data";
    public const string DatabaseFileName = "todo.db";
    private const string ServiceName = "TodoApp";

    private static readonly string[] Subcommands = ["migrate", "check-schema", "create-admin", "has-admin"];

    public static bool IsSubcommand(string arg) => Subcommands.Contains(arg, StringComparer.Ordinal);

    private const string Usage =
        "使い方: TodoApp.exe migrate --init|--plan|--apply / check-schema / create-admin --login-id <id> --display-name <名前>（パスワードは標準入力） / has-admin  [--data <dir>]";

    public static Task<int> RunAsync(string[] args, TextReader input, TextWriter output, TextWriter error) =>
        RunAsync(args, input, output, error, Migrator.DefaultMigrationsDirectory, IsServiceRunning);

    public static async Task<int> RunAsync(
        string[] args, TextReader input, TextWriter output, TextWriter error, string migrationsDirectory, Func<bool> isServiceRunning)
    {
        ArgumentNullException.ThrowIfNull(args);
        ArgumentNullException.ThrowIfNull(input);
        ArgumentNullException.ThrowIfNull(output);
        ArgumentNullException.ThrowIfNull(error);
        ArgumentNullException.ThrowIfNull(isServiceRunning);

        if (!TryParse(args, out var command, out var flags, out var options, out var dataDirectory))
        {
            await error.WriteLineAsync(Usage).ConfigureAwait(false);
            return ExitCodes.Failure;
        }

        var databasePath = Path.Combine(dataDirectory, DatabaseFileName);
        var db = new Db(databasePath);
        try
        {
            // migrations フォルダーが無い等の失敗も終了コード 1 で返すため try の内側で生成する
            var migrator = new Migrator(db, new SystemClock(), migrationsDirectory);
            return (command, flags) switch
            {
                ("migrate", "--init") => await InitAsync(migrator, databasePath, dataDirectory, output, error).ConfigureAwait(false),
                ("migrate", "--plan") => await PlanAsync(migrator, databasePath, output, error).ConfigureAwait(false),
                ("migrate", "--apply") => await ApplyAsync(migrator, databasePath, output, error, isServiceRunning).ConfigureAwait(false),
                ("check-schema", "") => await CheckSchemaAsync(migrator, databasePath, output, error).ConfigureAwait(false),
                ("create-admin", "") when options.TryGetValue("--login-id", out var loginId) && options.TryGetValue("--display-name", out var displayName)
                    => await CreateAdminAsync(db, databasePath, dataDirectory, loginId, displayName, input, output, error).ConfigureAwait(false),
                ("has-admin", "") => await HasAdminAsync(db, databasePath, output, error).ConfigureAwait(false),
                _ => await UsageAsync(error).ConfigureAwait(false),
            };
        }
        catch (Exception ex) when (ex is SqliteException or IOException or UnauthorizedAccessException or InvalidOperationException)
        {
            await error.WriteLineAsync(ex.Message).ConfigureAwait(false);
            return ExitCodes.Failure;
        }
        finally
        {
            // CLI 終了後に todo.db をプールが開いたままにしない
            SqliteConnection.ClearAllPools();
        }
    }

    /// <summary>
    /// DB の版。todo.db が無ければ 0（接続を開くと空ファイルが作られ、後の migrate --init が 10 になるため開かない）。
    /// </summary>
    public static async Task<int> GetDatabaseVersionAsync(Migrator migrator, string databasePath)
    {
        ArgumentNullException.ThrowIfNull(migrator);
        return File.Exists(databasePath) ? await migrator.GetCurrentVersionAsync().ConfigureAwait(false) : 0;
    }

    private static readonly string[] ValueOptions = ["--data", "--login-id", "--display-name"];

    private static bool TryParse(
        string[] args, out string command, out string flags, out Dictionary<string, string> options, out string dataDirectory)
    {
        command = args.Length > 0 ? args[0] : "";
        options = new Dictionary<string, string>(StringComparer.Ordinal);
        var rest = new List<string>();
        for (var i = 1; i < args.Length; i++)
        {
            if (ValueOptions.Contains(args[i], StringComparer.Ordinal))
            {
                if (i + 1 >= args.Length)
                {
                    flags = "";
                    dataDirectory = DefaultDataDirectory;
                    return false;
                }

                options[args[i]] = args[i + 1];
                i++;
            }
            else
            {
                rest.Add(args[i]);
            }
        }

        flags = string.Join(' ', rest);
        dataDirectory = options.GetValueOrDefault("--data", DefaultDataDirectory);
        return IsSubcommand(command);
    }

    private static async Task<int> UsageAsync(TextWriter error)
    {
        await error.WriteLineAsync(Usage).ConfigureAwait(false);
        return ExitCodes.Failure;
    }

    private static async Task<bool> RequireDatabaseAsync(string databasePath, TextWriter error)
    {
        if (File.Exists(databasePath))
        {
            return true;
        }

        await error.WriteLineAsync($"{databasePath} がありません。先に migrate --init を実行してください").ConfigureAwait(false);
        return false;
    }

    private static async Task<int> InitAsync(Migrator migrator, string databasePath, string dataDirectory, TextWriter output, TextWriter error)
    {
        if (File.Exists(databasePath))
        {
            await error.WriteLineAsync($"{databasePath} は既に存在します").ConfigureAwait(false);
            return ExitCodes.AlreadyExists;
        }

        Directory.CreateDirectory(dataDirectory);
        IReadOnlyList<MigrationFile> applied;
        try
        {
            applied = await migrator.ApplyAsync().ConfigureAwait(false);
        }
        catch
        {
            // 作りかけの todo.db を残すと再実行が 10（既に存在）になるため消してから失敗を返す
            SqliteConnection.ClearAllPools();
            foreach (var suffix in new[] { "", "-wal", "-shm" })
            {
                File.Delete(databasePath + suffix);
            }

            throw;
        }

        foreach (var file in applied)
        {
            await output.WriteLineAsync($"適用: {file.Name}").ConfigureAwait(false);
        }

        await output.WriteLineAsync($"{databasePath} を作成しました（版 {migrator.ExpectedSchemaVersion}）").ConfigureAwait(false);
        return ExitCodes.Success;
    }

    private static async Task<int> PlanAsync(Migrator migrator, string databasePath, TextWriter output, TextWriter error)
    {
        if (!await RequireDatabaseAsync(databasePath, error).ConfigureAwait(false))
        {
            return ExitCodes.Failure;
        }

        var pending = await migrator.GetPendingAsync().ConfigureAwait(false);
        if (pending.Count == 0)
        {
            await output.WriteLineAsync("未適用のスキーマ変更はありません").ConfigureAwait(false);
            return ExitCodes.NoPending;
        }

        foreach (var file in pending)
        {
            await output.WriteLineAsync($"-- {file.Name}").ConfigureAwait(false);
            await output.WriteLineAsync(await File.ReadAllTextAsync(file.FilePath).ConfigureAwait(false)).ConfigureAwait(false);
        }

        return ExitCodes.Success;
    }

    private static async Task<int> ApplyAsync(
        Migrator migrator, string databasePath, TextWriter output, TextWriter error, Func<bool> isServiceRunning)
    {
        if (!await RequireDatabaseAsync(databasePath, error).ConfigureAwait(false))
        {
            return ExitCodes.Failure;
        }

        if (isServiceRunning() || IsDatabaseOpenByOtherProcess(databasePath))
        {
            await error.WriteLineAsync($"{ErrorIds.CliServiceRunning}: {ErrorIds.Messages[ErrorIds.CliServiceRunning]}").ConfigureAwait(false);
            return ExitCodes.ServiceRunning;
        }

        var before = await migrator.GetCurrentVersionAsync().ConfigureAwait(false);
        try
        {
            var applied = await migrator.ApplyAsync().ConfigureAwait(false);
            foreach (var file in applied)
            {
                await output.WriteLineAsync($"適用: {file.Name}").ConfigureAwait(false);
            }

            return ExitCodes.Success;
        }
        catch (Exception ex) when (ex is SqliteException or InvalidOperationException or IOException)
        {
            var after = await migrator.GetCurrentVersionAsync().ConfigureAwait(false);
            await error.WriteLineAsync(ex.Message).ConfigureAwait(false);
            if (after == before)
            {
                await error.WriteLineAsync("最初のファイルで失敗しました。データベースは変更されていません").ConfigureAwait(false);
                return ExitCodes.ApplyFailedFirst;
            }

            await error.WriteLineAsync($"版 {after} まで適用した後に失敗しました（適用済みの分は取り消せません）").ConfigureAwait(false);
            return ExitCodes.ApplyFailedPartial;
        }
    }

    private static async Task<int> CheckSchemaAsync(Migrator migrator, string databasePath, TextWriter output, TextWriter error)
    {
        if (!await RequireDatabaseAsync(databasePath, error).ConfigureAwait(false))
        {
            return ExitCodes.Failure;
        }

        var current = await migrator.GetCurrentVersionAsync().ConfigureAwait(false);
        await output.WriteLineAsync($"データベースの版: {current} / アプリの版: {migrator.ExpectedSchemaVersion}").ConfigureAwait(false);
        return current == migrator.ExpectedSchemaVersion ? ExitCodes.Success : ExitCodes.SchemaMismatch;
    }

    /// <summary>ログイン ID の形式（DD-03 §4）。</summary>
    internal static readonly Regex LoginIdPattern = new("^[A-Za-z0-9._-]{1,50}$", RegexOptions.CultureInvariant);

    private static async Task WriteErrorAsync(TextWriter error, string errorId) =>
        await error.WriteLineAsync($"{errorId}: {ErrorIds.Messages[errorId]}").ConfigureAwait(false);

    private static async Task<int> CreateAdminAsync(
        Db db, string databasePath, string dataDirectory, string loginId, string displayName, TextReader input, TextWriter output, TextWriter error)
    {
        if (!await RequireDatabaseAsync(databasePath, error).ConfigureAwait(false))
        {
            return ExitCodes.Failure;
        }

        if (!LoginIdPattern.IsMatch(loginId))
        {
            await WriteErrorAsync(error, ErrorIds.UserLoginidFormat).ConfigureAwait(false);
            return ExitCodes.Failure;
        }

        if (displayName.Length is < 1 or > 50)
        {
            await error.WriteLineAsync("表示名は 1 文字以上 50 文字以下にしてください").ConfigureAwait(false);
            return ExitCodes.Failure;
        }

        // 秘密の値はプロセス一覧に残さないため引数ではなく標準入力の 1 行目で受け取る（DD-10 §1）
        var password = await input.ReadLineAsync().ConfigureAwait(false) ?? "";
        if (password.Length < ReadPasswordMinLength(dataDirectory) || password.Length > 128)
        {
            await WriteErrorAsync(error, ErrorIds.PwdLength).ConfigureAwait(false);
            return ExitCodes.PasswordRuleViolation;
        }

        var hash = Services.AuthService.PasswordHasher.HashPassword(new UserRecord(), password);
        var now = UserRepository.FormatUtc(DateTimeOffset.UtcNow);
        var created = await db.WriteAsync(async (connection, transaction) =>
        {
            if (await UserRepository.FindByLoginIdAsync(connection, transaction, loginId).ConfigureAwait(false) is not null)
            {
                return false;
            }

            await UserRepository.InsertAsync(connection, transaction, loginId, displayName, "admin", hash, now).ConfigureAwait(false);
            return true;
        }).ConfigureAwait(false);

        if (!created)
        {
            await WriteErrorAsync(error, ErrorIds.UserDuplicate).ConfigureAwait(false);
            return ExitCodes.DuplicateLoginId;
        }

        await output.WriteLineAsync($"管理者 {loginId} を作成しました（初回ログイン時にパスワード変更が必要です）").ConfigureAwait(false);
        return ExitCodes.Success;
    }

    private static async Task<int> HasAdminAsync(Db db, string databasePath, TextWriter output, TextWriter error)
    {
        if (!await RequireDatabaseAsync(databasePath, error).ConfigureAwait(false))
        {
            return ExitCodes.Failure;
        }

        var count = await new UserRepository(db).CountActiveAdminsAsync().ConfigureAwait(false);
        await output.WriteLineAsync(count > 0 ? $"有効な管理者: {count} 人" : "有効な管理者がいません").ConfigureAwait(false);
        return count > 0 ? ExitCodes.Success : ExitCodes.NoAdmin;
    }

    /// <summary>Auth:PasswordMinLength（DD-01 §7）。Web と同じく appsettings.json に data の appsettings.local.json を重ねて読む。</summary>
    private static int ReadPasswordMinLength(string dataDirectory)
    {
        var configuration = new ConfigurationBuilder()
            .AddJsonFile(Path.Combine(AppContext.BaseDirectory, "appsettings.json"), optional: false)
            .AddJsonFile(Path.Combine(Path.GetFullPath(dataDirectory), AppConfiguration.LocalSettingsFileName), optional: true)
            .Build();
        return configuration.GetValue<int>($"{AuthOptions.Section}:{nameof(AuthOptions.PasswordMinLength)}");
    }

    /// <summary>コンソールの入出力を UTF-8 にしてから CLI を実行する（DD-10 §1「出力は UTF-8」）。</summary>
    public static Task<int> RunConsoleAsync(string[] args)
    {
        var utf8 = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);
        Console.InputEncoding = utf8;
        Console.OutputEncoding = utf8;
        return RunAsync(args, Console.In, Console.Out, Console.Error);
    }

    private static bool IsServiceRunning()
    {
        if (!OperatingSystem.IsWindows())
        {
            return false;
        }

        try
        {
            using var service = new ServiceController(ServiceName);
            return service.Status != ServiceControllerStatus.Stopped;
        }
        catch (InvalidOperationException)
        {
            // サービス未登録（開発機・インストール前）は稼働していない
            return false;
        }
    }

    /// <summary>他プロセスが todo.db を開いていれば排他オープンに失敗する。</summary>
    private static bool IsDatabaseOpenByOtherProcess(string databasePath)
    {
        SqliteConnection.ClearAllPools();
        try
        {
            using var stream = new FileStream(databasePath, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            return false;
        }
        catch (IOException)
        {
            return true;
        }
    }
}

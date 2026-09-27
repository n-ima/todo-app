using System.ServiceProcess;
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
    public const int ServiceRunning = 40;
}

/// <summary>CLI サブコマンドの振り分け（DD-10 §1）。</summary>
public static class CliRunner
{
    public const string DefaultDataDirectory = @"C:\TodoApp\data";
    public const string DatabaseFileName = "todo.db";
    private const string ServiceName = "TodoApp";

    private static readonly string[] Subcommands = ["migrate", "check-schema"];

    public static bool IsSubcommand(string arg) => Subcommands.Contains(arg, StringComparer.Ordinal);

    public static Task<int> RunAsync(string[] args, TextWriter output, TextWriter error) =>
        RunAsync(args, output, error, Migrator.DefaultMigrationsDirectory, IsServiceRunning);

    public static async Task<int> RunAsync(
        string[] args, TextWriter output, TextWriter error, string migrationsDirectory, Func<bool> isServiceRunning)
    {
        ArgumentNullException.ThrowIfNull(args);
        ArgumentNullException.ThrowIfNull(output);
        ArgumentNullException.ThrowIfNull(error);
        ArgumentNullException.ThrowIfNull(isServiceRunning);

        if (!TryParse(args, out var command, out var flags, out var dataDirectory))
        {
            await error.WriteLineAsync("使い方: TodoApp.exe migrate --init|--plan|--apply [--data <dir>] / check-schema [--data <dir>]").ConfigureAwait(false);
            return ExitCodes.Failure;
        }

        var databasePath = Path.Combine(dataDirectory, DatabaseFileName);
        var migrator = new Migrator(new Db(databasePath), new SystemClock(), migrationsDirectory);
        try
        {
            return (command, flags) switch
            {
                ("migrate", "--init") => await InitAsync(migrator, databasePath, dataDirectory, output, error).ConfigureAwait(false),
                ("migrate", "--plan") => await PlanAsync(migrator, databasePath, output, error).ConfigureAwait(false),
                ("migrate", "--apply") => await ApplyAsync(migrator, databasePath, output, error, isServiceRunning).ConfigureAwait(false),
                ("check-schema", "") => await CheckSchemaAsync(migrator, databasePath, output, error).ConfigureAwait(false),
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

    private static bool TryParse(string[] args, out string command, out string flags, out string dataDirectory)
    {
        command = args.Length > 0 ? args[0] : "";
        dataDirectory = DefaultDataDirectory;
        var rest = new List<string>();
        for (var i = 1; i < args.Length; i++)
        {
            if (args[i] == "--data")
            {
                if (i + 1 >= args.Length)
                {
                    flags = "";
                    return false;
                }

                dataDirectory = args[++i];
            }
            else
            {
                rest.Add(args[i]);
            }
        }

        flags = string.Join(' ', rest);
        return IsSubcommand(command);
    }

    private static async Task<int> UsageAsync(TextWriter error)
    {
        await error.WriteLineAsync("使い方: TodoApp.exe migrate --init|--plan|--apply [--data <dir>] / check-schema [--data <dir>]").ConfigureAwait(false);
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
        var applied = await migrator.ApplyAsync().ConfigureAwait(false);
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
        catch (Exception ex) when (ex is SqliteException or InvalidOperationException)
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

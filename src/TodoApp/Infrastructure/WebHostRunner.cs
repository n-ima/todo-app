using System.Diagnostics;
using System.Globalization;
using Serilog;
using TodoApp.Cli;
using TodoApp.Data;

namespace TodoApp.Infrastructure;

/// <summary>
/// Web ホストの起動。起動失敗（Host 構築・版不一致・ポート使用中・証明書読み込み失敗）は
/// ログファイルとイベントログ（ソース TodoApp）に書いて終了コード 1 を返す（DD-01 §8・DD-02 §1）。
/// </summary>
public static class WebHostRunner
{
    public const string EventLogSource = "TodoApp";

    public static Task<int> RunAsync(string[] args) => RunAsync(args, Console.Error, WriteWindowsEventLog);

    public static async Task<int> RunAsync(string[] args, TextWriter error, Action<string> writeEventLog)
    {
        ArgumentNullException.ThrowIfNull(error);
        ArgumentNullException.ThrowIfNull(writeEventLog);
        string? dataDirectory = null;
        WebApplication? app = null;
        try
        {
            var builder = WebApplication.CreateBuilder(WebHostSetup.CreateOptions(args));
            dataDirectory = builder.Configuration[$"{PathsOptions.Section}:Data"];
            AppConfiguration.AddLocalSettings(builder.Configuration);
            AppConfiguration.AddAppServices(builder.Services, builder.Configuration);
            WebHostSetup.ConfigureServices(builder);
            app = builder.Build();
            dataDirectory = app.Configuration[$"{PathsOptions.Section}:Data"];

            var mismatch = await StartupSchemaCheck.GetMismatchMessageAsync(
                dataDirectory ?? CliRunner.DefaultDataDirectory, Migrator.DefaultMigrationsDirectory).ConfigureAwait(false);
            if (mismatch is not null)
            {
                // DD-02 §1: 版が一致しなければ自動適用せずに終了する
                await app.DisposeAsync().ConfigureAwait(false);
                app = null;
                await ReportAsync($"{ErrorIds.SysSchemaMismatch}: {mismatch}", null, dataDirectory, error, writeEventLog).ConfigureAwait(false);
                return 1;
            }

            WebHostSetup.ConfigurePipeline(app);
            await app.RunAsync().ConfigureAwait(false);
            return 0;
        }
        // WebApplicationFactory は Build 時に HostAbortedException で起動を打ち切るため、起動失敗として扱わない
        catch (Exception ex) when (ex is not HostAbortedException)
        {
            if (app is not null)
            {
                // アプリの Serilog がログファイルを掴んだままだと書けないため、先に閉じる
                await app.DisposeAsync().ConfigureAwait(false);
            }

            await ReportAsync("起動に失敗しました", ex, dataDirectory, error, writeEventLog).ConfigureAwait(false);
            return 1;
        }
    }

    private static async Task ReportAsync(string message, Exception? exception, string? dataDirectory, TextWriter error, Action<string> writeEventLog)
    {
        var logPath = Path.Combine(dataDirectory ?? CliRunner.DefaultDataDirectory, "logs", "app-.log");
        using (var logger = new LoggerConfiguration()
            .WriteTo.File(logPath, rollingInterval: RollingInterval.Day, retainedFileCountLimit: null, formatProvider: CultureInfo.InvariantCulture)
            .CreateLogger())
        {
            logger.Fatal(exception, "{StartupFailure}", message);
        }

        var text = exception is null ? message : $"{message}: {exception}";
        await error.WriteLineAsync(text).ConfigureAwait(false);
        try
        {
            writeEventLog(text);
        }
        catch (Exception ex) when (ex is System.Security.SecurityException or InvalidOperationException or UnauthorizedAccessException)
        {
            // ソース未登録（インストール前の手動起動）でも終了コードとログファイルは残す。書けなかったことは標準エラーに出す
            await error.WriteLineAsync($"イベントログに書けませんでした: {ex.Message}").ConfigureAwait(false);
        }
    }

    private static void WriteWindowsEventLog(string text)
    {
        if (OperatingSystem.IsWindows())
        {
            // イベントログ 1 件の上限（約 31,839 文字）を超えると書けないため切り詰める
            EventLog.WriteEntry(EventLogSource, text.Length > 30000 ? text[..30000] : text, EventLogEntryType.Error);
        }
    }
}

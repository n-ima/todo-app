using Microsoft.Extensions.Logging.EventLog;
using TodoApp.Cli;
using TodoApp.Data;
using TodoApp.Infrastructure;

if (args.Length > 0 && CliRunner.IsSubcommand(args[0]))
{
    return await CliRunner.RunAsync(args, Console.In, Console.Out, Console.Error);
}

var builder = WebApplication.CreateBuilder(WebHostSetup.CreateOptions(args));
AppConfiguration.AddLocalSettings(builder.Configuration);
AppConfiguration.AddAppServices(builder.Services, builder.Configuration);
WebHostSetup.ConfigureServices(builder);
// 起動失敗はイベントログ（ソース TodoApp）にも書く（DD-01 §8）
if (OperatingSystem.IsWindows())
{
    // ラムダ内は外側の IsWindows ガードを解析が追えないため抑止する
#pragma warning disable CA1416
    builder.Services.Configure<EventLogSettings>(s => s.SourceName = "TodoApp");
#pragma warning restore CA1416
}

var app = builder.Build();

var mismatch = await StartupSchemaCheck.GetMismatchMessageAsync(
    app.Configuration[$"{PathsOptions.Section}:Data"] ?? CliRunner.DefaultDataDirectory, Migrator.DefaultMigrationsDirectory);
if (mismatch is not null)
{
    // DD-02 §1: 版が一致しなければ自動適用せずに終了する
    StartupSchemaCheck.LogMismatch(app.Logger, ErrorIds.SysSchemaMismatch, mismatch);
    await Console.Error.WriteLineAsync($"{ErrorIds.SysSchemaMismatch}: {mismatch}");
    return 1;
}

WebHostSetup.ConfigurePipeline(app);
app.Run();
return 0;

// WebApplicationFactory<Program> からテストで参照するため公開する
public partial class Program;

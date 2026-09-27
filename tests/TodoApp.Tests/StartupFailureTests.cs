using TodoApp.Cli;
using TodoApp.Infrastructure;
using Xunit;

namespace TodoApp.Tests;

// レビュー M3: 起動失敗はログファイルとイベントログに残し、終了コード 1 で終わる（DD-01 §8・DD-02 §1）
[Collection(WebHostSerial.Name)]
public sealed class StartupFailureTests : IDisposable
{
    private readonly string _dataDir = Path.Combine(Path.GetTempPath(), "todoapp-startup-" + Guid.NewGuid().ToString("N"));
    private readonly List<string> _eventLog = [];
    private readonly StringWriter _error = new();

    public StartupFailureTests() => Directory.CreateDirectory(_dataDir);

    public void Dispose()
    {
        _error.Dispose();
        Directory.Delete(_dataDir, recursive: true);
    }

    private Task<int> Run() => WebHostRunner.RunAsync(["--Paths:Data=" + _dataDir], _error, _eventLog.Add);

    private string ReadLog()
    {
        var files = Directory.GetFiles(Path.Combine(_dataDir, "logs"), "app-*.log");
        return string.Concat(files.Select(File.ReadAllText));
    }

    [Fact]
    public async Task Run_DBの版が不一致_終了コード1でログとイベントログにE_SYS_SCHEMA_MISMATCH()
    {
        Assert.Equal(1, await Run());

        Assert.Contains(ErrorIds.SysSchemaMismatch, ReadLog(), StringComparison.Ordinal);
        Assert.Contains(_eventLog, e => e.Contains(ErrorIds.SysSchemaMismatch, StringComparison.Ordinal));
        Assert.False(File.Exists(Path.Combine(_dataDir, CliRunner.DatabaseFileName)));
    }

    [Fact]
    public async Task Run_構成ファイルが不正_終了コード1でログとイベントログに例外()
    {
        await File.WriteAllTextAsync(Path.Combine(_dataDir, AppConfiguration.LocalSettingsFileName), "{ \"Auth\": ", TestContext.Current.CancellationToken);

        Assert.Equal(1, await Run());

        var log = ReadLog();
        Assert.Contains("起動に失敗しました", log, StringComparison.Ordinal);
        Assert.Contains(AppConfiguration.LocalSettingsFileName, log, StringComparison.Ordinal);
        Assert.Contains(_eventLog, e => e.Contains(AppConfiguration.LocalSettingsFileName, StringComparison.Ordinal));
    }
}

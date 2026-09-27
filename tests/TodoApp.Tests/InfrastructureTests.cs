using System.Globalization;
using System.Reflection;
using Microsoft.Extensions.Configuration;
using TodoApp.Infrastructure;
using Xunit;

namespace TodoApp.Tests;

public sealed class InfrastructureTests
{
    [Theory]
    [InlineData("2026-09-27T14:59:59Z", "2026-09-27")]
    [InlineData("2026-09-27T15:00:00Z", "2026-09-28")]
    public void FakeClock_Today_UTC15時で日本の暦日が変わる(string utc, string expected)
    {
        var clock = new FakeClock(DateTimeOffset.Parse(utc, CultureInfo.InvariantCulture));
        Assert.Equal(DateOnly.Parse(expected, CultureInfo.InvariantCulture), clock.Today);
    }

    [Fact]
    public void SystemClock_Today_UtcNowの東京暦日と一致()
    {
        var clock = new SystemClock();
        Assert.Equal(TokyoTime.ToTokyoDate(clock.UtcNow), clock.Today);
    }

    [Fact]
    public void ErrorIds_DD12の全IDと1対1で文言を持つ()
    {
        var doc = File.ReadAllLines(Path.Combine(FindRepoRoot(), "docs", "02-design", "detailed-design", "DD-12-errors.md"));
        var docIds = doc.Where(l => l.StartsWith("| E-", StringComparison.Ordinal))
            .Select(l => l.Split('|')[1].Trim()).ToHashSet();
        var constIds = typeof(ErrorIds).GetFields(BindingFlags.Public | BindingFlags.Static)
            .Where(f => f.IsLiteral).Select(f => (string)f.GetRawConstantValue()!).ToHashSet();

        Assert.Equal(46, docIds.Count);
        Assert.Equal(docIds.Count, constIds.Count);
        Assert.True(docIds.SetEquals(constIds));
        Assert.True(docIds.SetEquals(ErrorIds.Messages.Keys));
    }

    [Fact]
    public void AppErrorException_差し込み値つき_IDと整形済み文言を持つ()
    {
        var ex = new AppErrorException(ErrorIds.LockHeld, "山田 花子", "10:42");
        Assert.Equal("E-LOCK-HELD", ex.ErrorId);
        Assert.Equal("山田 花子さんが編集中です（10:42 から）", ex.Message);
    }

    [Fact]
    public void AddLocalSettings_データフォルダのlocalが既定値を上書きする()
    {
        var dir = Directory.CreateTempSubdirectory("todoapp-test-").FullName;
        try
        {
            File.WriteAllText(Path.Combine(dir, AppConfiguration.LocalSettingsFileName), "{ \"Backup\": { \"SharePath\": \"X:\\\\share\" } }");
            var config = new ConfigurationManager();
            config.AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["Paths:Data"] = dir,
                ["Backup:SharePath"] = "",
            });
            AppConfiguration.AddLocalSettings(config);
            Assert.Equal(@"X:\share", config["Backup:SharePath"]);
        }
        finally
        {
            Directory.Delete(dir, true);
        }
    }

    [Fact]
    public void AppSettings_DD01の全キーが既定値を持つ()
    {
        var config = new ConfigurationBuilder()
            .AddJsonFile(Path.Combine(FindRepoRoot(), "src", "TodoApp", "appsettings.json"))
            .Build();
        string[] keys =
        [
            "Auth:MaxFailedAttempts", "Auth:LockoutMinutes", "Auth:SessionIdleHours", "Auth:PasswordMinLength",
            "Lock:IdleMinutes", "Lock:AutosaveSeconds", "Memo:MaxLength", "Trash:RetentionDays",
            "Backup:Generations", "Backup:RetryMinutes", "Backup:WarnAfterFailedDays", "Backup:SharePath",
            "Daily:CheckIntervalMinutes", "Log:RetentionDays", "Calendar:MonthMaxPerDay", "Paths:Data",
        ];
        Assert.All(keys, k => Assert.NotNull(config[k]));
    }

    private static string FindRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "TodoApp.sln")))
        {
            dir = dir.Parent;
        }
        return dir?.FullName ?? throw new InvalidOperationException("TodoApp.sln が見つからない");
    }
}
